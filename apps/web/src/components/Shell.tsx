"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Icon } from "./Icon";
import { MonitoringBanner } from "./MonitoringBanner";
import { ThemeToggle } from "./ThemeToggle";
import { Toasts } from "./Toasts";
import { isStale, useApp, useNow } from "@/lib/store";
import { ageSeconds, formatAge } from "@/lib/present";
import type { Permission } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  icon: "home" | "inbox" | "camera" | "users" | "log";
  perm: Permission;
}

const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: "home", perm: "view_status" },
  { href: "/incidents/", label: "Incidents", icon: "inbox", perm: "view_status" },
  { href: "/cameras/", label: "Cameras", icon: "camera", perm: "admin" },
  { href: "/users/", label: "Users", icon: "users", perm: "admin" },
  { href: "/audit/", label: "Audit", icon: "log", perm: "admin" },
];

const ROLE_LABEL = { caregiver: "Caregiver", lead: "Room lead", reviewer: "Reviewer", admin: "Administrator" } as const;

function isActive(path: string, href: string) {
  const p = path.endsWith("/") ? path : `${path}/`;
  return href === "/" ? p === "/" : p.startsWith(href);
}

function LiveIndicator() {
  const { live, reconnectNow } = useApp();
  const now = useNow();
  if (live.status === "open") {
    return (
      <span className="live live-ok" data-testid="live-indicator" data-status="open">
        <Icon name="live" size={18} /> <span>Live</span>
      </span>
    );
  }
  if (live.status === "connecting") {
    return (
      <span className="live live-wait" data-testid="live-indicator" data-status="connecting">
        <Icon name="question" size={18} /> <span>Connecting…</span>
      </span>
    );
  }
  const retryIn = live.reconnectAt ? Math.max(0, Math.ceil((live.reconnectAt - now) / 1000)) : 0;
  return (
    <span className="live live-down" data-testid="live-indicator" data-status="down" role="status">
      <Icon name="unplug" size={18} />
      <span>
        Live updates disconnected{live.downSince ? ` (${formatAge(ageSeconds(live.downSince, now))})` : ""}
        <span className="live-retry">{retryIn > 0 ? ` · retrying in ${retryIn}s` : " · retrying…"}</span>
      </span>
      <button type="button" className="btn btn-small" onClick={reconnectNow}>
        Retry now
      </button>
    </span>
  );
}

function FacilitySummary() {
  const { overview, overviewAt, live } = useApp();
  const now = useNow();
  if (!overview) return <span className="muted">Loading status…</span>;
  const total = overview.rooms.length;
  const ok = overview.rooms.filter((r) => r.state === "FULLY_OPERATIONAL").length;
  const stale = isStale(overviewAt, live.status, now);
  return (
    <span className="facility-summary" data-testid="facility-summary">
      {stale ? (
        <span className="summary-unknown">
          <Icon name="question" size={16} /> Status unknown
        </span>
      ) : (
        <span>
          {ok}/{total} rooms fully monitored
        </span>
      )}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, can, overview, sound, logout, alertPulse } = useApp();
  const path = usePathname() ?? "/";
  const items = NAV.filter((n) => can(n.perm));
  const openAlerts = overview?.open_alerts ?? null;

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <MonitoringBanner />
      <header className="topbar">
        {alertPulse ? <span className="alert-pulse" key={alertPulse} aria-hidden="true" data-testid="alert-pulse" /> : null}
        <div className="topbar-left">
          <Link href="/" className="brand" aria-label="Zeoatap overview">
            <span className="brand-mark" aria-hidden="true">
              Z
            </span>
            <span className="brand-text">
              <span className="brand-facility">{overview?.facility.name ?? "Zeoatap"}</span>
              <FacilitySummary />
            </span>
          </Link>
        </div>
        <div className="topbar-right">
          <Link href="/incidents/" className={`alerts-pill${openAlerts ? " has-open" : ""}`} data-testid="open-alerts">
            <span className="alerts-pill-label">Open</span>
            <span className="alerts-pill-count">{openAlerts ?? "–"}</span>
          </Link>
          <LiveIndicator />
          {sound.supported ? (
            <button
              type="button"
              className={`btn ${sound.enabled ? "btn-secondary" : "btn-attention"}`}
              onClick={() => void sound.toggle()}
              aria-pressed={sound.enabled}
              data-testid="sound-toggle"
            >
              <Icon name={sound.enabled ? "sound" : "mute"} size={18} />
              <span className="hide-sm">{sound.enabled ? "Alert sound on" : "Enable alert sound"}</span>
              <span className="show-sm">{sound.enabled ? "Sound on" : "Sound"}</span>
            </button>
          ) : null}
          <ThemeToggle />
          <div className="user-chip" data-testid="user-chip">
            <span className="user-name">{me.user.display_name}</span>
            <span className="user-role">{ROLE_LABEL[me.user.role] ?? me.user.role}</span>
          </div>
          <button type="button" className="icon-btn" onClick={() => void logout()} aria-label="Sign out" title="Sign out">
            <Icon name="logout" />
          </button>
        </div>
      </header>
      <div className="body">
        <nav className="sidenav" aria-label="Main">
          <ul>
            {items.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className="nav-link" aria-current={isActive(path, n.href) ? "page" : undefined} data-testid={`nav-${n.label.toLowerCase()}`}>
                  <Icon name={n.icon} />
                  <span>{n.label}</span>
                  {n.href === "/incidents/" && openAlerts ? <span className="nav-count">{openAlerts}</span> : null}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <main id="main" tabIndex={-1}>
          {children}
        </main>
      </div>
      <nav className="bottomnav" aria-label="Main (compact)">
        {items.map((n) => (
          <Link key={n.href} href={n.href} className="bottomnav-link" aria-current={isActive(path, n.href) ? "page" : undefined}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
            {n.href === "/incidents/" && openAlerts ? <span className="nav-count">{openAlerts}</span> : null}
          </Link>
        ))}
      </nav>
      <Toasts />
    </div>
  );
}
