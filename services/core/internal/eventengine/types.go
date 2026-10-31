// Package eventengine turns detector observations into monitoring states and
// incidents. Detectors only report what they saw; every safety decision is made
// here by explicit, versioned, per-event rules (ADR-002).
package eventengine

import (
	"errors"
	"fmt"
	"time"
)

// Role of a tracked subject.
type Role string

const (
	RoleInfant Role = "infant"
	RoleAdult  Role = "adult"
)

// Track is one tracked subject in one frame. Box is normalized [x1, y1, x2, y2]
// with y growing downwards, matching image coordinates.
type Track struct {
	ID          string     `json:"id"`
	Role        Role       `json:"role"`
	Score       float64    `json:"score"`
	Box         [4]float64 `json:"box"`
	PoseQuality float64    `json:"pose_quality"`
	Visibility  float64    `json:"visibility"`
	Held        bool       `json:"held"`
}

// FootPoint is the bottom-centre of the box, used for zone membership.
func (t Track) FootPoint() Point {
	return Point{X: (t.Box[0] + t.Box[2]) / 2, Y: t.Box[3]}
}

// Quality describes whether the frame is fit for analysis.
type Quality struct {
	Degraded bool   `json:"degraded"`
	Reason   string `json:"reason,omitempty"`
}

// Observation is the contract between the vision worker and core. It mirrors
// packages/contracts/observation.schema.json.
type Observation struct {
	CameraID      string            `json:"camera_id"`
	CaptureTS     time.Time         `json:"capture_ts"`
	TSSource      string            `json:"ts_source,omitempty"` // rtcp | receive
	Source        string            `json:"source"`
	ModelVersions map[string]string `json:"model_versions"`
	Quality       Quality           `json:"quality"`
	AIAvailable   bool              `json:"ai_available"`
	Tracks        []Track           `json:"tracks"`
}

// SourceMock marks observations produced by a mock detector. Incidents derived
// from them carry the same marker so they can never be mistaken for real
// detections.
const SourceMock = "MOCK"

// Validate rejects malformed observations before they reach any rule.
func (o Observation) Validate() error {
	if o.CameraID == "" {
		return errors.New("observation: camera_id is required")
	}
	if o.CaptureTS.IsZero() {
		return errors.New("observation: capture_ts is required")
	}
	if o.Source == "" {
		return errors.New("observation: source is required")
	}
	for _, t := range o.Tracks {
		if t.ID == "" {
			return errors.New("observation: track id is required")
		}
		if t.Role != RoleInfant && t.Role != RoleAdult {
			return fmt.Errorf("observation: track %s has unknown role %q", t.ID, t.Role)
		}
		for _, v := range []float64{t.Score, t.PoseQuality, t.Visibility, t.Box[0], t.Box[1], t.Box[2], t.Box[3]} {
			if v < 0 || v > 1 {
				return fmt.Errorf("observation: track %s has value outside [0,1]", t.ID)
			}
		}
		if t.Box[2] < t.Box[0] || t.Box[3] < t.Box[1] {
			return fmt.Errorf("observation: track %s has inverted box", t.ID)
		}
	}
	return nil
}

// Point is a normalized image coordinate.
type Point struct{ X, Y float64 }

// ZoneKind distinguishes how a rule interprets a zone.
type ZoneKind string

const (
	ZoneRestricted ZoneKind = "restricted"
	ZoneSurface    ZoneKind = "surface"
)

// Zone is a polygon drawn on one camera's calibration snapshot. RoomZone is the
// room-level identifier shared by every camera that sees the same area, which
// is what lets the engine merge one event seen by several cameras.
type Zone struct {
	ID       string   `json:"id"`
	RoomZone string   `json:"room_zone"`
	Kind     ZoneKind `json:"kind"`
	Polygon  []Point  `json:"polygon"`
}

// Contains reports whether p lies inside the polygon (ray casting).
func (z Zone) Contains(p Point) bool {
	in := false
	n := len(z.Polygon)
	for i, j := 0, n-1; i < n; j, i = i, i+1 {
		a, b := z.Polygon[i], z.Polygon[j]
		if (a.Y > p.Y) != (b.Y > p.Y) && p.X < (b.X-a.X)*(p.Y-a.Y)/(b.Y-a.Y)+a.X {
			in = !in
		}
	}
	return in
}

// Bottom is the largest y of the polygon, i.e. the lowest edge on screen.
func (z Zone) Bottom() float64 {
	m := 0.0
	for _, p := range z.Polygon {
		if p.Y > m {
			m = p.Y
		}
	}
	return m
}

// Camera is the per-camera configuration the engine needs.
type Camera struct {
	ID     string `json:"id"`
	RoomID string `json:"room_id"`
	Zones  []Zone `json:"zones"`
}
