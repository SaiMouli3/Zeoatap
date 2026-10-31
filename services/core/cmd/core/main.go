// Command core is the edge appliance's core service: event engine, incident
// store, evidence vault, alerting and the staff API.
package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/saimouli3/zeoatap/services/core/internal/alerts"
	"github.com/saimouli3/zeoatap/services/core/internal/auth"
	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
	"github.com/saimouli3/zeoatap/services/core/internal/evidence"
	"github.com/saimouli3/zeoatap/services/core/internal/hub"
	"github.com/saimouli3/zeoatap/services/core/internal/server"
	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

// SiteConfig is the facility layout bootstrapped into the database at start.
type SiteConfig struct {
	Facility store.Facility                      `json:"facility"`
	Rooms    []store.Room                        `json:"rooms"`
	Cameras  []siteCamera                        `json:"cameras"`
	Rules    map[string]eventengine.RuleOverride `json:"rules"`
}

type siteCamera struct {
	store.Camera
	Enabled *bool `json:"enabled"`
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func envDur(key string, def time.Duration) time.Duration {
	if v, err := time.ParseDuration(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(log)
	if err := run(log); err != nil {
		log.Error("core stopped", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	var (
		dbURL        = flag.String("db", env("DATABASE_URL", ""), "PostgreSQL URL (DATABASE_URL)")
		addr         = flag.String("addr", env("ZEOATAP_ADDR", ":8080"), "staff API listen address (ZEOATAP_ADDR)")
		internalAddr = flag.String("internal-addr", env("ZEOATAP_INTERNAL_ADDR", "127.0.0.1:8090"), "vision-worker listener, keep on localhost (ZEOATAP_INTERNAL_ADDR)")
		keyFile      = flag.String("master-key-file", env("ZEOATAP_MASTER_KEY_FILE", ""), "32-byte evidence master key, hex or raw (ZEOATAP_MASTER_KEY_FILE)")
		evidenceDir  = flag.String("evidence-dir", env("ZEOATAP_EVIDENCE_DIR", "/var/lib/zeoatap/evidence"), "encrypted clip directory (ZEOATAP_EVIDENCE_DIR)")
		spoolDir     = flag.String("spool-dir", env("ZEOATAP_CLIP_SPOOL_DIR", "/var/lib/zeoatap/spool"), "plaintext clip spool shared with vision-worker (ZEOATAP_CLIP_SPOOL_DIR)")
		visionURL    = flag.String("vision-url", env("ZEOATAP_VISION_URL", "http://127.0.0.1:8081"), "vision-worker control API; empty disables clip capture (ZEOATAP_VISION_URL)")
		retention    = flag.Int("retention-days", envInt("ZEOATAP_RETENTION_DAYS", 30), "incident clip retention in days (ZEOATAP_RETENTION_DAYS)")
		webhook      = flag.String("webhook-url", env("ZEOATAP_WEBHOOK_URL", ""), "optional alert webhook (ZEOATAP_WEBHOOK_URL)")
		ackTimeout   = flag.Duration("ack-timeout", envDur("ZEOATAP_ACK_TIMEOUT", 60*time.Second), "escalate alerts not acknowledged within this (ZEOATAP_ACK_TIMEOUT)")
		maxLevel     = flag.Int("max-escalation", envInt("ZEOATAP_MAX_ESCALATION", 2), "maximum escalation level (ZEOATAP_MAX_ESCALATION)")
		sitePath     = flag.String("site", env("ZEOATAP_SITE_CONFIG", ""), "site config JSON: facility, rooms, cameras, rule overrides (ZEOATAP_SITE_CONFIG)")
		webDir       = flag.String("web", env("ZEOATAP_WEB_DIR", ""), "dashboard static export to serve at / (ZEOATAP_WEB_DIR)")
		insecure     = flag.Bool("insecure-cookie", env("ZEOATAP_INSECURE_COOKIE", "") == "1", "drop the Secure cookie flag, for plain-HTTP development only (ZEOATAP_INSECURE_COOKIE=1)")
	)
	flag.Parse()

	token := os.Getenv("ZEOATAP_INTERNAL_TOKEN")
	switch {
	case *dbURL == "":
		return errors.New("DATABASE_URL (or -db) is required")
	case len(token) < 16:
		return errors.New("ZEOATAP_INTERNAL_TOKEN is required (at least 16 characters)")
	case *keyFile == "":
		return errors.New("ZEOATAP_MASTER_KEY_FILE (or -master-key-file) is required: evidence is never stored unencrypted")
	case *retention < 1:
		return errors.New("retention-days must be at least 1")
	}
	masterKey, err := readMasterKey(*keyFile, log)
	if err != nil {
		return err
	}

	var site SiteConfig
	if *sitePath != "" {
		if site, err = loadSite(*sitePath); err != nil {
			return err
		}
	}
	if err := checkRuleOverrides(site.Rules, os.Getenv("ZEOATAP_TEST_ALLOW_UNVALIDATED_ALERTS") == "1", log); err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	startCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	db, err := store.Open(startCtx, *dbURL, *retention)
	if err != nil {
		return err
	}
	defer db.Close()
	if err := db.Migrate(startCtx); err != nil {
		return fmt.Errorf("migrate: %w", err)
	}
	if *sitePath != "" {
		if err := bootstrapSite(startCtx, db, site); err != nil {
			return fmt.Errorf("site config: %w", err)
		}
	}
	if err := bootstrapAdmin(startCtx, db, os.Getenv("ZEOATAP_BOOTSTRAP_ADMIN"), log); err != nil {
		return err
	}

	vault, err := evidence.NewVault(*evidenceDir, masterKey)
	if err != nil {
		return fmt.Errorf("evidence vault: %w", err)
	}
	if err := vault.CheckWritable(); err != nil {
		return fmt.Errorf("evidence dir not writable: %w", err)
	}
	if err := os.MkdirAll(*spoolDir, 0o750); err != nil {
		return fmt.Errorf("clip spool dir: %w", err)
	}

	cams, err := db.Cameras(startCtx)
	if err != nil {
		return err
	}
	var engineCams []eventengine.Camera
	for _, c := range cams {
		if c.Enabled {
			engineCams = append(engineCams, c.EngineCamera())
		}
	}
	cancel()

	h := hub.New()
	authSvc := &auth.Service{DB: db, SecureCookie: !*insecure}
	disp := &alerts.Dispatcher{DB: db, Hub: h, Log: log, Client: &http.Client{Timeout: 10 * time.Second},
		Cfg: alerts.Config{WebhookURL: *webhook, AckTimeout: *ackTimeout, MaxLevel: *maxLevel}}
	srv := server.New(server.Config{
		InternalToken: token, VisionURL: *visionURL, ClipSpoolDir: *spoolDir, WebDir: *webDir,
	}, db, h, authSvc, vault, disp, log, engineCams, eventengine.DefaultRules(site.Rules))

	public := &http.Server{Addr: *addr, Handler: srv.Handler(), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 30 * time.Second, WriteTimeout: 60 * time.Second, IdleTimeout: 120 * time.Second}
	internal := &http.Server{Addr: *internalAddr, Handler: srv.InternalHandler(), ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout: 120 * time.Second} // no read timeout: observations are one long stream
	if host, _, err := net.SplitHostPort(*internalAddr); err == nil {
		if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
			log.Warn("internal listener is not bound to loopback; it must not be reachable from the LAN", "addr", *internalAddr)
		}
	}

	engineDone := make(chan struct{})
	go func() { defer close(engineDone); srv.Run(ctx) }()

	errCh := make(chan error, 2)
	for _, s := range []*http.Server{public, internal} {
		go func(s *http.Server) {
			if err := s.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				errCh <- fmt.Errorf("listen %s: %w", s.Addr, err)
			}
		}(s)
	}
	log.Info("core started", "addr", *addr, "internal_addr", *internalAddr, "cameras", len(engineCams),
		"retention_days", *retention, "webhook", *webhook != "", "web", *webDir != "")

	select {
	case <-ctx.Done():
		log.Info("shutting down")
	case err = <-errCh:
		stop()
	}
	shutCtx, shutCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutCancel()
	_ = internal.Shutdown(shutCtx)
	_ = public.Shutdown(shutCtx)
	<-engineDone
	return err
}

// readMasterKey accepts exactly 32 raw bytes or 64 hex characters.
func readMasterKey(path string, log *slog.Logger) ([]byte, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("master key: %w", err)
	}
	if st.Mode().Perm()&0o077 != 0 {
		log.Warn("master key file is readable by group or others; restrict it to the service user (chmod 600)", "path", path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("master key: %w", err)
	}
	if len(raw) == 32 {
		return raw, nil
	}
	if k, err := hex.DecodeString(strings.TrimSpace(string(raw))); err == nil && len(k) == 32 {
		return k, nil
	}
	return nil, errors.New("master key file must hold 32 raw bytes or 64 hex characters")
}

func loadSite(path string) (SiteConfig, error) {
	var site SiteConfig
	raw, err := os.ReadFile(path)
	if err != nil {
		return site, err
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&site); err != nil {
		return site, fmt.Errorf("site config %s: %w", path, err)
	}
	if site.Facility.ID == "" || site.Facility.Name == "" {
		return site, errors.New("site config: facility id and name are required")
	}
	rooms := map[string]bool{}
	for _, r := range site.Rooms {
		if r.ID == "" || r.Name == "" {
			return site, errors.New("site config: every room needs id and name")
		}
		rooms[r.ID] = true
	}
	for i, c := range site.Cameras {
		c.Camera.Enabled = c.Enabled == nil || *c.Enabled
		site.Cameras[i] = c
		if err := server.ValidateCamera(c.Camera); err != nil {
			return site, fmt.Errorf("site config: camera %q: %w", c.ID, err)
		}
		if !rooms[c.RoomID] {
			return site, fmt.Errorf("site config: camera %q: unknown room %q", c.ID, c.RoomID)
		}
	}
	for name, o := range site.Rules {
		if name != "adult_in_restricted_zone" && name != "infant_off_surface" {
			return site, fmt.Errorf("site config: unknown rule %q", name)
		}
		if o.Policy != "" && o.Policy != eventengine.PolicyAlert && o.Policy != eventengine.PolicyReview {
			return site, fmt.Errorf("site config: rule %q: policy must be alert or review", name)
		}
	}
	return site, nil
}

// checkRuleOverrides refuses validated_for_alerting unless explicitly allowed
// for testing: there is no signed-release mechanism yet (ADR-008), so no
// rule can carry real validation evidence.
func checkRuleOverrides(rules map[string]eventengine.RuleOverride, allowUnvalidated bool, log *slog.Logger) error {
	for name, o := range rules {
		if !o.ValidatedForAlerting {
			continue
		}
		if !allowUnvalidated {
			return fmt.Errorf("rule %q sets validated_for_alerting, which requires a signed release with a validation report (ADR-008); refusing to start", name)
		}
		log.Warn("!!! UNVALIDATED RULE IS ALERTING STAFF — TEST USE ONLY, NOT FOR A REAL FACILITY !!!",
			"rule", name, "reason", "ZEOATAP_TEST_ALLOW_UNVALIDATED_ALERTS=1")
	}
	return nil
}

func bootstrapSite(ctx context.Context, db *store.DB, site SiteConfig) error {
	if err := db.UpsertFacility(ctx, site.Facility.ID, site.Facility.Name); err != nil {
		return err
	}
	for _, r := range site.Rooms {
		if err := db.UpsertRoom(ctx, site.Facility.ID, r); err != nil {
			return fmt.Errorf("room %s: %w", r.ID, err)
		}
	}
	for _, c := range site.Cameras {
		if err := db.UpsertCamera(ctx, c.Camera); err != nil {
			return fmt.Errorf("camera %s: %w", c.ID, err)
		}
	}
	return db.Audit(ctx, "system", "site_config_loaded", site.Facility.ID, map[string]any{
		"rooms": len(site.Rooms), "cameras": len(site.Cameras), "rules": site.Rules})
}

// bootstrapAdmin creates the first admin from "user:password", only while
// the users table is empty.
func bootstrapAdmin(ctx context.Context, db *store.DB, spec string, log *slog.Logger) error {
	n, err := db.CountUsers(ctx)
	if err != nil {
		return err
	}
	if n > 0 {
		if spec != "" {
			log.Info("ZEOATAP_BOOTSTRAP_ADMIN ignored: users already exist; remove it from the environment")
		}
		return nil
	}
	if spec == "" {
		log.Warn("no users exist; set ZEOATAP_BOOTSTRAP_ADMIN=user:password to create the first admin")
		return nil
	}
	name, pw, ok := strings.Cut(spec, ":")
	if !ok || name == "" {
		return errors.New("ZEOATAP_BOOTSTRAP_ADMIN must be user:password")
	}
	hash, err := auth.HashPassword(pw)
	if err != nil {
		return fmt.Errorf("bootstrap admin: %w", err)
	}
	if _, err := db.CreateUser(ctx, store.User{Username: name, DisplayName: name, Role: "admin", PasswordHash: hash}); err != nil {
		return fmt.Errorf("bootstrap admin: %w", err)
	}
	log.Info("bootstrap admin created", "username", name)
	return db.Audit(ctx, "system", "user_created", name, map[string]any{"role": "admin", "bootstrap": true})
}
