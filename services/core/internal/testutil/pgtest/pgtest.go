// Package pgtest gives integration tests a private, migrated PostgreSQL
// database that is dropped afterwards. Tests skip when no server is reachable.
package pgtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

// DefaultURL is the local development server.
const DefaultURL = "postgres://zeoatap@127.0.0.1:15432/postgres?sslmode=disable"

// New creates a uniquely named database, migrates it and registers cleanup.
// It returns the store and the database URL.
func New(t testing.TB) (*store.DB, string) {
	t.Helper()
	admin := os.Getenv("ZEOATAP_TEST_DATABASE_URL")
	if admin == "" {
		admin = DefaultURL
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, admin)
	if err != nil {
		t.Skipf("PostgreSQL not reachable (set ZEOATAP_TEST_DATABASE_URL): %v", err)
	}
	defer conn.Close(context.Background())

	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	name := "zeoatap_test_" + hex.EncodeToString(b)
	if _, err := conn.Exec(ctx, "CREATE DATABASE "+name); err != nil {
		t.Fatalf("create test database: %v", err)
	}
	u, err := url.Parse(admin)
	if err != nil {
		t.Fatal(err)
	}
	u.Path = "/" + name
	dbURL := u.String()

	db, err := store.Open(ctx, dbURL, 30)
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	t.Cleanup(func() {
		db.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		c, err := pgx.Connect(ctx, admin)
		if err != nil {
			t.Logf("drop %s: %v", name, err)
			return
		}
		defer c.Close(ctx)
		if _, err := c.Exec(ctx, "DROP DATABASE IF EXISTS "+name+" WITH (FORCE)"); err != nil {
			t.Logf("drop %s: %v", name, err)
		}
	})
	if err := db.Migrate(ctx); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db, dbURL
}
