package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"github.com/saimouli3/zeoatap/services/core/internal/eventengine"
)

var ErrNotFound = errors.New("not found")
var ErrConflict = errors.New("conflict")

// ---- site ------------------------------------------------------------------

type Room struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type Camera struct {
	ID                 string             `json:"id"`
	RoomID             string             `json:"room_id"`
	Name               string             `json:"name"`
	Protected          bool               `json:"protected"`
	Zones              []eventengine.Zone `json:"zones"`
	AuthorizedBy       string             `json:"authorized_by"`
	AuthorizedAt       string             `json:"authorized_at"` // YYYY-MM-DD
	AuthorizationScope string             `json:"authorization_scope"`
	Enabled            bool               `json:"enabled"`
	State              string             `json:"state,omitempty"`
	StateSince         *time.Time         `json:"state_since,omitempty"`
}

func (c Camera) EngineCamera() eventengine.Camera {
	return eventengine.Camera{ID: c.ID, RoomID: c.RoomID, Zones: c.Zones}
}

func (d *DB) UpsertFacility(ctx context.Context, id, name string) error {
	_, err := d.Pool.Exec(ctx, `INSERT INTO facilities (id, name) VALUES ($1,$2)
		ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name`, id, name)
	return err
}

func (d *DB) UpsertRoom(ctx context.Context, facilityID string, r Room) error {
	_, err := d.Pool.Exec(ctx, `INSERT INTO rooms (id, facility_id, name) VALUES ($1,$2,$3)
		ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name`, r.ID, facilityID, r.Name)
	return err
}

func (d *DB) UpsertCamera(ctx context.Context, c Camera) error {
	zones, err := json.Marshal(c.Zones)
	if err != nil {
		return err
	}
	_, err = d.Pool.Exec(ctx, `INSERT INTO cameras (id, room_id, name, protected, zones, authorized_by,
		authorized_at, authorization_scope, enabled) VALUES ($1,$2,$3,$4,$5,$6,$7::date,$8,$9)
		ON CONFLICT (id) DO UPDATE SET room_id=EXCLUDED.room_id, name=EXCLUDED.name, protected=EXCLUDED.protected,
		zones=EXCLUDED.zones, authorized_by=EXCLUDED.authorized_by, authorized_at=EXCLUDED.authorized_at,
		authorization_scope=EXCLUDED.authorization_scope, enabled=EXCLUDED.enabled`,
		c.ID, c.RoomID, c.Name, c.Protected, zones, c.AuthorizedBy, c.AuthorizedAt, c.AuthorizationScope, c.Enabled)
	return err
}

// InsertCamera adds a camera, returning ErrConflict if the ID is taken and
// ErrNotFound if the room does not exist.
func (d *DB) InsertCamera(ctx context.Context, c Camera) error {
	zones, err := json.Marshal(c.Zones)
	if err != nil {
		return err
	}
	_, err = d.Pool.Exec(ctx, `INSERT INTO cameras (id, room_id, name, protected, zones, authorized_by,
		authorized_at, authorization_scope, enabled) VALUES ($1,$2,$3,$4,$5,$6,$7::date,$8,$9)`,
		c.ID, c.RoomID, c.Name, c.Protected, zones, c.AuthorizedBy, c.AuthorizedAt, c.AuthorizationScope, c.Enabled)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.Code {
		case "23505":
			return ErrConflict
		case "23503":
			return ErrNotFound
		}
	}
	return err
}

type Facility struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// Facility returns the appliance's facility (one per edge box).
func (d *DB) Facility(ctx context.Context) (*Facility, error) {
	var f Facility
	err := d.Pool.QueryRow(ctx, `SELECT id, name FROM facilities ORDER BY created_at, id LIMIT 1`).Scan(&f.ID, &f.Name)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &f, err
}

func (d *DB) Rooms(ctx context.Context) ([]Room, error) {
	rows, err := d.Pool.Query(ctx, `SELECT id, name FROM rooms ORDER BY name`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Room, error) {
		var x Room
		err := r.Scan(&x.ID, &x.Name)
		return x, err
	})
}

func (d *DB) Cameras(ctx context.Context) ([]Camera, error) {
	rows, err := d.Pool.Query(ctx, `SELECT c.id, c.room_id, c.name, c.protected, c.zones, c.authorized_by,
		to_char(c.authorized_at, 'YYYY-MM-DD'), c.authorization_scope, c.enabled, s.state, s.since
		FROM cameras c LEFT JOIN camera_states s ON s.camera_id = c.id ORDER BY c.room_id, c.id`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Camera, error) {
		var c Camera
		var zones []byte
		var state *string
		if err := r.Scan(&c.ID, &c.RoomID, &c.Name, &c.Protected, &zones, &c.AuthorizedBy, &c.AuthorizedAt,
			&c.AuthorizationScope, &c.Enabled, &state, &c.StateSince); err != nil {
			return c, err
		}
		if state != nil {
			c.State = *state
		}
		if err := json.Unmarshal(zones, &c.Zones); err != nil {
			return c, fmt.Errorf("camera %s zones: %w", c.ID, err)
		}
		if c.Zones == nil {
			c.Zones = []eventengine.Zone{}
		}
		return c, nil
	})
}

func (d *DB) SetCameraState(ctx context.Context, cameraID, state string, since time.Time) error {
	_, err := d.Pool.Exec(ctx, `INSERT INTO camera_states (camera_id, state, since) VALUES ($1,$2,$3)
		ON CONFLICT (camera_id) DO UPDATE SET state=EXCLUDED.state, since=EXCLUDED.since`, cameraID, state, since)
	return err
}

// ---- incidents ---------------------------------------------------------------

type IncidentView struct {
	eventengine.Incident
	Lifecycle       string       `json:"lifecycle"`
	EscalationLevel int          `json:"escalation_level"`
	AcknowledgedBy  *string      `json:"acknowledged_by"`
	AcknowledgedAt  *time.Time   `json:"acknowledged_at"`
	Disposition     *string      `json:"disposition"`
	ResolvedBy      *string      `json:"resolved_by"`
	ResolvedAt      *time.Time   `json:"resolved_at"`
	CreatedAt       time.Time    `json:"created_at"`
	RetentionUntil  time.Time    `json:"retention_deadline"`
	LegalHold       bool         `json:"legal_hold"`
}

// IncidentDetail is an incident with its action history and evidence clips.
type IncidentDetail struct {
	IncidentView
	Actions []Action     `json:"actions"`
	Clips   []ClipRecord `json:"clips"`
}

type Action struct {
	Actor       string    `json:"actor"`
	Action      string    `json:"action"`
	Disposition *string   `json:"disposition"`
	Note        *string   `json:"note"`
	At          time.Time `json:"at"`
}

const viewCols = incidentCols + `, lifecycle, escalation_level, acknowledged_by, acknowledged_at, disposition,
	resolved_by, resolved_at, created_at, retention_deadline, legal_hold`

func scanView(row pgx.Row) (*IncidentView, error) {
	var v IncidentView
	var models []byte
	var status string
	err := row.Scan(&v.ID, &v.IdempotencyKey, &v.RoomID, &v.RoomZone, &v.EventType, &v.Family, &v.Start, &v.End,
		&v.Cameras, &status, &v.RuleVersion, &models, &v.Source, &v.Detail, &v.Lifecycle, &v.EscalationLevel,
		&v.AcknowledgedBy, &v.AcknowledgedAt, &v.Disposition, &v.ResolvedBy, &v.ResolvedAt, &v.CreatedAt,
		&v.RetentionUntil, &v.LegalHold)
	if err != nil {
		return nil, err
	}
	v.Status = eventengine.IncidentStatus(status)
	if err := json.Unmarshal(models, &v.ModelVersions); err != nil {
		return nil, fmt.Errorf("incident %s model_versions: %w", v.ID, err)
	}
	return &v, nil
}

type IncidentFilter struct {
	OpenOnly      bool
	IncludeShadow bool
	Limit         int
}

func (d *DB) Incidents(ctx context.Context, f IncidentFilter) ([]IncidentView, error) {
	if f.Limit <= 0 || f.Limit > 500 {
		f.Limit = 200
	}
	rows, err := d.Pool.Query(ctx, `SELECT `+viewCols+` FROM incidents
		WHERE ($1 = false OR lifecycle <> 'resolved') AND ($2 = true OR status <> 'SHADOW')
		ORDER BY CASE WHEN status='ALERTED' AND lifecycle='new' THEN 0 ELSE 1 END, start_ts DESC
		LIMIT $3`, f.OpenOnly, f.IncludeShadow, f.Limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []IncidentView
	for rows.Next() {
		v, err := scanView(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *v)
	}
	return out, rows.Err()
}

// IncidentView loads one incident without its history.
func (d *DB) IncidentView(ctx context.Context, id string) (*IncidentView, error) {
	v, err := scanView(d.Pool.QueryRow(ctx, `SELECT `+viewCols+` FROM incidents WHERE id=$1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return v, err
}

func (d *DB) Incident(ctx context.Context, id string) (*IncidentDetail, error) {
	iv, err := d.IncidentView(ctx, id)
	if err != nil {
		return nil, err
	}
	v := &IncidentDetail{IncidentView: *iv}
	rows, err := d.Pool.Query(ctx, `SELECT actor, action, disposition, note, at FROM incident_actions
		WHERE incident_id=$1 ORDER BY at`, id)
	if err != nil {
		return nil, err
	}
	v.Actions, err = pgx.CollectRows(rows, pgx.RowToStructByPos[Action])
	if err != nil {
		return nil, err
	}
	if v.Clips, err = d.ClipsFor(ctx, id); err != nil {
		return nil, err
	}
	if v.Actions == nil {
		v.Actions = []Action{}
	}
	if v.Clips == nil {
		v.Clips = []ClipRecord{}
	}
	return v, nil
}

// OpenAlertCount counts alerted incidents that are not resolved yet.
func (d *DB) OpenAlertCount(ctx context.Context) (int, error) {
	var n int
	err := d.Pool.QueryRow(ctx, `SELECT count(*) FROM incidents WHERE status='ALERTED' AND lifecycle <> 'resolved'`).Scan(&n)
	return n, err
}

// Acknowledge marks a new incident acknowledged. Acknowledging twice is a
// no-op and reports changed=false.
func (d *DB) Acknowledge(ctx context.Context, id, actor string) (changed bool, err error) {
	err = pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE incidents SET lifecycle='acknowledged', acknowledged_by=$2, acknowledged_at=now()
			WHERE id=$1 AND lifecycle='new'`, id, actor)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM incidents WHERE id=$1)`, id).Scan(&exists); err != nil {
				return err
			}
			if !exists {
				return ErrNotFound
			}
			return nil
		}
		changed = true
		_, err = tx.Exec(ctx, `INSERT INTO incident_actions (incident_id, actor, action) VALUES ($1,$2,'acknowledged')`, id, actor)
		return err
	})
	return changed, err
}

// Resolve records the reviewer's disposition. It is final: a resolved
// incident can only receive notes, never a second disposition.
func (d *DB) Resolve(ctx context.Context, id, actor, disposition, note string) error {
	return pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE incidents SET lifecycle='resolved', disposition=$3, resolved_by=$2, resolved_at=now(),
			acknowledged_by=COALESCE(acknowledged_by,$2), acknowledged_at=COALESCE(acknowledged_at, now())
			WHERE id=$1 AND lifecycle <> 'resolved'`, id, actor, disposition)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM incidents WHERE id=$1)`, id).Scan(&exists); err != nil {
				return err
			}
			if !exists {
				return ErrNotFound
			}
			return ErrConflict
		}
		_, err = tx.Exec(ctx, `INSERT INTO incident_actions (incident_id, actor, action, disposition, note)
			VALUES ($1,$2,'disposition',$3,NULLIF($4,''))`, id, actor, disposition, note)
		return err
	})
}

// Unacknowledged returns alerted incidents still 'new' and older than age,
// below maxLevel escalations.
func (d *DB) Unacknowledged(ctx context.Context, age time.Duration, maxLevel int) ([]IncidentView, error) {
	rows, err := d.Pool.Query(ctx, `SELECT `+viewCols+` FROM incidents
		WHERE status='ALERTED' AND lifecycle='new' AND escalation_level < $2
		  AND created_at + make_interval(secs => $1::float8 * (escalation_level + 1)) < now()`, age.Seconds(), maxLevel)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []IncidentView
	for rows.Next() {
		v, err := scanView(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *v)
	}
	return out, rows.Err()
}

// Escalate raises an unacknowledged incident's escalation level. It returns
// ErrConflict when the incident was acknowledged in the meantime.
func (d *DB) Escalate(ctx context.Context, id string) (int, error) {
	var level int
	err := pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `UPDATE incidents SET escalation_level = escalation_level + 1
			WHERE id=$1 AND lifecycle='new' RETURNING escalation_level`, id).Scan(&level)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrConflict
		}
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO incident_actions (incident_id, actor, action, note) VALUES ($1,'system','escalated',$2)`,
			id, "not acknowledged in time")
		return err
	})
	return level, err
}

// ---- alerts ----------------------------------------------------------------

type Alert struct {
	ID         int64      `json:"id"`
	IncidentID string     `json:"incident_id"`
	Channel    string     `json:"channel"`
	Level      int        `json:"level"`
	State      string     `json:"state"`
	Attempts   int        `json:"attempts"`
	LastError  string     `json:"last_error"`
	CreatedAt  time.Time  `json:"created_at"`
	Delivered  *time.Time `json:"delivered_at"`
}

// CreateAlert is idempotent per (incident, channel, level); created reports
// whether a new row was written.
func (d *DB) CreateAlert(ctx context.Context, incidentID, channel string, level int) (created bool, err error) {
	tag, err := d.Pool.Exec(ctx, `INSERT INTO alerts (incident_id, channel, level, state) VALUES ($1,$2,$3,'pending')
		ON CONFLICT (incident_id, channel, level) DO NOTHING`, incidentID, channel, level)
	return tag.RowsAffected() == 1, err
}

// AlertsFor lists an incident's alert records.
func (d *DB) AlertsFor(ctx context.Context, incidentID string) ([]Alert, error) {
	rows, err := d.Pool.Query(ctx, `SELECT id, incident_id, channel, level, state, attempts, last_error, created_at, delivered_at
		FROM alerts WHERE incident_id=$1 ORDER BY id`, incidentID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[Alert])
}

func (d *DB) DueAlerts(ctx context.Context, channel string) ([]Alert, error) {
	rows, err := d.Pool.Query(ctx, `SELECT id, incident_id, channel, level, state, attempts, last_error, created_at, delivered_at
		FROM alerts WHERE channel=$1 AND state='pending' AND next_attempt_at <= now() ORDER BY id LIMIT 50`, channel)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[Alert])
}

func (d *DB) MarkAlertDelivered(ctx context.Context, id int64) error {
	_, err := d.Pool.Exec(ctx, `UPDATE alerts SET state='delivered', delivered_at=now(), attempts=attempts+1 WHERE id=$1`, id)
	return err
}

// MarkAlertAttemptFailed schedules a retry, or fails the alert permanently
// after maxAttempts.
func (d *DB) MarkAlertAttemptFailed(ctx context.Context, id int64, msg string, retryIn time.Duration, maxAttempts int) (failed bool, err error) {
	err = d.Pool.QueryRow(ctx, `UPDATE alerts SET attempts=attempts+1, last_error=$2,
		state = CASE WHEN attempts+1 >= $4 THEN 'failed' ELSE 'pending' END,
		next_attempt_at = now() + make_interval(secs => $3)
		WHERE id=$1 RETURNING state='failed'`, id, msg, retryIn.Seconds(), maxAttempts).Scan(&failed)
	return failed, err
}

// AlertHealth counts permanently failed deliveries and pending alerts
// waiting longer than lag, for the dashboard's "alerts delayed" state.
func (d *DB) AlertHealth(ctx context.Context, lag time.Duration) (failed, delayed int, err error) {
	err = d.Pool.QueryRow(ctx, `SELECT
		count(*) FILTER (WHERE state='failed' AND created_at > now() - interval '24 hours'),
		count(*) FILTER (WHERE state='pending' AND created_at < now() - make_interval(secs => $1))
		FROM alerts`, lag.Seconds()).Scan(&failed, &delayed)
	return
}

// ---- clips -----------------------------------------------------------------

type ClipRecord struct {
	ID         string     `json:"id"`
	IncidentID string     `json:"incident_id"`
	CameraID   string     `json:"camera_id"`
	Path       string     `json:"-"`
	SHA256     string     `json:"sha256"`
	SizeBytes  int64      `json:"size_bytes"`
	WrappedKey []byte     `json:"-"`
	Partial    bool       `json:"partial"`
	Start      time.Time  `json:"start"`
	End        time.Time  `json:"end"`
	CreatedAt  time.Time  `json:"created_at"`
	DeletedAt  *time.Time `json:"deleted_at"`
}

const clipCols = `id, incident_id, camera_id, path, sha256, size_bytes, wrapped_key, partial, start_ts, end_ts, created_at, deleted_at`

func (d *DB) InsertClip(ctx context.Context, c ClipRecord) error {
	_, err := d.Pool.Exec(ctx, `INSERT INTO evidence_clips (id, incident_id, camera_id, path, sha256, size_bytes,
		wrapped_key, partial, start_ts, end_ts) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (incident_id, camera_id) DO NOTHING`,
		c.ID, c.IncidentID, c.CameraID, c.Path, c.SHA256, c.SizeBytes, c.WrappedKey, c.Partial, c.Start, c.End)
	return err
}

func (d *DB) ClipsFor(ctx context.Context, incidentID string) ([]ClipRecord, error) {
	rows, err := d.Pool.Query(ctx, `SELECT `+clipCols+` FROM evidence_clips WHERE incident_id=$1 ORDER BY camera_id`, incidentID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[ClipRecord])
}

func (d *DB) Clip(ctx context.Context, id string) (*ClipRecord, error) {
	rows, err := d.Pool.Query(ctx, `SELECT `+clipCols+` FROM evidence_clips WHERE id=$1`, id)
	if err != nil {
		return nil, err
	}
	c, err := pgx.CollectExactlyOneRow(rows, pgx.RowToStructByPos[ClipRecord])
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &c, err
}

// ExpiredClips lists clips past their incident's retention deadline that are
// not under legal hold and not yet deleted.
func (d *DB) ExpiredClips(ctx context.Context) ([]ClipRecord, error) {
	rows, err := d.Pool.Query(ctx, `SELECT `+prefixed("c.", clipCols)+` FROM evidence_clips c JOIN incidents i ON i.id = c.incident_id
		WHERE c.deleted_at IS NULL AND NOT i.legal_hold AND i.retention_deadline < now()`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[ClipRecord])
}

// ShredClip destroys the clip's key, which makes any remaining copy of the
// ciphertext unreadable, and marks it deleted.
func (d *DB) ShredClip(ctx context.Context, id string) error {
	_, err := d.Pool.Exec(ctx, `UPDATE evidence_clips SET wrapped_key=NULL, deleted_at=now() WHERE id=$1`, id)
	return err
}

func (d *DB) SetLegalHold(ctx context.Context, incidentID, actor string, hold bool) error {
	return pgx.BeginFunc(ctx, d.Pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE incidents SET legal_hold=$2 WHERE id=$1`, incidentID, hold)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return ErrNotFound
		}
		action := "hold_released"
		if hold {
			action = "hold_set"
		}
		_, err = tx.Exec(ctx, `INSERT INTO incident_actions (incident_id, actor, action) VALUES ($1,$2,$3)`, incidentID, actor, action)
		return err
	})
}

func prefixed(p, cols string) string {
	out := ""
	for i, c := range splitCols(cols) {
		if i > 0 {
			out += ", "
		}
		out += p + c
	}
	return out
}

func splitCols(cols string) []string {
	var out []string
	cur := ""
	for _, r := range cols {
		switch r {
		case ',':
			out = append(out, cur)
			cur = ""
		case ' ', '\n', '\t':
		default:
			cur += string(r)
		}
	}
	return append(out, cur)
}

// ---- users & sessions --------------------------------------------------------

type User struct {
	ID           int64  `json:"id"`
	Username     string `json:"username"`
	DisplayName  string `json:"display_name"`
	Role         string `json:"role"`
	PasswordHash string `json:"-"`
	Disabled     bool   `json:"disabled"`
}

func (d *DB) CountUsers(ctx context.Context) (int, error) {
	var n int
	err := d.Pool.QueryRow(ctx, `SELECT count(*) FROM users`).Scan(&n)
	return n, err
}

// CreateUser returns ErrConflict if the username is taken.
func (d *DB) CreateUser(ctx context.Context, u User) (int64, error) {
	var id int64
	err := d.Pool.QueryRow(ctx, `INSERT INTO users (username, display_name, role, password_hash) VALUES ($1,$2,$3,$4) RETURNING id`,
		u.Username, u.DisplayName, u.Role, u.PasswordHash).Scan(&id)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		return 0, ErrConflict
	}
	return id, err
}

func (d *DB) UserByName(ctx context.Context, username string) (*User, error) {
	var u User
	err := d.Pool.QueryRow(ctx, `SELECT id, username, display_name, role, password_hash, disabled FROM users WHERE username=$1`,
		username).Scan(&u.ID, &u.Username, &u.DisplayName, &u.Role, &u.PasswordHash, &u.Disabled)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (d *DB) Users(ctx context.Context) ([]User, error) {
	rows, err := d.Pool.Query(ctx, `SELECT id, username, display_name, role, password_hash, disabled FROM users ORDER BY username`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[User])
}

func (d *DB) CreateSession(ctx context.Context, tokenHash string, userID int64, ttl time.Duration) error {
	_, err := d.Pool.Exec(ctx, `INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1,$2, now() + make_interval(secs => $3))`,
		tokenHash, userID, ttl.Seconds())
	return err
}

// SessionUser returns the user for a live session and slides its expiry.
func (d *DB) SessionUser(ctx context.Context, tokenHash string, ttl time.Duration) (*User, error) {
	var u User
	err := d.Pool.QueryRow(ctx, `UPDATE sessions s SET expires_at = now() + make_interval(secs => $2)
		FROM users u WHERE s.token_hash=$1 AND s.expires_at > now() AND u.id = s.user_id AND NOT u.disabled
		RETURNING u.id, u.username, u.display_name, u.role, u.password_hash, u.disabled`, tokenHash, ttl.Seconds()).
		Scan(&u.ID, &u.Username, &u.DisplayName, &u.Role, &u.PasswordHash, &u.Disabled)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &u, err
}

// SessionUserNoTouch is SessionUser without sliding the expiry, for checks
// that are not user activity (e.g. keeping a live event stream open).
func (d *DB) SessionUserNoTouch(ctx context.Context, tokenHash string) (*User, error) {
	var u User
	err := d.Pool.QueryRow(ctx, `SELECT u.id, u.username, u.display_name, u.role, u.password_hash, u.disabled
		FROM sessions s JOIN users u ON u.id = s.user_id
		WHERE s.token_hash=$1 AND s.expires_at > now() AND NOT u.disabled`, tokenHash).
		Scan(&u.ID, &u.Username, &u.DisplayName, &u.Role, &u.PasswordHash, &u.Disabled)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &u, err
}

// PurgeSessions removes expired sessions.
func (d *DB) PurgeSessions(ctx context.Context) error {
	_, err := d.Pool.Exec(ctx, `DELETE FROM sessions WHERE expires_at < now()`)
	return err
}

func (d *DB) DeleteSession(ctx context.Context, tokenHash string) error {
	_, err := d.Pool.Exec(ctx, `DELETE FROM sessions WHERE token_hash=$1`, tokenHash)
	return err
}

type AuditEntry struct {
	Seq     int64           `json:"seq"`
	At      time.Time       `json:"at"`
	Actor   string          `json:"actor"`
	Action  string          `json:"action"`
	Subject string          `json:"subject"`
	Payload json.RawMessage `json:"payload"`
}

func (d *DB) AuditEntries(ctx context.Context, limit int) ([]AuditEntry, error) {
	rows, err := d.Pool.Query(ctx, `SELECT seq, at, actor, action, subject, payload FROM audit_log ORDER BY seq DESC LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[AuditEntry])
}
