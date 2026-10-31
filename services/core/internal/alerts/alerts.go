// Package alerts delivers alert notifications, retries failures, escalates
// unacknowledged alerts, and makes delivery problems visible.
package alerts

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/hub"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

const (
	ChannelDashboard = "dashboard"
	ChannelWebhook   = "webhook"
)

type Config struct {
	// WebhookURL receives alerts for push/SMS relays. Optional. The payload
	// never contains images, clip links or child-identifying data.
	WebhookURL string
	AckTimeout time.Duration // escalate when not acknowledged within this
	MaxLevel   int           // stop escalating after this many levels

	Interval    time.Duration // delivery/escalation loop period (default 1 s)
	RetryBase   time.Duration // first retry delay, doubled per attempt (default 2 s)
	MaxAttempts int           // webhook attempts before the alert is failed (default 5)
}

func (c Config) withDefaults() Config {
	if c.Interval <= 0 {
		c.Interval = time.Second
	}
	if c.RetryBase <= 0 {
		c.RetryBase = 2 * time.Second
	}
	if c.MaxAttempts <= 0 {
		c.MaxAttempts = 5
	}
	return c
}

type Dispatcher struct {
	DB     *store.DB
	Hub    *hub.Hub
	Cfg    Config
	Client *http.Client
	Log    *slog.Logger
}

// Payload is deliberately minimal (PRIVACY §2: no media in notifications).
type Payload struct {
	IncidentID string    `json:"incident_id"`
	Room       string    `json:"room"`
	Message    string    `json:"message"`
	Level      int       `json:"escalation_level"`
	At         time.Time `json:"at"`
}

// Raise records alerts for an incident at the given escalation level and
// pushes the dashboard notification immediately.
// Raising the same level twice is a no-op.
func (d *Dispatcher) Raise(ctx context.Context, inc store.IncidentView, level int) error {
	created, err := d.DB.CreateAlert(ctx, inc.ID, ChannelDashboard, level)
	if err != nil {
		return err
	}
	if created {
		d.Hub.Publish("alert", map[string]any{"incident_id": inc.ID, "room_id": inc.RoomID, "event_type": inc.EventType, "level": level})
	}
	if d.Cfg.WebhookURL != "" {
		if _, err := d.DB.CreateAlert(ctx, inc.ID, ChannelWebhook, level); err != nil {
			return err
		}
	}
	return nil
}

// Run delivers pending webhook alerts and escalates until ctx ends.
func (d *Dispatcher) Run(ctx context.Context) {
	t := time.NewTicker(d.Cfg.withDefaults().Interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			d.deliver(ctx)
			d.escalate(ctx)
		}
	}
}

func (d *Dispatcher) deliver(ctx context.Context) {
	// Dashboard alerts are delivered by the SSE publish in Raise; mark them.
	due, err := d.DB.DueAlerts(ctx, ChannelDashboard)
	if err != nil {
		d.Log.Error("load due dashboard alerts", "err", err)
	}
	for _, a := range due {
		if err := d.DB.MarkAlertDelivered(ctx, a.ID); err != nil {
			d.Log.Error("mark alert delivered", "alert", a.ID, "err", err)
		}
	}
	if d.Cfg.WebhookURL == "" {
		return
	}
	due, err = d.DB.DueAlerts(ctx, ChannelWebhook)
	if err != nil {
		d.Log.Error("load due alerts", "err", err)
		return
	}
	for _, a := range due {
		inc, err := d.DB.IncidentView(ctx, a.IncidentID)
		if err != nil {
			d.Log.Error("load incident for alert", "incident", a.IncidentID, "err", err)
			continue
		}
		err = d.post(ctx, Payload{IncidentID: inc.ID, Room: inc.RoomID, Level: a.Level, At: time.Now().UTC(),
			Message: "Review required: possible event in " + inc.RoomID})
		if err == nil {
			if err := d.DB.MarkAlertDelivered(ctx, a.ID); err != nil {
				d.Log.Error("mark alert delivered", "alert", a.ID, "err", err)
			}
			continue
		}
		cfg := d.Cfg.withDefaults()
		backoff := cfg.RetryBase << min(a.Attempts, 10)
		failed, merr := d.DB.MarkAlertAttemptFailed(ctx, a.ID, err.Error(), backoff, cfg.MaxAttempts)
		if merr != nil {
			d.Log.Error("record alert failure", "alert", a.ID, "err", merr)
			continue
		}
		d.Log.Warn("alert delivery failed", "incident", inc.ID, "attempt", a.Attempts+1, "err", err)
		if failed {
			if err := d.DB.Audit(ctx, "system", "alert_delivery_failed", inc.ID, map[string]any{"channel": ChannelWebhook, "level": a.Level}); err != nil {
				d.Log.Error("audit", "err", err)
			}
			d.Hub.Publish("alert_delivery_failed", map[string]any{"incident_id": inc.ID, "channel": ChannelWebhook, "error": err.Error()})
		}
	}
}

func (d *Dispatcher) post(ctx context.Context, p Payload) error {
	body, err := json.Marshal(p)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.Cfg.WebhookURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := d.Client.Do(req)
	if err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("webhook returned %d", resp.StatusCode)
	}
	return nil
}

func (d *Dispatcher) escalate(ctx context.Context) {
	if d.Cfg.AckTimeout <= 0 {
		return
	}
	stale, err := d.DB.Unacknowledged(ctx, d.Cfg.AckTimeout, d.Cfg.MaxLevel)
	if err != nil {
		d.Log.Error("load unacknowledged", "err", err)
		return
	}
	for _, inc := range stale {
		level, err := d.DB.Escalate(ctx, inc.ID)
		if errors.Is(err, store.ErrConflict) {
			continue // acknowledged meanwhile
		}
		if err != nil {
			d.Log.Error("escalate", "incident", inc.ID, "err", err)
			continue
		}
		d.Log.Warn("alert not acknowledged, escalating", "incident", inc.ID, "level", level)
		if err := d.DB.Audit(ctx, "system", "incident_escalated", inc.ID, map[string]any{"level": level}); err != nil {
			d.Log.Error("audit", "err", err)
		}
		d.Hub.Publish("incident_escalated", map[string]any{"incident_id": inc.ID, "level": level})
		if err := d.Raise(ctx, inc, level); err != nil {
			d.Log.Error("raise escalated alert", "incident", inc.ID, "err", err)
		}
	}
}
