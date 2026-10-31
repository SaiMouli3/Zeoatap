package eventengine

import "time"

// RuleOverride lets a site tune a rule. ValidatedForAlerting may only be set
// by callers that have checked a validation report (ADR-008).
type RuleOverride struct {
	Policy               Policy `json:"policy,omitempty"`
	ValidatedForAlerting bool   `json:"validated_for_alerting,omitempty"`
}

// DefaultRules are the pilot starting points from AI_MODEL_STRATEGY §7.4.
// None is validated, so alert-policy rules produce SHADOW incidents only.
func DefaultRules(overrides map[string]RuleOverride) []Rule {
	d3 := RuleConfig{MinDuration: 2 * time.Second, MergeGap: 30 * time.Second, Bucket: time.Minute, Policy: PolicyReview}
	a1 := RuleConfig{MinDuration: 500 * time.Millisecond, MergeGap: 10 * time.Second, Bucket: time.Minute, Policy: PolicyAlert}
	apply := func(name string, c *RuleConfig) {
		if o, ok := overrides[name]; ok {
			if o.Policy != "" {
				c.Policy = o.Policy
			}
			c.ValidatedForAlerting = o.ValidatedForAlerting
		}
	}
	apply("adult_in_restricted_zone", &d3)
	apply("infant_off_surface", &a1)
	return []Rule{
		&AdultInRestrictedZone{MinScore: 0.5, Cfg: d3},
		&InfantOffSurface{MinScore: 0.4, MinVisibility: 0.5, Recent: 2 * time.Second, Cfg: a1},
	}
}

// SetCamera registers or replaces a camera. Like Process, it must be called
// from the goroutine that owns the engine.
func (e *Engine) SetCamera(c Camera) { e.cameras[c.ID] = c }
