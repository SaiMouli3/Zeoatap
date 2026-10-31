"use client";

import Link from "next/link";
import { AckButton } from "@/components/AckButton";
import { Icon } from "@/components/Icon";
import { EmptyState, ErrorState, Loading, PageHeader, SeverityBadge, Skeleton, StateBadge } from "@/components/ui";
import { isStale, useApp, useNow } from "@/lib/store";
import {
  STATE_PRESENTATION,
  STATE_SHORT,
  ageSeconds,
  eventLabel,
  formatAge,
  formatTime,
  humanize,
  needsAck,
  sortIncidents,
  type DisplayState,
} from "@/lib/present";
import type { Incident, Overview, OverviewRoom } from "@/lib/types";

function RoomTile({ room, stale, ageS, open }: { room: OverviewRoom; stale: boolean; ageS: number; open: Incident[] }) {
  const now = useNow();
  const shown: DisplayState = stale ? "UNKNOWN" : room.state;
  const p = STATE_PRESENTATION[shown];
  return (
    <article className={`tile tile-${p.tone}`} data-testid="room-tile" data-room={room.id} data-state={shown} data-tone={p.tone} aria-labelledby={`room-${room.id}`}>
      <header className="tile-head">
        <h3 id={`room-${room.id}`}>{room.name}</h3>
        <StateBadge state={shown} />
      </header>
      <p className="tile-detail">
        {stale
          ? `Last known: ${STATE_SHORT[room.state]}. ${p.detail}`
          : room.cameras.length === 0
            ? "No camera connected — this room is not monitored."
            : STATE_PRESENTATION[room.state].detail}
      </p>
      {room.cameras.length ? (
        <ul className="cam-list">
          {room.cameras.map((c) => (
            <li key={c.id}>
              <span className="cam-name">{c.name}</span>
              {stale ? (
                // Never green while the status is unconfirmed: show last-known values neutrally.
                <span className="cam-state tone-text-unknown" data-tone="unknown">
                  <Icon name="question" size={14} /> Last known: {STATE_SHORT[c.state]}
                </span>
              ) : (
                <span className={`cam-state tone-text-${STATE_PRESENTATION[c.state].tone}`} data-tone={STATE_PRESENTATION[c.state].tone}>
                  <Icon name={STATE_PRESENTATION[c.state].icon} size={14} /> {STATE_SHORT[c.state]}
                  <span className="muted"> · {formatAge(ageSeconds(c.since, now))}</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <footer className="tile-foot">
        <span className={`staleness${stale ? " is-stale" : ""}`} data-testid="staleness">
          Last update {formatAge(ageS)} ago
        </span>
        {open.length ? (
          <Link href={`/incidents/?room=${encodeURIComponent(room.id)}`} className="tile-open">
            {open.length} open {open.length === 1 ? "incident" : "incidents"}
          </Link>
        ) : (
          <span className="muted">No open incidents</span>
        )}
      </footer>
    </article>
  );
}

function AttentionList({ incidents, roomName }: { incidents: Incident[]; roomName: (id: string) => string }) {
  if (incidents.length === 0) return null;
  return (
    <section aria-labelledby="attention-h" className="section">
      <h2 id="attention-h">Needs acknowledgement ({incidents.length})</h2>
      <ul className="attention-list">
        {incidents.map((i) => (
          <li key={i.id} className={`attention attention-${i.status === "ALERTED" ? "urgent" : "review"}`} data-testid="attention-item" data-incident={i.id}>
            <div className="attention-main">
              <SeverityBadge incident={i} />
              <p className="attention-title">{eventLabel(i.event_type)}</p>
              <p className="muted">
                {roomName(i.room_id)} · {humanize(i.room_zone)} · <span className="tabular">{formatTime(i.start)}</span>
                {i.escalation_level > 0 ? ` · escalated (level ${i.escalation_level})` : ""}
              </p>
            </div>
            <div className="attention-actions">
              <AckButton incident={i} />
              <Link className="btn btn-secondary" href={`/incidents/?id=${encodeURIComponent(i.id)}`}>
                View
              </Link>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ServiceRow({ label, ok, okText, badText, testId }: { label: string; ok: boolean; okText: string; badText: string; testId: string }) {
  return (
    <li className="svc-row" data-testid={testId} data-ok={ok}>
      <span>{label}</span>
      <span className={`svc-state ${ok ? "svc-ok" : "svc-bad"}`}>
        <Icon name={ok ? "check" : "cross"} size={16} /> {ok ? okText : badText}
      </span>
    </li>
  );
}

function ServicesHealth({ o }: { o: Overview }) {
  const s = o.services;
  return (
    <section className="card" aria-labelledby="svc-h" data-testid="services-health">
      <h2 id="svc-h">System health</h2>
      <ul className="svc-list">
        <ServiceRow testId="svc-engine" label="Event engine" ok={s.engine === "ok"} okText="Running" badText={`Error${s.engine_error ? `: ${s.engine_error}` : ""}`} />
        <ServiceRow testId="svc-database" label="Database" ok={s.database === "ok"} okText="Available" badText="Unavailable — history delayed" />
        <ServiceRow testId="svc-evidence" label="Evidence storage" ok={s.evidence === "ok"} okText="Available" badText="Unavailable — clips may not be saved" />
        <ServiceRow testId="svc-delivery-failed" label="Alert delivery failures" ok={o.alerts_health.failed === 0} okText="None" badText={`${o.alerts_health.failed} failing`} />
        <ServiceRow testId="svc-delivery-delayed" label="Alert delivery delays" ok={o.alerts_health.delayed === 0} okText="None" badText={`${o.alerts_health.delayed} delayed`} />
      </ul>
      <p className="muted small">Status generated {formatTime(o.generated_at)}.</p>
    </section>
  );
}

export default function OverviewPage() {
  const { overview, overviewAt, overviewError, refreshOverview, incidents, live } = useApp();
  const now = useNow();
  const stale = isStale(overviewAt, live.status, now);
  const ageS = overviewAt ? ageSeconds(overviewAt, now) : 0;

  if (!overview) {
    return (
      <>
        <PageHeader title="Overview" />
        {overviewError ? (
          <ErrorState title="Monitoring status could not be loaded" message={`${overviewError} Until it loads, treat every room as not monitored.`} onRetry={() => void refreshOverview()} />
        ) : (
          <Loading label="Loading room status">
            <div className="tiles">
              {[0, 1, 2].map((k) => (
                <div className="tile tile-skeleton" key={k}>
                  <Skeleton lines={4} />
                </div>
              ))}
            </div>
          </Loading>
        )}
      </>
    );
  }

  const roomName = (id: string) => overview.rooms.find((r) => r.id === id)?.name ?? id;
  const open = (incidents ?? []).filter((i) => i.lifecycle !== "resolved" && i.status !== "SHADOW");
  const attention = sortIncidents(open.filter(needsAck));
  const okCount = overview.rooms.filter((r) => r.state === "FULLY_OPERATIONAL").length;

  return (
    <>
      <PageHeader title="Overview">
        <span className="muted" data-testid="overview-summary">
          {stale ? "Status unknown — updates stopped" : `${okCount} of ${overview.rooms.length} rooms fully monitored`} · {overview.open_alerts} open
        </span>
      </PageHeader>
      <AttentionList incidents={attention} roomName={roomName} />
      <div className="overview-grid">
        <section aria-labelledby="rooms-h" className="section">
          <h2 id="rooms-h">Rooms</h2>
          {overview.rooms.length === 0 ? (
            <EmptyState title="No rooms are set up yet">An administrator can register cameras under Cameras. Until then, nothing is being monitored.</EmptyState>
          ) : (
            <div className="tiles">
              {overview.rooms.map((r) => (
                <RoomTile key={r.id} room={r} stale={stale} ageS={ageS} open={open.filter((i) => i.room_id === r.id)} />
              ))}
            </div>
          )}
        </section>
        <aside className="overview-side">
          <ServicesHealth o={overview} />
          <section className="card" aria-labelledby="legend-h">
            <h2 id="legend-h">What the states mean</h2>
            <ul className="legend">
              {(["FULLY_OPERATIONAL", "VIDEO_DEGRADED", "CAMERA_UNAVAILABLE", "UNKNOWN"] as DisplayState[]).map((s) => (
                <li key={s}>
                  <StateBadge state={s} />
                  <span className="muted small">{s === "CAMERA_UNAVAILABLE" ? "Camera offline, not connected, or AI analysis unavailable." : STATE_PRESENTATION[s].detail}</span>
                </li>
              ))}
            </ul>
            <p className="muted small">Zeoatap supports supervision. It never replaces it, even when every room is fully monitored.</p>
          </section>
        </aside>
      </div>
    </>
  );
}
