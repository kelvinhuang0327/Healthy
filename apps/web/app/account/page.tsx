"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, type SessionSummary } from "../../lib/api";

export default function AccountPage() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.session().then((current) => {
      if (!cancelled) setSession(current);
    }).catch((reason) => {
      if (!cancelled && !(reason instanceof ApiError && reason.status === 401)) {
        setError("Could not load your account. Reload this page to retry.");
      }
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const next = String(data.get("new_password"));
    if (next !== String(data.get("confirmation"))) {
      setError("New passwords must match.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.changePassword({ current_password: String(data.get("current_password")), new_password: next });
      form.reset();
      setSession(null);
      setChanged(true);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        form.reset();
        setSession(null);
        setError("Your session has expired. Sign in again before changing your password.");
      } else {
        setError(reason instanceof Error ? reason.message : "Could not change password. Try signing in again if your session ended.");
      }
    } finally { setSaving(false); }
  }

  return <main>
    <header><Link href="/" className="back-link">Healthy</Link><h1>Account settings</h1></header>
    {error ? <p className="error" role="alert" data-testid="password-error">{error}</p> : null}
    {loading ? <p>Checking your session…</p> : session ?
      <section className="card">
        <h2>Change password</h2>
        <p>Changing your password signs you out of every session, including this one. Sign in again with the new password afterward.</p>
        <form onSubmit={changePassword} data-testid="password-change-form">
          <label>Current password<input type="password" name="current_password" autoComplete="current-password" required maxLength={1024} /></label>
          <label>New password<input type="password" name="new_password" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
          <label>Confirm new password<input type="password" name="confirmation" autoComplete="new-password" required minLength={12} maxLength={128} /></label>
          <button type="submit" disabled={saving}>{saving ? "Changing password…" : "Change password and sign out"}</button>
        </form>
      </section> : <section className="card">
        {changed ? <p role="status" data-testid="password-changed">Password changed. All previous sessions are signed out.</p> : <p>Sign in to change your password.</p>}
        <Link href="/" data-testid="password-sign-in">Sign in</Link>
      </section>}
  </main>;
}
