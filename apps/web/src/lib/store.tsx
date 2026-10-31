"use client";

// Signed-in application state: who am I, facility overview, incident list,
// the live event stream (with reconnect + full re-fetch), toasts and sound.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { eventLabel, worstState } from "./present";
import { enableSound, disableSound, playChime, soundSupported } from "./sound";
import type {
  AlertEvent,
  CameraStateEvent,
  DeliveryFailedEvent,
  EscalatedEvent,
  Incident,
  Me,
  Overview,
  Permission,
} from "./types";

/** Overview is polled this often, in addition to live events. */
export const OVERVIEW_POLL_MS = 5_000;
/** Older than this (or live stream down) → status shown as unknown, never green. */
export const STALE_MS = 15_000;
const INCIDENT_POLL_MS = 30_000;
const MAX_BACKOFF_MS = 15_000;

export type LiveStatus = "connecting" | "open" | "down";

export const SSE_TYPES = [
  "camera_state",
  "incident_created",
  "incident_merged",
  "incident_updated",
  "alert",
  "incident_escalated",
  "alert_delivery_failed",
  "clip_ready",
  "clip_failed",
] as const;
export type SseType = (typeof SSE_TYPES)[number];
export type SseHandler = (type: SseType, data: unknown) => void;

export interface Toast {
  id: number;
  tone: "urgent" | "review" | "degraded" | "neutral";
  title: string;
  body?: string;
  href?: string;
  sticky?: boolean;
}

interface AppState {
  me: Me;
  can: (p: Permission) => boolean;
  overview: Overview | null;
  overviewAt: number | null;
  overviewError: string | null;
  incidents: Incident[] | null;
  incidentsError: string | null;
  refreshOverview: () => Promise<void>;
  refreshIncidents: () => Promise<void>;
  refreshAll: () => Promise<void>;
  upsertIncident: (i: Incident) => void;
  live: { status: LiveStatus; downSince: number | null; reconnectAt: number | null };
  reconnectNow: () => void;
  subscribe: (h: SseHandler) => () => void;
  toasts: Toast[];
  pushToast: (t: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
  sound: { supported: boolean; enabled: boolean; toggle: () => Promise<void> };
  logout: () => Promise<void>;
  alertPulse: number; // increments on each alert, drives a visual pulse
}

const Ctx = createContext<AppState | null>(null);
const NowCtx = createContext<number>(0);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

/** Current time, ticking once per second (for "Ns ago" displays). */
export function useNow(): number {
  return useContext(NowCtx);
}

export function isStale(overviewAt: number | null, live: LiveStatus, now: number): boolean {
  return overviewAt === null || now - overviewAt > STALE_MS || live !== "open";
}

function mergeIncident(list: Incident[], inc: Incident): Incident[] {
  const idx = list.findIndex((x) => x.id === inc.id);
  if (idx === -1) return [inc, ...list];
  const next = list.slice();
  next[idx] = { ...list[idx], ...inc };
  return next;
}

export function AppProvider({ me, onSignedOut, children }: { me: Me; onSignedOut: (expired: boolean) => void; children: ReactNode }) {
  const perms = useMemo(() => new Set(me.permissions), [me.permissions]);
  const can = useCallback((p: Permission) => perms.has(p), [perms]);

  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewAt, setOverviewAt] = useState<number | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [incidentsError, setIncidentsError] = useState<string | null>(null);
  const [live, setLive] = useState<AppState["live"]>({ status: "connecting", downSince: null, reconnectAt: null });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [soundOk, setSoundOk] = useState(false);
  const [alertPulse, setAlertPulse] = useState(0);

  const soundRef = useRef(false);
  const handlers = useRef(new Set<SseHandler>());
  const toastSeq = useRef(0);
  const signedOut = useRef(false);
  const reconnectRef = useRef<() => void>(() => {});

  useEffect(() => {
    setSoundOk(soundSupported());
  }, []);

  // 401 anywhere → session over.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (signedOut.current) return;
      signedOut.current = true;
      onSignedOut(true);
    });
    return () => setUnauthorizedHandler(null);
  }, [onSignedOut]);

  const refreshOverview = useCallback(async () => {
    try {
      const o = await api.get<Overview>("/api/v1/overview");
      setOverview(o);
      setOverviewAt(Date.now());
      setOverviewError(null);
    } catch (e) {
      setOverviewError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const shadowQ = perms.has("view_shadow") ? "&shadow=1" : "";
  const refreshIncidents = useCallback(async () => {
    try {
      const [open, recent] = await Promise.all([
        api.get<Incident[]>(`/api/v1/incidents?open=1&limit=500${shadowQ}`),
        api.get<Incident[]>(`/api/v1/incidents?limit=200${shadowQ}`),
      ]);
      const byId = new Map<string, Incident>();
      for (const i of [...recent, ...open]) byId.set(i.id, i);
      setIncidents([...byId.values()]);
      setIncidentsError(null);
    } catch (e) {
      setIncidentsError(e instanceof Error ? e.message : String(e));
    }
  }, [shadowQ]);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshOverview(), refreshIncidents()]);
  }, [refreshOverview, refreshIncidents]);

  const upsertIncident = useCallback((inc: Incident) => {
    setIncidents((list) => mergeIncident(list ?? [], inc));
  }, []);

  const pushToast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++toastSeq.current;
    setToasts((ts) => [...ts.slice(-4), { ...t, id }]);
    if (!t.sticky) setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 8000);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((ts) => ts.filter((x) => x.id !== id)), []);

  // Polling: overview every 5 s (drives the staleness check), incidents every 30 s.
  useEffect(() => {
    void refreshAll();
    const a = setInterval(() => void refreshOverview(), OVERVIEW_POLL_MS);
    const b = setInterval(() => void refreshIncidents(), INCIDENT_POLL_MS);
    return () => {
      clearInterval(a);
      clearInterval(b);
    };
  }, [refreshAll, refreshOverview, refreshIncidents]);

  // Debounced overview refresh after events that change counts / states.
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const softRefresh = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => void refreshOverview(), 400);
  }, [refreshOverview]);

  const roomName = useCallback(
    (id: string) => overview?.rooms.find((r) => r.id === id)?.name ?? id,
    [overview],
  );
  const roomNameRef = useRef(roomName);
  useEffect(() => {
    roomNameRef.current = roomName;
  }, [roomName]);

  const onEvent = useCallback(
    (type: SseType, data: unknown) => {
      switch (type) {
        case "camera_state": {
          const ev = data as CameraStateEvent;
          setOverview((o) => {
            if (!o) return o;
            const rooms = o.rooms.map((r) => {
              if (!r.cameras.some((c) => c.id === ev.camera_id)) return r;
              const cameras = r.cameras.map((c) => (c.id === ev.camera_id ? { ...c, state: ev.state, since: ev.since } : c));
              return { ...r, cameras, state: worstState(cameras.map((c) => c.state)) };
            });
            return { ...o, rooms, state: worstState(rooms.map((r) => r.state)) };
          });
          softRefresh();
          break;
        }
        case "incident_created":
        case "incident_merged":
        case "incident_updated":
          upsertIncident(data as Incident);
          softRefresh();
          break;
        case "alert": {
          const ev = data as AlertEvent;
          if (soundRef.current) playChime();
          setAlertPulse((n) => n + 1);
          pushToast({
            tone: "urgent",
            sticky: true,
            title: `${eventLabel(ev.event_type)} — ${roomNameRef.current(ev.room_id)}`,
            body: "Review required. Please check the room and acknowledge.",
            href: `/incidents/?id=${encodeURIComponent(ev.incident_id)}`,
          });
          softRefresh();
          void refreshIncidents();
          break;
        }
        case "incident_escalated": {
          const ev = data as EscalatedEvent;
          setIncidents((list) => list?.map((i) => (i.id === ev.incident_id ? { ...i, escalation_level: ev.level } : i)) ?? list);
          if (soundRef.current) playChime();
          pushToast({ tone: "urgent", title: `Alert escalated to level ${ev.level}`, body: "Not yet acknowledged.", href: `/incidents/?id=${encodeURIComponent(ev.incident_id)}` });
          break;
        }
        case "alert_delivery_failed": {
          const ev = data as DeliveryFailedEvent;
          pushToast({ tone: "degraded", title: `Alert delivery failing (${ev.channel})`, body: ev.error || "A notification could not be delivered. Fallback channels are being used." });
          softRefresh();
          break;
        }
        default:
          break;
      }
      for (const h of handlers.current) h(type, data);
    },
    [pushToast, refreshIncidents, softRefresh, upsertIncident],
  );
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);
  const refreshAllRef = useRef(refreshAll);
  useEffect(() => {
    refreshAllRef.current = refreshAll;
  }, [refreshAll]);

  // Live stream with our own reconnect/backoff so the "disconnected" state is explicit.
  useEffect(() => {
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      if (timer) clearTimeout(timer);
      timer = null;
      es?.close();
      setLive((l) => ({ ...l, status: l.status === "down" ? "down" : "connecting", reconnectAt: null }));
      const src = new EventSource("/api/v1/events", { withCredentials: true });
      es = src;
      src.onopen = () => {
        attempt = 0;
        setLive({ status: "open", downSince: null, reconnectAt: null });
        // Events are not replayed: always re-fetch everything after (re)connecting.
        void refreshAllRef.current();
      };
      src.onerror = () => {
        if (es !== src) return;
        src.close();
        es = null;
        const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** attempt);
        attempt += 1;
        const at = Date.now() + delay;
        setLive((l) => ({ status: "down", downSince: l.downSince ?? Date.now(), reconnectAt: at }));
        // If the stream failed because the session ended, this triggers the 401 handler.
        api.get<Me>("/api/v1/me").catch(() => {});
        timer = setTimeout(connect, delay);
      };
      for (const t of SSE_TYPES) {
        src.addEventListener(t, (e) => {
          let data: unknown = null;
          try {
            data = JSON.parse((e as MessageEvent<string>).data);
          } catch {
            return;
          }
          onEventRef.current(t, data);
        });
      }
    };
    reconnectRef.current = () => {
      attempt = 0;
      connect();
    };
    const wake = () => {
      if (!es && !stopped) reconnectRef.current();
    };
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      es?.close();
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);

  const subscribe = useCallback((h: SseHandler) => {
    handlers.current.add(h);
    return () => {
      handlers.current.delete(h);
    };
  }, []);

  const toggleSound = useCallback(async () => {
    if (soundRef.current) {
      soundRef.current = false;
      setSoundEnabled(false);
      disableSound();
      return;
    }
    const ok = await enableSound();
    soundRef.current = ok;
    setSoundEnabled(ok);
    if (!ok) pushToast({ tone: "degraded", title: "Alert sound could not be enabled", body: "This browser blocked audio. Check the device volume and site sound settings." });
  }, [pushToast]);

  const logout = useCallback(async () => {
    signedOut.current = true;
    try {
      await api.post("/api/v1/logout");
    } catch {
      /* ignore: we are leaving anyway */
    }
    onSignedOut(false);
  }, [onSignedOut]);

  const reconnectNow = useCallback(() => reconnectRef.current(), []);
  const sound = useMemo(() => ({ supported: soundOk, enabled: soundEnabled, toggle: toggleSound }), [soundOk, soundEnabled, toggleSound]);
  const value = useMemo<AppState>(() => ({
    me,
    can,
    overview,
    overviewAt,
    overviewError,
    incidents,
    incidentsError,
    refreshOverview,
    refreshIncidents,
    refreshAll,
    upsertIncident,
    live,
    reconnectNow,
    subscribe,
    toasts,
    pushToast,
    dismissToast,
    sound,
    logout,
    alertPulse,
  }), [me, can, overview, overviewAt, overviewError, incidents, incidentsError, refreshOverview, refreshIncidents, refreshAll, upsertIncident, live, reconnectNow, subscribe, toasts, pushToast, dismissToast, sound, logout, alertPulse]);

  return (
    <Ctx.Provider value={value}>
      <NowProvider>{children}</NowProvider>
    </Ctx.Provider>
  );
}

function NowProvider({ children }: { children: ReactNode }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return <NowCtx.Provider value={now}>{children}</NowCtx.Provider>;
}
