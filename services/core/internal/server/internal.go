package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
)

// maxObservationLine bounds one NDJSON line.
const maxObservationLine = 1 << 20

// InternalHandler serves the localhost-only API vision-worker talks to.
func (s *Server) InternalHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/v1/observations", s.ingest)
	mux.HandleFunc("GET /internal/v1/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !bearerOK(r, s.Cfg.InternalToken) {
			writeErr(w, http.StatusUnauthorized, "missing or wrong bearer token")
			return
		}
		mux.ServeHTTP(w, r)
	})
}

// ingest reads a stream of NDJSON observations and queues each for the
// engine, in order. The first bad line stops the stream with 400; a full
// engine queue stops it with 503, so the worker backs off instead of core
// silently dropping frames.
func (s *Server) ingest(w http.ResponseWriter, r *http.Request) {
	sc := bufio.NewScanner(r.Body)
	sc.Buffer(make([]byte, 64<<10), maxObservationLine)
	accepted, ignored := 0, 0
	unknown := map[string]bool{}
	timer := time.NewTimer(time.Hour)
	timer.Stop()
	defer timer.Stop()
	defer func() {
		if len(unknown) > 0 {
			s.Log.Warn("observations for unregistered cameras ignored", "count", ignored, "cameras", keys(unknown))
		}
	}()
	reply := func(status int, msg string) {
		body := map[string]any{"accepted": accepted, "ignored": ignored}
		if msg != "" {
			body["error"] = msg
		}
		writeJSON(w, status, body)
	}
	for line := 1; sc.Scan(); line++ {
		raw := bytes.TrimSpace(sc.Bytes())
		if len(raw) == 0 {
			continue
		}
		var o eventengine.Observation
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields() // the schema forbids additional properties
		if err := dec.Decode(&o); err != nil {
			reply(http.StatusBadRequest, fmt.Sprintf("line %d: %v", line, err))
			return
		}
		if dec.More() {
			reply(http.StatusBadRequest, fmt.Sprintf("line %d: trailing data after the observation", line))
			return
		}
		if err := o.Validate(); err != nil {
			reply(http.StatusBadRequest, fmt.Sprintf("line %d: %v", line, err))
			return
		}
		if !s.knownCamera(o.CameraID) {
			ignored++
			unknown[o.CameraID] = true
			continue
		}
		select {
		case s.obsCh <- o:
		default:
			timer.Reset(s.Cfg.IngestTimeout)
			select {
			case s.obsCh <- o:
				timer.Stop() // Go 1.23+ timers need no drain
			case <-timer.C:
				reply(http.StatusServiceUnavailable, "engine backlog full")
				return
			case <-r.Context().Done():
				return
			}
		}
		accepted++
	}
	if err := sc.Err(); err != nil {
		if errors.Is(err, bufio.ErrTooLong) {
			reply(http.StatusBadRequest, "observation line too long")
			return
		}
		reply(http.StatusBadRequest, "reading body: "+err.Error())
		return
	}
	reply(http.StatusOK, "")
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
