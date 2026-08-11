"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type AssistantToday,
  type HealthAction,
  type HealthMetric,
  type HealthReportDetail,
  type HealthReportSummary,
  type Person,
  type SessionSummary,
  type SymptomLog,
} from "../lib/api";

export default function Home() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(
    null,
  );
  const [metrics, setMetrics] = useState<HealthMetric[]>([]);
  const [symptomLogs, setSymptomLogs] = useState<SymptomLog[]>([]);
  const [healthActions, setHealthActions] = useState<HealthAction[]>([]);
  const [healthReports, setHealthReports] = useState<HealthReportSummary[]>([]);
  const [selectedReportDetail, setSelectedReportDetail] =
    useState<HealthReportDetail | null>(null);
  const [assistantToday, setAssistantToday] = useState<AssistantToday | null>(
    null,
  );
  const [heightSaving, setHeightSaving] = useState(false);
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
      setSymptomLogs([]);
      setHealthActions([]);
      setHealthReports([]);
      setSelectedReportDetail(null);
      setAssistantToday(null);
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
    Promise.all([
      api.healthMetrics(effectiveSelectedPersonId),
      api.symptomLogs(effectiveSelectedPersonId),
      api.healthActions(effectiveSelectedPersonId),
      api.healthReports(effectiveSelectedPersonId),
      api.assistantToday(effectiveSelectedPersonId),
    ])
      .then(([metricRows, symptomRows, actionRows, reportRows, today]) => {
        if (!cancelled) {
          setMetrics(metricRows);
          setSymptomLogs(symptomRows);
          setHealthActions(actionRows);
          setHealthReports(reportRows);
          setAssistantToday(today);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMetrics([]);
          setSymptomLogs([]);
          setHealthActions([]);
          setHealthReports([]);
          setSelectedReportDetail(null);
          setAssistantToday(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveSelectedPersonId]);

  async function saveHeight(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const rawHeight = String(form.get("height_cm") ?? "").trim();
    const heightCm = rawHeight ? Number(rawHeight) : null;
    if (
      heightCm !== null &&
      (!Number.isFinite(heightCm) || heightCm <= 0)
    ) {
      setError("Height must be a finite number greater than zero.");
      return;
    }
    setHeightSaving(true);
    try {
      const updated = await api.updatePersonHeight(personId, heightCm);
      setPersons((current) =>
        current.map((person) => (person.id === updated.id ? updated : person)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Height update failed");
    } finally {
      setHeightSaving(false);
    }
  }


  async function refreshAssistantToday() {
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      setAssistantToday(await api.assistantToday(personId));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not refresh Today",
      );
    }
  }

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
      setSymptomLogs([]);
      setHealthActions([]);
      setAssistantToday(null);
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
        steps: numberFieldOrNull(form, "steps"),
        weight_kg: numberFieldOrNull(form, "weight_kg"),
        blood_glucose_mg_dl: numberFieldOrNull(form, "blood_glucose_mg_dl"),
        sleep_hours: numberFieldOrNull(form, "sleep_hours"),
        note: note || null,
      });
      formElement.reset();
      const rows = await api.healthMetrics(personId);
      setMetrics(rows);
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Metric entry failed",
      );
    }
  }

  async function createSymptomLog(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const occurredAtLocal = String(form.get("occurred_at") ?? "");
    const estimatedStartDate = String(form.get("estimated_start_date") ?? "").trim();
    const note = String(form.get("note") ?? "").trim();
    try {
      await api.createSymptomLog(personId, {
        symptom: String(form.get("symptom") ?? "").trim(),
        occurred_at: new Date(occurredAtLocal).toISOString(),
        severity: Number(form.get("severity")),
        duration_minutes: numberFieldOrNull(form, "duration_minutes"),
        estimated_start_date: estimatedStartDate || null,
        estimated_duration_days: numberFieldOrNull(form, "estimated_duration_days"),
        note: note || null,
      });
      formElement.reset();
      setSymptomLogs(await api.symptomLogs(personId));
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Symptom entry failed",
      );
    }
  }

  async function createHealthAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const dueAtLocal = String(form.get("due_at") ?? "");
    const description = String(form.get("description") ?? "").trim();
    try {
      await api.createHealthAction(personId, {
        title: String(form.get("title") ?? "").trim(),
        description: description || null,
        due_at: dueAtLocal ? new Date(dueAtLocal).toISOString() : null,
      });
      formElement.reset();
      setHealthActions(await api.healthActions(personId));
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Action creation failed",
      );
    }
  }

  async function completeHealthAction(actionId: string) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      await api.completeHealthAction(personId, actionId);
      setHealthActions(await api.healthActions(personId));
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Action completion failed",
      );
    }
  }

  async function createHealthActionOutcome(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const actionId = String(form.get("action_id") ?? "");
    const observedAtLocal = String(form.get("observed_at") ?? "");
    if (!actionId) {
      return;
    }
    try {
      await api.createHealthActionOutcome(personId, actionId, {
        note: String(form.get("note") ?? "").trim(),
        observed_at: observedAtLocal
          ? new Date(observedAtLocal).toISOString()
          : new Date().toISOString(),
      });
      formElement.reset();
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Outcome entry failed",
      );
    }
  }

  async function importHealthReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const rawJson = String(form.get("report_json") ?? "").trim();
    try {
      const parsedPayload = JSON.parse(rawJson);
      const reportDetail = await api.importReport(personId, parsedPayload);
      formElement.reset();
      setSelectedReportDetail(reportDetail);
      setHealthReports(await api.healthReports(personId));
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Report import failed",
      );
    }
  }

  async function confirmHealthReport(reportId: string) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      const confirmed = await api.confirmReport(personId, reportId);
      setSelectedReportDetail(confirmed);
      setHealthReports(await api.healthReports(personId));
      await refreshAssistantToday();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Report confirmation failed",
      );
    }
  }

  async function viewHealthReport(reportId: string) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      setSelectedReportDetail(await api.healthReport(personId, reportId));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not fetch report details",
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
            {selectedPerson ? (
              <a
                className="history-link"
                data-testid="history-link"
                href={`/history?person_id=${encodeURIComponent(selectedPerson.id)}`}
              >
                View Health History
              </a>
            ) : null}
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
            <article className="card" data-testid="height-profile">
              <h2>Height for {selectedPerson.display_name}</h2>
              {selectedPerson.height_cm == null ? (
                <p data-testid="height-empty">No height recorded yet.</p>
              ) : (
                <p data-testid="height-value">
                  Current height: {selectedPerson.height_cm} cm
                </p>
              )}
              <form
                key={`${selectedPerson.id}-${selectedPerson.height_cm ?? "empty"}`}
                onSubmit={saveHeight}
              >
                <label>
                  Height (cm)
                  <input
                    name="height_cm"
                    type="number"
                    step="0.01"
                    defaultValue={
                      selectedPerson.height_cm == null
                        ? ""
                        : String(selectedPerson.height_cm)
                    }
                    aria-describedby="height-help"
                  />
                </label>
                <p id="height-help">Use centimeters. Leave blank to clear.</p>
                <button type="submit" disabled={heightSaving}>
                  {heightSaving ? "Saving height…" : "Save height"}
                </button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Create an action for {selectedPerson.display_name}</h2>
              <form onSubmit={createHealthAction} data-testid="action-form">
                <label>
                  Title
                  <input name="title" maxLength={240} required />
                </label>
                <label>
                  Description
                  <input name="description" maxLength={2000} />
                </label>
                <label>
                  Due at
                  <input name="due_at" type="datetime-local" />
                </label>
                <button type="submit">Create action</button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Actions for {selectedPerson.display_name}</h2>
              <ul className="metrics" data-testid="action-list">
                {healthActions.map((action) => (
                  <li
                    className="metric"
                    key={action.id}
                    data-testid="action-card"
                    data-action-id={action.id}
                    data-action-status={action.status}
                    data-completed-at={action.completed_at ?? ""}
                  >
                    <strong>{action.title}</strong>
                    <span>Status: {action.status}</span>
                    {action.description ? <p>{action.description}</p> : null}
                    {action.due_at ? (
                      <span>Due {new Date(action.due_at).toLocaleString()}</span>
                    ) : null}
                    {action.completed_at ? (
                      <span>
                        Completed {new Date(action.completed_at).toLocaleString()}
                      </span>
                    ) : null}
                    <button
                      className="secondary"
                      type="button"
                      onClick={() => completeHealthAction(action.id)}
                    >
                      {action.status === "done"
                        ? "Complete again"
                        : "Complete action"}
                    </button>
                  </li>
                ))}
              </ul>
            </article>
          ) : null}

          {selectedPerson && healthActions.some((action) => action.status === "done") ? (
            <article className="card">
              <h2>Record an outcome for {selectedPerson.display_name}</h2>
              <form onSubmit={createHealthActionOutcome} data-testid="outcome-form">
                <label>
                  Action
                  <select name="action_id" required>
                    {healthActions
                      .filter((action) => action.status === "done")
                      .map((action) => (
                        <option key={action.id} value={action.id}>
                          {action.title}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  Observed at
                  <input name="observed_at" type="datetime-local" />
                </label>
                <label>
                  Note
                  <input name="note" maxLength={2000} required />
                </label>
                <button type="submit">Save outcome</button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Log a symptom for {selectedPerson.display_name}</h2>
              <form onSubmit={createSymptomLog} data-testid="symptom-form">
                <label>
                  Symptom
                  <input name="symptom" maxLength={120} required />
                </label>
                <label>
                  Occurred at
                  <input name="occurred_at" type="datetime-local" required />
                </label>
                <label>
                  Severity (1-5)
                  <input
                    name="severity"
                    type="number"
                    min={1}
                    max={5}
                    required
                  />
                </label>
                <label>
                  Duration (minutes, optional)
                  <input
                    name="duration_minutes"
                    type="number"
                    min={1}
                  />
                </label>
                <label>
                  Estimated start date (optional)
                  <input name="estimated_start_date" type="date" />
                </label>
                <label>
                  Estimated duration (days, optional)
                  <input
                    name="estimated_duration_days"
                    type="number"
                    min={1}
                    max={36500}
                  />
                </label>
                <label>
                  Note
                  <input name="note" maxLength={2000} />
                </label>
                <button type="submit">Save symptom</button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Symptom timeline for {selectedPerson.display_name}</h2>
              <ul className="metrics" data-testid="symptom-list">
                {symptomLogs.map((symptomLog) => (
                  <li
                    className="metric"
                    key={symptomLog.id}
                    data-testid="symptom-card"
                    data-symptom-id={symptomLog.id}
                  >
                    <strong>{symptomLog.symptom}</strong>
                    <span>
                      {new Date(symptomLog.occurred_at).toLocaleString()}
                    </span>
                    <ul className="metric-values">
                      <li>Severity {symptomLog.severity}/5</li>
                      {symptomLog.duration_minutes !== null ? (
                        <li>{symptomLog.duration_minutes} minutes</li>
                      ) : null}
                      {symptomLog.estimated_duration_days !== null ? (
                        <li>Estimated {symptomLog.estimated_duration_days} days</li>
                      ) : null}
                    </ul>
                    {symptomLog.note ? <p>{symptomLog.note}</p> : null}
                  </li>
                ))}
              </ul>
            </article>
          ) : null}

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
                  Steps (count)
                  <input name="steps" type="number" min={0} max={200000} step={1} />
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
                  Sleep duration (hours)
                  <input
                    name="sleep_hours"
                    type="number"
                    step="0.01"
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
                      {metric.steps !== null ? <li>{metric.steps} steps</li> : null}
                      {metric.weight_kg !== null ? (
                        <li>{metric.weight_kg} kg</li>
                      ) : null}
                      {metric.blood_glucose_mg_dl !== null ? (
                        <li>{metric.blood_glucose_mg_dl} mg/dL</li>
                      ) : null}
                      {metric.sleep_hours !== null ? (
                        <li>{metric.sleep_hours} hours</li>
                      ) : null}
                    </ul>
                    {metric.note ? <p>{metric.note}</p> : null}
                  </li>
                ))}
              </ul>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Import JSON health report for {selectedPerson.display_name}</h2>
              <form onSubmit={importHealthReport} data-testid="report-import-form">
                <label>
                  JSON report (schema healthy.health-report.v1)
                  <textarea
                    name="report_json"
                    rows={6}
                    placeholder='{"schema_version": "healthy.health-report.v1", "source_name": "LabCorp", "reported_at": "2026-08-01T08:00:00Z", "observations": [{"code": "GLUCOSE", "display_name": "Glucose", "value_numeric": 95.5, "unit": "mg/dL"}]}'
                    required
                  />
                </label>
                <button type="submit">Import structured report</button>
              </form>
            </article>
          ) : null}

          {selectedPerson ? (
            <article className="card">
              <h2>Imported health reports for {selectedPerson.display_name}</h2>
              <ul className="metrics" data-testid="report-list">
                {healthReports.map((report) => (
                  <li
                    className="metric"
                    key={report.id}
                    data-testid="report-card"
                    data-report-id={report.id}
                    data-report-status={report.status}
                  >
                    <strong>{report.source_name}</strong>
                    <span>
                      Status: {report.status} &middot; Reported{" "}
                      {new Date(report.reported_at).toLocaleString()}
                    </span>
                    <span style={{ fontSize: "0.8rem", wordBreak: "break-all" }}>
                      SHA-256: {report.canonical_sha256}
                    </span>
                    <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => viewHealthReport(report.id)}
                      >
                        View details
                      </button>
                      {report.status === "pending" ? (
                        <button
                          type="button"
                          data-testid="confirm-report-button"
                          onClick={() => confirmHealthReport(report.id)}
                        >
                          Confirm values
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>

              {selectedReportDetail ? (
                <div
                  style={{
                    marginTop: "16px",
                    padding: "12px",
                    border: "1px solid #ccc",
                    borderRadius: "4px",
                  }}
                  data-testid="report-detail-panel"
                >
                  <h3>Report details ({selectedReportDetail.source_name})</h3>
                  <p>
                    Status: <strong>{selectedReportDetail.status}</strong>
                  </p>
                  {selectedReportDetail.confirmed_at ? (
                    <p>
                      Confirmed at:{" "}
                      {new Date(selectedReportDetail.confirmed_at).toLocaleString()}
                    </p>
                  ) : null}
                  <h4>Observations</h4>
                  <ul>
                    {selectedReportDetail.observations.map((obs) => (
                      <li key={obs.id}>
                        <strong>{obs.display_name}</strong> ({obs.code}):{" "}
                        {obs.value_numeric !== null
                          ? obs.value_numeric
                          : obs.value_text}{" "}
                        {obs.unit ?? ""}
                        {obs.reference_range ? (
                          <span> [Ref: {obs.reference_range}]</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                  {selectedReportDetail.status === "pending" ? (
                    <button
                      type="button"
                      data-testid="confirm-detail-button"
                      onClick={() => confirmHealthReport(selectedReportDetail.id)}
                    >
                      Confirm this report
                    </button>
                  ) : null}
                </div>
              ) : null}
            </article>
          ) : null}


          {selectedPerson ? (
            <article className="card" data-testid="today-section">
              <div className="session">
                <h2>Today for {selectedPerson.display_name}</h2>
                <button
                  className="secondary"
                  type="button"
                  data-testid="today-refresh-button"
                  onClick={refreshAssistantToday}
                >
                  Refresh
                </button>
              </div>
              {assistantToday ? (
                <>
                  <p>
                    Generated{" "}
                    <span data-testid="today-generated-at">
                      {new Date(assistantToday.generated_at).toLocaleString()}
                    </span>{" "}
                    &middot; last{" "}
                    <span data-testid="today-lookback-days">
                      {assistantToday.lookback_days}
                    </span>{" "}
                    days
                  </p>

                  <h3>Latest metric</h3>
                  {assistantToday.latest_metric ? (
                    <p data-testid="today-latest-metric">
                      {new Date(
                        assistantToday.latest_metric.recorded_at,
                      ).toLocaleString()}
                    </p>
                  ) : (
                    <p data-testid="today-latest-metric-empty">
                      No health metric recorded yet.
                    </p>
                  )}

                  <h3>Evidence-linked insights</h3>
                  {assistantToday.insights.length === 0 ? (
                    <p data-testid="today-insights-empty">
                      No evidence-linked insights yet.
                    </p>
                  ) : (
                    <ul data-testid="today-insights-list">
                      {assistantToday.insights.map((insight) => (
                        <li
                          key={insight.id}
                          data-testid="today-insight-card"
                          data-insight-type={insight.insight_type}
                        >
                          <strong>{insight.headline}</strong>
                          <time dateTime={insight.observed_at}>
                            {new Date(insight.observed_at).toLocaleString()}
                          </time>
                          <span>
                            Evidence: {insight.evidence.map((item) => item.source_kind).join(", ")}
                          </span>
                          {insight.evidence.some(
                            (item) => item.source_kind === "report_observation",
                          ) && (
                            <span>
                              Source: {insight.evidence[0]?.report_source_name ?? "Confirmed report"}
                            </span>
                          )}
                          <a
                            className="history-link"
                            href={`/history?person_id=${encodeURIComponent(selectedPerson.id)}`}
                          >
                            View evidence in Health History
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}

                  <h3>Recent symptoms</h3>
                  <ul data-testid="today-symptom-list">
                    {assistantToday.recent_symptoms.map((symptom) => (
                      <li key={symptom.id} data-testid="today-symptom-card">
                        {symptom.symptom} &middot;{" "}
                        {new Date(symptom.occurred_at).toLocaleString()}
                      </li>
                    ))}
                  </ul>

                  <h3>Open or recently completed actions</h3>
                  <ul data-testid="today-action-list">
                    {assistantToday.open_or_recent_actions.map((action) => (
                      <li key={action.id} data-testid="today-action-card">
                        {action.title} &middot; {action.status}
                      </li>
                    ))}
                  </ul>

                  <h3>Recent outcomes</h3>
                  <ul data-testid="today-outcome-list">
                    {assistantToday.recent_outcomes.map((outcome) => (
                      <li key={outcome.id} data-testid="today-outcome-card">
                        {outcome.note} &middot;{" "}
                        {new Date(outcome.observed_at).toLocaleString()}
                      </li>
                    ))}
                  </ul>

                  <h3>Daily Attention Guidance</h3>
                  <ul data-testid="daily-attention-list">
                    {assistantToday.daily_attention.map((item) => (
                      <li
                        key={item.kind}
                        data-testid="daily-attention-item"
                        data-attention-kind={item.kind}
                        data-attention-confidence={item.confidence}
                      >
                        <strong>{item.title}</strong>
                        <p>{item.rationale}</p>
                        <span>Confidence: {item.confidence}</span>
                        <p>{item.limitations}</p>
                        <span data-testid="daily-attention-evidence-count">
                          {item.evidence_ids.length}
                        </span>{" "}
                        evidence record(s) &middot; rule {item.rule_version}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>Loading today&apos;s view&hellip;</p>
              )}
            </article>
          ) : null}
        </section>
      )}
    </main>
  );
}
