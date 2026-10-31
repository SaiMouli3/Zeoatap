package store_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
	"github.com/saimouli3/zeoatap/services/core/internal/testutil/pgtest"
)

var ctx = context.Background()

func seedSite(t *testing.T, db *store.DB) {
	t.Helper()
	if err := db.UpsertFacility(ctx, "f1", "Sunflower"); err != nil {
		t.Fatal(err)
	}
	if err := db.UpsertRoom(ctx, "f1", store.Room{ID: "infants-a", Name: "Infants A"}); err != nil {
		t.Fatal(err)
	}
}

func incident(key string, status eventengine.IncidentStatus, start time.Time) *eventengine.Incident {
	return &eventengine.Incident{ID: "INC-" + key, IdempotencyKey: key, RoomID: "infants-a", RoomZone: "changing-table-1",
		EventType: "infant_off_surface", Family: "A", Start: start, End: start.Add(time.Second), Cameras: []string{"cam1"},
		Status: status, RuleVersion: "a1-v0.1", ModelVersions: map[string]string{"detector": "mock-0"}, Source: "MOCK"}
}

func TestMigrationsIdempotent(t *testing.T) {
	db, _ := pgtest.New(t)
	for i := 0; i < 2; i++ {
		if err := db.Migrate(ctx); err != nil {
			t.Fatalf("migrate run %d: %v", i+2, err)
		}
	}
	var n int
	if err := db.Pool.QueryRow(ctx, `SELECT count(*) FROM schema_migrations`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("schema_migrations rows = %d, want 1", n)
	}
}

func TestInsertIdempotentAndMerge(t *testing.T) {
	db, _ := pgtest.New(t)
	t0 := time.Now().UTC().Truncate(time.Millisecond)
	inc := incident("k1", eventengine.StatusShadow, t0)
	got, created, err := db.InsertIfAbsent(inc)
	if err != nil || !created || got.ID != "INC-k1" {
		t.Fatalf("first insert: %v %v %v", got, created, err)
	}
	dup := incident("k1", eventengine.StatusShadow, t0)
	dup.ID = "INC-other"
	got, created, err = db.InsertIfAbsent(dup)
	if err != nil || created || got.ID != "INC-k1" {
		t.Fatalf("second insert must return the stored incident: %v %v %v", got, created, err)
	}

	m, err := db.FindMergeable("infants-a", "changing-table-1", "A", t0.Add(5*time.Second), 10*time.Second)
	if err != nil || m == nil || m.ID != "INC-k1" {
		t.Fatalf("FindMergeable = %v, %v", m, err)
	}
	if m, _ := db.FindMergeable("infants-a", "changing-table-1", "A", t0.Add(time.Minute), 10*time.Second); m != nil {
		t.Fatalf("incident outside the gap must not merge: %v", m.ID)
	}
	m.Cameras = []string{"cam1", "cam2"}
	m.End = t0.Add(4 * time.Second)
	m.Status = eventengine.StatusAlerted
	if err := db.Update(m); err != nil {
		t.Fatal(err)
	}
	back, err := db.Get("INC-k1")
	if err != nil || len(back.Cameras) != 2 || back.Status != eventengine.StatusAlerted || !back.End.Equal(m.End) {
		t.Fatalf("after update: %+v %v", back, err)
	}
	if g, err := db.Get("nope"); g != nil || err != nil {
		t.Fatalf("Get missing = %v %v", g, err)
	}
}

// Two cameras seeing the same room zone produce one incident through the
// real Postgres store.
func TestEngineMergesAcrossCamerasInPostgres(t *testing.T) {
	db, _ := pgtest.New(t)
	rect := func(x1, y1, x2, y2 float64) []eventengine.Point {
		return []eventengine.Point{{X: x1, Y: y1}, {X: x2, Y: y1}, {X: x2, Y: y2}, {X: x1, Y: y2}}
	}
	cams := []eventengine.Camera{
		{ID: "cam1", RoomID: "infants-a", Zones: []eventengine.Zone{{ID: "door", RoomZone: "kitchen-door", Kind: eventengine.ZoneRestricted, Polygon: rect(0.8, 0.5, 1, 1)}}},
		{ID: "cam2", RoomID: "infants-a", Zones: []eventengine.Zone{{ID: "door-b", RoomZone: "kitchen-door", Kind: eventengine.ZoneRestricted, Polygon: rect(0, 0.4, 0.3, 1)}}},
	}
	e := eventengine.New(cams, eventengine.DefaultRules(nil), db)
	t0 := time.Date(2026, 10, 31, 9, 0, 0, 0, time.UTC)
	adult := func(x float64) eventengine.Track {
		return eventengine.Track{ID: "a", Role: eventengine.RoleAdult, Score: 0.9, Box: [4]float64{x - 0.05, 0.5, x + 0.05, 0.8}, Visibility: 1}
	}
	for i := 0; i <= 30; i++ {
		ts := t0.Add(time.Duration(i) * 100 * time.Millisecond)
		for _, o := range []eventengine.Observation{
			{CameraID: "cam1", CaptureTS: ts, Source: "MOCK", AIAvailable: true, Tracks: []eventengine.Track{adult(0.9)}},
			{CameraID: "cam2", CaptureTS: ts, Source: "MOCK", AIAvailable: true, Tracks: []eventengine.Track{adult(0.15)}},
		} {
			if _, err := e.Process(o); err != nil {
				t.Fatal(err)
			}
		}
	}
	list, err := db.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || len(list[0].Cameras) != 2 || list[0].Status != eventengine.StatusReview {
		t.Fatalf("want one merged REVIEW_QUEUED incident from both cameras, got %+v", list)
	}
}

func TestAcknowledgeIdempotentAndResolveOnce(t *testing.T) {
	db, _ := pgtest.New(t)
	if _, _, err := db.InsertIfAbsent(incident("k2", eventengine.StatusAlerted, time.Now())); err != nil {
		t.Fatal(err)
	}
	changed, err := db.Acknowledge(ctx, "INC-k2", "jane")
	if err != nil || !changed {
		t.Fatalf("first ack: %v %v", changed, err)
	}
	changed, err = db.Acknowledge(ctx, "INC-k2", "bob")
	if err != nil || changed {
		t.Fatalf("second ack must be a no-op: %v %v", changed, err)
	}
	if _, err := db.Acknowledge(ctx, "INC-missing", "jane"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("ack missing: %v", err)
	}
	if err := db.Resolve(ctx, "INC-k2", "rev", "no_concern", "checked"); err != nil {
		t.Fatal(err)
	}
	if err := db.Resolve(ctx, "INC-k2", "rev", "routine_care", ""); !errors.Is(err, store.ErrConflict) {
		t.Fatalf("second disposition: %v, want ErrConflict", err)
	}
	inc, err := db.Incident(ctx, "INC-k2")
	if err != nil {
		t.Fatal(err)
	}
	if *inc.AcknowledgedBy != "jane" || *inc.Disposition != "no_concern" || inc.Lifecycle != "resolved" || len(inc.Actions) != 2 {
		t.Fatalf("unexpected incident state: %+v actions=%+v", inc.IncidentView, inc.Actions)
	}
}

func TestAuditChainDetectsTampering(t *testing.T) {
	db, _ := pgtest.New(t)
	for i, a := range []string{"login", "incident_acknowledged", "clip_viewed"} {
		if err := db.Audit(ctx, "jane", a, "INC-1", map[string]any{"n": i, "html": "<b>&</b>", "nested": map[string]any{"z": 1, "a": []int{1, 2}}}); err != nil {
			t.Fatal(err)
		}
	}
	if broken, err := db.VerifyAudit(ctx); err != nil || broken != 0 {
		t.Fatalf("intact chain: broken=%d err=%v", broken, err)
	}
	// The append-only trigger blocks edits.
	if _, err := db.Pool.Exec(ctx, `UPDATE audit_log SET actor='mallory' WHERE seq=2`); err == nil {
		t.Fatal("audit_log update must be refused by the trigger")
	}
	// An owner who disables the trigger can edit rows, but the chain shows it.
	err := pgx.BeginFunc(ctx, db.Pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change`); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `UPDATE audit_log SET payload='{"n": 99}' WHERE seq=2`); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, `ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change`)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	if broken, err := db.VerifyAudit(ctx); err != nil || broken != 2 {
		t.Fatalf("tampered chain: broken=%d err=%v, want 2", broken, err)
	}
}

func TestAuditChainDetectsDeletion(t *testing.T) {
	db, _ := pgtest.New(t)
	for i := 0; i < 3; i++ {
		if err := db.Audit(ctx, "system", "x", "s", nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.Pool.Exec(ctx, `ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change;
		DELETE FROM audit_log WHERE seq=2; ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change`); err != nil {
		t.Fatal(err)
	}
	if broken, _ := db.VerifyAudit(ctx); broken != 3 {
		t.Fatalf("deletion: broken=%d, want 3", broken)
	}
}

func TestCameraInsertAndSessions(t *testing.T) {
	db, _ := pgtest.New(t)
	seedSite(t, db)
	c := store.Camera{ID: "cam1", RoomID: "infants-a", Name: "Table", AuthorizedBy: "Director", AuthorizedAt: "2026-10-01",
		AuthorizationScope: "Room A", Enabled: true, Zones: []eventengine.Zone{}}
	if err := db.InsertCamera(ctx, c); err != nil {
		t.Fatal(err)
	}
	if err := db.InsertCamera(ctx, c); !errors.Is(err, store.ErrConflict) {
		t.Fatalf("duplicate camera: %v", err)
	}
	c.ID, c.RoomID = "cam2", "nope"
	if err := db.InsertCamera(ctx, c); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("unknown room: %v", err)
	}
	c.ID, c.RoomID, c.AuthorizedBy = "cam3", "infants-a", ""
	if err := db.InsertCamera(ctx, c); err == nil {
		t.Fatal("camera without authorization must be refused by the schema")
	}

	id, err := db.CreateUser(ctx, store.User{Username: "jane", DisplayName: "Jane", Role: "caregiver", PasswordHash: "x"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.CreateUser(ctx, store.User{Username: "jane", DisplayName: "J", Role: "lead", PasswordHash: "x"}); !errors.Is(err, store.ErrConflict) {
		t.Fatalf("duplicate user: %v", err)
	}
	if err := db.CreateSession(ctx, "h1", id, time.Minute); err != nil {
		t.Fatal(err)
	}
	if u, err := db.SessionUser(ctx, "h1", time.Minute); err != nil || u.Username != "jane" {
		t.Fatalf("session user: %v %v", u, err)
	}
	if err := db.CreateSession(ctx, "h2", id, -time.Second); err != nil {
		t.Fatal(err)
	}
	if _, err := db.SessionUser(ctx, "h2", time.Minute); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("expired session: %v", err)
	}
}
