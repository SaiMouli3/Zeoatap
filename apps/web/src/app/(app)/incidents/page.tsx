"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { IncidentInbox } from "./IncidentInbox";
import { IncidentDetail } from "./IncidentDetail";

function IncidentsInner() {
  const params = useSearchParams();
  const id = params.get("id");
  const room = params.get("room");
  return (
    <div className="two-pane" data-has-selection={id ? "true" : "false"}>
      <div className="pane pane-list">
        <IncidentInbox selectedId={id} initialRoom={room} />
      </div>
      <div className="pane pane-detail">
        {id ? (
          <IncidentDetail key={id} id={id} />
        ) : (
          <div className="pane-placeholder">
            <p className="muted">Select an incident to review its evidence and timeline.</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function IncidentsPage() {
  return (
    <Suspense fallback={null}>
      <IncidentsInner />
    </Suspense>
  );
}
