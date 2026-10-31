"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { RequirePermission } from "@/components/AdminOnly";
import { EmptyState, ErrorState, Loading, PageHeader, Skeleton } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Role, User } from "@/lib/types";

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "caregiver", label: "Caregiver", hint: "Room status and acknowledging alerts" },
  { value: "lead", label: "Room lead", hint: "Plus evidence clips" },
  { value: "reviewer", label: "Reviewer", hint: "Plus dispositions, shadow incidents, legal holds" },
  { value: "admin", label: "Administrator", hint: "Cameras, users, audit; no clip access" },
];
const roleLabel = (r: Role) => ROLES.find((x) => x.value === r)?.label ?? r;
const MIN_PASSWORD = 12;

function CreateUser({ onCreated }: { onCreated: (u: User) => void }) {
  const [f, setF] = useState({ username: "", display_name: "", role: "caregiver" as Role, password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const userErr = f.username && !/^[a-z0-9._-]{2,32}$/.test(f.username) ? "Use 2–32 characters: lowercase letters, digits, . _ -" : null;
  const pwErr = f.password && f.password.length < MIN_PASSWORD ? `At least ${MIN_PASSWORD} characters` : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setOk(null);
    if (!f.username || !f.password || userErr || pwErr) {
      setError("Fill in username and a password of at least 12 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const u = await api.post<User>("/api/v1/users", { ...f, display_name: f.display_name.trim() || f.username });
      onCreated(u);
      setOk(`Account “${u.username}” created as ${roleLabel(u.role)}. Share the password in person.`);
      setF({ username: "", display_name: "", role: "caregiver", password: "" });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit} noValidate aria-labelledby="new-user-h" data-testid="user-form">
      <h2 id="new-user-h">Add a staff account</h2>
      <label className="field">
        <span>
          Username <span className="req">(required)</span>
        </span>
        <input name="username" autoComplete="off" autoCapitalize="none" spellCheck={false} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} aria-invalid={userErr ? true : undefined} aria-describedby="u-hint" />
        <span id="u-hint" className={userErr ? "field-error" : "muted small"}>
          {userErr ?? "Lowercase, used to sign in"}
        </span>
      </label>
      <label className="field">
        <span>Display name</span>
        <input name="display_name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
      </label>
      <fieldset className="field">
        <legend>Role</legend>
        <div className="radio-grid">
          {ROLES.map((r) => (
            <label key={r.value} className={`radio-card${f.role === r.value ? " is-checked" : ""}`}>
              <input type="radio" name="role" value={r.value} checked={f.role === r.value} onChange={() => setF({ ...f, role: r.value })} />
              <span>
                <span className="radio-label">{r.label}</span>
                <span className="muted small">{r.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span>
          Initial password <span className="req">(required)</span>
        </span>
        <input name="password" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} aria-invalid={pwErr ? true : undefined} aria-describedby="pw-hint" />
        <span id="pw-hint" className={pwErr ? "field-error" : "muted small"}>
          {pwErr ?? `At least ${MIN_PASSWORD} characters`}
        </span>
      </label>
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
      <button type="submit" className="btn btn-primary" disabled={busy} data-testid="user-submit">
        {busy ? "Creating…" : "Create account"}
      </button>
    </form>
  );
}

function UsersInner() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setUsers(await api.get<User[]>("/api/v1/users"));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    // Fetch-on-mount: loads from the API (an external system); state is only set after the response.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <>
      <PageHeader title="Users" />
      <div className="admin-grid">
        <section className="card" aria-labelledby="users-h">
          <h2 id="users-h">Staff accounts</h2>
          {error && !users ? (
            <ErrorState title="Accounts could not be loaded" message={error} onRetry={() => void load()} />
          ) : !users ? (
            <Loading label="Loading accounts">
              <Skeleton lines={5} height={24} />
            </Loading>
          ) : users.length === 0 ? (
            <EmptyState title="No accounts yet">Create the first staff account with the form.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table" data-testid="user-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Username</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id} data-testid="user-row">
                      <td>{u.display_name}</td>
                      <td>
                        <code>{u.username}</code>
                      </td>
                      <td>{roleLabel(u.role)}</td>
                      <td>{u.disabled ? "Disabled" : "Active"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <CreateUser onCreated={(u) => setUsers((us) => [...(us ?? []), u])} />
      </div>
    </>
  );
}

export default function UsersPage() {
  return (
    <RequirePermission perm="admin">
      <UsersInner />
    </RequirePermission>
  );
}
