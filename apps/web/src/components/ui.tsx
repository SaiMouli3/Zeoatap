import Link from "next/link";
import type { ReactNode } from "react";
import { Icon } from "./Icon";
import { STATE_PRESENTATION, severity, type DisplayState, type IconName, type Tone } from "@/lib/present";
import type { Incident } from "@/lib/types";

/** Badge with icon + word + colour. Colour is never the only signal. */
export function Badge({ tone, icon, children, testId, large }: { tone: Tone; icon: IconName; children: ReactNode; testId?: string; large?: boolean }) {
  return (
    <span className={`badge tone-${tone}${large ? " badge-lg" : ""}`} data-tone={tone} data-testid={testId}>
      <Icon name={icon} size={large ? 20 : 16} />
      <span>{children}</span>
    </span>
  );
}

export function StateBadge({ state, large }: { state: DisplayState; large?: boolean }) {
  const p = STATE_PRESENTATION[state];
  return (
    <Badge tone={p.tone} icon={p.icon} large={large} testId="state-badge">
      {p.label}
    </Badge>
  );
}

export function SeverityBadge({ incident }: { incident: Pick<Incident, "status" | "lifecycle" | "escalation_level"> }) {
  const s = severity(incident);
  return (
    <Badge tone={s.tone} icon={s.icon} testId="severity-badge">
      {s.label}
    </Badge>
  );
}

export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  return (
    <div className="skeleton-group" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: height ?? 16, width: i === lines - 1 && lines > 1 ? "60%" : "100%" }} />
      ))}
    </div>
  );
}

/** Loading region announced to assistive tech as "Loading…" (never implies "all OK"). */
export function Loading({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" data-testid="loading">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty" data-testid="empty-state">
      <p className="empty-title">{title}</p>
      {children ? <div className="muted">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert" data-testid="error-state">
      <Icon name="cross" />
      <div>
        <p className="error-title">{title}</p>
        <p>{message}</p>
        {onRetry ? (
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            <Icon name="refresh" size={18} /> Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function PageHeader({ title, children, back }: { title: string; children?: ReactNode; back?: { href: string; label: string } }) {
  return (
    <div className="page-header">
      {back ? (
        <Link href={back.href} className="back-link">
          <Icon name="arrow-left" size={18} /> {back.label}
        </Link>
      ) : null}
      <div className="page-header-row">
        <h1>{title}</h1>
        {children ? <div className="page-header-actions">{children}</div> : null}
      </div>
    </div>
  );
}
