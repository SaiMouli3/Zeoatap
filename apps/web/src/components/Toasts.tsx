"use client";

import Link from "next/link";
import { Icon } from "./Icon";
import { useApp } from "@/lib/store";

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  const urgent = toasts.filter((t) => t.tone === "urgent");
  const other = toasts.filter((t) => t.tone !== "urgent");
  const render = (list: typeof toasts) =>
    list.map((t) => (
      <div key={t.id} className={`toast toast-${t.tone}`} data-testid="toast">
        <Icon name={t.tone === "urgent" ? "triangle" : t.tone === "degraded" ? "half" : "bang"} size={20} />
        <div className="toast-body">
          <p className="toast-title">{t.title}</p>
          {t.body ? <p>{t.body}</p> : null}
          {t.href ? (
            <Link href={t.href} className="toast-link" onClick={() => dismissToast(t.id)}>
              View incident
            </Link>
          ) : null}
        </div>
        <button type="button" className="icon-btn" aria-label="Dismiss notification" onClick={() => dismissToast(t.id)}>
          <Icon name="cross" size={18} />
        </button>
      </div>
    ));
  return (
    <div className="toasts">
      <div role="alert" aria-live="assertive" className="toast-stack">
        {render(urgent)}
      </div>
      <div role="status" aria-live="polite" className="toast-stack">
        {render(other)}
      </div>
    </div>
  );
}
