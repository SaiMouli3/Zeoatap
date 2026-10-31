// Package hub fans live events out to dashboard clients (server-sent events).
package hub

import (
	"encoding/json"
	"fmt"
	"sync"
)

// subBuffer bounds each client's queue.
const subBuffer = 64

// Sub is one connected client. C carries pre-formatted SSE frames and is
// closed when the hub drops the client.
type Sub struct {
	C          chan []byte
	viewShadow bool
}

type Hub struct {
	mu   sync.Mutex
	subs map[*Sub]struct{}
}

func New() *Hub { return &Hub{subs: map[*Sub]struct{}{}} }

// Subscribe registers a client. viewShadow says whether it may receive
// events about SHADOW incidents.
func (h *Hub) Subscribe(viewShadow bool) *Sub {
	s := &Sub{C: make(chan []byte, subBuffer), viewShadow: viewShadow}
	h.mu.Lock()
	h.subs[s] = struct{}{}
	h.mu.Unlock()
	return s
}

func (h *Hub) Unsubscribe(s *Sub) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.subs[s]; ok {
		delete(h.subs, s)
		close(s.C)
	}
}

// Publish sends an event to every client.
func (h *Hub) Publish(typ string, data any) { h.publish(typ, data, false) }

// PublishShadow sends an event only to clients allowed to see SHADOW
// incidents.
func (h *Hub) PublishShadow(typ string, data any) { h.publish(typ, data, true) }

// publish never blocks the engine. A client that falls behind is
// disconnected instead of silently missing events: the dashboard re-fetches
// state on reconnect, so it never shows a stale picture as current.
func (h *Hub) publish(typ string, data any, shadowOnly bool) {
	body, err := json.Marshal(data)
	if err != nil {
		return
	}
	msg := []byte(fmt.Sprintf("event: %s\ndata: %s\n\n", typ, body))
	h.mu.Lock()
	defer h.mu.Unlock()
	for s := range h.subs {
		if shadowOnly && !s.viewShadow {
			continue
		}
		select {
		case s.C <- msg:
		default:
			delete(h.subs, s)
			close(s.C)
		}
	}
}

func (h *Hub) Subscribers() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.subs)
}
