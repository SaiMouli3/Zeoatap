package server

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

func insertIncident(t *testing.T, e *env, key string, status eventengine.IncidentStatus) string {
	t.Helper()
	now := time.Now().UTC()
	inc, _, err := e.db.InsertIfAbsent(&eventengine.Incident{ID: "INC-" + key, IdempotencyKey: key, RoomID: "infants-a",
		RoomZone: "changing-table-1", EventType: "infant_off_surface", Family: "A", Start: now, End: now,
		Cameras: []string{"cam1"}, Status: status, RuleVersion: "a1-v0.1", Source: "MOCK", ModelVersions: map[string]string{}})
	if err != nil {
		t.Fatal(err)
	}
	return inc.ID
}

func auditActions(t *testing.T, e *env) (map[string]int, string) {
	t.Helper()
	var resp struct {
		Entries []store.AuditEntry `json:"entries"`
		ChainOK bool               `json:"chain_ok"`
	}
	e.getJSON(e.client("admin"), "/api/v1/audit?limit=1000", &resp)
	if !resp.ChainOK {
		t.Fatal("audit chain broken")
	}
	counts := map[string]int{}
	raw, _ := json.Marshal(resp.Entries)
	for _, en := range resp.Entries {
		counts[en.Action]++
	}
	return counts, string(raw)
}

func TestLoginLogoutAndAudit(t *testing.T) {
	e := newEnv(t, envOpts{noRun: true})
	anon := e.client("")
	if st, _ := e.do(anon, "POST", "/api/v1/login", `{"username":"care","password":"wrong password!"}`); st != http.StatusUnauthorized {
		t.Fatalf("wrong password: %d", st)
	}
	if st, _ := e.do(anon, "POST", "/api/v1/login", `{"username":"ghost","password":"`+testPassword+`"}`); st != http.StatusUnauthorized {
		t.Fatalf("unknown user: %d", st)
	}

	req, _ := http.NewRequest("POST", e.api.URL+"/api/v1/login", strings.NewReader(`{"username":"rev","password":"`+testPassword+`"}`))
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var me meResp
	_ = json.NewDecoder(resp.Body).Decode(&me)
	resp.Body.Close()
	if resp.StatusCode != 200 || me.User.Username != "rev" || me.User.Role != "reviewer" || len(me.Permissions) != 6 {
		t.Fatalf("login: %d %+v", resp.StatusCode, me)
	}
	var ck *http.Cookie
	for _, c := range resp.Cookies() {
		if c.Name == "zeoatap_session" {
			ck = c
		}
	}
	if ck == nil || !ck.HttpOnly || ck.SameSite != http.SameSiteStrictMode {
		t.Fatalf("session cookie flags: %+v", ck)
	}

	c := e.client("caregiver")
	var got meResp
	e.getJSON(c, "/api/v1/me", &got)
	if got.User.Username != "care" || strings.Join(permStrings(got), ",") != "view_status,acknowledge" {
		t.Fatalf("me: %+v", got)
	}
	if st, _ := e.do(c, "POST", "/api/v1/logout", ""); st != http.StatusNoContent {
		t.Fatalf("logout: %d", st)
	}
	if st, _ := e.do(c, "GET", "/api/v1/me", ""); st != http.StatusUnauthorized {
		t.Fatalf("me after logout: %d", st)
	}

	counts, raw := auditActions(t, e)
	if counts["login_failed"] != 2 || counts["login"] < 2 || counts["logout"] != 1 {
		t.Fatalf("audit counts %v", counts)
	}
	if strings.Contains(raw, "wrong password") || strings.Contains(raw, testPassword) {
		t.Fatal("passwords must never reach the audit log")
	}
}

func permStrings(m meResp) []string {
	var out []string
	for _, p := range m.Permissions {
		out = append(out, string(p))
	}
	return out
}

func TestRBACMatrix(t *testing.T) {
	e := newEnv(t, envOpts{})
	inc := insertIncident(t, e, "rbac", eventengine.StatusAlerted)
	type ep struct {
		method, path, body string
		allowed            []string // roles allowed; nil = any signed-in role
	}
	all := []string{"caregiver", "lead", "reviewer", "admin"}
	eps := []ep{
		{"GET", "/api/v1/me", "", all},
		{"GET", "/api/v1/overview", "", all},
		{"GET", "/api/v1/cameras", "", all},
		{"GET", "/api/v1/incidents", "", all},
		{"GET", "/api/v1/incidents/" + inc, "", all},
		{"GET", "/api/v1/events", "", all},
		{"POST", "/api/v1/incidents/INC-none/ack", "{}", all},
		{"POST", "/api/v1/incidents/INC-none/disposition", `{"disposition":"no_concern"}`, []string{"reviewer"}},
		{"POST", "/api/v1/incidents/INC-none/hold", `{"hold":true}`, []string{"reviewer", "admin"}},
		{"POST", "/api/v1/clips/none/token", "{}", []string{"lead", "reviewer"}},
		{"POST", "/api/v1/cameras", "{}", []string{"admin"}},
		{"GET", "/api/v1/users", "", []string{"admin"}},
		{"POST", "/api/v1/users", "{}", []string{"admin"}},
		{"GET", "/api/v1/audit", "", []string{"admin"}},
	}
	clients := map[string]*http.Client{"": e.client("")}
	for _, r := range all {
		clients[r] = e.client(r)
	}
	status := func(c *http.Client, x ep) int {
		if x.path == "/api/v1/events" {
			s := e.stream(c)
			defer s.cancel()
			return s.status
		}
		st, _ := e.do(c, x.method, x.path, x.body)
		return st
	}
	for _, x := range eps {
		if st := status(clients[""], x); st != http.StatusUnauthorized {
			t.Errorf("%s %s anonymous: %d, want 401", x.method, x.path, st)
		}
		for _, role := range all {
			allowed := false
			for _, a := range x.allowed {
				allowed = allowed || a == role
			}
			st := status(clients[role], x)
			if allowed && (st == http.StatusUnauthorized || st == http.StatusForbidden) {
				t.Errorf("%s %s as %s: %d, want allowed", x.method, x.path, role, st)
			}
			if !allowed && st != http.StatusForbidden {
				t.Errorf("%s %s as %s: %d, want 403", x.method, x.path, role, st)
			}
		}
	}
	// Clip media is token-only: a session cookie alone is not enough.
	if st, _ := e.do(clients["reviewer"], "GET", "/api/v1/clips/x/media", ""); st != http.StatusUnauthorized {
		t.Errorf("media without token: %d", st)
	}
}

func TestPostRequiresJSON(t *testing.T) {
	e := newEnv(t, envOpts{noRun: true})
	inc := insertIncident(t, e, "ct", eventengine.StatusAlerted)
	c := e.client("caregiver")
	for _, ct := range []string{"", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"} {
		if st, _ := e.doType(c, "POST", "/api/v1/incidents/"+inc+"/ack", "{}", ct); st != http.StatusUnsupportedMediaType {
			t.Errorf("ack with Content-Type %q: %d, want 415", ct, st)
		}
		if st, _ := e.doType(e.client(""), "POST", "/api/v1/login", `{"username":"care","password":"x"}`, ct); st != http.StatusUnsupportedMediaType {
			t.Errorf("login with Content-Type %q: %d, want 415", ct, st)
		}
	}
	if st, _ := e.doType(c, "POST", "/api/v1/incidents/"+inc+"/ack", "{}", "application/json; charset=utf-8"); st != http.StatusOK {
		t.Errorf("ack with JSON charset: %d", st)
	}
	var v store.IncidentDetail
	e.getJSON(c, "/api/v1/incidents/"+inc, &v)
	if v.Lifecycle != "acknowledged" {
		t.Fatalf("lifecycle %s", v.Lifecycle)
	}
}

func TestShadowHiddenFromCaregiver(t *testing.T) {
	e := newEnv(t, envOpts{})
	shadow := insertIncident(t, e, "shadow", eventengine.StatusShadow)
	visible := insertIncident(t, e, "review", eventengine.StatusReview)
	care, rev := e.client("caregiver"), e.client("reviewer")

	ids := func(c *http.Client, q string) map[string]bool {
		var list []store.IncidentView
		e.getJSON(c, "/api/v1/incidents"+q, &list)
		out := map[string]bool{}
		for _, i := range list {
			out[i.ID] = true
		}
		return out
	}
	for _, q := range []string{"", "?shadow=1", "?shadow=1&open=1"} {
		if got := ids(care, q); got[shadow] || !got[visible] {
			t.Errorf("caregiver list %q = %v", q, got)
		}
	}
	if got := ids(rev, ""); got[shadow] {
		t.Error("reviewer sees SHADOW only when asking for shadow=1")
	}
	if got := ids(rev, "?shadow=1"); !got[shadow] || !got[visible] {
		t.Errorf("reviewer shadow=1 list = %v", got)
	}
	if st, _ := e.do(care, "GET", "/api/v1/incidents/"+shadow, ""); st != http.StatusNotFound {
		t.Errorf("caregiver SHADOW detail: %d, want 404", st)
	}
	if st, _ := e.do(care, "POST", "/api/v1/incidents/"+shadow+"/ack", "{}"); st != http.StatusNotFound {
		t.Errorf("caregiver SHADOW ack: %d, want 404", st)
	}
	if st, _ := e.do(rev, "GET", "/api/v1/incidents/"+shadow, ""); st != http.StatusOK {
		t.Errorf("reviewer SHADOW detail: %d", st)
	}

	// Live events: the SHADOW incident reaches the reviewer only.
	cs, rs := e.stream(care), e.stream(rev)
	eventually(t, 2*time.Second, "both subscribed", func() bool { return e.s.Hub.Subscribers() == 2 })
	ctx := context.Background()
	for _, id := range []string{shadow, visible} {
		e.s.handle(ctx, eventengine.Output{Kind: "incident_created", Incident: &eventengine.Incident{ID: id}})
	}
	first := rs.next(t, 2*time.Second, "incident_created")
	if !strings.Contains(first.Data, shadow) {
		t.Fatalf("reviewer first event %v", first)
	}
	got := cs.next(t, 2*time.Second, "incident_created")
	if !strings.Contains(got.Data, visible) {
		t.Fatalf("caregiver got %v", got)
	}
	for _, ev := range cs.drain(200 * time.Millisecond) {
		if strings.Contains(ev.Data, shadow) {
			t.Fatalf("SHADOW event leaked to caregiver: %v", ev)
		}
	}
}

func TestAckDispositionHold(t *testing.T) {
	e := newEnv(t, envOpts{})
	inc := insertIncident(t, e, "flow", eventengine.StatusAlerted)
	care, rev := e.client("caregiver"), e.client("reviewer")
	sub := e.stream(care)

	for i := 0; i < 2; i++ {
		st, b := e.do(care, "POST", "/api/v1/incidents/"+inc+"/ack", "{}")
		var v store.IncidentDetail
		_ = json.Unmarshal(b, &v)
		if st != http.StatusOK || v.Lifecycle != "acknowledged" || *v.AcknowledgedBy != "care" || len(v.Actions) != 1 {
			t.Fatalf("ack %d: %d %s", i, st, b)
		}
	}
	if ev := sub.next(t, 2*time.Second, "incident_updated"); !strings.Contains(ev.Data, `"lifecycle":"acknowledged"`) {
		t.Fatalf("update event %v", ev)
	}
	if st, _ := e.do(e.client("reviewer"), "POST", "/api/v1/incidents/INC-missing/ack", "{}"); st != http.StatusNotFound {
		t.Fatalf("ack missing: %d", st)
	}
	if st, _ := e.do(rev, "POST", "/api/v1/incidents/"+inc+"/disposition", `{"disposition":"guilty"}`); st != http.StatusBadRequest {
		t.Fatalf("invalid disposition: %d", st)
	}
	st, b := e.do(rev, "POST", "/api/v1/incidents/"+inc+"/disposition", `{"disposition":"routine_care","note":"nappy change"}`)
	if st != http.StatusOK || !strings.Contains(string(b), `"disposition":"routine_care"`) || !strings.Contains(string(b), `"lifecycle":"resolved"`) {
		t.Fatalf("disposition: %d %s", st, b)
	}
	if st, _ := e.do(rev, "POST", "/api/v1/incidents/"+inc+"/disposition", `{"disposition":"no_concern"}`); st != http.StatusConflict {
		t.Fatalf("second disposition: %d, want 409", st)
	}
	if st, _ := e.do(rev, "POST", "/api/v1/incidents/"+inc+"/hold", `{}`); st != http.StatusBadRequest {
		t.Fatalf("hold without value: %d", st)
	}
	st, b = e.do(e.client("admin"), "POST", "/api/v1/incidents/"+inc+"/hold", `{"hold":true}`)
	if st != http.StatusOK || !strings.Contains(string(b), `"legal_hold":true`) {
		t.Fatalf("hold: %d %s", st, b)
	}
	st, b = e.do(rev, "POST", "/api/v1/incidents/"+inc+"/hold", `{"hold":false}`)
	if st != http.StatusOK || !strings.Contains(string(b), `"legal_hold":false`) {
		t.Fatalf("release: %d %s", st, b)
	}
	counts, raw := auditActions(t, e)
	if counts["incident_acknowledged"] != 1 || counts["incident_disposition"] != 1 || counts["legal_hold_set"] != 1 || counts["legal_hold_released"] != 1 {
		t.Fatalf("audit counts %v", counts)
	}
	if strings.Contains(raw, "nappy change") {
		t.Fatal("disposition notes must not be copied into the audit log")
	}
}

func TestAdminCamerasAndUsers(t *testing.T) {
	e := newEnv(t, envOpts{})
	adm := e.client("admin")
	cam := `{"id":"cam3","room_id":"toddlers","name":"Nap room","zones":[{"id":"cot","room_zone":"cot-1","kind":"surface","polygon":[{"X":0.1,"Y":0.1},{"X":0.5,"Y":0.1},{"X":0.5,"Y":0.5}]}]`
	if st, b := e.do(adm, "POST", "/api/v1/cameras", cam+`}`); st != http.StatusBadRequest || !strings.Contains(string(b), "authorized_by") {
		t.Fatalf("camera without authorization: %d %s", st, b)
	}
	auth := `,"authorized_by":"Director","authorized_at":"2026-10-02","authorization_scope":"Nap room, daytime"`
	st, b := e.do(adm, "POST", "/api/v1/cameras", cam+auth+`}`)
	var created store.Camera
	_ = json.Unmarshal(b, &created)
	if st != http.StatusCreated || !created.Enabled || created.State != "NOT_CONNECTED" || created.StateSince == nil {
		t.Fatalf("create camera: %d %s", st, b)
	}
	if st, _ := e.do(adm, "POST", "/api/v1/cameras", cam+auth+`}`); st != http.StatusConflict {
		t.Fatalf("duplicate camera: %d", st)
	}
	if st, _ := e.do(adm, "POST", "/api/v1/cameras", strings.Replace(cam, "cam3", "cam4", 1)+strings.Replace(auth, "2026-10-02", "yesterday", 1)+`}`); st != http.StatusBadRequest {
		t.Fatalf("bad date: %d", st)
	}
	if st, _ := e.do(adm, "POST", "/api/v1/cameras", strings.Replace(strings.Replace(cam, "cam3", "cam5", 1), "toddlers", "nope", 1)+auth+`}`); st != http.StatusBadRequest {
		t.Fatalf("unknown room: %d", st)
	}
	var cams []store.Camera
	e.getJSON(e.client("caregiver"), "/api/v1/cameras", &cams)
	if len(cams) != 3 {
		t.Fatalf("cameras = %d", len(cams))
	}
	// The new camera is registered with the running engine.
	eventually(t, 2*time.Second, "cam3 known", func() bool { return e.s.knownCamera("cam3") })

	st, b = e.do(adm, "POST", "/api/v1/users", `{"username":"newlead","display_name":"New Lead","role":"lead","password":"long enough pw"}`)
	if st != http.StatusCreated || strings.Contains(string(b), "password") || !strings.Contains(string(b), `"role":"lead"`) {
		t.Fatalf("create user: %d %s", st, b)
	}
	for _, body := range []string{
		`{"username":"newlead","display_name":"Dup","role":"lead","password":"long enough pw"}`,
	} {
		if st, _ := e.do(adm, "POST", "/api/v1/users", body); st != http.StatusConflict {
			t.Fatalf("duplicate user: %d", st)
		}
	}
	for _, body := range []string{
		`{"username":"x","display_name":"X","role":"lead","password":"short"}`,
		`{"username":"y","display_name":"Y","role":"superuser","password":"long enough pw"}`,
		`{"username":"Bad Name","display_name":"Z","role":"lead","password":"long enough pw"}`,
		`{"username":"z","display_name":"","role":"lead","password":"long enough pw"}`,
	} {
		if st, _ := e.do(adm, "POST", "/api/v1/users", body); st != http.StatusBadRequest {
			t.Errorf("invalid user %s: %d", body, st)
		}
	}
	st, b = e.do(adm, "GET", "/api/v1/users", "")
	if st != 200 || !strings.Contains(string(b), "newlead") || strings.Contains(string(b), "$2a$") {
		t.Fatalf("users: %d %s", st, b)
	}
	counts, raw := auditActions(t, e)
	if counts["camera_created"] != 1 || counts["user_created"] != 1 || strings.Contains(raw, "long enough pw") {
		t.Fatalf("audit %v", counts)
	}
}

func TestInternalIngest(t *testing.T) {
	e := newEnv(t, envOpts{noRun: true, cfg: Config{IngestTimeout: 100 * time.Millisecond}})
	now := time.Now().UTC()
	ok := observation("cam1", now)
	if st, _ := e.ingest("", ok); st != http.StatusUnauthorized {
		t.Fatalf("no token: %d", st)
	}
	if st, _ := e.ingest("wrong-token", ok); st != http.StatusUnauthorized {
		t.Fatalf("wrong token: %d", st)
	}
	st, out := e.ingest(testToken, ok, observation("cam-unknown", now))
	if st != http.StatusOK || out["accepted"] != float64(1) || out["ignored"] != float64(1) {
		t.Fatalf("ingest: %d %v", st, out)
	}
	line, _ := json.Marshal(ok)
	bad := string(line) + "\n" + `{"camera_id":"cam1"}` + "\n" + string(line) + "\n"
	if st, out := e.ingestRaw(testToken, bad); st != http.StatusBadRequest || out["accepted"] != float64(1) || !strings.Contains(out["error"].(string), "line 2") {
		t.Fatalf("bad line: %d %v", st, out)
	}
	if st, _ := e.ingestRaw(testToken, `{"camera_id":"cam1","capture_ts":"2026-10-31T00:00:00Z","source":"x","extra":1}`); st != http.StatusBadRequest {
		t.Fatalf("unknown field: %d", st)
	}
	// With no engine draining the queue, a full backlog answers 503.
	e.s.obsCh = make(chan eventengine.Observation, 1)
	st, out = e.ingest(testToken, ok, ok, ok)
	if st != http.StatusServiceUnavailable || out["accepted"] != float64(1) {
		t.Fatalf("backlog: %d %v", st, out)
	}
}

func TestRetentionShredsExpiredKeepsHeld(t *testing.T) {
	e := newEnv(t, envOpts{noRun: true})
	ctx := context.Background()
	expired := insertIncident(t, e, "old", eventengine.StatusReview)
	held := insertIncident(t, e, "held", eventengine.StatusReview)
	fresh := insertIncident(t, e, "fresh", eventengine.StatusReview)
	paths := map[string]string{}
	for _, id := range []string{expired, held, fresh} {
		src := filepath.Join(t.TempDir(), "c.mp4")
		if err := os.WriteFile(src, []byte("video "+id), 0o600); err != nil {
			t.Fatal(err)
		}
		clipID := id + "-cam1"
		stored, err := e.s.Vault.Store(clipID, src)
		if err != nil {
			t.Fatal(err)
		}
		paths[clipID] = stored.Path
		if err := e.db.InsertClip(ctx, store.ClipRecord{ID: clipID, IncidentID: id, CameraID: "cam1", Path: stored.Path, SHA256: stored.SHA256,
			SizeBytes: stored.Size, WrappedKey: stored.WrappedKey, Start: time.Now(), End: time.Now()}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := e.db.Pool.Exec(ctx, `UPDATE incidents SET retention_deadline = now() - interval '1 hour' WHERE id = ANY($1)`, []string{expired, held}); err != nil {
		t.Fatal(err)
	}
	if err := e.db.SetLegalHold(ctx, held, "rev", true); err != nil {
		t.Fatal(err)
	}
	n, err := e.s.RunRetention(ctx)
	if err != nil || n != 1 {
		t.Fatalf("retention deleted %d (%v), want 1", n, err)
	}
	for clipID, path := range paths {
		c, err := e.db.Clip(ctx, clipID)
		if err != nil {
			t.Fatal(err)
		}
		_, statErr := os.Stat(path)
		gone := clipID == expired+"-cam1"
		if gone != (c.DeletedAt != nil) || gone != (c.WrappedKey == nil) || gone != os.IsNotExist(statErr) {
			t.Errorf("clip %s: deleted_at=%v key=%v file=%v", clipID, c.DeletedAt, c.WrappedKey != nil, statErr)
		}
		if _, err := e.s.Vault.Open(c.ID, path, c.WrappedKey, c.SHA256); gone == (err == nil) {
			t.Errorf("clip %s readable=%v after retention", clipID, err == nil)
		}
	}
	if n, _ := e.s.RunRetention(ctx); n != 0 {
		t.Fatalf("second run deleted %d", n)
	}
	if st, _ := e.do(e.client("reviewer"), "POST", "/api/v1/clips/"+expired+"-cam1/token", "{}"); st != http.StatusGone {
		t.Fatalf("token for shredded clip: %d, want 410", st)
	}
	counts, _ := auditActions(t, e)
	if counts["clip_deleted_retention"] != 1 {
		t.Fatalf("audit %v", counts)
	}
}

func TestWebStaticFallback(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{"index.html": "<html>app</html>", "incidents.html": "<html>incidents</html>", "_next/app.js": "js"} {
		p := filepath.Join(dir, filepath.FromSlash(name))
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	e := newEnv(t, envOpts{noRun: true, cfg: Config{WebDir: dir}})
	c := e.client("")
	for path, want := range map[string]string{"/": "app", "/incidents": "incidents", "/rooms/infants-a": "app", "/_next/app.js": "js", "/../../etc/passwd": "app"} {
		st, b := e.do(c, "GET", path, "")
		if st != 200 || !strings.Contains(string(b), want) {
			t.Errorf("GET %s: %d %q, want %q", path, st, b, want)
		}
	}
	if st, _ := e.do(c, "GET", "/_next/missing.js", ""); st != http.StatusNotFound {
		t.Errorf("missing asset: %d", st)
	}
	if st, b := e.do(c, "GET", "/api/v1/nope", ""); st != http.StatusNotFound || !strings.Contains(string(b), `"error"`) {
		t.Errorf("unknown api path: %d %s", st, b)
	}
	if st, _ := e.do(c, "GET", "/healthz", ""); st != 200 {
		t.Errorf("healthz %d", st)
	}
}
