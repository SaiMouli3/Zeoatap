// Command replay feeds a JSONL observation file through the event engine and
// prints monitoring-state changes and incidents as JSON lines. It is the
// offline-replay harness from SAFETY_VALIDATION Phase 1.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
)

type config struct {
	Cameras []eventengine.Camera `json:"cameras"`
}

func main() {
	cfgPath := flag.String("config", "", "camera/zone config JSON")
	obsPath := flag.String("observations", "", "observations JSONL")
	flag.Parse()
	if *cfgPath == "" || *obsPath == "" {
		fmt.Fprintln(os.Stderr, "usage: replay -config cameras.json -observations obs.jsonl")
		os.Exit(2)
	}
	if err := run(*cfgPath, *obsPath); err != nil {
		fmt.Fprintln(os.Stderr, "replay:", err)
		os.Exit(1)
	}
}

func run(cfgPath, obsPath string) error {
	raw, err := os.ReadFile(cfgPath)
	if err != nil {
		return err
	}
	var cfg config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return fmt.Errorf("config: %w", err)
	}

	rules := eventengine.DefaultRules(nil)
	store := eventengine.NewMemoryStore()
	eng := eventengine.New(cfg.Cameras, rules, store)

	f, err := os.Open(obsPath)
	if err != nil {
		return err
	}
	defer f.Close()

	enc := json.NewEncoder(os.Stdout)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	var last time.Time
	mock := false
	for line := 1; sc.Scan(); line++ {
		var o eventengine.Observation
		if err := json.Unmarshal(sc.Bytes(), &o); err != nil {
			return fmt.Errorf("line %d: %w", line, err)
		}
		mock = mock || o.Source == eventengine.SourceMock
		// Advance the clock to this observation first, so a gap in a camera's
		// stream is reported as unavailable before it resumes.
		ticked, err := eng.Tick(o.CaptureTS)
		if err != nil {
			return err
		}
		for _, ev := range ticked {
			if err := enc.Encode(ev); err != nil {
				return err
			}
		}
		out, err := eng.Process(o)
		if err != nil {
			return fmt.Errorf("line %d: %w", line, err)
		}
		for _, ev := range out {
			if err := enc.Encode(ev); err != nil {
				return err
			}
		}
		last = o.CaptureTS
	}
	if err := sc.Err(); err != nil {
		return err
	}
	// Advance the clock past the end of input so silent cameras are reported.
	out, err := eng.Tick(last.Add(5 * time.Second))
	if err != nil {
		return err
	}
	for _, ev := range out {
		if err := enc.Encode(ev); err != nil {
			return err
		}
	}
	if mock {
		fmt.Fprintln(os.Stderr, "WARNING: input contains MOCK detector observations; incidents are not real detections.")
	}
	return nil
}
