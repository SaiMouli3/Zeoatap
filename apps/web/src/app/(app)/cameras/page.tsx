"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { RequirePermission } from "@/components/AdminOnly";
import { EmptyState, ErrorState, Loading, PageHeader, Skeleton, StateBadge } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApp, useNow } from "@/lib/store";
import { ageSeconds, formatAge } from "@/lib/present";
import { ZONES_EXAMPLE, validateZonesJson } from "@/lib/zones";
import type { Camera, CameraStateEvent } from "@/lib/types";

const today = () => new Date().toISOString().slice(0, 10);

function RegisterForm({ onCreated, rooms }: { onCreated: (c: Camera) => void; rooms: { id: string; name: string }[] }) {
  const [f, setF] = useState({
    id: "",
    room_id: "",
    name: "",
    protected: true,
    enabled: true,
    authorized_by: "",
    authorized_at: today(),
    authorization_scope: "",
  });
  const [zonesText, setZonesText] = useState(ZONES_EXAMPLE);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const zones = useMemo(() => validateZonesJson(zonesText), [zonesText]);
  const missing = (["id", "room_id", "name", "authorized_by", "authorized_at", "authorization_scope"] as const).filter((k) => !f[k].trim());
  const valid = missing.length === 0 && zones.ok;
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));
  const fieldErr = (k: (typeof missing)[number]) => (touched && missing.includes(k) ? "Required" : null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    setOk(null);
    if (!valid || !zones.ok) {
      setError("Complete the required fields and fix the zone errors before registering.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const c = await api.post<Camera>("/api/v1/cameras", {
        ...f,
        id: f.id.trim(),
        room_id: f.room_id.trim(),
        name: f.name.trim(),
        authorized_by: f.authorized_by.trim(),
        authorization_scope: f.authorization_scope.trim(),
        zones: zones.zones,
      });
      onCreated(c);
      setOk(`Camera ${c.id} registered. It shows “Not monitored” until its stream connects.`);
      setF((x) => ({ ...x, id: "", name: "" }));
      setTouched(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const Text = ({ k, label, hint, type = "text", list }: { k: (typeof missing)[number]; label: string; hint?: string; type?: string; list?: string }) => {
    const errMsg = fieldErr(k);
    return (
      <label className="field">
        <span>
          {label} <span className="req">(required)</span>
        </span>
        <input
          name={k}
          type={type}
          list={list}
          value={f[k]}
          onChange={(e) => set(k, e.target.value)}
          required
          aria-invalid={errMsg ? true : undefined}
          aria-describedby={hint || errMsg ? `${k}-hint` : undefined}
        />
        {hint || errMsg ? (
          <span id={`${k}-hint`} className={errMsg ? "field-error" : "muted small"}>
            {errMsg ?? hint}
          </span>
        ) : null}
      </label>
    );
  };

  return (
    <form className="card" onSubmit={submit} noValidate aria-labelledby="reg-h" data-testid="camera-form">
      <h2 id="reg-h">Register a camera</h2>
      <p className="muted small">A camera can only be registered with a written authorization record (who approved it, when, and for what scope).</p>
      <div className="form-grid">
        {Text({ k: "id", label: "Camera ID", hint: "Short unique ID, e.g. cam7" })}
        {Text({ k: "name", label: "Camera name", hint: "Where it points, e.g. Changing table" })}
        {Text({ k: "room_id", label: "Room ID", hint: "Existing room ID, or a new one", list: "room-ids" })}
        <datalist id="room-ids">
          {rooms.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </datalist>
      </div>
      <fieldset className="auth-fieldset">
        <legend>Authorization record</legend>
        <div className="form-grid">
          {Text({ k: "authorized_by", label: "Authorized by", hint: "Name and role of the person who approved this camera" })}
          {Text({ k: "authorized_at", label: "Authorization date", type: "date" })}
          {Text({ k: "authorization_scope", label: "Authorization scope", hint: "e.g. Infant room A, operating hours only" })}
        </div>
      </fieldset>
      <div className="check-row">
        <label className="check">
          <input type="checkbox" checked={f.protected} onChange={(e) => set("protected", e.target.checked)} />
          <span>Protected camera (safety rules apply)</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          <span>Enabled</span>
        </label>
      </div>
      <label className="field">
        <span>Zones (JSON)</span>
        <textarea
          className="code-input"
          rows={10}
          value={zonesText}
          onChange={(e) => setZonesText(e.target.value)}
          spellCheck={false}
          aria-invalid={!zones.ok}
          aria-describedby="zones-status"
          data-testid="zones-json"
        />
      </label>
      <div id="zones-status" aria-live="polite" data-testid="zones-status">
        {zones.ok ? (
          <p className="muted small">
            {zones.zones.length} {zones.zones.length === 1 ? "zone" : "zones"} valid. Kinds: surface, restricted, sleep. Coordinates are fractions of the frame (0–1).
          </p>
        ) : (
          <ul className="field-error-list">
            {zones.errors.slice(0, 8).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
      </div>
      {error ? (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      ) : null}
      {ok ? (
        <p className="notice notice-info" role="status">
          {ok}
        </p>
      ) : null}
      <button type="submit" className="btn btn-primary" disabled={busy} data-testid="camera-submit">
        {busy ? "Registering…" : "Register camera"}
      </button>
    </form>
  );
}

function CamerasInner() {
  const { subscribe, overview } = useApp();
  const now = useNow();
  const [cams, setCams] = useState<Camera[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCams(await api.get<Camera[]>("/api/v1/cameras"));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(
    () =>
      subscribe((type, data) => {
        if (type !== "camera_state") return;
        const ev = data as CameraStateEvent;
        setCams((cs) => cs?.map((c) => (c.id === ev.camera_id ? { ...c, state: ev.state, state_since: ev.since } : c)) ?? cs);
      }),
    [subscribe],
  );
  const rooms = overview?.rooms.map((r) => ({ id: r.id, name: r.name })) ?? [];
  const roomName = (id: string) => rooms.find((r) => r.id === id)?.name ?? id;

  return (
    <>
      <PageHeader title="Cameras" />
      <div className="admin-grid">
        <section className="card" aria-labelledby="cams-h">
          <h2 id="cams-h">Registered cameras</h2>
          {error && !cams ? (
            <ErrorState title="Cameras could not be loaded" message={error} onRetry={() => void load()} />
          ) : !cams ? (
            <Loading label="Loading cameras">
              <Skeleton lines={5} height={24} />
            </Loading>
          ) : cams.length === 0 ? (
            <EmptyState title="No cameras registered">Register the first camera with its authorization record using the form.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table" data-testid="camera-table">
                <thead>
                  <tr>
                    <th scope="col">Camera</th>
                    <th scope="col">Room</th>
                    <th scope="col">State</th>
                    <th scope="col">Authorization</th>
                    <th scope="col">Zones</th>
                  </tr>
                </thead>
                <tbody>
                  {cams.map((c) => (
                    <tr key={c.id} data-testid="camera-row">
                      <td>
                        <strong>{c.name}</strong>
                        <div className="muted small">
                          {c.id}
                          {c.protected ? " · protected" : ""}
                          {!c.enabled ? " · disabled" : ""}
                        </div>
                      </td>
                      <td>{roomName(c.room_id)}</td>
                      <td>
                        <StateBadge state={c.state} />
                        <div className="muted small">for {formatAge(ageSeconds(c.state_since, now))}</div>
                      </td>
                      <td className="small">
                        {c.authorized_by}
                        <div className="muted">
                          {c.authorized_at} · {c.authorization_scope}
                        </div>
                      </td>
                      <td className="small">{c.zones?.length ? c.zones.map((z) => `${z.id} (${z.kind})`).join(", ") : "None"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <RegisterForm rooms={rooms} onCreated={(c) => setCams((cs) => [...(cs ?? []), c])} />
      </div>
    </>
  );
}

export default function CamerasPage() {
  return (
    <RequirePermission perm="admin">
      <CamerasInner />
    </RequirePermission>
  );
}
