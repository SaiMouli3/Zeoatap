package server

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/crypto/bcrypt"

	"github.com/saimouli3/zeoatap/services/core/internal/alerts"
	"github.com/saimouli3/zeoatap/services/core/internal/auth"
	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/evidence"
	"github.com/saimouli3/zeoatap/services/core/internal/hub"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
	"github.com/saimouli3/zeoatap/services/core/internal/testutil/pgtest"
)

const (
	testPassword = "correct horse battery"
	testToken    = "test-internal-token-0123456789"
)

// Test users, one per role.
var roleUser = map[string]string{"caregiver": "care", "lead": "lead", "reviewer": "rev", "admin": "adm"}

func rect(x1, y1, x2, y2 float64) []eventengine.Point {
	return []eventengine.Point{{X: x1, Y: y1}, {X: x2, Y: y1}, {X: x2, Y: y2}, {X: x1, Y: y2}}
}

var testCams = []store.Camera{
	{ID: "cam1", RoomID: "infants-a", Name: "Changing table", Protected: true, Enabled: true,
		AuthorizedBy: "Director", AuthorizedAt: "2026-10-01", AuthorizationScope: "Room A",
		Zones: []eventengine.Zone{
			{ID: "table", RoomZone: "changing-table-1", Kind: eventengine.ZoneSurface, Polygon: rect(0.1, 0.3, 0.4, 0.5)},
			{ID: "door", RoomZone: "kitchen-door", Kind: eventengine.ZoneRestricted, Polygon: rect(0.8, 0.5, 1.0, 1.0)},
		}},
	{ID: "cam2", RoomID: "infants-a", Name: "Overview", Enabled: true,
		AuthorizedBy: "Director", AuthorizedAt: "2026-10-01", AuthorizationScope: "Room A",
		Zones: []eventengine.Zone{{ID: "door-b", RoomZone: "kitchen-door", Kind: eventengine.ZoneRestricted, Polygon: rect(0, 0.4, 0.3, 1)}}},
}

type env struct {
	t        *testing.T
	s        *Server
	db       *store.DB
	dbURL    string
	api      *httptest.Server
	internal *httptest.Server
	vision   *httptest.Server
	visionN  atomic.Int32
	cancel   context.CancelFunc
	done     chan struct{}
}

type envOpts struct {
	cfg     Config
	rules   []eventengine.Rule
	noRun   bool // do not start the engine loop
	webhook string
}

func quietLog() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func newEnv(t *testing.T, o envOpts) *env {
	t.Helper()
	db, dbURL := pgtest.New(t)
	ctx := context.Background()
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(db.UpsertFacility(ctx, "f1", "Sunflower Nursery"))
	must(db.UpsertRoom(ctx, "f1", store.Room{ID: "infants-a", Name: "Infants A"}))
	must(db.UpsertRoom(ctx, "f1", store.Room{ID: "toddlers", Name: "Toddlers"}))
	var engineCams []eventengine.Camera
	for _, c := range testCams {
		must(db.UpsertCamera(ctx, c))
		engineCams = append(engineCams, c.EngineCamera())
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(testPassword), bcrypt.MinCost)
	must(err)
	for role, name := range roleUser {
		_, err := db.CreateUser(ctx, store.User{Username: name, DisplayName: strings.ToUpper(name), Role: role, PasswordHash: string(hash)})
		must(err)
	}

	key := make([]byte, 32)
	_, err = rand.Read(key)
	must(err)
	vault, err := evidence.NewVault(t.TempDir(), key)
	must(err)

	e := &env{t: t, db: db, dbURL: dbURL, done: make(chan struct{})}
	e.vision = httptest.NewServer(http.HandlerFunc(e.fakeVision))
	cfg := o.cfg
	cfg.InternalToken = testToken
	cfg.VisionURL = e.vision.URL
	cfg.ClipSpoolDir = t.TempDir()
	rules := o.rules
	if rules == nil {
		rules = eventengine.DefaultRules(nil)
	}
	h := hub.New()
	disp := &alerts.Dispatcher{DB: db, Hub: h, Client: &http.Client{Timeout: 2 * time.Second}, Log: quietLog(),
		Cfg: alerts.Config{WebhookURL: o.webhook, AckTimeout: time.Hour, MaxLevel: 2, Interval: 20 * time.Millisecond}}
	e.s = New(cfg, db, h, &auth.Service{DB: db}, vault, disp, quietLog(), engineCams, rules)
	e.api = httptest.NewServer(e.s.Handler())
	e.internal = httptest.NewServer(e.s.InternalHandler())

	runCtx, cancel := context.WithCancel(context.Background())
	e.cancel = cancel
	if o.noRun {
		close(e.done)
	} else {
		go func() { defer close(e.done); e.s.Run(runCtx) }()
	}
	t.Cleanup(func() {
		e.stop()
		e.api.CloseClientConnections()
		e.api.Close()
		e.internal.CloseClientConnections()
		e.internal.Close()
		e.vision.Close()
	})
	return e
}

// stop ends the engine loop and waits for it (and in-flight clips).
func (e *env) stop() {
	e.cancel()
	<-e.done
}

// fakeVision stands in for vision-worker's POST /v1/clips: it writes a small
// plaintext clip where core asked and reports its hash.
func (e *env) fakeVision(w http.ResponseWriter, r *http.Request) {
	e.visionN.Add(1)
	if r.Header.Get("Authorization") != "Bearer "+testToken || r.URL.Path != "/v1/clips" {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	var req clipReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.End <= req.Start {
		http.Error(w, `{"error":"bad request"}`, http.StatusBadRequest)
		return
	}
	data := []byte(strings.Repeat("clip-for-"+req.CameraID+";", 100))
	if err := os.WriteFile(req.Path, data, 0o600); err != nil {
		http.Error(w, `{"error":"write"}`, http.StatusInternalServerError)
		return
	}
	sum := sha256.Sum256(data)
	writeJSON(w, http.StatusOK, map[string]any{"path": req.Path, "sha256": hex.EncodeToString(sum[:]), "partial": false})
}

// client returns an HTTP client signed in as the user for role ("" = anonymous).
func (e *env) client(role string) *http.Client {
	e.t.Helper()
	jar, _ := cookiejar.New(nil)
	c := &http.Client{Jar: jar, Timeout: 10 * time.Second}
	if role == "" {
		return c
	}
	st, body := e.do(c, "POST", "/api/v1/login", `{"username":"`+roleUser[role]+`","password":"`+testPassword+`"}`)
	if st != http.StatusOK {
		e.t.Fatalf("login %s: %d %s", role, st, body)
	}
	return c
}

func (e *env) do(c *http.Client, method, path, body string) (int, []byte) {
	e.t.Helper()
	return e.doType(c, method, path, body, "application/json")
}

func (e *env) doType(c *http.Client, method, path, body, ctype string) (int, []byte) {
	e.t.Helper()
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, e.api.URL+path, rd)
	if err != nil {
		e.t.Fatal(err)
	}
	if method == http.MethodPost && ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	resp, err := c.Do(req)
	if err != nil {
		e.t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, b
}

func (e *env) getJSON(c *http.Client, path string, v any) {
	e.t.Helper()
	st, b := e.do(c, "GET", path, "")
	if st != http.StatusOK {
		e.t.Fatalf("GET %s: %d %s", path, st, b)
	}
	if err := json.Unmarshal(b, v); err != nil {
		e.t.Fatalf("GET %s: %v in %s", path, err, b)
	}
}

// ingest posts observations as NDJSON to the internal listener.
func (e *env) ingest(token string, obs ...eventengine.Observation) (int, map[string]any) {
	e.t.Helper()
	var sb strings.Builder
	for _, o := range obs {
		b, err := json.Marshal(o)
		if err != nil {
			e.t.Fatal(err)
		}
		sb.Write(b)
		sb.WriteByte('\n')
	}
	return e.ingestRaw(token, sb.String())
}

func (e *env) ingestRaw(token, body string) (int, map[string]any) {
	e.t.Helper()
	req, _ := http.NewRequest("POST", e.internal.URL+"/internal/v1/observations", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/x-ndjson")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

type sseEvent struct {
	Type string
	Data string
}

type sseStream struct {
	C      chan sseEvent
	status int
	pings  atomic.Int32
	cancel context.CancelFunc
}

// stream opens GET /api/v1/events and parses frames into C.
func (e *env) stream(c *http.Client) *sseStream {
	e.t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "GET", e.api.URL+"/api/v1/events", nil)
	nc := *c
	nc.Timeout = 0
	resp, err := nc.Do(req)
	if err != nil {
		cancel()
		e.t.Fatal(err)
	}
	s := &sseStream{C: make(chan sseEvent, 256), status: resp.StatusCode, cancel: cancel}
	e.t.Cleanup(cancel)
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		close(s.C)
		return s
	}
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		e.t.Errorf("events content type %q", ct)
	}
	go func() {
		defer resp.Body.Close()
		defer close(s.C)
		sc := bufio.NewScanner(resp.Body)
		var ev sseEvent
		for sc.Scan() {
			line := sc.Text()
			switch {
			case line == ": ping":
				s.pings.Add(1)
			case strings.HasPrefix(line, "event: "):
				ev.Type = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				ev.Data = strings.TrimPrefix(line, "data: ")
			case line == "" && ev.Type != "":
				s.C <- ev
				ev = sseEvent{}
			}
		}
	}()
	return s
}

// next returns the next event of one of the given types, skipping others.
func (s *sseStream) next(t *testing.T, within time.Duration, types ...string) sseEvent {
	t.Helper()
	deadline := time.After(within)
	for {
		select {
		case ev, ok := <-s.C:
			if !ok {
				t.Fatalf("event stream closed while waiting for %v", types)
			}
			for _, typ := range types {
				if ev.Type == typ {
					return ev
				}
			}
		case <-deadline:
			t.Fatalf("no %v event within %v", types, within)
		}
	}
}

// drain collects every event that arrives within d.
func (s *sseStream) drain(d time.Duration) []sseEvent {
	var out []sseEvent
	deadline := time.After(d)
	for {
		select {
		case ev, ok := <-s.C:
			if !ok {
				return out
			}
			out = append(out, ev)
		case <-deadline:
			return out
		}
	}
}

// eventually polls cond until it holds or the timeout passes.
func eventually(t *testing.T, within time.Duration, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatalf("timed out after %v waiting for %s", within, what)
}

// Observation builders. Boxes are [x1,y1,x2,y2] with the foot at (x, y).

func adultAt(id string, x, y float64) eventengine.Track {
	return eventengine.Track{ID: id, Role: eventengine.RoleAdult, Score: 0.9, Box: [4]float64{x - 0.05, y - 0.3, x + 0.05, y}, PoseQuality: 0.8, Visibility: 1}
}

func infantAt(id string, x, y float64) eventengine.Track {
	return eventengine.Track{ID: id, Role: eventengine.RoleInfant, Score: 0.8, Box: [4]float64{x - 0.04, y - 0.08, x + 0.04, y}, PoseQuality: 0.7, Visibility: 1}
}

func observation(cam string, ts time.Time, tracks ...eventengine.Track) eventengine.Observation {
	if tracks == nil {
		tracks = []eventengine.Track{}
	}
	return eventengine.Observation{CameraID: cam, CaptureTS: ts, Source: eventengine.SourceMock, AIAvailable: true,
		ModelVersions: map[string]string{"detector": "mock-0"}, Tracks: tracks}
}
