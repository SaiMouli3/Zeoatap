#!/usr/bin/env node
// Zeoatap mock core API — plain Node, no dependencies.
//
// Implements the staff endpoints of packages/contracts/API.md with seeded data
// covering every monitoring state, incident status/lifecycle and role, plus SSE.
// It can also serve the static export (default ./out) at "/", the same way the
// Go core does, so the dashboard and API share one origin.
//
//   node mock/server.mjs                     # :8080, serves ./out if present
//   PORT=8181 STATIC_DIR=out PING_MS=15000 node mock/server.mjs
//
// Seeded accounts (password for all: "zeoatap-demo"):
//   carol (caregiver) · leo (lead) · rita (reviewer) · ada (admin)
//
// Test-control endpoints (mock only, never part of the real contract):
//   POST /__mock/reset                  {scenario?: "default" | "all_ok"}
//   POST /__mock/scenario               {name: "default" | "all_ok" | "audit_broken"}
//   POST /__mock/camera_state           {camera_id, state}            -> emits camera_state
//   POST /__mock/incident               {room_id?, event_type?, status?} -> creates + emits incident_created + alert
//   POST /__mock/emit                   {type, data}                  -> raw SSE event
//   POST /__mock/sse/drop               {refuse_ms?: number}          -> closes streams, refuses reconnects for refuse_ms
//   POST /__mock/sessions/expire        {}                            -> invalidates every session
//   POST /__mock/latency                {ms}                          -> adds latency to API responses
//   GET  /__mock/state                                                 -> debug dump

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 8080);
const HOST = process.env.HOST ?? "127.0.0.1";
const STATIC_DIR = path.resolve(process.cwd(), process.env.STATIC_DIR ?? "out");
const PING_MS = Number(process.env.PING_MS ?? 15000);
const IDLE_MS = 15 * 60 * 1000;
const PASSWORD = "zeoatap-demo";
const CLIP_FILE = path.join(HERE, "fixtures", "sample.mp4");

const PERMS = {
  caregiver: ["view_status", "acknowledge"],
  lead: ["view_status", "acknowledge", "view_clips"],
  reviewer: ["view_status", "acknowledge", "view_clips", "disposition", "view_shadow", "legal_hold"],
  admin: ["view_status", "acknowledge", "view_shadow", "legal_hold", "admin"],
};
const STATE_RANK = {
  CAMERA_UNAVAILABLE: 4,
  NOT_CONNECTED: 3,
  AI_UNAVAILABLE: 2,
  VIDEO_DEGRADED: 1,
  FULLY_OPERATIONAL: 0,
};
const DISPOSITIONS = ["no_concern", "routine_care", "false_detection", "concern_followed_up", "escalated_external", "inconclusive"];

// ---------------------------------------------------------------- state
let db;
let sessions = new Map(); // token -> {userId, lastSeen}
let tokens = new Map(); // clip token -> {clipId, expires}
let sseClients = new Set(); // {res, user}
let refuseSseUntil = 0;
let latencyMs = 0;

const iso = (msAgo = 0) => new Date(Date.now() - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
const MIN = 60_000;
const DAY = 24 * 60 * MIN;

function seed(scenario = "default") {
  const allOk = scenario === "all_ok";
  const cams = [
    cam("cam1", "infants-a", "Changing table", "FULLY_OPERATIONAL", 3 * 60 * MIN, true),
    cam("cam2", "infants-a", "Cot row", "FULLY_OPERATIONAL", 3 * 60 * MIN, true),
    cam("cam3", "infants-b", "Changing table", allOk ? "FULLY_OPERATIONAL" : "VIDEO_DEGRADED", 4 * MIN, true),
    cam("cam4", "infants-b", "Play mat", "FULLY_OPERATIONAL", 2 * 60 * MIN, true),
    cam("cam5", "toddlers-b", "Room overview", allOk ? "FULLY_OPERATIONAL" : "CAMERA_UNAVAILABLE", 2 * MIN, true),
    cam("cam6", "sleep-room", "Cots 1–6", allOk ? "FULLY_OPERATIONAL" : "AI_UNAVAILABLE", 7 * MIN, true),
  ];
  const rooms = [
    { id: "infants-a", name: "Infants A" },
    { id: "infants-b", name: "Infants B" },
    { id: "toddlers-b", name: "Toddlers B" },
    { id: "sleep-room", name: "Sleep room" },
  ];
  if (!allOk) rooms.push({ id: "garden-room", name: "Garden room" }); // no cameras -> NOT_CONNECTED

  const users = [
    { id: 1, username: "carol", display_name: "Carol Diaz", role: "caregiver", disabled: false },
    { id: 2, username: "leo", display_name: "Leo Park", role: "lead", disabled: false },
    { id: 3, username: "rita", display_name: "Rita Okafor", role: "reviewer", disabled: false },
    { id: 4, username: "ada", display_name: "Ada Mensah", role: "admin", disabled: false },
    { id: 5, username: "former", display_name: "Former Staff", role: "caregiver", disabled: true },
  ];

  const incidents = allOk
    ? []
    : [
        incident({
          id: "INC-7f3a9c21b001", room_id: "infants-b", room_zone: "changing-table-2", event_type: "infant_off_surface",
          family: "A", agoStart: 50_000, cameras: ["cam3", "cam4"], status: "ALERTED", lifecycle: "new", escalation_level: 0,
          detail: "Infant appears below the changing-table surface and not held. Visibility partial: an adult occludes the table for 0.4 s.",
          uncertainty: "medium", visibility: "partial (adult occludes table 0.4 s)",
        }),
        incident({
          id: "INC-2b8e44d0c002", room_id: "toddlers-b", room_zone: "kitchen-door", event_type: "adult_in_restricted_zone",
          family: "B", agoStart: 14 * MIN, cameras: ["cam5"], status: "ALERTED", lifecycle: "acknowledged", escalation_level: 1,
          acknowledged_by: "leo", acknowledged_at: iso(13 * MIN),
          detail: "Person detected in the configured restricted zone near the kitchen door for 6 s.",
          actions: [{ actor: "system", action: "escalated", disposition: null, note: "level 1", at: iso(13.5 * MIN) }, { actor: "leo", action: "acknowledged", disposition: null, note: null, at: iso(13 * MIN) }],
        }),
        incident({
          id: "INC-91c0de55a003", room_id: "infants-a", room_zone: "cot-3", event_type: "infant_off_surface",
          family: "A", agoStart: 42 * MIN, cameras: ["cam2"], status: "REVIEW_QUEUED", lifecycle: "acknowledged",
          acknowledged_by: "carol", acknowledged_at: iso(40 * MIN),
          detail: "Brief loss of tracking near cot 3. Evidence quality low; review required.",
          uncertainty: "high", visibility: "good",
          actions: [{ actor: "carol", action: "acknowledged", disposition: null, note: null, at: iso(40 * MIN) }],
        }),
        incident({
          id: "INC-4d2f11e9b004", room_id: "infants-b", room_zone: "play-mat", event_type: "infant_off_surface",
          family: "A", agoStart: 3 * 60 * MIN, cameras: ["cam4"], status: "INCONCLUSIVE", lifecycle: "resolved",
          acknowledged_by: "carol", acknowledged_at: iso(179 * MIN), disposition: "inconclusive", resolved_by: "rita",
          resolved_at: iso(150 * MIN), legal_hold: true,
          detail: "Camera view partly blocked during the event window.",
          actions: [
            { actor: "carol", action: "acknowledged", disposition: null, note: null, at: iso(179 * MIN) },
            { actor: "rita", action: "disposition", disposition: "inconclusive", note: "View blocked by a coat on the camera arm. Room lead informed.", at: iso(150 * MIN) },
            { actor: "rita", action: "legal_hold_set", disposition: null, note: null, at: iso(149 * MIN) },
          ],
        }),
        incident({
          id: "INC-c05e7a8fd005", room_id: "sleep-room", room_zone: "cot-1", event_type: "infant_prone_sleep",
          family: "C", agoStart: 25 * MIN, cameras: ["cam6"], status: "SHADOW", lifecycle: "new",
          rule_version: "c1-v0.0.3-shadow",
          detail: "Shadow-mode rule (not shown to room staff). Possible change of sleep position.",
        }),
        incident({
          id: "INC-e1aa0b77c006", room_id: "infants-a", room_zone: "changing-table-1", event_type: "infant_off_surface",
          family: "A", agoStart: 26 * 60 * MIN, cameras: ["cam1"], status: "ALERTED", lifecycle: "resolved",
          acknowledged_by: "carol", acknowledged_at: iso(26 * 60 * MIN - 20_000), disposition: "routine_care",
          resolved_by: "rita", resolved_at: iso(25 * 60 * MIN),
          detail: "Infant lifted from the changing table by staff.",
          actions: [
            { actor: "carol", action: "acknowledged", disposition: null, note: null, at: iso(26 * 60 * MIN - 20_000) },
            { actor: "rita", action: "disposition", disposition: "routine_care", note: "Routine care, staff member lifting infant.", at: iso(25 * 60 * MIN) },
          ],
        }),
      ];

  const audit = [];
  db = {
    scenario,
    facility: { id: "f1", name: "Sunflower Nursery" },
    rooms,
    cameras: cams,
    users,
    incidents,
    audit,
    nextUserId: 6,
    alerts_health: allOk ? { failed: 0, delayed: 0 } : { failed: 1, delayed: 2 },
    services: allOk
      ? { engine: "ok", engine_error: "", database: "ok", evidence: "ok" }
      : { engine: "ok", engine_error: "", database: "ok", evidence: "error" },
    auditBroken: scenario === "audit_broken",
  };
  const seedAudit = [
    ["system", "camera_registered", "cam1"], ["system", "camera_registered", "cam2"], ["ada", "user_created", "rita"],
    ["carol", "login", "carol"], ["carol", "incident_acknowledged", "INC-4d2f11e9b004"], ["rita", "clip_viewed", "INC-4d2f11e9b004-cam4"],
    ["rita", "incident_disposition", "INC-4d2f11e9b004"], ["rita", "legal_hold_set", "INC-4d2f11e9b004"], ["leo", "incident_acknowledged", "INC-2b8e44d0c002"],
  ];
  seedAudit.forEach(([actor, action, subject], i) => addAudit(actor, action, subject, {}, (seedAudit.length - i) * 7 * MIN));
}

function cam(id, room_id, name, state, sinceAgo, prot) {
  return {
    id, room_id, name, protected: prot,
    zones: [{ id: "table", room_zone: `${room_id}-zone-1`, kind: "surface", polygon: [{ X: 0.1, Y: 0.3 }, { X: 0.6, Y: 0.3 }, { X: 0.6, Y: 0.7 }, { X: 0.1, Y: 0.7 }] }],
    authorized_by: "Facility director (M. Rossi)", authorized_at: "2026-10-01", authorization_scope: "Room coverage during operating hours",
    enabled: true, state, state_since: iso(sinceAgo),
  };
}

function incident(o) {
  const start = iso(o.agoStart);
  const end = iso(o.agoStart - 6000);
  const id = o.id;
  return {
    id, room_id: o.room_id, room_zone: o.room_zone, event_type: o.event_type, family: o.family,
    start, end, cameras: o.cameras, status: o.status, lifecycle: o.lifecycle, escalation_level: o.escalation_level ?? 0,
    acknowledged_by: o.acknowledged_by ?? null, acknowledged_at: o.acknowledged_at ?? null,
    disposition: o.disposition ?? null, resolved_by: o.resolved_by ?? null, resolved_at: o.resolved_at ?? null,
    rule_version: o.rule_version ?? (o.family === "B" ? "b1-v0.2" : "a1-v0.1"),
    model_versions: { detector: "yolo-det-v0.3.1", pose: "pose-v0.2.0", tracker: "bytetrack-1.0" },
    source: "MOCK",
    detail: o.detail ?? "",
    ...(o.uncertainty ? { uncertainty: o.uncertainty } : {}),
    ...(o.visibility ? { visibility: o.visibility } : {}),
    created_at: start,
    retention_deadline: new Date(Date.now() - o.agoStart + 30 * DAY).toISOString().replace(/\.\d{3}Z$/, "Z"),
    legal_hold: o.legal_hold ?? false,
    actions: o.actions ?? [],
    clips: o.cameras.map((c, i) => ({
      id: `${id}-${c}`, camera_id: c, sha256: crypto.createHash("sha256").update(`${id}-${c}`).digest("hex"),
      size_bytes: 18699, partial: i === 1, start: iso(o.agoStart + 10_000), end: iso(o.agoStart - 16_000), created_at: end, deleted_at: null,
    })),
  };
}

function addAudit(actor, action, subject, payload = {}, msAgo = 0) {
  db.audit.push({ seq: db.audit.length + 1, at: iso(msAgo), actor, action, subject, payload });
}

function roomState(roomId) {
  const cs = db.cameras.filter((c) => c.room_id === roomId && c.enabled);
  if (cs.length === 0) return "NOT_CONNECTED";
  return cs.reduce((w, c) => (STATE_RANK[c.state] > STATE_RANK[w] ? c.state : w), "FULLY_OPERATIONAL");
}

function overview(user) {
  const rooms = db.rooms.map((r) => ({
    id: r.id, name: r.name, state: roomState(r.id),
    cameras: db.cameras.filter((c) => c.room_id === r.id).map((c) => ({ id: c.id, name: c.name, state: c.state, since: c.state_since, protected: c.protected })),
  }));
  const state = rooms.reduce((w, r) => (STATE_RANK[r.state] > STATE_RANK[w] ? r.state : w), "FULLY_OPERATIONAL");
  return {
    facility: db.facility, state, rooms,
    open_alerts: visibleIncidents(user, true).filter((i) => i.lifecycle !== "resolved").length,
    alerts_health: db.alerts_health, services: db.services, generated_at: iso(),
  };
}

function visibleIncidents(user, includeShadow) {
  const canShadow = PERMS[user.role].includes("view_shadow");
  return db.incidents.filter((i) => i.status !== "SHADOW" || (canShadow && includeShadow));
}

const summary = (i) => {
  const { actions: _a, clips: _c, ...rest } = i; // eslint-disable-line no-unused-vars
  return rest;
};

// ---------------------------------------------------------------- SSE
function broadcast(type, data, { shadow = false } = {}) {
  const msg = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const c of sseClients) {
    if (shadow && !PERMS[c.user.role].includes("view_shadow")) continue;
    c.res.write(msg);
  }
}
setInterval(() => {
  for (const c of sseClients) c.res.write(": ping\n\n");
}, PING_MS).unref();

// ---------------------------------------------------------------- http helpers
function send(res, status, body, headers = {}) {
  const payload = body === undefined ? "" : JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers });
  res.end(payload);
}
const err = (res, status, msg) => send(res, status, { error: msg });

function readBody(req) {
  return new Promise((resolve) => {
    let s = "";
    req.on("data", (d) => (s += d));
    req.on("end", () => resolve(s));
  });
}
function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k) out[k] = decodeURIComponent(v.join("="));
  }
  return out;
}
function currentUser(req) {
  const tok = parseCookies(req).zeoatap_session;
  if (!tok) return null;
  const s = sessions.get(tok);
  if (!s) return null;
  if (Date.now() - s.lastSeen > IDLE_MS) {
    sessions.delete(tok);
    return null;
  }
  s.lastSeen = Date.now();
  const u = db.users.find((x) => x.id === s.userId);
  return u && !u.disabled ? u : null;
}

const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain", ".woff2": "font/woff2" };
function serveStatic(req, res, pathname) {
  if (!fs.existsSync(STATIC_DIR)) return err(res, 404, "not found (no static build; run next build)");
  let p = path.normalize(decodeURIComponent(pathname)).replace(/^(\.\.[/\\])+/, "");
  let file = path.join(STATIC_DIR, p);
  if (!file.startsWith(STATIC_DIR)) return err(res, 400, "bad path");
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
    if (!pathname.endsWith("/")) {
      res.writeHead(301, { Location: pathname + "/" });
      return res.end();
    }
    file = path.join(file, "index.html");
  }
  if (!fs.existsSync(file)) {
    file = path.join(STATIC_DIR, "404.html");
    if (!fs.existsSync(file)) return err(res, 404, "not found");
    res.writeHead(404, { "Content-Type": MIME[".html"] });
    return fs.createReadStream(file).pipe(res);
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] ?? "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}

function serveClip(req, res) {
  const stat = fs.statSync(CLIP_FILE);
  const range = req.headers.range;
  const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Cache-Control": "no-store" };
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    const start = m && m[1] ? Number(m[1]) : 0;
    const end = m && m[2] ? Number(m[2]) : stat.size - 1;
    if (start >= stat.size) {
      res.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
      return res.end();
    }
    res.writeHead(206, { ...headers, "Content-Range": `bytes ${start}-${end}/${stat.size}`, "Content-Length": end - start + 1 });
    return fs.createReadStream(CLIP_FILE, { start, end }).pipe(res);
  }
  res.writeHead(200, { ...headers, "Content-Length": stat.size });
  fs.createReadStream(CLIP_FILE).pipe(res);
}

// ---------------------------------------------------------------- validation
function validateZones(zones) {
  if (!Array.isArray(zones)) return "zones must be an array";
  for (const z of zones) {
    if (!z || typeof z.id !== "string" || !z.id) return "each zone needs an id";
    if (!Array.isArray(z.polygon) || z.polygon.length < 3) return `zone ${z.id}: polygon needs at least 3 points`;
    for (const p of z.polygon) if (typeof p?.X !== "number" || typeof p?.Y !== "number" || p.X < 0 || p.X > 1 || p.Y < 0 || p.Y > 1) return `zone ${z.id}: points must be {X,Y} in 0..1`;
  }
  return null;
}

// ---------------------------------------------------------------- router
async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host ?? "localhost"}`);
  const p = url.pathname;
  const method = req.method ?? "GET";

  if (p.startsWith("/__mock/")) return mockControl(req, res, p);
  if (p === "/healthz") return send(res, 200, { ok: true });
  if (p === "/readyz") return db.services.engine === "ok" && db.services.database === "ok" ? send(res, 200, { ok: true }) : err(res, 503, "unhealthy");
  if (!p.startsWith("/api/")) return serveStatic(req, res, p);

  if (latencyMs) await new Promise((r) => setTimeout(r, latencyMs));

  let body = {};
  if (method === "POST") {
    const ct = req.headers["content-type"] ?? "";
    if (!ct.toLowerCase().startsWith("application/json")) return err(res, 415, "Content-Type must be application/json");
    const raw = await readBody(req);
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch {
      return err(res, 400, "invalid JSON");
    }
  }

  // --- unauthenticated
  if (p === "/api/v1/login" && method === "POST") {
    const u = db.users.find((x) => x.username === body.username);
    if (!u || u.disabled || body.password !== PASSWORD) return err(res, 401, "Incorrect username or password");
    const tok = crypto.randomBytes(24).toString("hex");
    sessions.set(tok, { userId: u.id, lastSeen: Date.now() });
    addAudit(u.username, "login", u.username);
    return send(res, 200, { user: u }, { "Set-Cookie": `zeoatap_session=${tok}; HttpOnly; SameSite=Strict; Path=/` });
  }
  if (p === "/api/v1/logout" && method === "POST") {
    const tok = parseCookies(req).zeoatap_session;
    if (tok) sessions.delete(tok);
    res.writeHead(204, { "Set-Cookie": "zeoatap_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" });
    return res.end();
  }

  const clipMedia = /^\/api\/v1\/clips\/([^/]+)\/media$/.exec(p);
  if (clipMedia && method === "GET") {
    const t = tokens.get(url.searchParams.get("token") ?? "");
    if (!t || t.clipId !== clipMedia[1] || t.expires < Date.now()) return err(res, 403, "clip token invalid or expired");
    if (!req.headers.range || req.headers.range.startsWith("bytes=0-")) addAudit(t.actor, "clip_viewed", t.clipId);
    return serveClip(req, res);
  }

  // --- authenticated
  const user = currentUser(req);
  if (!user) return err(res, 401, "not signed in");
  const perms = PERMS[user.role];
  const need = (perm) => {
    if (!perms.includes(perm)) {
      err(res, 403, `your role (${user.role}) does not have the ${perm} permission`);
      return false;
    }
    return true;
  };

  if (p === "/api/v1/me" && method === "GET") return send(res, 200, { user, permissions: perms });

  if (p === "/api/v1/overview" && method === "GET") return need("view_status") && send(res, 200, overview(user));

  if (p === "/api/v1/events" && method === "GET") {
    if (!need("view_status")) return;
    if (Date.now() < refuseSseUntil) return err(res, 503, "event stream temporarily unavailable");
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
    res.write(": connected\n\n");
    const client = { res, user };
    sseClients.add(client);
    req.on("close", () => sseClients.delete(client));
    return;
  }

  if (p === "/api/v1/cameras") {
    if (method === "GET") return need("view_status") && send(res, 200, db.cameras);
    if (method === "POST") {
      if (!need("admin")) return;
      for (const f of ["id", "room_id", "name", "authorized_by", "authorized_at", "authorization_scope"]) {
        if (typeof body[f] !== "string" || !body[f].trim()) return err(res, 400, `${f} is required`);
      }
      if (db.cameras.some((c) => c.id === body.id)) return err(res, 409, `camera ${body.id} already exists`);
      const zerr = validateZones(body.zones ?? []);
      if (zerr) return err(res, 400, zerr);
      if (!db.rooms.some((r) => r.id === body.room_id)) db.rooms.push({ id: body.room_id, name: body.room_id });
      const c = {
        id: body.id, room_id: body.room_id, name: body.name, protected: !!body.protected, zones: body.zones ?? [],
        authorized_by: body.authorized_by, authorized_at: body.authorized_at, authorization_scope: body.authorization_scope,
        enabled: body.enabled !== false, state: "NOT_CONNECTED", state_since: iso(),
      };
      db.cameras.push(c);
      addAudit(user.username, "camera_registered", c.id, { authorized_by: c.authorized_by });
      broadcast("camera_state", { camera_id: c.id, state: c.state, since: c.state_since });
      return send(res, 201, c);
    }
  }

  if (p === "/api/v1/incidents" && method === "GET") {
    if (!need("view_status")) return;
    const wantShadow = url.searchParams.get("shadow") === "1";
    let list = visibleIncidents(user, wantShadow);
    if (url.searchParams.get("open") === "1") list = list.filter((i) => i.lifecycle !== "resolved");
    list = [...list].sort((a, b) => b.start.localeCompare(a.start));
    const limit = Number(url.searchParams.get("limit") ?? 100);
    return send(res, 200, list.slice(0, limit).map(summary));
  }

  const inc = /^\/api\/v1\/incidents\/([^/]+)(?:\/(ack|disposition|hold))?$/.exec(p);
  if (inc) {
    if (!need("view_status")) return;
    const i = db.incidents.find((x) => x.id === inc[1]);
    if (!i || (i.status === "SHADOW" && !perms.includes("view_shadow"))) return err(res, 404, "incident not found");
    const op = inc[2];
    if (!op && method === "GET") return send(res, 200, i);
    if (op === "ack" && method === "POST") {
      if (!need("acknowledge")) return;
      if (i.lifecycle === "new") {
        i.lifecycle = "acknowledged";
        i.acknowledged_by = user.username;
        i.acknowledged_at = iso();
        i.actions.push({ actor: user.username, action: "acknowledged", disposition: null, note: null, at: iso() });
        addAudit(user.username, "incident_acknowledged", i.id);
        broadcast("incident_updated", i, { shadow: i.status === "SHADOW" });
      }
      return send(res, 200, i);
    }
    if (op === "disposition" && method === "POST") {
      if (!need("disposition")) return;
      if (i.lifecycle === "resolved") return err(res, 409, "incident already resolved");
      if (!DISPOSITIONS.includes(body.disposition)) return err(res, 400, "invalid disposition");
      i.lifecycle = "resolved";
      i.disposition = body.disposition;
      i.resolved_by = user.username;
      i.resolved_at = iso();
      if (!i.acknowledged_at) {
        i.acknowledged_by = user.username;
        i.acknowledged_at = iso();
      }
      i.actions.push({ actor: user.username, action: "disposition", disposition: body.disposition, note: body.note || null, at: iso() });
      addAudit(user.username, "incident_disposition", i.id, { disposition: body.disposition });
      broadcast("incident_updated", i, { shadow: i.status === "SHADOW" });
      return send(res, 200, i);
    }
    if (op === "hold" && method === "POST") {
      if (!need("legal_hold")) return;
      i.legal_hold = !!body.hold;
      i.actions.push({ actor: user.username, action: i.legal_hold ? "legal_hold_set" : "legal_hold_released", disposition: null, note: null, at: iso() });
      addAudit(user.username, i.legal_hold ? "legal_hold_set" : "legal_hold_released", i.id);
      broadcast("incident_updated", i, { shadow: i.status === "SHADOW" });
      return send(res, 200, i);
    }
  }

  const clipTok = /^\/api\/v1\/clips\/([^/]+)\/token$/.exec(p);
  if (clipTok && method === "POST") {
    if (!need("view_clips")) return;
    const clip = db.incidents.flatMap((x) => x.clips).find((c) => c.id === clipTok[1]);
    if (!clip || clip.deleted_at) return err(res, 404, "clip not found");
    const t = crypto.randomBytes(16).toString("hex");
    const expires = Date.now() + 5 * MIN;
    tokens.set(t, { clipId: clip.id, expires, actor: user.username });
    addAudit(user.username, "clip_token_issued", clip.id);
    return send(res, 200, { url: `/api/v1/clips/${clip.id}/media?token=${t}`, expires_at: new Date(expires).toISOString() });
  }

  if (p === "/api/v1/users") {
    if (!need("admin")) return;
    if (method === "GET") return send(res, 200, db.users);
    if (method === "POST") {
      const { username, display_name, role, password } = body;
      if (!username || !/^[a-z0-9._-]{2,32}$/.test(username)) return err(res, 400, "username must be 2–32 characters: a–z, 0–9, . _ -");
      if (!PERMS[role]) return err(res, 400, "invalid role");
      if (typeof password !== "string" || password.length < 12) return err(res, 400, "password must be at least 12 characters");
      if (db.users.some((u) => u.username === username)) return err(res, 409, "username already exists");
      const u = { id: db.nextUserId++, username, display_name: display_name || username, role, disabled: false };
      db.users.push(u);
      addAudit(user.username, "user_created", username, { role });
      return send(res, 201, u);
    }
  }

  if (p === "/api/v1/audit" && method === "GET") {
    if (!need("admin")) return;
    const limit = Number(url.searchParams.get("limit") ?? 100);
    const entries = [...db.audit].reverse().slice(0, limit);
    return send(res, 200, { entries, chain_ok: !db.auditBroken, broken_at: db.auditBroken ? 7 : 0 });
  }

  return err(res, 404, "not found");
}

async function mockControl(req, res, p) {
  const raw = req.method === "POST" ? await readBody(req) : "";
  const body = raw ? JSON.parse(raw) : {};
  switch (p) {
    case "/__mock/reset":
      seed(body.scenario ?? "default");
      sessions = new Map();
      tokens = new Map();
      refuseSseUntil = 0;
      latencyMs = 0;
      for (const c of sseClients) c.res.end();
      sseClients = new Set();
      return send(res, 200, { ok: true });
    case "/__mock/scenario":
      seed(body.name ?? "default");
      return send(res, 200, { ok: true });
    case "/__mock/camera_state": {
      const c = db.cameras.find((x) => x.id === body.camera_id);
      if (!c) return err(res, 404, "no such camera");
      c.state = body.state;
      c.state_since = iso();
      broadcast("camera_state", { camera_id: c.id, state: c.state, since: c.state_since });
      return send(res, 200, c);
    }
    case "/__mock/incident": {
      const n = crypto.randomBytes(6).toString("hex");
      const i = incident({
        id: `INC-${n}`, room_id: body.room_id ?? "infants-a", room_zone: body.room_zone ?? "changing-table-1",
        event_type: body.event_type ?? "infant_off_surface", family: "A", agoStart: 2000, cameras: body.cameras ?? ["cam1"],
        status: body.status ?? "ALERTED", lifecycle: "new", detail: body.detail ?? "Infant appears below the surface and not held.",
        uncertainty: "low", visibility: "good",
      });
      db.incidents.unshift(i);
      const shadow = i.status === "SHADOW";
      broadcast("incident_created", i, { shadow });
      if (i.status === "ALERTED") broadcast("alert", { incident_id: i.id, room_id: i.room_id, event_type: i.event_type, level: 0 });
      return send(res, 200, i);
    }
    case "/__mock/emit":
      broadcast(body.type, body.data);
      return send(res, 200, { ok: true });
    case "/__mock/sse/drop":
      refuseSseUntil = Date.now() + Number(body.refuse_ms ?? 0);
      for (const c of sseClients) c.res.destroy();
      sseClients = new Set();
      return send(res, 200, { ok: true });
    case "/__mock/sessions/expire":
      sessions = new Map();
      for (const c of sseClients) c.res.destroy();
      sseClients = new Set();
      return send(res, 200, { ok: true });
    case "/__mock/latency":
      latencyMs = Number(body.ms ?? 0);
      return send(res, 200, { ok: true });
    case "/__mock/state":
      return send(res, 200, { ...db, sessions: sessions.size, sse_clients: sseClients.size });
    default:
      return err(res, 404, "unknown mock control");
  }
}

seed(process.env.SCENARIO ?? "default");
const server = http.createServer((req, res) => {
  handle(req, res).catch((e) => {
    console.error(e);
    if (!res.headersSent) err(res, 500, "mock server error");
  });
});
server.listen(PORT, HOST, () => {
  console.log(`zeoatap mock core listening on http://${HOST}:${PORT} (static: ${fs.existsSync(STATIC_DIR) ? STATIC_DIR : "none"})`);
});
