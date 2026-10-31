"use client";

import { Icon } from "./Icon";
import { isStale, useApp, useNow } from "@/lib/store";
import { STATE_SHORT, STATE_PRESENTATION, ageSeconds, formatAge } from "@/lib/present";
import type { Overview, OverviewRoom } from "@/lib/types";

interface Problem {
  key: string;
  text: string;
  severe: boolean;
}

function roomProblem(r: OverviewRoom, now: number): Problem | null {
  if (r.state === "FULLY_OPERATIONAL") return null;
  const worst = r.cameras.filter((c) => c.state === r.state);
  const since = worst.map((c) => Date.parse(c.since)).filter((t) => !Number.isNaN(t)).sort((a, b) => a - b)[0];
  const dur = since ? ` (${formatAge(ageSeconds(since, now))})` : "";
  const cams = worst.length && worst.length < r.cameras.length ? ` — ${worst.map((c) => c.name).join(", ")}` : "";
  const what = r.cameras.length === 0 ? "No camera connected" : STATE_SHORT[r.state];
  const severe = STATE_PRESENTATION[r.state].tone === "unavailable";
  return { key: `room-${r.id}`, text: `${r.name}: ${what}${cams}${dur}${severe ? " — not monitored" : ""}`, severe };
}

export function serviceProblems(o: Overview): Problem[] {
  const out: Problem[] = [];
  if (o.services.engine !== "ok") out.push({ key: "engine", text: `Event engine error${o.services.engine_error ? `: ${o.services.engine_error}` : ""} — no automated prompts`, severe: true });
  if (o.services.database !== "ok") out.push({ key: "db", text: "Database unavailable — incident history delayed", severe: true });
  if (o.services.evidence !== "ok") out.push({ key: "evidence", text: "Evidence storage unavailable — clips may not be saved", severe: false });
  if (o.alerts_health.failed > 0) out.push({ key: "afail", text: `Alert delivery failing (${o.alerts_health.failed})`, severe: true });
  if (o.alerts_health.delayed > 0) out.push({ key: "adelay", text: `Alert delivery delayed (${o.alerts_health.delayed})`, severe: false });
  return out;
}

/** Full-width, non-dismissable. Shown on every signed-in page while any condition persists. */
export function MonitoringBanner() {
  const { overview, overviewAt, live, overviewError } = useApp();
  const now = useNow();
  const stale = isStale(overviewAt, live.status, now);

  if (!overview) {
    return (
      <section className="banner banner-unknown" aria-label="Monitoring status" data-testid="monitoring-banner" data-kind="loading">
        <Icon name="question" size={22} />
        <div>
          <p className="banner-title">{overviewError ? "Monitoring status unavailable" : "Checking monitoring status…"}</p>
          {overviewError ? <p>{overviewError} Do not rely on this screen until the status is shown.</p> : null}
        </div>
      </section>
    );
  }

  const rooms = overview.rooms.map((r) => roomProblem(r, now)).filter((p): p is Problem => p !== null);
  const services = serviceProblems(overview);
  if (!stale && rooms.length === 0 && services.length === 0) return null;

  const severe = stale || rooms.some((p) => p.severe) || services.some((p) => p.severe);
  const age = overviewAt ? formatAge(ageSeconds(overviewAt, now)) : "unknown";
  let title: string;
  if (stale) title = "Monitoring status cannot be confirmed";
  else if (rooms.length) title = rooms.some((p) => p.severe) ? "Monitoring unavailable" : "Monitoring degraded";
  else title = "System problem";

  return (
    <section
      className={`banner ${severe ? "banner-severe" : "banner-degraded"}`}
      aria-label="Monitoring warnings"
      data-testid="monitoring-banner"
      data-kind={stale ? "stale" : "problems"}
    >
      <Icon name={severe ? "triangle" : "half"} size={22} />
      <div className="banner-body">
        <p className="banner-title">
          {title}
          {rooms.length ? <span className="banner-count"> · {rooms.length} {rooms.length === 1 ? "room" : "rooms"} affected</span> : null}
        </p>
        <ul aria-live="polite">
          {stale ? (
            <li data-testid="banner-stale">
              {live.status !== "open"
                ? "Live updates disconnected — new alerts may not appear on this screen. Reconnecting…"
                : `No status update for ${age}. Check this device's network connection.`}
              {" "}Room states below are the last known values.
            </li>
          ) : null}
          {rooms.map((p) => (
            <li key={p.key} data-testid="banner-room">
              {p.text}
            </li>
          ))}
          {services.map((p) => (
            <li key={p.key} data-testid="banner-service">
              {p.text}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
