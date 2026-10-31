package server

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/auth"
	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

// maxBody bounds every staff API request body.
const maxBody = 1 << 20

// Handler returns the public (staff) HTTP handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/login", s.login)
	mux.HandleFunc("POST /api/v1/logout", s.logout)
	mux.HandleFunc("GET /api/v1/me", s.require("", s.me))
	mux.HandleFunc("GET /api/v1/overview", s.require(auth.PermViewStatus, s.overview))
	mux.HandleFunc("GET /api/v1/cameras", s.require(auth.PermViewStatus, s.listCameras))
	mux.HandleFunc("POST /api/v1/cameras", s.require(auth.PermAdmin, s.createCamera))
	mux.HandleFunc("GET /api/v1/incidents", s.require(auth.PermViewStatus, s.listIncidents))
	mux.HandleFunc("GET /api/v1/incidents/{id}", s.require(auth.PermViewStatus, s.getIncident))
	mux.HandleFunc("POST /api/v1/incidents/{id}/ack", s.require(auth.PermAcknowledge, s.ackIncident))
	mux.HandleFunc("POST /api/v1/incidents/{id}/disposition", s.require(auth.PermDisposition, s.dispositionIncident))
	mux.HandleFunc("POST /api/v1/incidents/{id}/hold", s.require(auth.PermLegalHold, s.holdIncident))
	mux.HandleFunc("POST /api/v1/clips/{id}/token", s.require(auth.PermViewClips, s.clipToken))
	mux.HandleFunc("GET /api/v1/clips/{id}/media", s.clipMedia)
	mux.HandleFunc("GET /api/v1/events", s.require(auth.PermViewStatus, s.events))
	mux.HandleFunc("GET /api/v1/users", s.require(auth.PermAdmin, s.listUsers))
	mux.HandleFunc("POST /api/v1/users", s.require(auth.PermAdmin, s.createUser))
	mux.HandleFunc("GET /api/v1/audit", s.require(auth.PermAdmin, s.listAudit))
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /readyz", s.readyz)
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		writeErr(w, http.StatusNotFound, "no such endpoint")
	})
	if s.Cfg.WebDir != "" {
		mux.Handle("/", s.webHandler())
	} else {
		mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) { writeErr(w, http.StatusNotFound, "not found") })
	}
	return s.middleware(mux)
}

func (s *Server) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		if strings.HasPrefix(r.URL.Path, "/api/") {
			h.Set("Cache-Control", "no-store")
			// JSON-only POSTs plus SameSite=Strict cookies are the CSRF defence.
			if r.Method == http.MethodPost {
				mt, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
				if err != nil || mt != "application/json" {
					writeErr(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json")
					return
				}
			}
			r.Body = http.MaxBytesReader(w, r.Body, maxBody)
		}
		next.ServeHTTP(w, r)
	})
}

type authedHandler func(w http.ResponseWriter, r *http.Request, u *store.User)

// require resolves the session (401 without one) and checks the permission
// (403 without it). An empty permission means any signed-in user.
func (s *Server) require(p auth.Permission, h authedHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		u, err := s.Auth.UserFrom(r)
		if errors.Is(err, store.ErrNotFound) {
			writeErr(w, http.StatusUnauthorized, "not signed in")
			return
		}
		if err != nil {
			s.Log.Error("session lookup", "err", err)
			writeErr(w, http.StatusServiceUnavailable, "session check failed")
			return
		}
		if p != "" && !auth.Can(u.Role, p) {
			writeErr(w, http.StatusForbidden, "your role does not allow this")
			return
		}
		h(w, r.WithContext(auth.WithUser(r.Context(), u)), u)
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func (s *Server) serverErr(w http.ResponseWriter, what string, err error) {
	s.Log.Error(what, "err", err)
	writeErr(w, http.StatusInternalServerError, what+" failed")
}

// decode reads a JSON body. An empty body is allowed when optional is set.
func decode(r *http.Request, v any, optional bool) error {
	err := json.NewDecoder(r.Body).Decode(v)
	if errors.Is(err, io.EOF) && optional {
		return nil
	}
	if err != nil {
		var mbe *http.MaxBytesError
		if errors.As(err, &mbe) {
			return errors.New("request body too large")
		}
		return fmt.Errorf("invalid JSON body: %v", err)
	}
	return nil
}

// ---- session -----------------------------------------------------------------

type meResp struct {
	User        *store.User       `json:"user"`
	Permissions []auth.Permission `json:"permissions"`
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := decode(r, &req, false); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	name := req.Username
	if len(name) > 64 {
		name = name[:64]
	}
	if req.Username == "" || req.Password == "" || len(req.Username) > 64 || len(req.Password) > 256 {
		s.audit(r.Context(), name, "login_failed", name, map[string]any{"reason": "malformed"})
		writeErr(w, http.StatusUnauthorized, auth.ErrInvalidLogin.Error())
		return
	}
	u, err := s.Auth.Login(r.Context(), w, req.Username, req.Password)
	if errors.Is(err, auth.ErrInvalidLogin) {
		s.audit(r.Context(), name, "login_failed", name, nil)
		writeErr(w, http.StatusUnauthorized, err.Error())
		return
	}
	if err != nil {
		s.serverErr(w, "login", err)
		return
	}
	s.audit(r.Context(), u.Username, "login", u.Username, map[string]any{"role": u.Role})
	writeJSON(w, http.StatusOK, meResp{User: u, Permissions: auth.Permissions(u.Role)})
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	if u, err := s.Auth.UserFrom(r); err == nil {
		s.audit(r.Context(), u.Username, "logout", u.Username, nil)
	}
	s.Auth.Logout(r.Context(), w, r)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) me(w http.ResponseWriter, r *http.Request, u *store.User) {
	writeJSON(w, http.StatusOK, meResp{User: u, Permissions: auth.Permissions(u.Role)})
}

// ---- monitoring state --------------------------------------------------------

var stateRank = map[string]int{
	string(eventengine.StateOperational):    0,
	string(eventengine.StateVideoDegraded):  1,
	string(eventengine.StateAIUnavailable):  2,
	string(eventengine.StateNeverConnected): 3,
	string(eventengine.StateCameraUnavail):  4,
}

func rank(st string) int {
	if r, ok := stateRank[st]; ok {
		return r
	}
	return stateRank[string(eventengine.StateCameraUnavail)] // unknown: fail closed
}

func worse(a, b string) string {
	if rank(b) > rank(a) {
		return b
	}
	return a
}

// cameraState is what staff are shown for one camera. It starts from the
// engine's latest report and is never better than the engine's own health
// allows: if the engine loop is stalled or failing, nothing is analysed, so no
// camera may look operational.
func (s *Server) cameraState(id string, enabled, engineOK bool, now time.Time) (string, time.Time) {
	st, since := string(eventengine.StateNeverConnected), s.startedAt
	if enabled {
		s.mu.RLock()
		cs, ok := s.camStates[id]
		s.mu.RUnlock()
		if ok {
			st, since = cs.State, cs.Since
		}
	}
	if _, ok := stateRank[st]; !ok {
		st = string(eventengine.StateCameraUnavail)
	}
	if !engineOK && rank(st) < rank(string(eventengine.StateAIUnavailable)) {
		st, since = string(eventengine.StateAIUnavailable), now
	}
	return st, since
}

type overviewCamera struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	State     string    `json:"state"`
	Since     time.Time `json:"since"`
	Protected bool      `json:"protected"`
}

type overviewRoom struct {
	ID      string           `json:"id"`
	Name    string           `json:"name"`
	State   string           `json:"state"`
	Cameras []overviewCamera `json:"cameras"`
}

type overviewResp struct {
	Facility     store.Facility    `json:"facility"`
	State        string            `json:"state"`
	Rooms        []overviewRoom    `json:"rooms"`
	OpenAlerts   int               `json:"open_alerts"`
	AlertsHealth map[string]int    `json:"alerts_health"`
	Services     map[string]string `json:"services"`
	GeneratedAt  time.Time         `json:"generated_at"`
}

// alertDelayAfter is when a pending alert counts as delayed (ARCH §10).
const alertDelayAfter = 10 * time.Second

func (s *Server) overview(w http.ResponseWriter, r *http.Request, _ *store.User) {
	ctx := r.Context()
	now := time.Now().UTC()
	engineOK, engineMsg := s.engineHealth(now)

	fac, err := s.DB.Facility(ctx)
	if errors.Is(err, store.ErrNotFound) {
		fac, err = &store.Facility{}, nil
	}
	if err != nil {
		s.serverErr(w, "load facility", err)
		return
	}
	rooms, err := s.DB.Rooms(ctx)
	if err != nil {
		s.serverErr(w, "load rooms", err)
		return
	}
	cams, err := s.DB.Cameras(ctx)
	if err != nil {
		s.serverErr(w, "load cameras", err)
		return
	}
	open, err := s.DB.OpenAlertCount(ctx)
	if err != nil {
		s.serverErr(w, "count alerts", err)
		return
	}
	failed, delayed, err := s.DB.AlertHealth(ctx, alertDelayAfter)
	if err != nil {
		s.serverErr(w, "alert health", err)
		return
	}

	byRoom := map[string][]overviewCamera{}
	for _, c := range cams {
		st, since := s.cameraState(c.ID, c.Enabled, engineOK, now)
		byRoom[c.RoomID] = append(byRoom[c.RoomID], overviewCamera{ID: c.ID, Name: c.Name, State: st, Since: since, Protected: c.Protected})
	}
	resp := overviewResp{
		Facility: *fac, Rooms: []overviewRoom{}, OpenAlerts: open,
		AlertsHealth: map[string]int{"failed": failed, "delayed": delayed},
		Services:     map[string]string{"engine": "ok", "engine_error": "", "database": "ok", "evidence": "ok"},
		GeneratedAt:  now,
	}
	// A facility with no rooms monitors nothing.
	facState := string(eventengine.StateNeverConnected)
	for i, rm := range rooms {
		rc := byRoom[rm.ID]
		st := string(eventengine.StateNeverConnected) // no cameras: not monitored
		if len(rc) > 0 {
			st = string(eventengine.StateOperational)
			for _, c := range rc {
				st = worse(st, c.State)
			}
		} else {
			rc = []overviewCamera{}
		}
		if i == 0 {
			facState = st
		} else {
			facState = worse(facState, st)
		}
		resp.Rooms = append(resp.Rooms, overviewRoom{ID: rm.ID, Name: rm.Name, State: st, Cameras: rc})
	}
	resp.State = facState
	if !engineOK {
		resp.Services["engine"], resp.Services["engine_error"] = "error", engineMsg
	}
	if err := s.DB.Ping(ctx); err != nil {
		resp.Services["database"] = "error"
	}
	if err := s.Vault.CheckWritable(); err != nil {
		resp.Services["evidence"] = "error"
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	if err := s.DB.Ping(r.Context()); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "database unreachable"})
		return
	}
	if ok, msg := s.engineHealth(time.Now()); !ok {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": msg})
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// ---- cameras -----------------------------------------------------------------

func (s *Server) withState(c store.Camera, engineOK bool, now time.Time) store.Camera {
	st, since := s.cameraState(c.ID, c.Enabled, engineOK, now)
	c.State, c.StateSince = st, &since
	return c
}

func (s *Server) listCameras(w http.ResponseWriter, r *http.Request, _ *store.User) {
	cams, err := s.DB.Cameras(r.Context())
	if err != nil {
		s.serverErr(w, "load cameras", err)
		return
	}
	now := time.Now().UTC()
	engineOK, _ := s.engineHealth(now)
	out := make([]store.Camera, 0, len(cams))
	for _, c := range cams {
		out = append(out, s.withState(c, engineOK, now))
	}
	writeJSON(w, http.StatusOK, out)
}

var idRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

// ValidateCamera checks a camera record, including its written authorization
// (US-05) and zone geometry.
func ValidateCamera(c store.Camera) error {
	switch {
	case !idRe.MatchString(c.ID):
		return errors.New("camera id must be 1-64 letters, digits, '.', '_' or '-'")
	case c.RoomID == "":
		return errors.New("room_id is required")
	case strings.TrimSpace(c.Name) == "":
		return errors.New("name is required")
	case strings.TrimSpace(c.AuthorizedBy) == "":
		return errors.New("authorized_by is required")
	case strings.TrimSpace(c.AuthorizationScope) == "":
		return errors.New("authorization_scope is required")
	}
	if _, err := time.Parse("2006-01-02", c.AuthorizedAt); err != nil {
		return errors.New("authorized_at must be a date (YYYY-MM-DD)")
	}
	seen := map[string]bool{}
	for _, z := range c.Zones {
		if z.ID == "" || z.RoomZone == "" {
			return errors.New("every zone needs id and room_zone")
		}
		if seen[z.ID] {
			return fmt.Errorf("duplicate zone id %q", z.ID)
		}
		seen[z.ID] = true
		if z.Kind != eventengine.ZoneRestricted && z.Kind != eventengine.ZoneSurface {
			return fmt.Errorf("zone %s: kind must be %q or %q", z.ID, eventengine.ZoneRestricted, eventengine.ZoneSurface)
		}
		if len(z.Polygon) < 3 {
			return fmt.Errorf("zone %s: polygon needs at least 3 points", z.ID)
		}
		for _, p := range z.Polygon {
			if p.X < 0 || p.X > 1 || p.Y < 0 || p.Y > 1 {
				return fmt.Errorf("zone %s: polygon points must be normalized to [0,1]", z.ID)
			}
		}
	}
	return nil
}

func (s *Server) createCamera(w http.ResponseWriter, r *http.Request, u *store.User) {
	var req struct {
		store.Camera
		Enabled *bool `json:"enabled"`
	}
	if err := decode(r, &req, false); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	c := req.Camera
	c.Enabled = req.Enabled == nil || *req.Enabled
	c.State, c.StateSince = "", nil
	if c.Zones == nil {
		c.Zones = []eventengine.Zone{}
	}
	if err := ValidateCamera(c); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	switch err := s.DB.InsertCamera(r.Context(), c); {
	case errors.Is(err, store.ErrConflict):
		writeErr(w, http.StatusConflict, "a camera with this id already exists")
		return
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, http.StatusBadRequest, "unknown room_id")
		return
	case err != nil:
		s.serverErr(w, "create camera", err)
		return
	}
	s.audit(r.Context(), u.Username, "camera_created", c.ID, map[string]any{
		"room_id": c.RoomID, "name": c.Name, "protected": c.Protected, "zones": len(c.Zones), "enabled": c.Enabled,
		"authorized_by": c.AuthorizedBy, "authorized_at": c.AuthorizedAt, "authorization_scope": c.AuthorizationScope})
	if c.Enabled {
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		if err := s.AddCamera(ctx, c.EngineCamera()); err != nil {
			s.serverErr(w, "register camera with engine", err)
			return
		}
	}
	now := time.Now().UTC()
	engineOK, _ := s.engineHealth(now)
	writeJSON(w, http.StatusCreated, s.withState(c, engineOK, now))
}

// ---- incidents -----------------------------------------------------------------

func boolParam(q url.Values, k string) bool {
	v := q.Get(k)
	return v == "1" || v == "true"
}

func (s *Server) listIncidents(w http.ResponseWriter, r *http.Request, u *store.User) {
	q := r.URL.Query()
	f := store.IncidentFilter{
		OpenOnly:      boolParam(q, "open"),
		IncludeShadow: boolParam(q, "shadow") && auth.Can(u.Role, auth.PermViewShadow),
	}
	if l := q.Get("limit"); l != "" {
		n, err := strconv.Atoi(l)
		if err != nil || n < 1 {
			writeErr(w, http.StatusBadRequest, "limit must be a positive integer")
			return
		}
		f.Limit = n
	}
	list, err := s.DB.Incidents(r.Context(), f)
	if err != nil {
		s.serverErr(w, "list incidents", err)
		return
	}
	if list == nil {
		list = []store.IncidentView{}
	}
	writeJSON(w, http.StatusOK, list)
}

// visibleIncident loads an incident the user may see. SHADOW incidents are
// reported as missing to users without view_shadow, so their existence does
// not leak.
func (s *Server) visibleIncident(w http.ResponseWriter, r *http.Request, u *store.User, id string) (*store.IncidentDetail, bool) {
	inc, err := s.DB.Incident(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) || (err == nil && inc.Status == eventengine.StatusShadow && !auth.Can(u.Role, auth.PermViewShadow)) {
		writeErr(w, http.StatusNotFound, "incident not found")
		return nil, false
	}
	if err != nil {
		s.serverErr(w, "load incident", err)
		return nil, false
	}
	return inc, true
}

func (s *Server) getIncident(w http.ResponseWriter, r *http.Request, u *store.User) {
	if inc, ok := s.visibleIncident(w, r, u, r.PathValue("id")); ok {
		writeJSON(w, http.StatusOK, inc)
	}
}

// changedIncident reloads, publishes and returns an incident after an action.
func (s *Server) changedIncident(w http.ResponseWriter, r *http.Request, id string, publish bool) {
	inc, err := s.DB.Incident(r.Context(), id)
	if err != nil {
		s.serverErr(w, "load incident", err)
		return
	}
	if publish {
		s.publishIncident("incident_updated", &inc.IncidentView)
	}
	writeJSON(w, http.StatusOK, inc)
}

func (s *Server) ackIncident(w http.ResponseWriter, r *http.Request, u *store.User) {
	var body struct{}
	if err := decode(r, &body, true); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	inc, ok := s.visibleIncident(w, r, u, r.PathValue("id"))
	if !ok {
		return
	}
	changed, err := s.DB.Acknowledge(r.Context(), inc.ID, u.Username)
	if err != nil {
		s.serverErr(w, "acknowledge", err)
		return
	}
	if changed {
		s.audit(r.Context(), u.Username, "incident_acknowledged", inc.ID, nil)
	}
	s.changedIncident(w, r, inc.ID, changed)
}

var dispositions = map[string]bool{
	"no_concern": true, "routine_care": true, "false_detection": true,
	"concern_followed_up": true, "escalated_external": true, "inconclusive": true,
}

// maxNote bounds a reviewer's note.
const maxNote = 4000

func (s *Server) dispositionIncident(w http.ResponseWriter, r *http.Request, u *store.User) {
	var req struct {
		Disposition string `json:"disposition"`
		Note        string `json:"note"`
	}
	if err := decode(r, &req, false); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if !dispositions[req.Disposition] {
		writeErr(w, http.StatusBadRequest, "unknown disposition")
		return
	}
	if len(req.Note) > maxNote {
		writeErr(w, http.StatusBadRequest, "note is too long")
		return
	}
	inc, ok := s.visibleIncident(w, r, u, r.PathValue("id"))
	if !ok {
		return
	}
	switch err := s.DB.Resolve(r.Context(), inc.ID, u.Username, req.Disposition, req.Note); {
	case errors.Is(err, store.ErrConflict):
		writeErr(w, http.StatusConflict, "incident is already resolved")
		return
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, http.StatusNotFound, "incident not found")
		return
	case err != nil:
		s.serverErr(w, "record disposition", err)
		return
	}
	// The note may contain personal details, so only its presence is audited.
	s.audit(r.Context(), u.Username, "incident_disposition", inc.ID, map[string]any{
		"disposition": req.Disposition, "has_note": req.Note != ""})
	s.changedIncident(w, r, inc.ID, true)
}

func (s *Server) holdIncident(w http.ResponseWriter, r *http.Request, u *store.User) {
	var req struct {
		Hold *bool `json:"hold"`
	}
	if err := decode(r, &req, false); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if req.Hold == nil {
		writeErr(w, http.StatusBadRequest, "hold (true or false) is required")
		return
	}
	inc, ok := s.visibleIncident(w, r, u, r.PathValue("id"))
	if !ok {
		return
	}
	if err := s.DB.SetLegalHold(r.Context(), inc.ID, u.Username, *req.Hold); err != nil {
		s.serverErr(w, "set legal hold", err)
		return
	}
	action := "legal_hold_released"
	if *req.Hold {
		action = "legal_hold_set"
	}
	s.audit(r.Context(), u.Username, action, inc.ID, nil)
	s.changedIncident(w, r, inc.ID, true)
}

// ---- clips -----------------------------------------------------------------------

func (s *Server) clipToken(w http.ResponseWriter, r *http.Request, u *store.User) {
	var body struct{}
	if err := decode(r, &body, true); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	clip, err := s.DB.Clip(r.Context(), r.PathValue("id"))
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "clip not found")
		return
	}
	if err != nil {
		s.serverErr(w, "load clip", err)
		return
	}
	if _, ok := s.visibleIncident(w, r, u, clip.IncidentID); !ok {
		return
	}
	if clip.DeletedAt != nil || clip.WrappedKey == nil {
		writeErr(w, http.StatusGone, "clip was deleted under the retention policy")
		return
	}
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		s.serverErr(w, "issue clip token", err)
		return
	}
	tok := base64.RawURLEncoding.EncodeToString(raw)
	exp := time.Now().UTC().Add(s.Cfg.ClipTokenTTL).Truncate(time.Second)
	s.mu.Lock()
	s.clipTokens[tok] = clipToken{clipID: clip.ID, userID: u.ID, user: u.Username, expires: exp}
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"url":        "/api/v1/clips/" + url.PathEscape(clip.ID) + "/media?token=" + tok,
		"expires_at": exp,
	})
}

func (s *Server) pruneClipTokens(now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, t := range s.clipTokens {
		if now.After(t.expires) {
			delete(s.clipTokens, k)
		}
	}
}

// clipMedia serves a decrypted clip to the holder of a valid token. The first
// request made with a token is audit-logged as a view; the browser's later
// Range requests for the same playback reuse the token and are not logged
// again.
func (s *Server) clipMedia(w http.ResponseWriter, r *http.Request) {
	id, tok := r.PathValue("id"), r.URL.Query().Get("token")
	now := time.Now()
	s.mu.Lock()
	t, ok := s.clipTokens[tok]
	valid := ok && tok != "" && t.clipID == id && now.Before(t.expires)
	firstView := valid && !t.viewed
	if firstView {
		t.viewed = true
		s.clipTokens[tok] = t
	}
	s.mu.Unlock()
	if !valid {
		writeErr(w, http.StatusUnauthorized, "clip link is invalid or has expired")
		return
	}
	// The token is bound to its user: a disabled account loses access at once.
	user, err := s.DB.UserByName(r.Context(), t.user)
	if err != nil || user.ID != t.userID || user.Disabled || !auth.Can(user.Role, auth.PermViewClips) {
		writeErr(w, http.StatusUnauthorized, "clip link is no longer valid")
		return
	}
	clip, err := s.DB.Clip(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "clip not found")
		return
	}
	if err != nil {
		s.serverErr(w, "load clip", err)
		return
	}
	if clip.DeletedAt != nil || clip.WrappedKey == nil {
		writeErr(w, http.StatusGone, "clip was deleted under the retention policy")
		return
	}
	if firstView {
		// No audit entry, no video.
		if err := s.DB.Audit(r.Context(), t.user, "clip_viewed", clip.ID, map[string]any{"incident_id": clip.IncidentID}); err != nil {
			s.Log.Error("audit clip view", "err", err)
			writeErr(w, http.StatusServiceUnavailable, "cannot record the view, so the clip is not shown")
			return
		}
	}
	plain, err := s.Vault.Open(clip.ID, clip.Path, clip.WrappedKey, clip.SHA256)
	if err != nil {
		s.serverErr(w, "decrypt clip", err)
		return
	}
	rc := http.NewResponseController(w)
	_ = rc.SetWriteDeadline(time.Now().Add(10 * time.Minute))
	w.Header().Set("Content-Type", "video/mp4")
	w.Header().Set("Cache-Control", "no-store, private")
	w.Header().Set("Content-Disposition", "inline")
	http.ServeContent(w, r, "", clip.CreatedAt, bytes.NewReader(plain))
}

// ---- live events ---------------------------------------------------------------

func (s *Server) events(w http.ResponseWriter, r *http.Request, u *store.User) {
	rc := http.NewResponseController(w)
	_ = rc.SetWriteDeadline(time.Time{}) // the stream outlives the server's write timeout
	sub := s.Hub.Subscribe(auth.Can(u.Role, auth.PermViewShadow))
	defer s.Hub.Unsubscribe(sub)

	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	if _, err := io.WriteString(w, "retry: 3000\n: connected\n\n"); err != nil {
		return
	}
	if err := rc.Flush(); err != nil {
		return
	}
	ping := time.NewTicker(s.Cfg.PingEvery)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case msg, ok := <-sub.C:
			if !ok {
				return // dropped for falling behind; the client reconnects and re-fetches
			}
			if _, err := w.Write(msg); err != nil {
				return
			}
		case <-ping.C:
			// An open stream is not activity: end it when the session's idle
			// timeout passes.
			ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
			live, err := s.Auth.StillValid(ctx, r)
			cancel()
			if err == nil && !live {
				return
			}
			if _, err := io.WriteString(w, ": ping\n\n"); err != nil {
				return
			}
		}
		if err := rc.Flush(); err != nil {
			return
		}
	}
}

// ---- admin -----------------------------------------------------------------------

func (s *Server) listUsers(w http.ResponseWriter, r *http.Request, _ *store.User) {
	users, err := s.DB.Users(r.Context())
	if err != nil {
		s.serverErr(w, "list users", err)
		return
	}
	if users == nil {
		users = []store.User{}
	}
	writeJSON(w, http.StatusOK, users)
}

var usernameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)

func (s *Server) createUser(w http.ResponseWriter, r *http.Request, admin *store.User) {
	var req struct {
		Username    string `json:"username"`
		DisplayName string `json:"display_name"`
		Role        string `json:"role"`
		Password    string `json:"password"`
	}
	if err := decode(r, &req, false); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	req.DisplayName = strings.TrimSpace(req.DisplayName)
	switch {
	case !usernameRe.MatchString(req.Username):
		writeErr(w, http.StatusBadRequest, "username must be 1-64 lowercase letters, digits, '.', '_' or '-'")
		return
	case req.DisplayName == "" || len(req.DisplayName) > 128:
		writeErr(w, http.StatusBadRequest, "display_name is required (max 128 characters)")
		return
	case !auth.ValidRole(req.Role):
		writeErr(w, http.StatusBadRequest, "role must be caregiver, lead, reviewer or admin")
		return
	case len(req.Password) > 256:
		writeErr(w, http.StatusBadRequest, "password is too long")
		return
	}
	hash, err := auth.HashPassword(req.Password)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	u := store.User{Username: req.Username, DisplayName: req.DisplayName, Role: req.Role, PasswordHash: hash}
	u.ID, err = s.DB.CreateUser(r.Context(), u)
	if errors.Is(err, store.ErrConflict) {
		writeErr(w, http.StatusConflict, "username is taken")
		return
	}
	if err != nil {
		s.serverErr(w, "create user", err)
		return
	}
	s.audit(r.Context(), admin.Username, "user_created", u.Username, map[string]any{"role": u.Role, "display_name": u.DisplayName})
	writeJSON(w, http.StatusCreated, u)
}

func (s *Server) listAudit(w http.ResponseWriter, r *http.Request, _ *store.User) {
	limit := 100
	if l := r.URL.Query().Get("limit"); l != "" {
		n, err := strconv.Atoi(l)
		if err != nil || n < 1 {
			writeErr(w, http.StatusBadRequest, "limit must be a positive integer")
			return
		}
		limit = min(n, 1000)
	}
	entries, err := s.DB.AuditEntries(r.Context(), limit)
	if err != nil {
		s.serverErr(w, "list audit", err)
		return
	}
	broken, err := s.DB.VerifyAudit(r.Context())
	if err != nil {
		s.serverErr(w, "verify audit", err)
		return
	}
	if entries == nil {
		entries = []store.AuditEntry{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries, "chain_ok": broken == 0, "broken_at": broken})
}

// ---- dashboard files -----------------------------------------------------------

// webHandler serves the dashboard's static export. Unknown paths fall back to
// index.html so client-side routes work; /api paths never reach here.
func (s *Server) webHandler() http.Handler {
	root := s.Cfg.WebDir
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		clean := path.Clean("/" + r.URL.Path)
		candidates := []string{clean, clean + ".html", path.Join(clean, "index.html")}
		if clean == "/" {
			candidates = []string{"/index.html"}
		}
		for _, c := range candidates {
			if serveFile(w, r, filepath.Join(root, filepath.FromSlash(c))) {
				return
			}
		}
		// A missing asset is a real 404; anything else is a client-side route.
		if path.Ext(clean) != "" && path.Ext(clean) != ".html" {
			http.NotFound(w, r)
			return
		}
		if !serveFile(w, r, filepath.Join(root, "index.html")) {
			http.NotFound(w, r)
		}
	})
}

func serveFile(w http.ResponseWriter, r *http.Request, p string) bool {
	f, err := os.Open(p)
	if err != nil {
		return false
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || st.IsDir() {
		return false
	}
	if strings.HasSuffix(p, ".html") {
		w.Header().Set("Cache-Control", "no-cache")
	}
	http.ServeContent(w, r, st.Name(), st.ModTime(), f)
	return true
}
