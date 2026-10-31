// Package store is core's PostgreSQL persistence. It implements the event
// engine's Store interface and everything the API needs.
package store

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
)

//go:embed migrations/*.up.sql
var migrations embed.FS

// opTimeout bounds every database call made on behalf of the event engine.
const opTimeout = 5 * time.Second

type DB struct {
	Pool          *pgxpool.Pool
	RetentionDays int
}

func Open(ctx context.Context, url string, retentionDays int) (*DB, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("database unreachable: %w", err)
	}
	return &DB{Pool: pool, RetentionDays: retentionDays}, nil
}

func (d *DB) Close() { d.Pool.Close() }

// Ping checks the database with a short timeout, for health reporting.
func (d *DB) Ping(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return d.Pool.Ping(ctx)
}

// Migrate applies embedded migrations that have not run yet, each in its own
// transaction.
func (d *DB) Migrate(ctx context.Context) error {
	if _, err := d.Pool.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return err
	}
	names, err := fs.Glob(migrations, "migrations/*.up.sql")
	if err != nil {
		return err
	}
	sort.Strings(names)
	for _, name := range names {
		version := strings.TrimSuffix(strings.TrimPrefix(name, "migrations/"), ".up.sql")
		var done bool
		if err := d.Pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version=$1)`, version).Scan(&done); err != nil {
			return err
		}
		if done {
			continue
		}
		sql, err := migrations.ReadFile(name)
		if err != nil {
			return err
		}
		err = pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, string(sql)); err != nil {
				return fmt.Errorf("migration %s: %w", version, err)
			}
			_, err := tx.Exec(ctx, `INSERT INTO schema_migrations (version) VALUES ($1)`, version)
			return err
		})
		if err != nil {
			return err
		}
	}
	return nil
}

// ---- eventengine.Store -------------------------------------------------

const incidentCols = `id, idempotency_key, room_id, room_zone, event_type, family, start_ts, end_ts,
	cameras, status, rule_version, model_versions, source, detail`

func scanIncident(row pgx.Row) (*eventengine.Incident, error) {
	var inc eventengine.Incident
	var models []byte
	var status string
	err := row.Scan(&inc.ID, &inc.IdempotencyKey, &inc.RoomID, &inc.RoomZone, &inc.EventType, &inc.Family,
		&inc.Start, &inc.End, &inc.Cameras, &status, &inc.RuleVersion, &models, &inc.Source, &inc.Detail)
	if err != nil {
		return nil, err
	}
	inc.Status = eventengine.IncidentStatus(status)
	if err := json.Unmarshal(models, &inc.ModelVersions); err != nil {
		return nil, fmt.Errorf("incident %s model_versions: %w", inc.ID, err)
	}
	return &inc, nil
}

func (d *DB) InsertIfAbsent(inc *eventengine.Incident) (*eventengine.Incident, bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), opTimeout)
	defer cancel()
	models, err := json.Marshal(inc.ModelVersions)
	if err != nil {
		return nil, false, err
	}
	if inc.ModelVersions == nil {
		models = []byte("{}")
	}
	tag, err := d.Pool.Exec(ctx, `INSERT INTO incidents (id, idempotency_key, room_id, room_zone, event_type, family,
		start_ts, end_ts, cameras, status, rule_version, model_versions, source, detail, retention_deadline)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, now() + make_interval(days => $15))
		ON CONFLICT (idempotency_key) DO NOTHING`,
		inc.ID, inc.IdempotencyKey, inc.RoomID, inc.RoomZone, inc.EventType, inc.Family, inc.Start, inc.End,
		inc.Cameras, string(inc.Status), inc.RuleVersion, models, inc.Source, inc.Detail, d.RetentionDays)
	if err != nil {
		return nil, false, err
	}
	if tag.RowsAffected() == 1 {
		return inc, true, nil
	}
	got, err := scanIncident(d.Pool.QueryRow(ctx, `SELECT `+incidentCols+` FROM incidents WHERE idempotency_key=$1`, inc.IdempotencyKey))
	return got, false, err
}

func (d *DB) Update(inc *eventengine.Incident) error {
	ctx, cancel := context.WithTimeout(context.Background(), opTimeout)
	defer cancel()
	_, err := d.Pool.Exec(ctx, `UPDATE incidents SET end_ts=$2, cameras=$3, status=$4 WHERE id=$1`,
		inc.ID, inc.End, inc.Cameras, string(inc.Status))
	return err
}

func (d *DB) Get(id string) (*eventengine.Incident, error) {
	ctx, cancel := context.WithTimeout(context.Background(), opTimeout)
	defer cancel()
	inc, err := scanIncident(d.Pool.QueryRow(ctx, `SELECT `+incidentCols+` FROM incidents WHERE id=$1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return inc, err
}

func (d *DB) FindMergeable(roomID, roomZone, family string, start time.Time, gap time.Duration) (*eventengine.Incident, error) {
	ctx, cancel := context.WithTimeout(context.Background(), opTimeout)
	defer cancel()
	inc, err := scanIncident(d.Pool.QueryRow(ctx, `SELECT `+incidentCols+` FROM incidents
		WHERE room_id=$1 AND room_zone=$2 AND family=$3 AND lifecycle <> 'resolved'
		  AND $4 >= start_ts - make_interval(secs => $5) AND $4 <= end_ts + make_interval(secs => $5)
		ORDER BY start_ts DESC LIMIT 1`, roomID, roomZone, family, start, gap.Seconds()))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return inc, err
}

func (d *DB) List() ([]*eventengine.Incident, error) {
	ctx, cancel := context.WithTimeout(context.Background(), opTimeout)
	defer cancel()
	rows, err := d.Pool.Query(ctx, `SELECT `+incidentCols+` FROM incidents ORDER BY start_ts`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*eventengine.Incident
	for rows.Next() {
		inc, err := scanIncident(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, inc)
	}
	return out, rows.Err()
}

// ---- audit ---------------------------------------------------------------

// Audit appends a hash-chained entry. Each row's hash covers the previous
// row's hash, so any edit or deletion breaks the chain (VerifyAudit).
func (d *DB) Audit(ctx context.Context, actor, action, subject string, payload any) error {
	body, err := canonicalJSON(payload)
	if err != nil {
		return err
	}
	return pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
		// Serialize writers so two entries never share a prev_hash.
		if _, err := tx.Exec(ctx, `LOCK TABLE audit_log IN EXCLUSIVE MODE`); err != nil {
			return err
		}
		prev := "genesis"
		err := tx.QueryRow(ctx, `SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1`).Scan(&prev)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		at := time.Now().UTC().Truncate(time.Microsecond)
		h := auditHash(prev, at, actor, action, subject, body)
		_, err = tx.Exec(ctx, `INSERT INTO audit_log (at, actor, action, subject, payload, prev_hash, hash)
			VALUES ($1,$2,$3,$4,$5,$6,$7)`, at, actor, action, subject, body, prev, h)
		return err
	})
}

// canonicalJSON re-encodes v with sorted object keys, matching what survives
// a round trip through jsonb.
func canonicalJSON(v any) ([]byte, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var generic any
	if err := json.Unmarshal(raw, &generic); err != nil {
		return nil, err
	}
	return json.Marshal(generic)
}

func auditHash(prev string, at time.Time, actor, action, subject string, payload []byte) string {
	sum := sha256.Sum256([]byte(strings.Join([]string{prev, at.Format(time.RFC3339Nano), actor, action, subject, string(payload)}, "\x1f")))
	return hex.EncodeToString(sum[:])
}

// VerifyAudit walks the chain and returns the first broken sequence number, or 0.
func (d *DB) VerifyAudit(ctx context.Context) (int64, error) {
	rows, err := d.Pool.Query(ctx, `SELECT seq, at, actor, action, subject, payload::text, prev_hash, hash FROM audit_log ORDER BY seq`)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	prev := "genesis"
	for rows.Next() {
		var seq int64
		var at time.Time
		var actor, action, subject, payload, prevHash, hash string
		if err := rows.Scan(&seq, &at, &actor, &action, &subject, &payload, &prevHash, &hash); err != nil {
			return 0, err
		}
		// jsonb normalizes formatting, so re-hash from the canonical JSON.
		canon, err := canonicalJSON(json.RawMessage(payload))
		if err != nil {
			return seq, nil
		}
		if prevHash != prev || auditHash(prev, at.UTC(), actor, action, subject, canon) != hash {
			return seq, nil
		}
		prev = hash
	}
	return 0, rows.Err()
}
