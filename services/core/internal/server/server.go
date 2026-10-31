// Package server is the long-running core service: it owns the event engine,
// ingests observations from vision-worker, persists incidents, captures and
// encrypts evidence, and serves the staff API and live event stream.
package server

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/alerts"
	"github.com/saimouli3/zeoatap/services/core/internal/auth"
	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/evidence"
	"github.com/saimouli3/zeoatap/services/core/internal/hub"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

type Config struct {
	InternalToken  string        // shared secret vision-worker presents on /internal, and core presents to it
	VisionURL      string        // vision-worker control API, e.g. http://127.0.0.1:8081
	ClipSpoolDir   string        // where vision-worker writes plaintext clips before encryption
	ClipPre        time.Duration // video before the event start
	ClipPost       time.Duration // video after the event start
	RetentionEvery time.Duration // how often expired clips are shredded
	WebDir         string        // built dashboard (static export); optional

	TickEvery      time.Duration // engine clock tick (default 1 s)
	PingEvery      time.Duration // SSE keep-alive comment (default 15 s)
	ClipTokenTTL   time.Duration // clip media token lifetime (default 5 min)
	IngestTimeout  time.Duration // max wait for engine queue space (default 2 s)
	EngineStaleAge time.Duration // engine without a tick for this long is unhealthy (default 5 s)
}

func (c *Config) defaults() {
	set := func(d *time.Duration, v time.Duration) {
		if *d <= 0 {
			*d = v
		}
	}
	set(&c.ClipPre, 10*time.Second)
	set(&c.ClipPost, 20*time.Second)
	set(&c.RetentionEvery, time.Hour)
	set(&c.TickEvery, time.Second)
	set(&c.PingEvery, 15*time.Second)
	set(&c.ClipTokenTTL, 5*time.Minute)
	set(&c.IngestTimeout, 2*time.Second)
	set(&c.EngineStaleAge, 5*time.Second)
}

// engineErrorHold keeps a transient engine error visible long enough for the
// dashboard to notice it.
const engineErrorHold = 10 * time.Second

type Server struct {
	Cfg    Config
	DB     *store.DB
	Hub    *hub.Hub
	Auth   *auth.Service
	Vault  *evidence.Vault
	Alerts *alerts.Dispatcher
	Log    *slog.Logger

	engine *eventengine.Engine
	obsCh  chan eventengine.Observation
	ctlCh  chan func(*eventengine.Engine)

	mu          sync.RWMutex
	known       map[string]bool // cameras registered with the engine
	camStates   map[string]camState
	engineErr   string
	engineErrAt time.Time
	lastEngine  time.Time
	clipPending map[string]bool
	clipTokens  map[string]clipToken

	clips      sync.WaitGroup
	httpClient *http.Client
	startedAt  time.Time
}

type camState struct {
	State string
	Since time.Time
}

type clipToken struct {
	clipID  string
	userID  int64
	user    string
	expires time.Time
	viewed  bool
}

func New(cfg Config, db *store.DB, h *hub.Hub, a *auth.Service, v *evidence.Vault, al *alerts.Dispatcher,
	log *slog.Logger, cams []eventengine.Camera, rules []eventengine.Rule) *Server {
	cfg.defaults()
	known := map[string]bool{}
	for _, c := range cams {
		known[c.ID] = true
	}
	return &Server{
		Cfg: cfg, DB: db, Hub: h, Auth: a, Vault: v, Alerts: al, Log: log,
		engine:      eventengine.New(cams, rules, db),
		obsCh:       make(chan eventengine.Observation, 1024),
		ctlCh:       make(chan func(*eventengine.Engine), 16),
		known:       known,
		camStates:   map[string]camState{},
		clipPending: map[string]bool{},
		clipTokens:  map[string]clipToken{},
		httpClient:  &http.Client{}, // per-request deadlines are set with contexts
		startedAt:   time.Now().UTC(),
	}
}

// Run owns the engine: observations and the clock tick are processed on this
// one goroutine, so the engine needs no locking. It returns when ctx ends,
// after in-flight clip captures finish.
func (s *Server) Run(ctx context.Context) {
	tick := time.NewTicker(s.Cfg.TickEvery)
	defer tick.Stop()
	retention := time.NewTicker(s.Cfg.RetentionEvery)
	defer retention.Stop()
	var bg sync.WaitGroup
	defer bg.Wait()
	defer s.clips.Wait()
	bg.Add(1)
	go func() { defer bg.Done(); s.Alerts.Run(ctx) }()
	retentionBusy := make(chan struct{}, 1)
	for {
		// Control functions (camera registration) go first, so an
		// observation for a just-added camera is never processed before it.
		select {
		case f := <-s.ctlCh:
			f(s.engine)
			continue
		default:
		}
		var out []eventengine.Output
		var err error
		select {
		case <-ctx.Done():
			return
		case f := <-s.ctlCh:
			f(s.engine)
			continue
		case o := <-s.obsCh:
			out, err = s.engine.Process(o)
		case now := <-tick.C:
			out, err = s.engine.Tick(now)
			s.pruneClipTokens(now)
		case <-retention.C:
			select {
			case retentionBusy <- struct{}{}:
				bg.Add(1)
				go func() {
					defer bg.Done()
					defer func() { <-retentionBusy }()
					if _, err := s.RunRetention(ctx); err != nil {
						s.Log.Error("retention", "err", err)
					}
					if err := s.DB.PurgeSessions(ctx); err != nil {
						s.Log.Error("purge sessions", "err", err)
					}
				}()
			default: // previous run still busy
			}
			continue
		}
		now := time.Now()
		s.mu.Lock()
		s.lastEngine = now
		if err != nil {
			s.engineErr, s.engineErrAt = err.Error(), now
		}
		s.mu.Unlock()
		if err != nil {
			s.Log.Error("event engine", "err", err)
		}
		for _, ev := range out {
			s.handle(ctx, ev)
		}
	}
}

// engineHealth reports whether the engine loop is alive and error-free. A
// stalled loop would freeze every camera at its last state, so it must never
// be shown as healthy.
func (s *Server) engineHealth(now time.Time) (ok bool, msg string) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	switch {
	case s.lastEngine.IsZero():
		return false, "engine not started"
	case now.Sub(s.lastEngine) > s.Cfg.EngineStaleAge:
		return false, fmt.Sprintf("engine stalled: last tick %.0fs ago", now.Sub(s.lastEngine).Seconds())
	case s.engineErr != "" && now.Sub(s.engineErrAt) < engineErrorHold:
		return false, s.engineErr
	}
	return true, ""
}

func (s *Server) audit(ctx context.Context, actor, action, subject string, payload any) {
	if payload == nil {
		payload = map[string]any{}
	}
	if err := s.DB.Audit(ctx, actor, action, subject, payload); err != nil {
		s.Log.Error("audit write failed", "action", action, "subject", subject, "err", err)
	}
}

// publishIncident sends an incident event, keeping SHADOW incidents away from
// clients that may not see them.
func (s *Server) publishIncident(typ string, inc *store.IncidentView) {
	if inc.Status == eventengine.StatusShadow {
		s.Hub.PublishShadow(typ, inc)
		return
	}
	s.Hub.Publish(typ, inc)
}

// publishFor sends a non-incident event about an incident, with the same
// SHADOW filtering as the incident itself.
func (s *Server) publishFor(ctx context.Context, incidentID, typ string, data any) {
	inc, err := s.DB.IncidentView(ctx, incidentID)
	if err != nil || inc.Status == eventengine.StatusShadow {
		s.Hub.PublishShadow(typ, data) // unknown status: fail closed
		return
	}
	s.Hub.Publish(typ, data)
}

func (s *Server) handle(ctx context.Context, ev eventengine.Output) {
	switch ev.Kind {
	case "state_change":
		now := time.Now().UTC()
		s.mu.Lock()
		s.camStates[ev.CameraID] = camState{State: string(ev.State), Since: now}
		s.mu.Unlock()
		if err := s.DB.SetCameraState(ctx, ev.CameraID, string(ev.State), now); err != nil {
			s.Log.Error("save camera state", "camera", ev.CameraID, "err", err)
		}
		s.Hub.Publish("camera_state", map[string]any{"camera_id": ev.CameraID, "state": ev.State, "since": now})
	case "incident_created", "incident_merged":
		inc, err := s.DB.IncidentView(ctx, ev.Incident.ID)
		if err != nil {
			s.Log.Error("load incident", "id", ev.Incident.ID, "err", err)
			return
		}
		if ev.Kind == "incident_created" {
			s.audit(ctx, "system", "incident_created", inc.ID, map[string]any{
				"event_type": inc.EventType, "status": inc.Status, "rule_version": inc.RuleVersion,
				"model_versions": inc.ModelVersions, "source": inc.Source})
		}
		// Raise is idempotent per level, so a merge that upgrades an
		// incident to ALERTED raises it exactly once.
		if inc.Status == eventengine.StatusAlerted {
			if err := s.Alerts.Raise(ctx, *inc, 0); err != nil {
				s.Log.Error("raise alert", "incident", inc.ID, "err", err)
			}
		}
		s.publishIncident(ev.Kind, inc)
		s.scheduleClip(inc.ID, ev.CameraID, inc.Start)
	}
}

// ---- evidence ---------------------------------------------------------------

func (s *Server) scheduleClip(incidentID, cameraID string, start time.Time) {
	if s.Cfg.VisionURL == "" || cameraID == "" {
		return
	}
	key := incidentID + "/" + cameraID
	s.mu.Lock()
	if s.clipPending[key] {
		s.mu.Unlock()
		return
	}
	s.clipPending[key] = true
	s.mu.Unlock()
	s.clips.Add(1)
	go func() {
		defer s.clips.Done()
		if err := s.captureClip(incidentID, cameraID, start.Add(-s.Cfg.ClipPre), start.Add(s.Cfg.ClipPost)); err != nil {
			s.Log.Error("evidence clip", "incident", incidentID, "camera", cameraID, "err", err)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			s.audit(ctx, "system", "clip_failed", incidentID, map[string]any{"camera_id": cameraID, "error": err.Error()})
			s.publishFor(ctx, incidentID, "clip_failed", map[string]any{"incident_id": incidentID, "camera_id": cameraID, "error": err.Error()})
		}
	}()
}

type clipReq struct {
	CameraID string  `json:"camera_id"`
	Start    float64 `json:"start"`
	End      float64 `json:"end"`
	Path     string  `json:"path"`
}

type clipResp struct {
	Path    string `json:"path"`
	SHA256  string `json:"sha256"`
	Partial bool   `json:"partial"`
	Error   string `json:"error"`
}

// clipGrace is how long vision-worker may take after the window ends.
const clipGrace = 150 * time.Second

func (s *Server) captureClip(incidentID, cameraID string, start, end time.Time) error {
	clipID := fmt.Sprintf("%s-%s", incidentID, cameraID)
	spool := filepath.Join(s.Cfg.ClipSpoolDir, clipID+".mp4")
	deadline := time.Now()
	if end.After(deadline) {
		deadline = end
	}
	ctx, cancel := context.WithDeadline(context.Background(), deadline.Add(clipGrace))
	defer cancel()

	body, err := json.Marshal(clipReq{CameraID: cameraID, Start: float64(start.UnixNano()) / 1e9,
		End: float64(end.UnixNano()) / 1e9, Path: spool})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(s.Cfg.VisionURL, "/")+"/v1/clips", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+s.Cfg.InternalToken)
	resp, err := s.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var cr clipResp
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&cr); err != nil && resp.StatusCode == http.StatusOK {
		os.Remove(spool)
		return fmt.Errorf("vision-worker: bad clip response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		os.Remove(spool)
		return fmt.Errorf("vision-worker: %d %s", resp.StatusCode, cr.Error)
	}
	// Only ever read (and then delete) a file inside the spool directory.
	src, err := s.inSpool(cr.Path)
	if err != nil {
		os.Remove(spool)
		return err
	}
	stored, err := s.Vault.Store(clipID, src)
	if err != nil {
		os.Remove(src)
		return err
	}
	if cr.SHA256 != "" && !strings.EqualFold(stored.SHA256, cr.SHA256) {
		if rerr := s.Vault.Remove(stored.Path); rerr != nil {
			s.Log.Error("remove mismatched clip", "clip", clipID, "err", rerr)
		}
		return errors.New("clip hash changed between capture and encryption")
	}
	dbctx, dbcancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer dbcancel()
	rec := store.ClipRecord{ID: clipID, IncidentID: incidentID, CameraID: cameraID, Path: stored.Path,
		SHA256: stored.SHA256, SizeBytes: stored.Size, WrappedKey: stored.WrappedKey, Partial: cr.Partial,
		Start: start, End: end}
	if err := s.DB.InsertClip(dbctx, rec); err != nil {
		return err
	}
	s.audit(dbctx, "system", "clip_stored", clipID, map[string]any{"sha256": stored.SHA256, "partial": cr.Partial})
	s.publishFor(dbctx, incidentID, "clip_ready", map[string]any{"incident_id": incidentID, "clip_id": clipID, "partial": cr.Partial})
	return nil
}

func (s *Server) inSpool(p string) (string, error) {
	if p == "" {
		return "", errors.New("vision-worker returned no clip path")
	}
	spool, err := filepath.Abs(s.Cfg.ClipSpoolDir)
	if err != nil {
		return "", err
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(spool, abs)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
		return "", fmt.Errorf("vision-worker returned a clip path outside the spool directory")
	}
	return abs, nil
}

// RunRetention crypto-shreds clips past their retention deadline that are not
// under legal hold, and returns how many it deleted.
func (s *Server) RunRetention(ctx context.Context) (int, error) {
	clips, err := s.DB.ExpiredClips(ctx)
	if err != nil {
		return 0, fmt.Errorf("list expired clips: %w", err)
	}
	n := 0
	var errs []error
	for _, c := range clips {
		if err := s.DB.ShredClip(ctx, c.ID); err != nil {
			errs = append(errs, fmt.Errorf("shred %s: %w", c.ID, err))
			continue
		}
		n++
		if err := s.Vault.Remove(c.Path); err != nil {
			s.Log.Warn("retention: key destroyed but file not removed", "clip", c.ID, "err", err)
		}
		s.audit(ctx, "system", "clip_deleted_retention", c.ID, map[string]any{"incident_id": c.IncidentID})
	}
	if n > 0 {
		s.Log.Info("retention: deleted expired clips", "count", n)
	}
	return n, errors.Join(errs...)
}

// AddCamera registers a camera with the running engine.
func (s *Server) AddCamera(ctx context.Context, c eventengine.Camera) error {
	select {
	case s.ctlCh <- func(e *eventengine.Engine) { e.SetCamera(c) }:
	case <-ctx.Done():
		return ctx.Err()
	}
	s.mu.Lock()
	s.known[c.ID] = true
	s.mu.Unlock()
	return nil
}

func (s *Server) knownCamera(id string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.known[id]
}

func bearerOK(r *http.Request, token string) bool {
	got := r.Header.Get("Authorization")
	want := "Bearer " + token
	return token != "" && subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1
}
