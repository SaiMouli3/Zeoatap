"use client";

import { useState } from "react";
import { Icon } from "./Icon";
import { api, errorMessage } from "@/lib/api";
import { useApp } from "@/lib/store";
import type { Incident } from "@/lib/types";

/** One-tap acknowledge. Records who/when on the server; stops repeat alerts. */
export function AckButton({ incident, onDone, block }: { incident: Incident; onDone?: (i: Incident) => void; block?: boolean }) {
  const { can, upsertIncident, pushToast, refreshOverview } = useApp();
  const [busy, setBusy] = useState(false);
  if (!can("acknowledge") || incident.lifecycle !== "new") return null;
  const ack = async () => {
    setBusy(true);
    try {
      const updated = await api.post<Incident>(`/api/v1/incidents/${encodeURIComponent(incident.id)}/ack`, {});
      upsertIncident(updated);
      onDone?.(updated);
      void refreshOverview();
    } catch (e) {
      pushToast({ tone: "degraded", title: "Acknowledgement not recorded", body: `${errorMessage(e)} Tap Acknowledge again.` });
    } finally {
      setBusy(false);
    }
  };
  return (
    <button type="button" className={`btn btn-ack${block ? " btn-block" : ""}`} onClick={() => void ack()} disabled={busy} data-testid="ack-button">
      <Icon name="check" size={18} />
      {busy ? "Recording…" : "Acknowledge"}
    </button>
  );
}
