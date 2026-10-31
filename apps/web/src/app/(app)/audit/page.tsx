"use client";

import { useCallback, useEffect, useState } from "react";
import { RequirePermission } from "@/components/AdminOnly";
import { Icon } from "@/components/Icon";
import { EmptyState, ErrorState, Loading, PageHeader, Skeleton } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, humanize } from "@/lib/present";
import type { AuditResponse } from "@/lib/types";

const LIMITS = [50, 200, 1000];

function ChainStatus({ a }: { a: AuditResponse }) {
  if (a.chain_ok) {
    return (
      <section className="chain chain-ok" data-testid="chain-status" data-ok="true" aria-labelledby="chain-h">
        <Icon name="check" size={28} />
        <div>
          <h2 id="chain-h">Audit chain verified</h2>
          <p>Every entry links to the one before it. No entry has been altered or removed.</p>
        </div>
      </section>
    );
  }
  return (
    <section className="chain chain-broken" role="alert" data-testid="chain-status" data-ok="false" aria-labelledby="chain-h">
      <Icon name="triangle" size={28} />
      <div>
        <h2 id="chain-h">Audit chain broken at entry #{a.broken_at}</h2>
        <p>
          Entry #{a.broken_at} does not match the record before it, so entries from #{a.broken_at} onward cannot be trusted as unaltered. Preserve this device and
          the database, and follow the facility’s incident procedure (contact your vendor support).
        </p>
      </div>
    </section>
  );
}

function AuditInner() {
  const [limit, setLimit] = useState(200);
  const [data, setData] = useState<AuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await api.get<AuditResponse>(`/api/v1/audit?limit=${limit}`));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [limit]);
  useEffect(() => {
    void load();
  }, [load]);

  const q = filter.trim().toLowerCase();
  const entries = data?.entries.filter((e) => !q || `${e.actor} ${e.action} ${e.subject}`.toLowerCase().includes(q)) ?? [];

  return (
    <>
      <PageHeader title="Audit">
        <button type="button" className="btn btn-secondary" onClick={() => void load()}>
          <Icon name="refresh" size={18} /> Refresh
        </button>
      </PageHeader>
      {error && !data ? (
        <ErrorState title="Audit log could not be loaded" message={error} onRetry={() => void load()} />
      ) : !data ? (
        <Loading label="Loading audit log">
          <Skeleton lines={1} height={72} />
          <div style={{ height: 16 }} />
          <Skeleton lines={8} height={22} />
        </Loading>
      ) : (
        <>
          <ChainStatus a={data} />
          <div className="filters" role="group" aria-label="Filter audit entries">
            <label className="field field-inline">
              <span>Search</span>
              <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="actor, action or subject" />
            </label>
            <label className="field field-inline">
              <span>Entries</span>
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                {LIMITS.map((l) => (
                  <option key={l} value={l}>
                    Latest {l}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {entries.length === 0 ? (
            <EmptyState title={q ? "No entries match this search" : "No audit entries yet"}>{q ? "Try a shorter search term." : "Entries appear here as staff sign in, view clips and act on incidents."}</EmptyState>
          ) : (
            <div className="card table-wrap">
              <table className="table" data-testid="audit-table">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Time</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Action</th>
                    <th scope="col">Subject</th>
                    <th scope="col">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr key={e.seq} data-testid="audit-row" className={!data.chain_ok && e.seq >= data.broken_at ? "row-flag" : undefined}>
                      <td className="tabular">
                        {e.seq}
                        {!data.chain_ok && e.seq === data.broken_at ? <span className="sr-only"> (chain breaks here)</span> : null}
                      </td>
                      <td className="tabular small">{formatDateTime(e.at)}</td>
                      <td>{e.actor}</td>
                      <td>{humanize(e.action)}</td>
                      <td>
                        <code className="small">{e.subject}</code>
                      </td>
                      <td className="small">
                        {e.payload && typeof e.payload === "object" && Object.keys(e.payload).length ? <code>{JSON.stringify(e.payload)}</code> : <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}

export default function AuditPage() {
  return (
    <RequirePermission perm="admin">
      <AuditInner />
    </RequirePermission>
  );
}
