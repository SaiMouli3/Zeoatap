#!/usr/bin/env node
// Captures the main screens (light + dark, desktop + phone) into ./screenshots.
//   npx next build && node e2e/screenshots.mjs

import fs from "node:fs";
import path from "node:path";
import { launch, login, startMock, ROOT } from "./harness.mjs";

const OUT = path.join(ROOT, "screenshots");
fs.mkdirSync(OUT, { recursive: true });
const mock = await startMock({ port: 8191 });
const browser = await launch();
const base = mock.base;

const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 900 } },
  phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
};

const shots = [
  { name: "login", role: null, go: "/login/" },
  { name: "overview-degraded", role: "caregiver", go: "/", wait: "room-tile" },
  { name: "overview-all-ok", role: "caregiver", go: "/", wait: "room-tile", scenario: "all_ok" },
  {
    name: "overview-disconnected",
    role: "caregiver",
    go: "/",
    wait: "room-tile",
    scenario: "all_ok",
    after: async (page) => {
      await mock.control("sse/drop", { refuse_ms: 60_000 });
      await page.locator('[data-testid="live-indicator"][data-status="down"]').waitFor();
    },
  },
  { name: "incidents-inbox", role: "reviewer", go: "/incidents/", wait: "incident-row" },
  {
    name: "incident-detail",
    role: "reviewer",
    go: "/incidents/?id=INC-7f3a9c21b001",
    wait: "incident-detail",
    after: async (page) => {
      await page.getByTestId("clip-request").click();
      await page.getByTestId("clip-video").waitFor();
      await page.waitForTimeout(600);
    },
  },
  { name: "incident-resolved", role: "reviewer", go: "/incidents/?id=INC-4d2f11e9b004", wait: "incident-detail" },
  {
    name: "alert-toast",
    role: "caregiver",
    go: "/",
    wait: "room-tile",
    after: async (page) => {
      await mock.control("incident", { room_id: "toddlers-b", room_zone: "stairs-gate" });
      await page.getByTestId("toast").first().waitFor();
    },
  },
  { name: "cameras", role: "admin", go: "/cameras/", wait: "camera-row" },
  { name: "users", role: "admin", go: "/users/", wait: "user-row" },
  { name: "audit-ok", role: "admin", go: "/audit/", wait: "audit-row" },
  { name: "audit-broken", role: "admin", go: "/audit/", wait: "audit-row", scenario: "audit_broken" },
];

const written = [];
for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
  for (const scheme of ["light", "dark"]) {
    for (const s of shots) {
      await mock.control("reset", { scenario: s.scenario ?? "default" });
      const ctx = await browser.newContext({ ...vp, colorScheme: scheme, reducedMotion: "reduce" });
      const page = await ctx.newPage();
      if (s.role) await login(page, base, s.role);
      await page.goto(base + s.go);
      if (s.wait) await page.getByTestId(s.wait).first().waitFor();
      if (s.after) await s.after(page);
      await page.waitForTimeout(250);
      const file = path.join(OUT, `${s.name}-${vpName}-${scheme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      written.push(path.relative(ROOT, file));
      await ctx.close();
    }
  }
}
console.log(`${written.length} screenshots:\n  ${written.join("\n  ")}`);
await browser.close();
mock.stop();
