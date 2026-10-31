package alerts_test

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/alerts"
	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/hub"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
	"github.com/saimouli3/zeoatap/services/core/internal/testutil/pgtest"
)

func alertedIncident(t *testing.T, db *store.DB, key string) store.IncidentView {
	t.Helper()
	now := time.Now().UTC()
	_, _, err := db.InsertIfAbsent(&eventengine.Incident{ID: "INC-" + key, IdempotencyKey: key, RoomID: "infants-a",
		RoomZone: "changing-table-1", EventType: "infant_off_surface", Family: "A", Start: now, End: now,
		Cameras: []string{"cam1"}, Status: eventengine.StatusAlerted, RuleVersion: "a1-v0.1", Source: "MOCK"})
	if err != nil {
		t.Fatal(err)
	}
	v, err := db.IncidentView(context.Background(), "INC-"+key)
	if err != nil {
		t.Fatal(err)
	}
	return *v
}

// next waits for the next hub event of the given type.
func next(t *testing.T, sub *hub.Sub, typ string, within time.Duration) string {
	t.Helper()
	deadline := time.After(within)
	for {
		select {
		case msg, ok := <-sub.C:
			if !ok {
				t.Fatal("subscription closed")
			}
			if strings.HasPrefix(string(msg), "event: "+typ+"\n") {
				return string(msg)
			}
		case <-deadline:
			t.Fatalf("no %s event within %v", typ, within)
		}
	}
}

func TestWebhookRetriesThenFails(t *testing.T) {
	db, _ := pgtest.New(t)
	var calls atomic.Int32
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "clip") || strings.Contains(string(body), "http") {
			t.Errorf("webhook payload must not carry media links: %s", body)
		}
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer hook.Close()

	h := hub.New()
	sub := h.Subscribe(false)
	d := &alerts.Dispatcher{DB: db, Hub: h, Client: hook.Client(), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Cfg: alerts.Config{WebhookURL: hook.URL, Interval: 20 * time.Millisecond, RetryBase: 10 * time.Millisecond, MaxAttempts: 3}}
	inc := alertedIncident(t, db, "w1")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := d.Raise(ctx, inc, 0); err != nil {
		t.Fatal(err)
	}
	next(t, sub, "alert", time.Second)
	// Raising the same level again is idempotent: no second alert event.
	if err := d.Raise(ctx, inc, 0); err != nil {
		t.Fatal(err)
	}
	go d.Run(ctx)

	msg := next(t, sub, "alert_delivery_failed", 5*time.Second)
	if !strings.Contains(msg, `"incident_id":"INC-w1"`) || !strings.Contains(msg, `"channel":"webhook"`) {
		t.Fatalf("unexpected event %q", msg)
	}
	if n := calls.Load(); n != 3 {
		t.Fatalf("webhook calls = %d, want 3", n)
	}
	list, err := db.AlertsFor(ctx, "INC-w1")
	if err != nil {
		t.Fatal(err)
	}
	var webhook store.Alert
	for _, a := range list {
		if a.Channel == alerts.ChannelWebhook {
			webhook = a
		}
	}
	if webhook.State != "failed" || webhook.Attempts != 3 || !strings.Contains(webhook.LastError, "500") {
		t.Fatalf("webhook alert = %+v", webhook)
	}
	failed, _, err := db.AlertHealth(ctx, 10*time.Second)
	if err != nil || failed != 1 {
		t.Fatalf("alert health failed=%d err=%v", failed, err)
	}
}

func TestEscalatesUnacknowledged(t *testing.T) {
	db, _ := pgtest.New(t)
	var delivered atomic.Int32
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		delivered.Add(1)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer hook.Close()
	h := hub.New()
	sub := h.Subscribe(false)
	d := &alerts.Dispatcher{DB: db, Hub: h, Client: hook.Client(), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Cfg: alerts.Config{WebhookURL: hook.URL, AckTimeout: 150 * time.Millisecond, MaxLevel: 2, Interval: 20 * time.Millisecond}}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	inc := alertedIncident(t, db, "e1")
	acked := alertedIncident(t, db, "e2")
	if _, err := db.Acknowledge(ctx, acked.ID, "jane"); err != nil {
		t.Fatal(err)
	}
	for _, i := range []store.IncidentView{inc, acked} {
		if err := d.Raise(ctx, i, 0); err != nil {
			t.Fatal(err)
		}
	}
	go d.Run(ctx)

	for _, want := range []string{`"level":1`, `"level":2`} {
		msg := next(t, sub, "incident_escalated", 3*time.Second)
		if !strings.Contains(msg, `"incident_id":"INC-e1"`) || !strings.Contains(msg, want) {
			t.Fatalf("escalation event %q, want %s", msg, want)
		}
	}
	time.Sleep(400 * time.Millisecond) // past another timeout: must stop at MaxLevel
	got, err := db.IncidentView(ctx, "INC-e1")
	if err != nil || got.EscalationLevel != 2 {
		t.Fatalf("escalation level = %d (%v), want 2", got.EscalationLevel, err)
	}
	if a, _ := db.IncidentView(ctx, "INC-e2"); a.EscalationLevel != 0 {
		t.Fatalf("acknowledged incident escalated to %d", a.EscalationLevel)
	}
	list, _ := db.AlertsFor(ctx, "INC-e1")
	if len(list) != 6 { // dashboard + webhook at levels 0, 1, 2
		t.Fatalf("alerts for escalated incident = %d, want 6: %+v", len(list), list)
	}
	if delivered.Load() < 3 {
		t.Fatalf("webhook deliveries = %d, want >= 3", delivered.Load())
	}
}
