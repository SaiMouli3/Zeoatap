package eventengine

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"time"
)

// IncidentStatus is the initial routing decision for an incident.
type IncidentStatus string

const (
	StatusAlerted      IncidentStatus = "ALERTED"       // staff are notified
	StatusReview       IncidentStatus = "REVIEW_QUEUED" // low-priority review
	StatusShadow       IncidentStatus = "SHADOW"        // alert policy, not yet validated: hidden from staff
	StatusInconclusive IncidentStatus = "INCONCLUSIVE"  // evidence lost before confirmation
)

// Incident is a possible event requiring human review. It is never a finding.
type Incident struct {
	ID             string            `json:"id"`
	IdempotencyKey string            `json:"idempotency_key"`
	RoomID         string            `json:"room_id"`
	RoomZone       string            `json:"room_zone"`
	EventType      string            `json:"event_type"`
	Family         string            `json:"family"`
	Start          time.Time         `json:"start"`
	End            time.Time         `json:"end"`
	Cameras        []string          `json:"cameras"`
	Status         IncidentStatus    `json:"status"`
	RuleVersion    string            `json:"rule_version"`
	ModelVersions  map[string]string `json:"model_versions"`
	Source         string            `json:"source"`
	Detail         string            `json:"detail"`
}

func (i *Incident) addCamera(id string) {
	for _, c := range i.Cameras {
		if c == id {
			return
		}
	}
	i.Cameras = append(i.Cameras, id)
	sort.Strings(i.Cameras)
}

// Output is everything the engine reports to the rest of core.
type Output struct {
	Kind     string          `json:"kind"` // state_change | incident_created | incident_merged
	CameraID string          `json:"camera_id,omitempty"`
	State    MonitoringState `json:"state,omitempty"`
	Incident *Incident       `json:"incident,omitempty"`
}

type candKey struct{ camera, track, event string }

type candidate struct {
	rule       Rule
	start      time.Time
	lastSeen   time.Time
	roomZone   string
	detail     string
	source     string
	models     map[string]string
	incidentID string // set once confirmed
}

// Engine is not safe for concurrent use; core feeds it from one goroutine per
// facility so observations stay ordered.
type Engine struct {
	cameras        map[string]Camera
	rules          []Rule
	store          Store
	staleAfter     time.Duration
	trackLostAfter time.Duration

	health   map[string]*cameraHealth
	reported map[string]MonitoringState
	cands    map[candKey]*candidate
}

func New(cameras []Camera, rules []Rule, store Store) *Engine {
	cm := make(map[string]Camera, len(cameras))
	for _, c := range cameras {
		cm[c.ID] = c
	}
	return &Engine{
		cameras:        cm,
		rules:          rules,
		store:          store,
		staleAfter:     defaultStaleAfter,
		trackLostAfter: time.Second,
		health:         map[string]*cameraHealth{},
		reported:       map[string]MonitoringState{},
		cands:          map[candKey]*candidate{},
	}
}

// State returns the camera's monitoring state at now.
func (e *Engine) State(cameraID string, now time.Time) MonitoringState {
	h, ok := e.health[cameraID]
	if !ok {
		return StateNeverConnected
	}
	return h.stateAt(now, e.staleAfter)
}

// Tick re-evaluates health without a new observation, so a camera that stops
// sending is reported unavailable rather than staying at its last state.
func (e *Engine) Tick(now time.Time) ([]Output, error) {
	var out []Output
	ids := make([]string, 0, len(e.cameras))
	for id := range e.cameras {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		st := e.State(id, now)
		out = append(out, e.reportState(id, st)...)
		if !st.Analyzable() {
			o, err := e.closeCandidates(id, now, func(*candidate) bool { return true })
			if err != nil {
				return out, err
			}
			out = append(out, o...)
		}
	}
	return out, nil
}

// Process consumes one observation. Observations for a camera must arrive in
// capture order.
func (e *Engine) Process(obs Observation) ([]Output, error) {
	if err := obs.Validate(); err != nil {
		return nil, err
	}
	cam, ok := e.cameras[obs.CameraID]
	if !ok {
		return nil, fmt.Errorf("observation for unregistered camera %q", obs.CameraID)
	}
	h := e.health[cam.ID]
	if h == nil {
		h = &cameraHealth{}
		e.health[cam.ID] = h
	}
	h.lastObs, h.lastQuality, h.aiAvailable = obs.CaptureTS, obs.Quality, obs.AIAvailable

	st := h.stateAt(obs.CaptureTS, e.staleAfter)
	out := e.reportState(cam.ID, st)
	if !st.Analyzable() {
		o, err := e.closeCandidates(cam.ID, obs.CaptureTS, func(*candidate) bool { return true })
		return append(out, o...), err
	}

	for _, t := range obs.Tracks {
		for _, r := range e.rules {
			o, err := e.step(cam, obs, t, r)
			if err != nil {
				return out, err
			}
			out = append(out, o...)
		}
	}

	o, err := e.closeCandidates(cam.ID, obs.CaptureTS, func(c *candidate) bool {
		return obs.CaptureTS.Sub(c.lastSeen) > e.trackLostAfter
	})
	return append(out, o...), err
}

func (e *Engine) step(cam Camera, obs Observation, t Track, r Rule) ([]Output, error) {
	key := candKey{cam.ID, t.ID, r.EventType()}
	v := r.Evaluate(cam, obs, t)
	c := e.cands[key]
	ts := obs.CaptureTS

	switch {
	case c == nil:
		if v.Trigger && v.EvidenceOK {
			e.cands[key] = &candidate{rule: r, start: ts, lastSeen: ts, roomZone: v.RoomZone, detail: v.Detail, source: obs.Source, models: obs.ModelVersions}
		}
		return nil, nil

	case c.incidentID != "":
		if !v.Trigger {
			delete(e.cands, key)
			return nil, nil
		}
		c.lastSeen = ts
		return nil, e.extend(c.incidentID, ts)

	case !v.EvidenceOK:
		delete(e.cands, key)
		return e.finalize(cam, ts, c, StatusInconclusive)

	case !v.Trigger:
		delete(e.cands, key) // did not persist: discarded, kept only in FP logs
		return nil, nil

	default:
		c.lastSeen = ts
		if ts.Sub(c.start) < r.Config().MinDuration {
			return nil, nil
		}
		return e.finalize(cam, ts, c, initialStatus(r.Config()))
	}
}

var statusRank = map[IncidentStatus]int{StatusInconclusive: 0, StatusReview: 1, StatusShadow: 2, StatusAlerted: 3}

func initialStatus(cfg RuleConfig) IncidentStatus {
	switch {
	case cfg.Policy == PolicyAlert && cfg.ValidatedForAlerting:
		return StatusAlerted
	case cfg.Policy == PolicyAlert:
		return StatusShadow
	default:
		return StatusReview
	}
}

// finalize creates an incident for the candidate, or merges it into an open
// incident for the same room zone and event family.
func (e *Engine) finalize(cam Camera, ts time.Time, c *candidate, status IncidentStatus) ([]Output, error) {
	cfg := c.rule.Config()
	if existing, err := e.store.FindMergeable(cam.RoomID, c.roomZone, c.rule.Family(), c.start, cfg.MergeGap); err != nil {
		return nil, err
	} else if existing != nil {
		existing.addCamera(cam.ID)
		if ts.After(existing.End) {
			existing.End = ts
		}
		if statusRank[status] > statusRank[existing.Status] {
			existing.Status = status // confirmed evidence outranks an earlier inconclusive window
		}
		if err := e.store.Update(existing); err != nil {
			return nil, err
		}
		c.incidentID = existing.ID
		return []Output{{Kind: "incident_merged", CameraID: cam.ID, Incident: existing}}, nil
	}

	bucket := cfg.Bucket
	if bucket <= 0 {
		bucket = time.Minute
	}
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s|%s|%s|%s|%d",
		cam.RoomID, c.roomZone, c.rule.EventType(), status, c.start.Truncate(bucket).Unix())))
	idem := hex.EncodeToString(sum[:])
	inc := &Incident{
		ID:             "INC-" + idem[:12],
		IdempotencyKey: idem,
		RoomID:         cam.RoomID,
		RoomZone:       c.roomZone,
		EventType:      c.rule.EventType(),
		Family:         c.rule.Family(),
		Start:          c.start,
		End:            ts,
		Cameras:        []string{cam.ID},
		Status:         status,
		RuleVersion:    c.rule.Version(),
		ModelVersions:  c.models,
		Source:         c.source,
		Detail:         c.detail,
	}
	stored, created, err := e.store.InsertIfAbsent(inc)
	if err != nil {
		return nil, err
	}
	c.incidentID = stored.ID
	if !created {
		return nil, nil
	}
	return []Output{{Kind: "incident_created", CameraID: cam.ID, Incident: stored}}, nil
}

func (e *Engine) extend(id string, ts time.Time) error {
	inc, err := e.store.Get(id)
	if err != nil || inc == nil || !ts.After(inc.End) {
		return err
	}
	inc.End = ts
	return e.store.Update(inc)
}

// closeCandidates ends open candidates on a camera. Unconfirmed candidates
// whose evidence disappeared become INCONCLUSIVE review items: losing sight of
// a subject is never treated as "nothing happened".
func (e *Engine) closeCandidates(cameraID string, now time.Time, match func(*candidate) bool) ([]Output, error) {
	keys := make([]candKey, 0)
	for k, c := range e.cands {
		if k.camera == cameraID && match(c) {
			keys = append(keys, k)
		}
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].track+keys[i].event < keys[j].track+keys[j].event })
	var out []Output
	for _, k := range keys {
		c := e.cands[k]
		delete(e.cands, k)
		if c.incidentID != "" {
			continue
		}
		o, err := e.finalize(e.cameras[cameraID], now, c, StatusInconclusive)
		if err != nil {
			return out, err
		}
		out = append(out, o...)
	}
	return out, nil
}

func (e *Engine) reportState(cameraID string, st MonitoringState) []Output {
	if e.reported[cameraID] == st {
		return nil
	}
	e.reported[cameraID] = st
	return []Output{{Kind: "state_change", CameraID: cameraID, State: st}}
}
