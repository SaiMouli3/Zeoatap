# Core API contract (v1)

Single source of truth for `services/core` (implements), `apps/web` (consumes) and `services/vision-worker` (internal endpoints). Change this file first if the contract changes.

## Conventions
- JSON everywhere. Errors: `{"error": "human-readable message"}` with a proper status code.
- Every staff `POST` must send `Content-Type: application/json`, otherwise `415`. (The internal observation stream uses `application/x-ndjson`.) Together with `SameSite=Strict` cookies, this is the CSRF defence.
- Staff auth: HttpOnly cookie `zeoatap_session` (15 min sliding idle timeout). `401` = not signed in, `403` = signed in but role lacks permission.
- Times are RFC 3339 UTC strings.

## Roles → permissions
| Permission | caregiver | lead | reviewer | admin |
|---|---|---|---|---|
| view_status (overview, incidents list/detail, events) | ✓ | ✓ | ✓ | ✓ |
| acknowledge | ✓ | ✓ | ✓ | ✓ |
| view_clips | | ✓ | ✓ | |
| disposition | | | ✓ | |
| view_shadow (see SHADOW incidents) | | | ✓ | ✓ |
| legal_hold | | | ✓ | ✓ |
| admin (cameras, users, audit) | | | | ✓ |

## Monitoring states (worst first)
`CAMERA_UNAVAILABLE` > `NOT_CONNECTED` > `AI_UNAVAILABLE` > `VIDEO_DEGRADED` > `FULLY_OPERATIONAL`.
A room's state is the worst state among its cameras (no cameras → `NOT_CONNECTED`). The facility state is the worst room. **Only `FULLY_OPERATIONAL` may be shown green.**

## Staff endpoints (public listener, default `:8080`)

| Method & path | Perm | Request | Response |
|---|---|---|---|
| `POST /api/v1/login` | — | `{username, password}` | `200 {user}` / `401` |
| `POST /api/v1/logout` | — | — | `204` |
| `GET /api/v1/me` | any | — | `{user, permissions: [..]}` |
| `GET /api/v1/overview` | view_status | — | Overview (below) |
| `GET /api/v1/cameras` | view_status | — | `[Camera]` |
| `POST /api/v1/cameras` | admin | Camera (authorization fields required) | `201 Camera` |
| `GET /api/v1/incidents?open=1&shadow=1&limit=N` | view_status (`shadow=1` needs view_shadow; otherwise ignored) | — | `[Incident]` |
| `GET /api/v1/incidents/{id}` | view_status (SHADOW needs view_shadow, else 404) | — | Incident with `actions`, `clips` |
| `POST /api/v1/incidents/{id}/ack` | acknowledge | `{}` | `200 Incident` |
| `POST /api/v1/incidents/{id}/disposition` | disposition | `{disposition, note}` | `200 Incident` / `409` if already resolved |
| `POST /api/v1/incidents/{id}/hold` | legal_hold | `{hold: bool}` | `200 Incident` |
| `POST /api/v1/clips/{id}/token` | view_clips | `{}` | `{url, expires_at}` (url = `/api/v1/clips/{id}/media?token=…`, valid 5 min) |
| `GET /api/v1/clips/{id}/media?token=…` | token | — | `video/mp4`, supports Range. Every view is audit-logged |
| `GET /api/v1/events` | view_status | — | `text/event-stream` (below) |
| `GET /api/v1/users` | admin | — | `[User]` |
| `POST /api/v1/users` | admin | `{username, display_name, role, password}` | `201 User` |
| `GET /api/v1/audit?limit=N` | admin | — | `{entries: [AuditEntry], chain_ok: bool, broken_at: int}` |
| `GET /healthz` | — | — | `200 {"ok":true}` |
| `GET /readyz` | — | — | `200`, or `503` if the database or engine is unhealthy |

`disposition` ∈ `no_concern | routine_care | false_detection | concern_followed_up | escalated_external | inconclusive`.

### Shapes
```jsonc
// User
{"id": 1, "username": "jane", "display_name": "Jane", "role": "reviewer", "disabled": false}

// Camera
{"id": "cam1", "room_id": "infants-a", "name": "Changing table", "protected": true,
 "zones": [{"id": "table", "room_zone": "changing-table-1", "kind": "surface", "polygon": [{"X":0.1,"Y":0.3}, ...]}],
 "authorized_by": "Facility director", "authorized_at": "2026-10-01", "authorization_scope": "Infant room A, daytime",
 "enabled": true, "state": "FULLY_OPERATIONAL", "state_since": "2026-10-31T12:00:00Z"}

// Incident
{"id": "INC-392ecdca3371", "room_id": "infants-a", "room_zone": "changing-table-1",
 "event_type": "infant_off_surface", "family": "A", "start": "...", "end": "...", "cameras": ["cam1"],
 "status": "ALERTED | REVIEW_QUEUED | SHADOW | INCONCLUSIVE",
 "lifecycle": "new | acknowledged | resolved", "escalation_level": 0,
 "acknowledged_by": null, "acknowledged_at": null, "disposition": null, "resolved_by": null, "resolved_at": null,
 "rule_version": "a1-v0.1", "model_versions": {"detector": "..."}, "source": "MOCK | <pipeline id>",
 "detail": "infant below table and not held", "created_at": "...", "retention_deadline": "...", "legal_hold": false,
 "actions": [{"actor": "jane", "action": "acknowledged", "disposition": null, "note": null, "at": "..."}],
 "clips": [{"id": "INC-…-cam1", "camera_id": "cam1", "sha256": "…", "size_bytes": 123, "partial": false,
            "start": "...", "end": "...", "created_at": "...", "deleted_at": null}]}

// Overview
{"facility": {"id": "f1", "name": "Sunflower Nursery"},
 "state": "FULLY_OPERATIONAL",
 "rooms": [{"id": "infants-a", "name": "Infants A", "state": "...",
            "cameras": [{"id": "cam1", "name": "...", "state": "...", "since": "...", "protected": true}]}],
 "open_alerts": 1,
 "alerts_health": {"failed": 0, "delayed": 0},
 "services": {"engine": "ok | error", "engine_error": "", "database": "ok | error", "evidence": "ok | error"},
 "generated_at": "..."}

// AuditEntry
{"seq": 12, "at": "...", "actor": "jane", "action": "clip_viewed", "subject": "INC-…-cam1", "payload": {}}
```

### Server-sent events (`GET /api/v1/events`)
Each message: `event: <type>\ndata: <json>\n\n`. A `: ping` comment every 15 s. After a reconnect, clients must re-fetch `/overview` and `/incidents`, because events are not replayed.

| type | data |
|---|---|
| `camera_state` | `{camera_id, state, since}` |
| `incident_created` / `incident_merged` / `incident_updated` | Incident |
| `alert` | `{incident_id, room_id, event_type, level}` |
| `incident_escalated` | `{incident_id, level}` |
| `alert_delivery_failed` | `{incident_id, channel, error}` |
| `clip_ready` | `{incident_id, clip_id, partial}` |
| `clip_failed` | `{incident_id, camera_id, error}` |

SHADOW incidents are never sent to clients without view_shadow.

## Internal endpoints (vision-worker ↔ core, localhost only, `Authorization: Bearer $ZEOATAP_INTERNAL_TOKEN`)

| Direction | Method & path | Request | Response |
|---|---|---|---|
| vision → core (internal listener, default `127.0.0.1:8090`) | `POST /internal/v1/observations` | NDJSON body: one Observation per line ([observation.schema.json](observation.schema.json)), streamed | `200 {"accepted": n}` · `400` bad line (rest rejected) · `503` engine backlog full |
| core → vision (vision health server, default `127.0.0.1:8081`) | `POST /v1/clips` | `{camera_id, start, end, path}` (start/end = unix seconds, float) | `200 {path, sha256, partial}` · `404 {error}` unknown camera · `422 {error}` no video for the window. Blocks until the clip is written (≤ end + 120 s) |

Core encrypts the plaintext clip from `path` into its vault and deletes the plaintext.
