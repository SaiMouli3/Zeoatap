-- Initial schema for core. NOT yet exercised by automated tests (no Postgres in CI yet).
CREATE TABLE facilities (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  name        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cameras (
  id                 text PRIMARY KEY,
  facility_id        uuid NOT NULL REFERENCES facilities(id),
  room_id            text NOT NULL,
  rtsp_secret_ref    text NOT NULL,          -- reference into the secret store, never the credential
  authorized_by      text NOT NULL,          -- written authorization record (US-05)
  authorized_at      date NOT NULL,
  authorization_scope text NOT NULL,
  enabled            boolean NOT NULL DEFAULT false
);

CREATE TABLE incidents (
  id               text PRIMARY KEY,
  idempotency_key  text NOT NULL UNIQUE,     -- makes incident creation idempotent
  facility_id      uuid NOT NULL REFERENCES facilities(id),
  room_id          text NOT NULL,
  room_zone        text NOT NULL,
  event_type       text NOT NULL,
  family           text NOT NULL,
  start_ts         timestamptz NOT NULL,
  end_ts           timestamptz NOT NULL,
  cameras          text[] NOT NULL,
  status           text NOT NULL CHECK (status IN ('ALERTED','REVIEW_QUEUED','SHADOW','INCONCLUSIVE')),
  rule_version     text NOT NULL,
  model_versions   jsonb NOT NULL DEFAULT '{}',
  source           text NOT NULL,
  detail           text NOT NULL DEFAULT '',
  retention_deadline timestamptz NOT NULL,
  legal_hold       boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incidents_merge_idx ON incidents (room_id, room_zone, family, start_ts);

CREATE TABLE incident_actions (
  id           bigserial PRIMARY KEY,
  incident_id  text NOT NULL REFERENCES incidents(id),
  actor        text NOT NULL,
  action       text NOT NULL CHECK (action IN ('acknowledged','assigned','disposition','escalated','note','hold_set','hold_released')),
  disposition  text CHECK (disposition IN ('no_concern','routine_care','false_detection','concern_followed_up','escalated_external','inconclusive')),
  note         text,
  at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE evidence_clips (
  id            text PRIMARY KEY,
  incident_id   text NOT NULL REFERENCES incidents(id),
  camera_id     text NOT NULL REFERENCES cameras(id),
  sha256        text NOT NULL,
  wrapped_key   bytea NOT NULL,             -- per-clip key; destroying it crypto-shreds the clip
  start_ts      timestamptz NOT NULL,
  end_ts        timestamptz NOT NULL,
  deleted_at    timestamptz
);

-- Append-only, hash-chained audit log.
CREATE TABLE audit_log (
  seq        bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text NOT NULL,
  action     text NOT NULL,
  subject    text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  prev_hash  text NOT NULL,
  hash       text NOT NULL
);
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
