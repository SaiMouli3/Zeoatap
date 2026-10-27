package eventengine

import (
	"sort"
	"sync"
	"time"
)

// Store persists incidents. The Postgres implementation (infra/migrations)
// enforces idempotency with a unique index on idempotency_key.
type Store interface {
	InsertIfAbsent(inc *Incident) (stored *Incident, created bool, err error)
	Update(inc *Incident) error
	Get(id string) (*Incident, error)
	// FindMergeable returns an open incident in the same room zone and event
	// family whose window is within gap of start, or nil.
	FindMergeable(roomID, roomZone, family string, start time.Time, gap time.Duration) (*Incident, error)
	List() ([]*Incident, error)
}

// MemoryStore is an in-process Store for tests and offline replay.
type MemoryStore struct {
	mu    sync.Mutex
	byKey map[string]*Incident
	byID  map[string]*Incident
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{byKey: map[string]*Incident{}, byID: map[string]*Incident{}}
}

func (s *MemoryStore) InsertIfAbsent(inc *Incident) (*Incident, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if got, ok := s.byKey[inc.IdempotencyKey]; ok {
		return got, false, nil
	}
	s.byKey[inc.IdempotencyKey] = inc
	s.byID[inc.ID] = inc
	return inc, true, nil
}

func (s *MemoryStore) Update(inc *Incident) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.byID[inc.ID] = inc
	s.byKey[inc.IdempotencyKey] = inc
	return nil
}

func (s *MemoryStore) Get(id string) (*Incident, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.byID[id], nil
}

func (s *MemoryStore) FindMergeable(roomID, roomZone, family string, start time.Time, gap time.Duration) (*Incident, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, inc := range s.byID {
		if inc.RoomID == roomID && inc.RoomZone == roomZone && inc.Family == family &&
			!start.Before(inc.Start.Add(-gap)) && !start.After(inc.End.Add(gap)) {
			return inc, nil
		}
	}
	return nil, nil
}

func (s *MemoryStore) List() ([]*Incident, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]*Incident, 0, len(s.byID))
	for _, inc := range s.byID {
		out = append(out, inc)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out, nil
}
