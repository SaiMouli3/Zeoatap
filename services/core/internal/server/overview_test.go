package server

import (
	"context"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
)

func getOverview(t *testing.T, e *env, c *http.Client) overviewResp {
	t.Helper()
	var o overviewResp
	e.getJSON(c, "/api/v1/overview", &o)
	return o
}

func roomState(o overviewResp, id string) string {
	for _, r := range o.Rooms {
		if r.ID == id {
			return r.State
		}
	}
	return "missing"
}

func camStateOf(o overviewResp, id string) string {
	for _, r := range o.Rooms {
		for _, c := range r.Cameras {
			if c.ID == id {
				return c.State
			}
		}
	}
	return "missing"
}

// The overview is never green unless every camera is fresh, of good quality
// and analysed, and the engine itself is alive.
func TestOverviewStates(t *testing.T) {
	e := newEnv(t, envOpts{cfg: Config{TickEvery: 50 * time.Millisecond}})
	care := e.client("caregiver")

	o := getOverview(t, e, care)
	if o.State == "FULLY_OPERATIONAL" {
		t.Fatal("green before any observation")
	}
	eventually(t, 2*time.Second, "first tick", func() bool {
		o := getOverview(t, e, care)
		return o.Services["engine"] == "ok" && camStateOf(o, "cam1") == "NOT_CONNECTED"
	})
	o = getOverview(t, e, care)
	if o.Facility.Name != "Sunflower Nursery" || o.State != "NOT_CONNECTED" || roomState(o, "toddlers") != "NOT_CONNECTED" ||
		o.Services["engine"] != "ok" || o.Services["database"] != "ok" || o.Services["evidence"] != "ok" {
		t.Fatalf("initial overview %+v", o)
	}
	if len(o.Rooms) != 2 || len(o.Rooms[1].Cameras) != 0 { // a room with no cameras still appears, not monitored
		t.Fatalf("rooms %+v", o.Rooms)
	}

	// Give the empty room a camera so the facility can become operational.
	cam3 := `{"id":"cam3","room_id":"toddlers","name":"Nap room","authorized_by":"Director","authorized_at":"2026-10-02","authorization_scope":"Nap room"}`
	if st, b := e.do(e.client("admin"), "POST", "/api/v1/cameras", cam3); st != http.StatusCreated {
		t.Fatalf("create cam3: %d %s", st, b)
	}

	// Feed every camera at 5 Hz; cam2's condition is switchable.
	var mu sync.Mutex
	cam2 := func(o *eventengine.Observation) {}
	setCam2 := func(f func(*eventengine.Observation)) { mu.Lock(); cam2 = f; mu.Unlock() }
	ctx, stopFeed := context.WithCancel(context.Background())
	fed := make(chan struct{})
	go func() {
		defer close(fed)
		tk := time.NewTicker(200 * time.Millisecond)
		defer tk.Stop()
		for {
			now := time.Now().UTC()
			o2 := observation("cam2", now)
			mu.Lock()
			cam2(&o2)
			mu.Unlock()
			e.ingest(testToken, observation("cam1", now), o2, observation("cam3", now))
			select {
			case <-ctx.Done():
				return
			case <-tk.C:
			}
		}
	}()
	defer func() { stopFeed(); <-fed }()

	eventually(t, 3*time.Second, "facility operational", func() bool { return getOverview(t, e, care).State == "FULLY_OPERATIONAL" })

	setCam2(func(o *eventengine.Observation) { o.Quality = eventengine.Quality{Degraded: true, Reason: "blur"} })
	eventually(t, 3*time.Second, "video degraded", func() bool {
		o := getOverview(t, e, care)
		return o.State == "VIDEO_DEGRADED" && roomState(o, "infants-a") == "VIDEO_DEGRADED" && roomState(o, "toddlers") == "FULLY_OPERATIONAL"
	})

	setCam2(func(o *eventengine.Observation) { o.AIAvailable = false })
	eventually(t, 3*time.Second, "AI unavailable", func() bool {
		o := getOverview(t, e, care)
		return o.State == "AI_UNAVAILABLE" && camStateOf(o, "cam2") == "AI_UNAVAILABLE" && camStateOf(o, "cam1") == "FULLY_OPERATIONAL"
	})

	// Feed stops: after the stale window every camera is unavailable.
	stopFeed()
	<-fed
	eventually(t, 6*time.Second, "cameras unavailable", func() bool {
		o := getOverview(t, e, care)
		return o.State == "CAMERA_UNAVAILABLE" && camStateOf(o, "cam1") == "CAMERA_UNAVAILABLE" && camStateOf(o, "cam3") == "CAMERA_UNAVAILABLE"
	})

	// A stalled engine loop freezes the last reported states; they must not
	// be trusted. Simulate a stall with healthy-looking cached states.
	e.stop()
	e.s.mu.Lock()
	for id := range e.s.camStates {
		e.s.camStates[id] = camState2("FULLY_OPERATIONAL")
	}
	e.s.lastEngine = time.Now().Add(-10 * time.Second)
	e.s.mu.Unlock()
	o = getOverview(t, e, care)
	if o.State != "AI_UNAVAILABLE" || camStateOf(o, "cam1") != "AI_UNAVAILABLE" || o.Services["engine"] != "error" || o.Services["engine_error"] == "" {
		t.Fatalf("stalled engine overview: state=%s services=%v", o.State, o.Services)
	}
	var cams []struct{ ID, State string }
	e.getJSON(care, "/api/v1/cameras", &cams)
	for _, c := range cams {
		if c.State == "FULLY_OPERATIONAL" {
			t.Fatalf("camera %s green while the engine is stalled", c.ID)
		}
	}
	if st, _ := e.do(care, "GET", "/readyz", ""); st != http.StatusServiceUnavailable {
		t.Fatalf("readyz with stalled engine: %d", st)
	}
}

func camState2(st string) camState { return camState{State: st, Since: time.Now()} }

func TestStateRanking(t *testing.T) {
	order := []string{"FULLY_OPERATIONAL", "VIDEO_DEGRADED", "AI_UNAVAILABLE", "NOT_CONNECTED", "CAMERA_UNAVAILABLE"}
	for i := 1; i < len(order); i++ {
		if worse(order[i-1], order[i]) != order[i] || worse(order[i], order[i-1]) != order[i] {
			t.Fatalf("%s must be worse than %s", order[i], order[i-1])
		}
	}
	if worse("FULLY_OPERATIONAL", "SOMETHING_NEW") == "FULLY_OPERATIONAL" {
		t.Fatal("unknown states must never rank as operational")
	}
}
