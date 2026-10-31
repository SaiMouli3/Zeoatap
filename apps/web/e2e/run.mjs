#!/usr/bin/env node
// End-to-end tests: static export (./out) served by the mock core + Chromium.
//   npx next build && node e2e/run.mjs            (optional: --only <substring>)

import assert from "node:assert/strict";
import { launch, login, startMock, USERS } from "./harness.mjs";

const only = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1] : null;
const T = [];
const test = (name, fn) => T.push({ name, fn });

const mock = await startMock({ port: 8190, pingMs: 2000 });
const browser = await launch();
const base = mock.base;

async function fresh(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, ...opts });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return { ctx, page, errors };
}

const tileTones = (page) =>
  page.$$eval('[data-testid="room-tile"]', (els) => els.map((e) => ({ room: e.getAttribute("data-room"), state: e.getAttribute("data-state"), tone: e.getAttribute("data-tone") })));

/** Green (tone ok) must only ever appear with state FULLY_OPERATIONAL. */
async function assertNeverFalseGreen(page) {
  const tones = await tileTones(page);
  for (const t of tones) if (t.tone === "ok") assert.equal(t.state, "FULLY_OPERATIONAL", `room ${t.room} shown green in state ${t.state}`);
  const camRows = await page.$$eval('[data-testid="room-tile"] .cam-state', (els) => els.map((e) => e.getAttribute("data-tone")));
  const anyStale = tones.some((t) => t.state === "UNKNOWN");
  if (anyStale) assert.ok(camRows.every((t) => t === "unknown"), "no green camera rows while stale");
  const badges = await page.$$eval('[data-testid="state-badge"]', (els) => els.map((e) => ({ tone: e.getAttribute("data-tone"), text: e.textContent })));
  for (const b of badges) if (b.tone === "ok") assert.match(b.text ?? "", /Fully monitored/);
  return tones;
}

// ------------------------------------------------------------------ tests

test("login per role, and bad password", async () => {
  for (const role of Object.keys(USERS)) {
    const { ctx, page, errors } = await fresh();
    await login(page, base, role);
    assert.equal(new URL(page.url()).pathname, "/");
    assert.deepEqual(errors, []);
    await ctx.close();
  }
  const { ctx, page } = await fresh();
  await page.goto(`${base}/login/`);
  await page.getByLabel("Username").fill("carol");
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByTestId("login-error").waitFor();
  assert.match(await page.getByTestId("login-error").innerText(), /Incorrect username or password/);
  await ctx.close();
});

test("unauthenticated visit redirects to login (no expiry message)", async () => {
  const { ctx, page } = await fresh();
  await page.goto(`${base}/incidents/`);
  await page.waitForURL(/\/login\//);
  assert.equal(await page.getByTestId("session-expired").count(), 0);
  await ctx.close();
});

test("nav visibility per role", async () => {
  const expected = {
    caregiver: ["overview", "incidents"],
    lead: ["overview", "incidents"],
    reviewer: ["overview", "incidents"],
    admin: ["overview", "incidents", "cameras", "users", "audit"],
  };
  for (const [role, items] of Object.entries(expected)) {
    const { ctx, page } = await fresh();
    await login(page, base, role);
    const shown = await page.$$eval('[data-testid^="nav-"]', (els) => els.map((e) => e.getAttribute("data-testid").slice(4)));
    assert.deepEqual(shown, items, `nav for ${role}`);
    if (role !== "admin") {
      await page.goto(`${base}/audit/`);
      await page.getByText("This page is for administrators").waitFor();
    }
    await ctx.close();
  }
});

test("banner lists non-operational rooms; never green when degraded", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  const banner = page.getByTestId("monitoring-banner");
  await banner.waitFor();
  const text = await banner.innerText();
  for (const r of ["Infants B", "Toddlers B", "Sleep room", "Garden room"]) assert.ok(text.includes(r), `banner mentions ${r}`);
  assert.ok(!text.includes("Infants A"), "banner must not list the fully operational room");
  assert.match(text, /Alert delivery failing/);
  assert.equal(await banner.getByRole("button").count(), 0, "banner has no dismiss control");
  await page.getByTestId("room-tile").first().waitFor();
  const tones = await assertNeverFalseGreen(page);
  const byRoom = Object.fromEntries(tones.map((t) => [t.room, t]));
  assert.equal(byRoom["infants-a"].tone, "ok");
  assert.equal(byRoom["infants-b"].tone, "degraded");
  assert.equal(byRoom["toddlers-b"].tone, "unavailable");
  assert.equal(byRoom["sleep-room"].tone, "unavailable");
  assert.equal(byRoom["garden-room"].state, "NOT_CONNECTED");
  assert.match(await page.getByTestId("staleness").first().innerText(), /Last update \d+s ago/);

  // live camera_state event turns the green room red without reload
  await mock.control("camera_state", { camera_id: "cam1", state: "CAMERA_UNAVAILABLE" });
  await page.locator('[data-room="infants-a"][data-tone="unavailable"]').waitFor({ timeout: 5000 });
  assert.ok((await banner.innerText()).includes("Infants A"));
  await assertNeverFalseGreen(page);
  await ctx.close();
});

test("all operational: no banner, green tiles", async () => {
  await mock.control("reset", { scenario: "all_ok" });
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  await page.getByTestId("room-tile").first().waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="monitoring-banner"]'));
  const tones = await assertNeverFalseGreen(page);
  assert.ok(tones.length >= 4 && tones.every((t) => t.tone === "ok"));
  await ctx.close();
});

test("stream down: stale banner, nothing green, disconnected indicator, re-fetch after reconnect", async () => {
  await mock.control("reset", { scenario: "all_ok" });
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  await page.getByTestId("room-tile").first().waitFor();
  await mock.control("sse/drop", { refuse_ms: 4000 });
  const ind = page.locator('[data-testid="live-indicator"][data-status="down"]');
  await ind.waitFor({ timeout: 5000 });
  assert.match(await ind.innerText(), /Live updates disconnected/);
  await page.getByTestId("banner-stale").waitFor();
  const tones = await assertNeverFalseGreen(page);
  assert.ok(tones.every((t) => t.state === "UNKNOWN" && t.tone === "unknown"), "all tiles unknown while stream is down");
  // an incident created while disconnected is never pushed; it must appear via re-fetch after reconnect
  const inc = await mock.control("incident", { room_id: "infants-a", silent: true });
  await page.locator('[data-testid="live-indicator"][data-status="open"]').waitFor({ timeout: 20_000 });
  await page.locator(`[data-testid="attention-item"][data-incident="${inc.id}"]`).waitFor({ timeout: 5000 });
  await page.waitForFunction(() => !document.querySelector('[data-testid="monitoring-banner"]'));
  await ctx.close();
});

test("one-tap acknowledge", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  const item = page.locator('[data-testid="attention-item"][data-incident="INC-7f3a9c21b001"]');
  await item.waitFor();
  const t0 = Date.now();
  await item.getByTestId("ack-button").click();
  await item.waitFor({ state: "detached", timeout: 3000 });
  const elapsed = Date.now() - t0;
  const s = await mock.state();
  const inc = s.incidents.find((i) => i.id === "INC-7f3a9c21b001");
  assert.equal(inc.lifecycle, "acknowledged");
  assert.equal(inc.acknowledged_by, "carol");
  await page.goto(`${base}/incidents/?id=INC-7f3a9c21b001`);
  await page.getByTestId("ack-info").waitFor();
  assert.match(await page.getByTestId("ack-info").innerText(), /Acknowledged by carol/);
  console.log(`    (ack round-trip in UI: ${elapsed} ms)`);
  await ctx.close();
});

test("acknowledge from incident inbox (pinned section)", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "lead");
  await page.getByTestId("nav-incidents").click();
  const pinned = page.getByTestId("pinned-section");
  await pinned.waitFor();
  assert.equal(await pinned.getByTestId("incident-row").count(), 1);
  await pinned.getByTestId("ack-button").click();
  await pinned.waitFor({ state: "detached" });
  await page.locator('[data-incident="INC-7f3a9c21b001"][data-lifecycle="acknowledged"]').waitFor();
  await ctx.close();
});

test("disposition flow: final, then disabled", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "reviewer");
  await page.goto(`${base}/incidents/?id=INC-91c0de55a003`);
  await page.getByTestId("not-a-finding").waitFor();
  assert.equal(await page.getByTestId("not-a-finding").innerText(), "This is an automated observation requiring human review. It is not a finding.");
  assert.match(await page.getByTestId("model-versions").innerText(), /detector/);
  const form = page.getByTestId("disposition-form");
  await form.getByLabel(/No concern/).check();
  await form.getByLabel("Note (optional)").fill("Reviewed clip; infant settled in cot.");
  await form.getByTestId("disposition-submit").click();
  await form.getByTestId("disposition-confirm").click();
  await page.locator('[data-testid="incident-detail"][data-lifecycle="resolved"]').waitFor();
  await form.getByTestId("disposition-final").waitFor();
  assert.ok(await form.locator('input[type="radio"]').first().isDisabled(), "radios disabled after resolve");
  assert.match(await page.getByTestId("timeline").innerText(), /Disposition recorded: No concern/);
  const s = await mock.state();
  assert.equal(s.incidents.find((i) => i.id === "INC-91c0de55a003").disposition, "no_concern");
  await ctx.close();
});

test("disposition 409 when another reviewer resolved first", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "reviewer");
  await page.goto(`${base}/incidents/?id=INC-2b8e44d0c002`);
  const form = page.getByTestId("disposition-form");
  await form.getByLabel(/Routine care/).check();
  await mock.control("resolve", { id: "INC-2b8e44d0c002", disposition: "false_detection", actor: "other" });
  await form.getByTestId("disposition-submit").click();
  await form.getByTestId("disposition-confirm").click();
  await form.getByTestId("disposition-error").waitFor();
  assert.match(await form.getByTestId("disposition-error").innerText(), /Another reviewer resolved this incident first/);
  await form.getByTestId("disposition-final").waitFor();
  assert.match(await form.getByTestId("disposition-final").innerText(), /False detection/);
  assert.ok(await form.locator('input[type="radio"]').first().isDisabled(), "radios disabled after resolve");
  await ctx.close();
});

test("legal hold toggle (reviewer)", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "reviewer");
  await page.goto(`${base}/incidents/?id=INC-91c0de55a003`);
  const sw = page.getByTestId("legal-hold");
  await sw.waitFor();
  assert.equal(await sw.getAttribute("aria-checked"), "false");
  await sw.click();
  await page.locator('[data-testid="legal-hold"][aria-checked="true"]').waitFor();
  assert.equal((await mock.state()).incidents.find((i) => i.id === "INC-91c0de55a003").legal_hold, true);
  await ctx.close();
});

test("clip playback via token (lead); caregiver sees no clip", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "lead");
  await page.goto(`${base}/incidents/?id=INC-7f3a9c21b001`);
  await page.getByTestId("clip-logged").waitFor();
  assert.match(await page.getByTestId("clip-logged").innerText(), /Access to this clip is logged/);
  const tokenReq = page.waitForResponse((r) => r.url().includes("/api/v1/clips/") && r.url().endsWith("/token") && r.request().method() === "POST");
  await page.getByTestId("clip-request").click();
  assert.equal((await tokenReq).status(), 200);
  const video = page.getByTestId("clip-video");
  await video.waitFor();
  const src = await video.getAttribute("src");
  assert.match(src, /^\/api\/v1\/clips\/INC-7f3a9c21b001-cam3\/media\?token=[0-9a-f]+$/);
  const ready = await page.waitForFunction(() => document.querySelector('[data-testid="clip-video"]')?.readyState >= 1, null, { timeout: 8000 }).then(() => true, () => false);
  console.log(`    (video metadata loaded: ${ready})`);
  const s = await mock.state();
  assert.ok(s.audit.some((a) => a.action === "clip_viewed" && a.actor === "leo"), "clip view audit-logged");
  await ctx.close();

  const c2 = await fresh();
  await login(c2.page, base, "caregiver");
  await c2.page.goto(`${base}/incidents/?id=INC-7f3a9c21b001`);
  await c2.page.getByTestId("clip-locked").waitFor();
  assert.equal(await c2.page.getByTestId("disposition-form").count(), 0);
  assert.equal(await c2.page.getByTestId("legal-hold").count(), 0);
  await c2.ctx.close();
});

test("shadow incidents: hidden by default, opt-in toggle only for view_shadow roles", async () => {
  const c1 = await fresh();
  await login(c1.page, base, "caregiver");
  await c1.page.goto(`${base}/incidents/`);
  await c1.page.getByTestId("incident-row").first().waitFor();
  assert.equal(await c1.page.getByTestId("toggle-shadow").count(), 0);
  assert.equal(await c1.page.locator('[data-incident="INC-c05e7a8fd005"]').count(), 0);
  await c1.page.goto(`${base}/incidents/?id=INC-c05e7a8fd005`);
  await c1.page.getByText("Incident not found").waitFor();
  await c1.ctx.close();

  const { ctx, page } = await fresh();
  await login(page, base, "reviewer");
  await page.goto(`${base}/incidents/`);
  await page.getByTestId("incident-row").first().waitFor();
  assert.equal(await page.locator('[data-incident="INC-c05e7a8fd005"]').count(), 0, "not shown before opting in");
  const req = page.waitForRequest((r) => r.url().includes("/api/v1/incidents?") && r.url().includes("shadow=1"));
  await page.getByTestId("toggle-shadow").check();
  await req;
  await page.locator('[data-testid="incident-row"][data-incident="INC-c05e7a8fd005"]').waitFor();
  await ctx.close();
});

test("clip deleted under retention (410) and audit failure (503)", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "reviewer");
  await page.goto(`${base}/incidents/?id=INC-e1aa0b77c006`);
  await page.getByTestId("clip-deleted").waitFor(); // deleted_at already set: no token requested
  await page.goto(`${base}/incidents/?id=INC-7f3a9c21b001`);
  await page.getByTestId("clip-request").waitFor();
  await mock.control("clip_delete", { id: "INC-7f3a9c21b001-cam3" });
  await page.getByTestId("clip-request").click();
  await page.getByTestId("clip-deleted").waitFor();
  assert.match(await page.getByTestId("clip-deleted").innerText(), /deleted under the retention policy/);
  await mock.control("audit_fail", { on: true });
  await page.getByRole("button", { name: "cam4" }).click();
  await page.getByTestId("clip-request").click();
  await page.getByText(/could not be recorded in the audit log/).waitFor();
  await ctx.close();
});

test("engine error: rooms shown as AI unavailable, never green", async () => {
  await mock.control("reset", { scenario: "all_ok" });
  await mock.control("services", { engine: "error", engine_error: "worker heartbeat lost" });
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  await page.getByTestId("room-tile").first().waitFor();
  await page.locator('[data-testid="banner-service"]', { hasText: "worker heartbeat lost" }).waitFor();
  const tones = await assertNeverFalseGreen(page);
  assert.ok(tones.every((t) => t.state === "AI_UNAVAILABLE"));
  await ctx.close();
});

test("new incident appears live via SSE, with alert toast", async () => {
  const { ctx, page, errors } = await fresh();
  await login(page, base, "caregiver");
  await page.goto(`${base}/incidents/`);
  await page.getByTestId("incident-row").first().waitFor();
  await page.getByTestId("sound-toggle").click(); // user gesture enables WebAudio chime
  await page.locator('[data-testid="sound-toggle"][aria-pressed="true"]').waitFor();
  const inc = await mock.control("incident", { room_id: "toddlers-b", room_zone: "stairs-gate" });
  await page.locator(`[data-testid="incident-row"][data-incident="${inc.id}"]`).waitFor({ timeout: 4000 });
  const toast = page.getByTestId("toast").filter({ hasText: "Toddlers B" });
  await toast.waitFor();
  assert.match(await toast.innerText(), /Possible infant off raised surface/);
  assert.equal(await page.getByTestId("pinned-section").getByTestId("incident-row").count(), 2);
  assert.deepEqual(errors, []);
  await ctx.close();
});

test("session expiry (401) returns to login with a message", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "caregiver");
  await page.goto(`${base}/incidents/`);
  await page.getByTestId("incident-row").first().waitFor();
  await mock.control("sessions/expire");
  await page.waitForURL(/\/login\/\?/, { timeout: 10_000 });
  await page.getByTestId("session-expired").waitFor();
  assert.match(page.url(), /expired=1/);
  assert.match(decodeURIComponent(page.url()), /next=\/incidents\//);
  // signing in again returns to where the user was
  await page.getByLabel("Username").fill("carol");
  await page.getByLabel("Password").fill("zeoatap-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/incidents\/$/);
  await ctx.close();
});

test("admin: camera registration requires authorization; zones JSON validated", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "admin");
  await page.getByTestId("nav-cameras").click();
  await page.getByTestId("camera-row").first().waitFor();
  const before = await page.getByTestId("camera-row").count();
  const form = page.getByTestId("camera-form");
  await form.getByLabel(/Camera ID/).fill("cam7");
  await form.getByLabel(/Camera name/).fill("Nap corner");
  await form.getByLabel(/Room ID/).fill("infants-a");
  await form.getByTestId("camera-submit").click();
  await form.getByText("Complete the required fields").waitFor();
  assert.ok((await form.getByText("Required").count()) >= 2);
  await form.getByTestId("zones-json").fill('[{"id":"z","room_zone":"r","kind":"surface","polygon":[{"X":0,"Y":0},{"X":2,"Y":0}]}]');
  assert.match(await form.getByTestId("zones-status").innerText(), /at least 3 points/);
  await form.getByTestId("zones-json").fill("[{oops");
  assert.match(await form.getByTestId("zones-status").innerText(), /Not valid JSON/);
  await form.getByTestId("zones-json").fill('[{"id":"nap","room_zone":"nap-corner","kind":"sleep","polygon":[{"X":0.1,"Y":0.1},{"X":0.5,"Y":0.1},{"X":0.5,"Y":0.5}]}]');
  assert.match(await form.getByTestId("zones-status").innerText(), /1 zone valid/);
  await form.getByLabel(/Authorized by/).fill("M. Rossi, Facility director");
  await form.getByLabel(/Authorization scope/).fill("Infants A nap corner, operating hours");
  await form.getByTestId("camera-submit").click();
  await page.waitForFunction((n) => document.querySelectorAll('[data-testid="camera-row"]').length === n + 1, before);
  const cam = (await mock.state()).cameras.find((c) => c.id === "cam7");
  assert.equal(cam.authorized_by, "M. Rossi, Facility director");
  assert.equal(cam.zones[0].kind, "sleep");
  await ctx.close();
});

test("admin: users list + create", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "admin");
  await page.goto(`${base}/users/`);
  await page.getByTestId("user-row").first().waitFor();
  const form = page.getByTestId("user-form");
  await form.getByLabel(/Username/).fill("newlead");
  await form.getByLabel("Display name").fill("New Lead");
  await form.getByLabel(/Room lead/).check();
  await form.getByLabel(/Initial password/).fill("short");
  assert.match(await form.innerText(), /At least 12 characters/);
  await form.getByLabel(/Initial password/).fill("a-long-enough-password");
  await form.getByTestId("user-submit").click();
  await page.getByTestId("user-row").filter({ hasText: "newlead" }).waitFor();
  assert.equal((await mock.state()).users.find((u) => u.username === "newlead").role, "lead");
  await ctx.close();
});

test("admin: audit chain status ok and broken", async () => {
  const { ctx, page } = await fresh();
  await login(page, base, "admin");
  await page.goto(`${base}/audit/`);
  await page.locator('[data-testid="chain-status"][data-ok="true"]').waitFor();
  assert.ok((await page.getByTestId("audit-row").count()) > 5);
  await mock.control("scenario", { name: "audit_broken" });
  await page.getByRole("button", { name: "Refresh" }).click();
  const broken = page.locator('[data-testid="chain-status"][data-ok="false"]');
  await broken.waitFor();
  assert.match(await broken.innerText(), /Audit chain broken at entry #7/);
  await ctx.close();
});

test("phone width: single column, bottom nav, alert-first", async () => {
  const { ctx, page } = await fresh({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  await login(page, base, "caregiver");
  await page.getByTestId("attention-item").first().waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `no horizontal scroll (overflow ${overflow}px)`);
  assert.ok(await page.locator(".bottomnav").isVisible());
  assert.ok(!(await page.locator(".sidenav").isVisible()));
  const ackBox = await page.getByTestId("ack-button").first().boundingBox();
  assert.ok(ackBox.height >= 44, "ack touch target >= 44px");
  const attY = (await page.getByTestId("attention-item").first().boundingBox()).y;
  const tileY = (await page.getByTestId("room-tile").first().boundingBox()).y;
  assert.ok(attY < tileY, "alerts come before room tiles");
  await ctx.close();
});

// ------------------------------------------------------------------ runner

let pass = 0;
let fail = 0;
const t0 = Date.now();
for (const t of T) {
  if (only && !t.name.includes(only)) continue;
  await mock.control("reset");
  const s = Date.now();
  try {
    await t.fn();
    pass++;
    console.log(`  ✓ ${t.name} (${Date.now() - s} ms)`);
  } catch (e) {
    fail++;
    console.log(`  ✗ ${t.name}\n    ${String(e?.stack ?? e).split("\n").slice(0, 6).join("\n    ")}`);
  }
}
console.log(`\n${pass} passed, ${fail} failed (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
await browser.close();
mock.stop();
process.exit(fail ? 1 : 0);
