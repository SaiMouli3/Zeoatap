// Presentation rules: wording, tones and icons for states and incidents.
// Wording is neutral and non-accusatory: these are automated observations
// that need human review, never findings.

import type { Disposition, Incident, IncidentStatus, Lifecycle, MonitoringState } from "./types";

/** "unknown" is a client-side state: data is stale or the live stream is down. */
export type DisplayState = MonitoringState | "UNKNOWN";
export type Tone = "ok" | "degraded" | "unavailable" | "unknown" | "review" | "urgent" | "neutral";
export type IconName = "check" | "half" | "cross" | "question" | "bang" | "triangle" | "dot" | "pause";

export const STATE_RANK: Record<MonitoringState, number> = {
  CAMERA_UNAVAILABLE: 4,
  NOT_CONNECTED: 3,
  AI_UNAVAILABLE: 2,
  VIDEO_DEGRADED: 1,
  FULLY_OPERATIONAL: 0,
};

export function worstState(states: MonitoringState[], empty: MonitoringState = "NOT_CONNECTED"): MonitoringState {
  if (states.length === 0) return empty;
  return states.reduce((w, s) => (STATE_RANK[s] > STATE_RANK[w] ? s : w), "FULLY_OPERATIONAL" as MonitoringState);
}

interface StatePresentation {
  tone: Tone;
  icon: IconName;
  label: string; // short word(s) on the badge
  detail: string; // sentence for banners / tiles
}

export const STATE_PRESENTATION: Record<DisplayState, StatePresentation> = {
  FULLY_OPERATIONAL: { tone: "ok", icon: "check", label: "Fully monitored", detail: "All cameras and AI analysis are working." },
  VIDEO_DEGRADED: { tone: "degraded", icon: "half", label: "Degraded", detail: "Video quality is reduced. Detection may be less reliable." },
  AI_UNAVAILABLE: { tone: "unavailable", icon: "cross", label: "Not monitored", detail: "AI analysis is unavailable. No automated prompts will be raised." },
  NOT_CONNECTED: { tone: "unavailable", icon: "cross", label: "Not monitored", detail: "No camera is connected." },
  CAMERA_UNAVAILABLE: { tone: "unavailable", icon: "cross", label: "Not monitored", detail: "Camera offline." },
  UNKNOWN: { tone: "unknown", icon: "question", label: "Status unknown", detail: "Status cannot be confirmed: updates from the system have stopped." },
};

export const STATE_SHORT: Record<MonitoringState, string> = {
  FULLY_OPERATIONAL: "Fully operational",
  VIDEO_DEGRADED: "Video degraded",
  AI_UNAVAILABLE: "AI analysis unavailable",
  NOT_CONNECTED: "Not connected",
  CAMERA_UNAVAILABLE: "Camera offline",
};

// ------------------------------------------------------------------ incidents

const EVENT_LABELS: Record<string, string> = {
  infant_off_surface: "Possible infant off raised surface",
  adult_in_restricted_zone: "Possible person in restricted zone",
  infant_prone_sleep: "Possible change of sleep position",
};

export function eventLabel(type: string): string {
  return EVENT_LABELS[type] ?? `Possible event: ${humanize(type)}`;
}

export function humanize(s: string): string {
  const t = s.replace(/[_-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

interface SeverityPresentation {
  tone: Tone;
  icon: IconName;
  label: string;
  rank: number; // higher = more severe, used for sorting
}

/** Severity derives from status + lifecycle (the API has no separate severity field). */
export function severity(i: Pick<Incident, "status" | "lifecycle" | "escalation_level">): SeverityPresentation {
  if (i.status === "ALERTED") {
    if (i.lifecycle === "new") return { tone: "urgent", icon: "triangle", label: "Urgent alert", rank: 5 };
    if (i.lifecycle === "acknowledged") return { tone: "review", icon: "bang", label: "Alert · review required", rank: 4 };
    return { tone: "neutral", icon: "check", label: "Alert · resolved", rank: 1 };
  }
  if (i.status === "REVIEW_QUEUED") {
    return i.lifecycle === "resolved"
      ? { tone: "neutral", icon: "check", label: "Reviewed", rank: 1 }
      : { tone: "review", icon: "bang", label: "Review required", rank: 3 };
  }
  if (i.status === "INCONCLUSIVE") return { tone: "neutral", icon: "half", label: "Inconclusive", rank: i.lifecycle === "resolved" ? 1 : 2 };
  return { tone: "neutral", icon: "pause", label: "Shadow mode", rank: 0 };
}

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  ALERTED: "Alerted",
  REVIEW_QUEUED: "Review queued",
  SHADOW: "Shadow (hidden from room staff)",
  INCONCLUSIVE: "Inconclusive",
};

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = {
  new: "New — not yet acknowledged",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
};
export const LIFECYCLE_SHORT: Record<Lifecycle, string> = { new: "New", acknowledged: "Acknowledged", resolved: "Resolved" };

export const DISPOSITIONS: { value: Disposition; label: string; hint: string }[] = [
  { value: "no_concern", label: "No concern", hint: "Reviewed; nothing requiring action." },
  { value: "routine_care", label: "Routine care", hint: "Expected care activity (e.g. lifting, changing)." },
  { value: "false_detection", label: "False detection", hint: "The system misread the scene." },
  { value: "concern_followed_up", label: "Concern followed up", hint: "Followed up internally per facility policy." },
  { value: "escalated_external", label: "Escalated externally", hint: "Referred outside the facility per policy." },
  { value: "inconclusive", label: "Inconclusive", hint: "Evidence is insufficient to decide." },
];
export const DISPOSITION_LABEL = Object.fromEntries(DISPOSITIONS.map((d) => [d.value, d.label])) as Record<Disposition, string>;

export function needsAck(i: Pick<Incident, "lifecycle" | "status">): boolean {
  return i.lifecycle === "new" && i.status !== "SHADOW";
}

export function sortIncidents(list: Incident[]): Incident[] {
  return [...list].sort((a, b) => {
    const pa = needsAck(a) ? 1 : 0;
    const pb = needsAck(b) ? 1 : 0;
    if (pa !== pb) return pb - pa;
    const sa = severity(a).rank;
    const sb = severity(b).rank;
    if (sa !== sb) return sb - sa;
    return b.start.localeCompare(a.start);
  });
}

// ------------------------------------------------------------------ time

export function ageSeconds(isoOrMs: string | number, now: number): number {
  const t = typeof isoOrMs === "number" ? isoOrMs : Date.parse(isoOrMs);
  if (Number.isNaN(t)) return 0;
  return Math.max(0, Math.round((now - t) / 1000));
}

export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} days`;
}

const timeFmt = typeof Intl !== "undefined" ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : null;
const dateTimeFmt =
  typeof Intl !== "undefined"
    ? new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sameDay = new Date().toDateString() === d.toDateString();
  return (sameDay ? timeFmt : dateTimeFmt)?.format(d) ?? iso;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFmt?.format(d) ?? iso;
}

export function durationSeconds(start: string, end: string): number | null {
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round(Math.abs(b - a) / 100) / 10);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
