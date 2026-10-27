package eventengine

import (
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 27, 14, 0, 0, 0, time.UTC)

func rect(x1, y1, x2, y2 float64) []Point {
	return []Point{{x1, y1}, {x2, y1}, {x2, y2}, {x1, y2}}
}

func testCameras() []Camera {
	return []Camera{
		{ID: "cam1", RoomID: "infants-a", Zones: []Zone{
			{ID: "door", RoomZone: "kitchen-door", Kind: ZoneRestricted, Polygon: rect(0.8, 0.5, 1.0, 1.0)},
			{ID: "table", RoomZone: "changing-table-1", Kind: ZoneSurface, Polygon: rect(0.1, 0.3, 0.4, 0.5)},
		}},
		{ID: "cam2", RoomID: "infants-a", Zones: []Zone{
			{ID: "door-b", RoomZone: "kitchen-door", Kind: ZoneRestricted, Polygon: rect(0.0, 0.4, 0.3, 1.0)},
		}},
	}
}

func testRules(validated bool) []Rule {
	return []Rule{
		&AdultInRestrictedZone{MinScore: 0.5, Cfg: RuleConfig{MinDuration: 2 * time.Second, MergeGap: 30 * time.Second, Bucket: time.Minute, Policy: PolicyReview}},
		&InfantOffSurface{MinScore: 0.4, MinVisibility: 0.5, Recent: 2 * time.Second,
			Cfg: RuleConfig{MinDuration: 500 * time.Millisecond, MergeGap: 10 * time.Second, Bucket: time.Minute, Policy: PolicyAlert, ValidatedForAlerting: validated}},
	}
}

func obs(cam string, dt time.Duration, tracks ...Track) Observation {
	return Observation{CameraID: cam, CaptureTS: t0.Add(dt), Source: SourceMock, AIAvailable: true,
		ModelVersions: map[string]string{"detector": "mock-0"}, Tracks: tracks}
}

func adult(id string, x, y float64) Track {
	return Track{ID: id, Role: RoleAdult, Score: 0.9, Box: [4]float64{x - 0.05, y - 0.3, x + 0.05, y}, PoseQuality: 0.8, Visibility: 1}
}

func infant(id string, x, y float64, held bool, vis float64) Track {
	return Track{ID: id, Role: RoleInfant, Score: 0.8, Box: [4]float64{x - 0.04, y - 0.08, x + 0.04, y}, PoseQuality: 0.7, Visibility: vis, Held: held}
}

func feed(t *testing.T, e *Engine, list ...Observation) []Output {
	t.Helper()
	var all []Output
	for _, o := range list {
		out, err := e.Process(o)
		if err != nil {
			t.Fatalf("process: %v", err)
		}
		all = append(all, out...)
	}
	return all
}

func incidents(t *testing.T, s Store) []*Incident {
	t.Helper()
	l, err := s.List()
	if err != nil {
		t.Fatal(err)
	}
	return l
}

// steps produces observations every 200 ms over [from, to).
func steps(cam string, from, to time.Duration, tr func(time.Duration) []Track) []Observation {
	var out []Observation
	for d := from; d < to; d += 200 * time.Millisecond {
		out = append(out, obs(cam, d, tr(d)...))
	}
	return out
}

func TestAdultInZonePersistingCreatesOneReviewIncident(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	feed(t, e, steps("cam1", 0, 4*time.Second, func(time.Duration) []Track { return []Track{adult("a1", 0.9, 0.9)} })...)
	got := incidents(t, s)
	if len(got) != 1 {
		t.Fatalf("want 1 incident, got %d", len(got))
	}
	if got[0].Status != StatusReview || got[0].EventType != "adult_in_restricted_zone" || got[0].Source != SourceMock {
		t.Fatalf("unexpected incident %+v", got[0])
	}
}

func TestBriefZoneEntryIsDiscarded(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	feed(t, e, steps("cam1", 0, time.Second, func(time.Duration) []Track { return []Track{adult("a1", 0.9, 0.9)} })...)
	feed(t, e, steps("cam1", time.Second, 3*time.Second, func(time.Duration) []Track { return []Track{adult("a1", 0.5, 0.9)} })...)
	if n := len(incidents(t, s)); n != 0 {
		t.Fatalf("want 0 incidents, got %d", n)
	}
}

func TestReplayIsIdempotent(t *testing.T) {
	s := NewMemoryStore()
	stream := steps("cam1", 0, 4*time.Second, func(time.Duration) []Track { return []Track{adult("a1", 0.9, 0.9)} })
	feed(t, New(testCameras(), testRules(false), s), stream...)
	feed(t, New(testCameras(), testRules(false), s), stream...)
	if n := len(incidents(t, s)); n != 1 {
		t.Fatalf("replay must not duplicate incidents, got %d", n)
	}
}

func TestTwoCamerasSameRoomZoneMergeIntoOneIncident(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	for d := time.Duration(0); d < 4*time.Second; d += 200 * time.Millisecond {
		feed(t, e, obs("cam1", d, adult("a1", 0.9, 0.9)), obs("cam2", d+50*time.Millisecond, adult("b7", 0.1, 0.9)))
	}
	got := incidents(t, s)
	if len(got) != 1 {
		t.Fatalf("want 1 merged incident, got %d", len(got))
	}
	if len(got[0].Cameras) != 2 {
		t.Fatalf("want evidence from 2 cameras, got %v", got[0].Cameras)
	}
}

func onTableThenFloor(held bool) func(time.Duration) []Track {
	return func(d time.Duration) []Track {
		if d < time.Second {
			return []Track{infant("i1", 0.25, 0.45, false, 0.9)}
		}
		return []Track{infant("i1", 0.25, 0.9, held, 0.9)}
	}
}

func TestInfantHeldByAdultDoesNotTrigger(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	feed(t, e, steps("cam1", 0, 3*time.Second, onTableThenFloor(true))...)
	if n := len(incidents(t, s)); n != 0 {
		t.Fatalf("held infant must not trigger, got %d", n)
	}
}

func TestUnvalidatedAlertRuleStaysInShadow(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	feed(t, e, steps("cam1", 0, 3*time.Second, onTableThenFloor(false))...)
	got := incidents(t, s)
	if len(got) != 1 || got[0].Status != StatusShadow {
		t.Fatalf("want 1 SHADOW incident, got %+v", got)
	}
}

func TestValidatedAlertRuleAlerts(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(true), s)
	feed(t, e, steps("cam1", 0, 3*time.Second, onTableThenFloor(false))...)
	got := incidents(t, s)
	if len(got) != 1 || got[0].Status != StatusAlerted {
		t.Fatalf("want 1 ALERTED incident, got %+v", got)
	}
}

func TestVisibilityLostMidCandidateIsInconclusive(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(true), s)
	feed(t, e, steps("cam1", 0, time.Second, onTableThenFloor(false))...)
	feed(t, e, obs("cam1", time.Second, infant("i1", 0.25, 0.9, false, 0.9)))
	feed(t, e, obs("cam1", 1200*time.Millisecond, infant("i1", 0.25, 0.9, false, 0.2)))
	got := incidents(t, s)
	if len(got) != 1 || got[0].Status != StatusInconclusive {
		t.Fatalf("want 1 INCONCLUSIVE incident, got %+v", got)
	}
}

func TestStaleCameraIsNeverOperational(t *testing.T) {
	e := New(testCameras(), testRules(false), NewMemoryStore())
	if st := e.State("cam1", t0); st != StateNeverConnected {
		t.Fatalf("want NOT_CONNECTED before any observation, got %s", st)
	}
	feed(t, e, obs("cam1", 0))
	if st := e.State("cam1", t0.Add(time.Second)); st != StateOperational {
		t.Fatalf("want operational, got %s", st)
	}
	out, err := e.Tick(t0.Add(5 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, o := range out {
		if o.CameraID == "cam1" && o.State == StateCameraUnavail {
			found = true
		}
	}
	if !found {
		t.Fatalf("tick must report cam1 unavailable, got %+v", out)
	}
}

func TestDegradedVideoClosesCandidateAsInconclusive(t *testing.T) {
	s := NewMemoryStore()
	e := New(testCameras(), testRules(false), s)
	feed(t, e, steps("cam1", 0, time.Second, func(time.Duration) []Track { return []Track{adult("a1", 0.9, 0.9)} })...)
	o := obs("cam1", time.Second, adult("a1", 0.9, 0.9))
	o.Quality = Quality{Degraded: true, Reason: "obstructed"}
	out := feed(t, e, o)
	if e.State("cam1", o.CaptureTS) != StateVideoDegraded {
		t.Fatal("want VIDEO_DEGRADED")
	}
	got := incidents(t, s)
	if len(got) != 1 || got[0].Status != StatusInconclusive {
		t.Fatalf("want INCONCLUSIVE after degradation, got %+v (out %+v)", got, out)
	}
}

func TestAIUnavailableIsReported(t *testing.T) {
	e := New(testCameras(), testRules(false), NewMemoryStore())
	o := obs("cam1", 0)
	o.AIAvailable = false
	feed(t, e, o)
	if st := e.State("cam1", t0); st != StateAIUnavailable {
		t.Fatalf("want AI_UNAVAILABLE, got %s", st)
	}
}

func TestInvalidObservationsAreRejected(t *testing.T) {
	e := New(testCameras(), testRules(false), NewMemoryStore())
	bad := []Observation{
		{CaptureTS: t0, Source: SourceMock},
		obs("unknown", 0),
		obs("cam1", 0, Track{ID: "x", Role: "dog"}),
		obs("cam1", 0, Track{ID: "x", Role: RoleAdult, Score: 1.5}),
	}
	for i, o := range bad {
		if _, err := e.Process(o); err == nil {
			t.Fatalf("case %d: want error", i)
		}
	}
}
