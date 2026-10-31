"use client";

import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError, errorMessage } from "@/lib/api";
import { markSignedIn, safeNext } from "@/lib/session";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Icon } from "@/components/Icon";
import type { User } from "@/lib/types";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const expired = params.get("expired") === "1";
  const next = safeNext(params.get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post<{ user: User }>("/api/v1/login", { username: username.trim(), password });
      markSignedIn(true);
      router.replace(next);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Incorrect username or password. Check both and try again." : errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form className="card login-card" onSubmit={submit} noValidate aria-describedby={error ? "login-error" : undefined}>
      <div className="login-brand">
        <span className="brand-mark" aria-hidden="true">
          Z
        </span>
        <div>
          <h1>Zeoatap</h1>
          <p className="muted">Staff dashboard · safety support</p>
        </div>
      </div>
      {expired ? (
        <p className="notice notice-info" role="status" data-testid="session-expired">
          <Icon name="lock" size={18} /> Your session has ended (signed out after inactivity or from another device). Sign in again to continue.
        </p>
      ) : null}
      {error ? (
        <p className="notice notice-error" role="alert" id="login-error" data-testid="login-error">
          {error}
        </p>
      ) : null}
      <label className="field">
        <span>Username</span>
        <input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label className="field">
        <span>Password</span>
        <input name="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="muted small">
        Zeoatap supports staff supervision; it does not replace it. Shared device? Sign out when you leave.
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="login-page">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <main id="main">
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </main>
    </div>
  );
}
