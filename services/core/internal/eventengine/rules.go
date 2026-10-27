package eventengine

import "time"

// Policy says what a confirmed incident of this rule may do.
type Policy string

const (
	PolicyReview Policy = "review"
	PolicyAlert  Policy = "alert"
)

// RuleConfig holds the per-event thresholds. Each event type has its own; there
// is deliberately no global risk score.
type RuleConfig struct {
	MinDuration time.Duration // trigger must persist this long
	MergeGap    time.Duration // windows closer than this in the same room zone merge
	Bucket      time.Duration // idempotency time bucket
	Policy      Policy
	// ValidatedForAlerting may only be true for rules shipped in a signed
	// release that references a passed validation report (ADR-008).
	ValidatedForAlerting bool
}

// Verdict is one rule's reading of one track in one observation.
type Verdict struct {
	Trigger    bool
	EvidenceOK bool
	RoomZone   string
	Detail     string
}

// Rule evaluates a single event type. Rules are pure apart from the per-track
// memory they keep for themselves.
type Rule interface {
	EventType() string
	Family() string
	Version() string
	Config() RuleConfig
	Evaluate(cam Camera, obs Observation, t Track) Verdict
}

// AdultInRestrictedZone (D3): an adult's foot point is inside a restricted zone.
type AdultInRestrictedZone struct {
	Cfg      RuleConfig
	MinScore float64
}

func (r *AdultInRestrictedZone) EventType() string  { return "adult_in_restricted_zone" }
func (r *AdultInRestrictedZone) Family() string     { return "D" }
func (r *AdultInRestrictedZone) Version() string    { return "d3-v0.1" }
func (r *AdultInRestrictedZone) Config() RuleConfig { return r.Cfg }

func (r *AdultInRestrictedZone) Evaluate(cam Camera, _ Observation, t Track) Verdict {
	if t.Role != RoleAdult {
		return Verdict{EvidenceOK: true}
	}
	for _, z := range cam.Zones {
		if z.Kind == ZoneRestricted && z.Contains(t.FootPoint()) {
			return Verdict{Trigger: true, EvidenceOK: t.Score >= r.MinScore, RoomZone: z.RoomZone, Detail: "adult foot point inside " + z.ID}
		}
	}
	return Verdict{EvidenceOK: true}
}

// InfantOffSurface (A1): the infant was on a raised surface within Recent and
// is now below that surface's lower edge while not held. This is state-change
// logic, so it does not depend on capturing the fall motion itself.
type InfantOffSurface struct {
	Cfg           RuleConfig
	Recent        time.Duration
	MinScore      float64
	MinVisibility float64

	lastOnSurface map[string]surfaceSeen // camera/track -> last time on a surface
}

type surfaceSeen struct {
	at   time.Time
	zone Zone
}

func (r *InfantOffSurface) EventType() string  { return "infant_off_surface" }
func (r *InfantOffSurface) Family() string     { return "A" }
func (r *InfantOffSurface) Version() string    { return "a1-v0.1" }
func (r *InfantOffSurface) Config() RuleConfig { return r.Cfg }

func (r *InfantOffSurface) Evaluate(cam Camera, obs Observation, t Track) Verdict {
	if t.Role != RoleInfant {
		return Verdict{EvidenceOK: true}
	}
	if r.lastOnSurface == nil {
		r.lastOnSurface = map[string]surfaceSeen{}
	}
	key := cam.ID + "/" + t.ID
	fp := t.FootPoint()
	for _, z := range cam.Zones {
		if z.Kind == ZoneSurface && z.Contains(fp) {
			r.lastOnSurface[key] = surfaceSeen{at: obs.CaptureTS, zone: z}
			return Verdict{EvidenceOK: true}
		}
	}
	seen, ok := r.lastOnSurface[key]
	if !ok || obs.CaptureTS.Sub(seen.at) > r.Recent || t.Held || fp.Y <= seen.zone.Bottom() {
		return Verdict{EvidenceOK: true}
	}
	return Verdict{
		Trigger:    true,
		EvidenceOK: t.Score >= r.MinScore && t.Visibility >= r.MinVisibility,
		RoomZone:   seen.zone.RoomZone,
		Detail:     "infant below " + seen.zone.ID + " and not held",
	}
}
