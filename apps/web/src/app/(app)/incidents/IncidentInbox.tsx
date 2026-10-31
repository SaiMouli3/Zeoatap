"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AckButton } from "@/components/AckButton";
import { Badge, EmptyState, ErrorState, Loading, PageHeader, SeverityBadge, Skeleton } from "@/components/ui";
import { useApp } from "@/lib/store";
import { LIFECYCLE_SHORT, STATUS_LABEL, eventLabel, formatTime, humanize, needsAck, sortIncidents } from "@/lib/present";
import type { Incident, IncidentStatus } from "@/lib/types";

type LifecycleFilter = "open" | "all" | "resolved";

function Row({ i, selected, roomName }: { i: Incident; selected: boolean; roomName: (id: string) => string }) {
  return (
    <li
      className={`inbox-row${selected ? " is-selected" : ""}${needsAck(i) && i.status === "ALERTED" ? " is-urgent" : ""}`}
      data-testid="incident-row"
      data-incident={i.id}
      data-lifecycle={i.lifecycle}
    >
      <Link href={`/incidents/?id=${encodeURIComponent(i.id)}`} className="inbox-link" aria-current={selected ? "true" : undefined}>
        <span className="inbox-top">
          <SeverityBadge incident={i} />
          <span className="tabular muted">{formatTime(i.start)}</span>
        </span>
        <span className="inbox-title">{eventLabel(i.event_type)}</span>
        <span className="inbox-meta muted">
          {roomName(i.room_id)} · {humanize(i.room_zone)}
        </span>
        <span className="inbox-chips">
          <span className="chip">{LIFECYCLE_SHORT[i.lifecycle]}</span>
          <span className="chip">{STATUS_LABEL[i.status]}</span>
          {i.escalation_level > 0 ? <span className="chip chip-strong">Escalated · level {i.escalation_level}</span> : null}
          {i.legal_hold ? <span className="chip">Legal hold</span> : null}
          {i.source === "MOCK" ? <span className="chip">Test data</span> : null}
        </span>
      </Link>
      {needsAck(i) ? (
        <div className="inbox-ack">
          <AckButton incident={i} />
        </div>
      ) : null}
    </li>
  );
}

export function IncidentInbox({ selectedId, initialRoom }: { selectedId: string | null; initialRoom: string | null }) {
  const { incidents, incidentsError, refreshIncidents, overview, can, showShadow, setShowShadow } = useApp();
  const [lifecycle, setLifecycle] = useState<LifecycleFilter>("open");
  const [status, setStatus] = useState<IncidentStatus | "all">("all");
  const [room, setRoom] = useState<string>(initialRoom ?? "all");

  const roomName = (id: string) => overview?.rooms.find((r) => r.id === id)?.name ?? id;
  const rooms = useMemo(() => {
    const ids = new Set<string>([...(overview?.rooms.map((r) => r.id) ?? []), ...(incidents?.map((i) => i.room_id) ?? [])]);
    return [...ids];
  }, [overview, incidents]);

  const filtered = useMemo(() => {
    if (!incidents) return null;
    return sortIncidents(
      incidents.filter((i) => {
        if (lifecycle === "open" && i.lifecycle === "resolved") return false;
        if (lifecycle === "resolved" && i.lifecycle !== "resolved") return false;
        if (status !== "all" && i.status !== status) return false;
        if (room !== "all" && i.room_id !== room) return false;
        return true;
      }),
    );
  }, [incidents, lifecycle, status, room]);

  const pinned = filtered?.filter(needsAck) ?? [];
  const rest = filtered?.filter((i) => !needsAck(i)) ?? [];
  const statuses: IncidentStatus[] = ["ALERTED", "REVIEW_QUEUED", "INCONCLUSIVE", ...(can("view_shadow") && showShadow ? (["SHADOW"] as IncidentStatus[]) : [])];
  const filtersActive = lifecycle !== "open" || status !== "all" || room !== "all";

  return (
    <>
      <PageHeader title="Incidents">
        <span className="muted">Automated observations for human review</span>
      </PageHeader>
      <div className="filters" role="group" aria-label="Filter incidents">
        <label className="field field-inline">
          <span>Show</span>
          <select value={lifecycle} onChange={(e) => setLifecycle(e.target.value as LifecycleFilter)} data-testid="filter-lifecycle">
            <option value="open">Open (new + acknowledged)</option>
            <option value="all">All recent</option>
            <option value="resolved">Resolved</option>
          </select>
        </label>
        <label className="field field-inline">
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as IncidentStatus | "all")} data-testid="filter-status">
            <option value="all">Any status</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span>Room</span>
          <select value={room} onChange={(e) => setRoom(e.target.value)} data-testid="filter-room">
            <option value="all">All rooms</option>
            {rooms.map((r) => (
              <option key={r} value={r}>
                {roomName(r)}
              </option>
            ))}
          </select>
        </label>
        {can("view_shadow") ? (
          <label className="check shadow-toggle">
            <input
              type="checkbox"
              checked={showShadow}
              onChange={(e) => {
                setShowShadow(e.target.checked);
                if (!e.target.checked && status === "SHADOW") setStatus("all");
              }}
              data-testid="toggle-shadow"
            />
            <span>Include shadow-mode incidents (hidden from room staff)</span>
          </label>
        ) : null}
      </div>

      {incidentsError && !incidents ? (
        <ErrorState title="Incidents could not be loaded" message={`${incidentsError} New alerts may still arrive through live updates.`} onRetry={() => void refreshIncidents()} />
      ) : filtered === null ? (
        <Loading label="Loading incidents">
          <ul className="inbox">
            {[0, 1, 2, 3].map((k) => (
              <li key={k} className="inbox-row">
                <Skeleton lines={3} />
              </li>
            ))}
          </ul>
        </Loading>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={filtersActive ? "No incidents match these filters" : "No open incidents"}
          action={
            filtersActive ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setLifecycle("open");
                  setStatus("all");
                  setRoom("all");
                }}
              >
                Clear filters
              </button>
            ) : null
          }
        >
          {filtersActive ? "Try a wider filter, such as “All recent”." : "Nothing is waiting for acknowledgement or review. Check the Overview for the monitoring status of each room."}
        </EmptyState>
      ) : (
        <>
          {pinned.length ? (
            <section aria-labelledby="pinned-h" className="inbox-section" data-testid="pinned-section">
              <h2 id="pinned-h" className="inbox-heading">
                <Badge tone="urgent" icon="triangle">
                  Unacknowledged · {pinned.length}
                </Badge>
              </h2>
              <ul className="inbox">
                {pinned.map((i) => (
                  <Row key={i.id} i={i} selected={i.id === selectedId} roomName={roomName} />
                ))}
              </ul>
            </section>
          ) : null}
          {rest.length ? (
            <section aria-labelledby="rest-h" className="inbox-section">
              <h2 id="rest-h" className="inbox-heading">
                {lifecycle === "resolved" ? "Resolved" : pinned.length ? "Other incidents" : "Incidents"}
              </h2>
              <ul className="inbox">
                {rest.map((i) => (
                  <Row key={i.id} i={i} selected={i.id === selectedId} roomName={roomName} />
                ))}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </>
  );
}
