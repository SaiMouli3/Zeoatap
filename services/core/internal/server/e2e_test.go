package server

import (
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

// NDJSON ingest -> engine -> incident -> SSE -> clip capture via the
// vision-worker API -> encrypted clip -> token -> Range playback -> expiry.
func TestEndToEnd(t *testing.T) {
	rules := eventengine.DefaultRules(map[string]eventengine.RuleOverride{
		// Unvalidated alert policy: produces SHADOW incidents.
		"adult_in_restricted_zone": {Policy: eventengine.PolicyAlert},
		// Test-only: lets A1 alert so the full alert path runs.
		"infant_off_surface": {Policy: eventengine.PolicyAlert, ValidatedForAlerting: true},
	})
	// A long tick keeps wall-clock staleness out of this test; the overview
	// test covers it.
	e := newEnv(t, envOpts{rules: rules, cfg: Config{TickEvery: time.Hour, PingEvery: 100 * time.Millisecond}})
	care, rev, lead := e.client("caregiver"), e.client("reviewer"), e.client("lead")
	cs, rs := e.stream(care), e.stream(rev)
	eventually(t, 2*time.Second, "subscribers", func() bool { return e.s.Hub.Subscribers() == 2 })

	// 1. An adult stays in the restricted kitchen-door zone for > 2 s: SHADOW.
	now := time.Now().UTC()
	var batch []eventengine.Observation
	for dt := -2300 * time.Millisecond; dt <= -100*time.Millisecond; dt += 200 * time.Millisecond {
		batch = append(batch, observation("cam1", now.Add(dt), adultAt("a1", 0.9, 0.8)))
	}
	if st, out := e.ingest(testToken, batch...); st != http.StatusOK || out["accepted"] != float64(len(batch)) {
		t.Fatalf("ingest shadow batch: %d %v", st, out)
	}
	if ev := rs.next(t, 3*time.Second, "camera_state"); !strings.Contains(ev.Data, `"state":"FULLY_OPERATIONAL"`) {
		t.Fatalf("camera state %v", ev)
	}
	shadowEv := rs.next(t, 3*time.Second, "incident_created")
	var shadow store.IncidentView
	if err := json.Unmarshal([]byte(shadowEv.Data), &shadow); err != nil || shadow.Status != eventengine.StatusShadow || shadow.EventType != "adult_in_restricted_zone" {
		t.Fatalf("reviewer shadow incident: %v %s", err, shadowEv.Data)
	}

	// 2. An infant leaves the changing table unheld: ALERTED.
	now = time.Now().UTC()
	batch = []eventengine.Observation{observation("cam1", now.Add(-900*time.Millisecond), infantAt("i1", 0.25, 0.45))}
	for dt := -700 * time.Millisecond; dt <= -100*time.Millisecond; dt += 200 * time.Millisecond {
		batch = append(batch, observation("cam1", now.Add(dt), infantAt("i1", 0.25, 0.75)))
	}
	if st, out := e.ingest(testToken, batch...); st != http.StatusOK {
		t.Fatalf("ingest alert batch: %d %v", st, out)
	}

	// The caregiver's first incident event is the alerted one: the SHADOW
	// incident never reached them.
	created := cs.next(t, 3*time.Second, "incident_created", "incident_merged", "incident_updated")
	var alerted store.IncidentView
	if err := json.Unmarshal([]byte(created.Data), &alerted); err != nil {
		t.Fatal(err)
	}
	if created.Type != "incident_created" || alerted.Status != eventengine.StatusAlerted || alerted.EventType != "infant_off_surface" ||
		alerted.Lifecycle != "new" || alerted.Source != eventengine.SourceMock || alerted.ID == shadow.ID {
		t.Fatalf("caregiver incident event: %s %s", created.Type, created.Data)
	}
	if ev := cs.next(t, 3*time.Second, "alert"); !strings.Contains(ev.Data, alerted.ID) || !strings.Contains(ev.Data, `"level":0`) {
		t.Fatalf("alert event %v", ev)
	}
	ready := cs.next(t, 5*time.Second, "clip_ready")
	var cr struct {
		IncidentID string `json:"incident_id"`
		ClipID     string `json:"clip_id"`
		Partial    bool   `json:"partial"`
	}
	if err := json.Unmarshal([]byte(ready.Data), &cr); err != nil || cr.IncidentID != alerted.ID || cr.ClipID != alerted.ID+"-cam1" {
		t.Fatalf("clip_ready %v", ready)
	}
	// The reviewer also gets the SHADOW incident's clip; the caregiver must not.
	eventually(t, 5*time.Second, "shadow clip stored", func() bool {
		c, err := e.db.Clip(t.Context(), shadow.ID+"-cam1")
		return err == nil && c != nil
	})
	for _, ev := range cs.drain(300 * time.Millisecond) {
		if strings.Contains(ev.Data, shadow.ID) {
			t.Fatalf("SHADOW event leaked to caregiver: %v", ev)
		}
	}
	if cs.pings.Load() == 0 {
		t.Error("no keep-alive pings on the event stream")
	}

	// Detail includes the encrypted clip; the plaintext spool is gone.
	var detail store.IncidentDetail
	e.getJSON(lead, "/api/v1/incidents/"+alerted.ID, &detail)
	if len(detail.Clips) != 1 || detail.Clips[0].SizeBytes == 0 || detail.Clips[0].SHA256 == "" {
		t.Fatalf("detail clips %+v", detail.Clips)
	}
	if entries, _ := filepathGlob(e.s.Cfg.ClipSpoolDir); len(entries) != 0 {
		t.Fatalf("plaintext left in spool: %v", entries)
	}

	// 3. Clip playback through a short-lived token.
	if st, _ := e.do(care, "POST", "/api/v1/clips/"+cr.ClipID+"/token", "{}"); st != http.StatusForbidden {
		t.Fatalf("caregiver token: %d", st)
	}
	if st, _ := e.do(lead, "POST", "/api/v1/clips/"+shadow.ID+"-cam1/token", "{}"); st != http.StatusNotFound {
		t.Fatalf("lead token for SHADOW clip: %d, want 404", st)
	}
	st, b := e.do(lead, "POST", "/api/v1/clips/"+cr.ClipID+"/token", "{}")
	var tok struct {
		URL       string    `json:"url"`
		ExpiresAt time.Time `json:"expires_at"`
	}
	if err := json.Unmarshal(b, &tok); st != 200 || err != nil || !strings.HasPrefix(tok.URL, "/api/v1/clips/"+cr.ClipID+"/media?token=") {
		t.Fatalf("token: %d %s", st, b)
	}
	if d := time.Until(tok.ExpiresAt); d < 4*time.Minute || d > 5*time.Minute+time.Second {
		t.Fatalf("token lifetime %v", d)
	}
	want := strings.Repeat("clip-for-cam1;", 100)
	anon := e.client("") // the token alone authorizes playback (a <video> element)
	get := func(u, rng string) (*http.Response, string) {
		req, _ := http.NewRequest("GET", e.api.URL+u, nil)
		if rng != "" {
			req.Header.Set("Range", rng)
		}
		resp, err := anon.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp, string(body)
	}
	resp, body := get(tok.URL, "bytes=0-13")
	if resp.StatusCode != http.StatusPartialContent || body != want[:14] || resp.Header.Get("Content-Type") != "video/mp4" ||
		!strings.HasPrefix(resp.Header.Get("Content-Range"), "bytes 0-13/1400") {
		t.Fatalf("range: %d %q %v", resp.StatusCode, body, resp.Header)
	}
	if resp, body = get(tok.URL, ""); resp.StatusCode != 200 || body != want {
		t.Fatalf("full: %d %d bytes", resp.StatusCode, len(body))
	}
	if resp, _ = get(strings.Replace(tok.URL, cr.ClipID, shadow.ID+"-cam1", 1), ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("token reused for another clip: %d", resp.StatusCode)
	}
	counts, _ := auditActions(t, e)
	if counts["clip_viewed"] != 1 || counts["incident_created"] != 2 || counts["clip_stored"] != 2 {
		t.Fatalf("audit %v", counts)
	}
	// Expired token.
	e.s.mu.Lock()
	for k, v := range e.s.clipTokens {
		v.expires = time.Now().Add(-time.Second)
		e.s.clipTokens[k] = v
	}
	e.s.mu.Unlock()
	if resp, _ = get(tok.URL, "bytes=0-1"); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expired token: %d", resp.StatusCode)
	}

	// 4. Acknowledging stops the alert and reaches every client.
	if st, _ := e.do(care, "POST", "/api/v1/incidents/"+alerted.ID+"/ack", "{}"); st != 200 {
		t.Fatalf("ack %d", st)
	}
	if ev := rs.next(t, 2*time.Second, "incident_updated"); !strings.Contains(ev.Data, `"acknowledged_by":"care"`) {
		t.Fatalf("reviewer update %v", ev)
	}
	if n := e.visionN.Load(); n != 2 {
		t.Fatalf("vision-worker clip calls = %d, want 2 (one per incident and camera)", n)
	}
}

func filepathGlob(dir string) ([]string, error) { return filepath.Glob(filepath.Join(dir, "*")) }
