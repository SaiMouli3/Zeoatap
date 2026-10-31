// Shared e2e harness: starts the mock core (serving the static export in ./out)
// and a Chromium instance. Uses the globally installed `playwright` package.

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PASSWORD = "zeoatap-demo";
export const USERS = {
  caregiver: { username: "carol", name: "Carol Diaz" },
  lead: { username: "leo", name: "Leo Park" },
  reviewer: { username: "rita", name: "Rita Okafor" },
  admin: { username: "ada", name: "Ada Mensah" },
};

export async function startMock({ port = 8190, pingMs = 2000 } = {}) {
  if (!fs.existsSync(path.join(ROOT, "out", "index.html"))) throw new Error("out/ missing: run `npx next build` first");
  const proc = spawn(process.execPath, ["mock/server.mjs"], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(port), STATIC_DIR: "out", PING_MS: String(pingMs) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  proc.stdout.on("data", (d) => (log += d));
  proc.stderr.on("data", (d) => (log += d));
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${base}/healthz`);
      if (r.ok) break;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  const control = async (p, body = {}) => {
    const r = await fetch(`${base}/__mock/${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`mock control ${p} failed: ${r.status}`);
    return r.json();
  };
  const state = async () => (await fetch(`${base}/__mock/state`)).json();
  return { base, proc, control, state, log: () => log, stop: () => proc.kill() };
}

export async function launch() {
  return chromium.launch({ headless: true });
}

export async function login(page, base, role) {
  const u = USERS[role];
  await page.goto(`${base}/login/`);
  await page.getByLabel("Username").fill(u.username);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByTestId("user-chip").filter({ hasText: u.name }).waitFor({ state: "attached" });
  await page.getByTestId("live-indicator").and(page.locator('[data-status="open"]')).waitFor({ state: "attached", timeout: 10_000 });
}
