"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type HealthMetric,
  type Person,
  type SessionSummary,
} from "../lib/api";

export default function Home() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(
    null,
  );
  const [metrics, setMetrics] = useState<HealthMetric[]>([]);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [current, rows] = await Promise.all([
        api.session(),
        api.persons(),
      ]);
      setSession(current);
      setPersons(rows);
      setError("");
    } catch {
      setSession(null);
      setPersons([]);
      setSelectedPersonId(null);
      setMetrics([]);
    }
  }

  useEffect(() => {
    Promise.all([api.session(), api.persons()])
      .then(([current, rows]) => {
        setSession(current);
        setPersons(rows);
      })
      .catch(() => {
        setSession(null);
        setPersons([]);
      });
  }, []);

  const selectedPerson =
    persons.find((person) => person.id === selectedPersonId) ??
    persons.find((person) => person.is_default) ??
    persons[0];
  const effectiveSelectedPersonId = selectedPerson?.id;

  useEffect(() => {
    if (!effectiveSelectedPersonId) {
      return;
    }
    let cancelled = false;
    api
      .healthMetrics(effectiveSelectedPersonId)
      .then((rows) => {
        if (!cancelled) {
          setMetrics(rows);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMetrics([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveSelectedPersonId]);

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.register({
        email: String(form.get("email")),
        password: String(form.get("password")),
        display_name: String(form.get("display_name")),
      });
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Registration failed");
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const current = await api.login({
        email: String(form.get("email")),
        password: String(form.get("password")),
      });
      setSession(current);
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Login failed");
    }
  }

  async function createPerson(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.createPerson({
        display_name: String(form.get("display_name")),
        relationship: String(form.get("relationship")),
      });
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Person creation failed");
    }
  }

  async function logout() {
    try {
      await api.logout();
      setSession(null);
      setPersons([]);
      setSelectedPersonId(null);
      setMetrics([]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Logout failed");
    }
  }

  function numberFieldOrNull(form: FormData, name: string): number | null {
    const raw = form.get(name);
    if (raw === null || raw === "") {
      return null;
    }
    return Number(raw);
  }

  async function createHealthMetric(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const recordedAtLocal = String(form.get("recorded_at") ?? "");
    const recordedAt = recordedAtLocal
      ? new Date(recordedAtLocal).toISOString()
      : new Date().toISOString();
    const note = String(form.get("note") ?? "").trim();
    try {
      await api.createHealthMetric(personId, {
        recorded_at: recordedAt,
        systolic_bp_mm_hg: numberFieldOrNull(form, "systolic_bp_mm_hg"),
        diastolic_bp_mm_hg: numberFieldOrNull(form, "diastolic_bp_mm_hg"),
        heart_rate_bpm: numberFieldOrNull(form, "heart_rate_bpm"),
        weight_kg: numberFieldOrNull(form, "weight_kg"),
        blood_glucose_mg_dl: numberFieldOrNull(form, "blood_glucose_mg_dl"),
        note: note || null,
      });
      formElement.reset();
      const rows = await api.healthMetrics(personId);
      setMetrics(rows);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Metric entry failed",
      );
    }
  }

  return (
    <main>
      <header>
        <h1>Healthy</h1>
        <p className="lede">
          A secure foundation where accounts authenticate and every health
          context belongs to an explicitly owned Person.
        </p>
      </header>

      <p className="error" role="alert">
        {error}
      </p>

      {!session ? (
        <section className="grid" aria-label="Authentication">
          <article className="card">
            <h2>Create account</h2>
            <form onSubmit={register} data-testid="register-form">
              <label>
                Email
                <input name="email" type="email" autoComplete="email" required />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  minLength={12}
                  autoComplete="new-password"
                  required
                />
              </label>
              <label>
                Your display name
                <input name="display_name" maxLength={120} required />
              </label>
              <button type="submit">Register securely</button>
            </form>
          </article>

          <article className="card">
            <h2>Sign in</h2>
            <form onSubmit={login} data-testid="login-form">
              <label>
                Email
                <input name="email" type="email" autoComplete="email" required />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <button type="submit">Sign in</button>
            </form>
          </article>
        </section>
      ) : (
        <section className="grid" aria-label="Authenticated account">
          <article className="card">
            <div className="session">
              <div>
                <span className="pill">Session active</span>
                <h2 data-testid="session-email">
                  {session.account.normalized_email}
                </h2>
              </div>
              <button className="secondary" type="button" onClick={logout}>
                Log out
              </button>
            </div>
            <ul className="persons" data-testid="person-list">
              {persons.map((person) => (
                <li
                  className="person"
                  key={person.id}
                  data-testid="person-card"
                  data-person-id={person.id}
                  onClick={() => setSelectedPersonId(person.id)}
                  role="button"
                  tabIndex={0}
                >
                  <span>
                    <strong>{person.display_name}</strong>
                    <br />
                    {person.relationship}
                  </span>
                  {person.is_default ? (
                    <span className="pill">Default Person</span>
                  ) : null}
                  {person.id === selectedPersonId ? (
                    <span className="pill" data-testid="selected-person-pill">
                      Selected
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </article>

          <article className="card">
            <h2>Add a Person</h2>
            <form onSubmit={createPerson} data-testid="person-form">
              <label>
                Display name
                <input name="display_name" maxLength={120} required />
              </label>
              <label>
                Relationship
                <select name="relationship" defaultValue="family">
                  <option value="family">Family</option>
                  <option value="child">Child</option>
                  <option value="parent">Parent</option>
                  <option value="spouse">Spouse</option>
                  <option value="caregiver">Caregiver</option>
                </select>
              </label>
              <button type="submit">Create Person</button>
            </form>
          </article>

          {selectedPerson ? (
            <article className="card">
              <h2>Log a health metric for {selectedPerson.display_name}</h2>
              <form onSubmit={createHealthMetric} data-testid="metric-form">
                <label>
                  Recorded at
                  <input name="recorded_at" type="datetime-local" />
                </label>
                <label>
                  Systolic blood pressure (mmHg)
                  <input
                    name="systolic_bp_mm_hg"
                    type="number"
                    min={30}
                    max={300}
                  />
                </label>
                <label>
                  Diastolic blood pressure (mmHg)
                  <input
                    name="diastolic_bp_mm_hg"
                    type="number"
                    min={20}
                    max={200}
                  />
                </label>
                <label>
                  Heart rate (bpm)
                  <input name="heart_rate_bpm" type="number" min={20} max={300} />
                </label>
                <label>
                  Weight (kg)
                  <input
                    name="weight_kg"
                    type="number"
                    step="0.01"
                    min={1}
                    max={500}
                  />
                </label>
                <label>
                  Blood glucose (mg/dL)
                  <input
                    name="blood_glucose_mg_dl"
                    type="number"
                    step="0.1"
                    min={10}
                    max={1000}
                  />
                </label>
                <label>
                  Note
                  <input name="note" maxLength={2000} />
                </label>
                <button type="submit">Save metric</button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Health metric history for {selectedPerson.display_name}</h2>
              <ul className="metrics" data-testid="metric-list">
                {metrics.map((metric) => (
                  <li
                    className="metric"
                    key={metric.id}
                    data-testid="metric-card"
                    data-metric-id={metric.id}
                  >
                    <span>{new Date(metric.recorded_at).toLocaleString()}</span>
                    <ul className="metric-values">
                      {metric.systolic_bp_mm_hg !== null &&
                      metric.diastolic_bp_mm_hg !== null ? (
                        <li>
                          {metric.systolic_bp_mm_hg}/{metric.diastolic_bp_mm_hg}{" "}
                          mmHg
                        </li>
                      ) : null}
                      {metric.heart_rate_bpm !== null ? (
                        <li>{metric.heart_rate_bpm} bpm</li>
                      ) : null}
                      {metric.weight_kg !== null ? (
                        <li>{metric.weight_kg} kg</li>
                      ) : null}
                      {metric.blood_glucose_mg_dl !== null ? (
                        <li>{metric.blood_glucose_mg_dl} mg/dL</li>
                      ) : null}
                    </ul>
                    {metric.note ? <p>{metric.note}</p> : null}
                  </li>
                ))}
              </ul>
            </article>
          ) : null}
        </section>
      )}
    </main>
  );
}
