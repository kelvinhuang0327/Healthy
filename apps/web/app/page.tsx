"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type ActionRecommendation,
  type ActionRecommendations,
  type AssistantToday,
  type DueHealthActionReminder,
  type HealthAction,
  type HealthActionReminder,
  type HealthMetric,
  type HealthReportDetail,
  type HealthReportSummary,
  type HealthScore,
  type Person,
  type RiskAlert,
  type RiskAlerts,
  type SessionSummary,
  type SymptomLog,
} from "../lib/api";

function compareRiskAlerts(left: RiskAlert, right: RiskAlert): number {
  const severityRank = { high: 0, medium: 1 } as const;
  const severityDifference =
    severityRank[left.severity] - severityRank[right.severity];
  if (severityDifference !== 0) {
    return severityDifference;
  }

  const observedAtDifference =
    new Date(right.evidence.observed_at).getTime() -
    new Date(left.evidence.observed_at).getTime();
  if (observedAtDifference !== 0) {
    return observedAtDifference;
  }

  if (left.rule_code !== right.rule_code) {
    return left.rule_code < right.rule_code ? -1 : 1;
  }
  if (left.evidence.source_id !== right.evidence.source_id) {
    return left.evidence.source_id < right.evidence.source_id ? -1 : 1;
  }
  return 0;
}

function recommendationIdentityKey(recommendation: ActionRecommendation): string {
  return JSON.stringify([
    recommendation.evidence.person_id,
    recommendation.recommendation_code,
    recommendation.rule_version,
    recommendation.evidence.source_kind,
    recommendation.evidence.source_id,
    recommendation.evidence.observation_id,
    recommendation.evidence.report_id,
  ]);
}

function acceptedActionIdentityKey(action: HealthAction): string | null {
  if (
    action.origin_type !== "action_recommendation" ||
    action.recommendation_code === null ||
    action.recommendation_rule_version === null ||
    action.source_evidence_kind === null ||
    action.source_evidence_id === null
  ) {
    return null;
  }
  return JSON.stringify([
    action.person_id,
    action.recommendation_code,
    action.recommendation_rule_version,
    action.source_evidence_kind,
    action.source_evidence_id,
    action.source_observation_id,
    action.source_report_id,
  ]);
}

export default function Home() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(
    null,
  );
  const [metrics, setMetrics] = useState<HealthMetric[]>([]);
  const [healthScore, setHealthScore] = useState<HealthScore | null>(null);
  const [symptomLogs, setSymptomLogs] = useState<SymptomLog[]>([]);
  const [healthActions, setHealthActions] = useState<HealthAction[]>([]);
  const [actionReminders, setActionReminders] = useState<
    Record<string, HealthActionReminder | null>
  >({});
  const [dueReminders, setDueReminders] = useState<DueHealthActionReminder[]>([]);
  const [healthReports, setHealthReports] = useState<HealthReportSummary[]>([]);
  const [selectedReportDetail, setSelectedReportDetail] =
    useState<HealthReportDetail | null>(null);
  const [riskAlerts, setRiskAlerts] = useState<RiskAlerts | null>(null);
  const [actionRecommendations, setActionRecommendations] =
    useState<ActionRecommendations | null>(null);
  const [acceptingRecommendationKeys, setAcceptingRecommendationKeys] =
    useState<string[]>([]);
  const [assistantToday, setAssistantToday] = useState<AssistantToday | null>(
    null,
  );
  const [browserTimeZone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [heightSaving, setHeightSaving] = useState(false);
  const [error, setError] = useState("");

  async function loadActionReminders(
    personId: string,
    actions: HealthAction[],
  ): Promise<Record<string, HealthActionReminder | null>> {
    const entries = await Promise.all(
      actions.map(async (action) => {
        try {
          return [action.id, await api.healthActionReminder(personId, action.id)] as const;
        } catch (reason) {
          if (reason instanceof ApiError && reason.status === 404) {
            return [action.id, null] as const;
          }
          throw reason;
        }
      }),
    );
    return Object.fromEntries(entries);
  }

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
      setHealthScore(null);
      setSymptomLogs([]);
      setHealthActions([]);
      setActionReminders({});
      setDueReminders([]);
      setHealthReports([]);
      setSelectedReportDetail(null);
      setRiskAlerts(null);
      setActionRecommendations(null);
      setAssistantToday(null);
      setAcceptingRecommendationKeys([]);
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
      api.healthScore(effectiveSelectedPersonId),
      api.symptomLogs(effectiveSelectedPersonId),
      api.healthActions(effectiveSelectedPersonId),
      api.healthReports(effectiveSelectedPersonId),
      api.assistantToday(effectiveSelectedPersonId),
      api.riskAlerts(effectiveSelectedPersonId),
      api.actionRecommendations(effectiveSelectedPersonId),
      api.dueHealthActionReminders(effectiveSelectedPersonId),
    ])
      .then(async ([
          metricRows,
          score,
          symptomRows,
          actionRows,
          reportRows,
          today,
          alerts,
          recommendations,
          due,
        ]) => {
          const reminderEntries = await loadActionReminders(
            effectiveSelectedPersonId,
            actionRows,
          );
          if (!cancelled) {
            setMetrics(metricRows);
            setHealthScore(score);
            setSymptomLogs(symptomRows);
            setHealthActions(actionRows);
            setHealthReports(reportRows);
            setAssistantToday(today);
            setRiskAlerts(alerts);
            setActionRecommendations(recommendations);
            setActionReminders(reminderEntries);
            setDueReminders(due);
            setAcceptingRecommendationKeys([]);
          }
        })
      .catch(() => {
        if (!cancelled) {
          setMetrics([]);
          setHealthScore(null);
          setSymptomLogs([]);
          setHealthActions([]);
          setActionReminders({});
          setDueReminders([]);
          setHealthReports([]);
          setSelectedReportDetail(null);
          setAssistantToday(null);
          setRiskAlerts(null);
          setActionRecommendations(null);
          setAcceptingRecommendationKeys([]);
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
      await refreshRiskSignals();
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

  async function refreshDueReminders() {
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      setDueReminders(await api.dueHealthActionReminders(personId));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not refresh reminders",
      );
    }
  }

  async function refreshRiskSignals() {
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      const [alerts, recommendations] = await Promise.all([
        api.riskAlerts(personId),
        api.actionRecommendations(personId),
      ]);
      setRiskAlerts(alerts);
      setActionRecommendations(recommendations);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not refresh Risk Alerts",
      );
    }
  }

  async function refreshToday() {
    await Promise.all([
      refreshAssistantToday(),
      refreshRiskSignals(),
      refreshDueReminders(),
    ]);
  }

  async function refreshHealthScore() {
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      setHealthScore(await api.healthScore(personId));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not refresh health score",
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
      setHealthScore(null);
      setSymptomLogs([]);
      setHealthActions([]);
      setActionReminders({});
      setDueReminders([]);
      setHealthReports([]);
      setSelectedReportDetail(null);
      setRiskAlerts(null);
      setActionRecommendations(null);
      setAssistantToday(null);
      setAcceptingRecommendationKeys([]);
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
      await refreshHealthScore();
      await refreshAssistantToday();
      await refreshRiskSignals();
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
      await refreshHealthScore();
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
      const actions = await api.healthActions(personId);
      setHealthActions(actions);
      setActionReminders(await loadActionReminders(personId, actions));
      await Promise.all([refreshAssistantToday(), refreshDueReminders()]);
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
      const actions = await api.healthActions(personId);
      setHealthActions(actions);
      setActionReminders(await loadActionReminders(personId, actions));
      await Promise.all([refreshAssistantToday(), refreshDueReminders()]);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Action completion failed",
      );
    }
  }

  async function saveHealthActionReminder(
    event: FormEvent<HTMLFormElement>,
    actionId: string,
  ) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const form = new FormData(event.currentTarget);
    try {
      const reminder = await api.upsertHealthActionReminder(personId, actionId, {
        timezone_name: String(form.get("timezone_name") ?? "").trim(),
        local_time: String(form.get("local_time") ?? ""),
      });
      setActionReminders((current) => ({ ...current, [actionId]: reminder }));
      await refreshDueReminders();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Reminder schedule failed",
      );
    }
  }

  async function removeHealthActionReminder(actionId: string) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      await api.deleteHealthActionReminder(personId, actionId);
      setActionReminders((current) => ({ ...current, [actionId]: null }));
      await refreshDueReminders();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Reminder removal failed",
      );
    }
  }

  async function acknowledgeReminder(actionId: string) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    try {
      const reminder = await api.acknowledgeHealthActionReminder(personId, actionId);
      setActionReminders((current) => ({ ...current, [actionId]: reminder }));
      await refreshDueReminders();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Reminder acknowledgement failed",
      );
    }
  }

  async function snoozeReminder(
    event: FormEvent<HTMLFormElement>,
    actionId: string,
  ) {
    event.preventDefault();
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const untilLocal = String(form.get("snooze_until") ?? "");
    if (!untilLocal) {
      setError("Choose a future time to snooze this reminder.");
      return;
    }
    try {
      const reminder = await api.snoozeHealthActionReminder(
        personId,
        actionId,
        new Date(untilLocal).toISOString(),
      );
      setActionReminders((current) => ({ ...current, [actionId]: reminder }));
      await refreshDueReminders();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reminder snooze failed");
    }
  }

  async function acceptActionRecommendation(recommendation: ActionRecommendation) {
    setError("");
    const personId = selectedPerson?.id;
    if (!personId) {
      return;
    }
    const identityKey = recommendationIdentityKey(recommendation);
    const accepted = healthActions.some(
      (action) => acceptedActionIdentityKey(action) === identityKey,
    );
    if (accepted || acceptingRecommendationKeys.includes(identityKey)) {
      return;
    }
    setAcceptingRecommendationKeys((current) => [...current, identityKey]);
    try {
      await api.acceptActionRecommendation(personId, recommendation.recommendation_code, {
        rule_version: recommendation.rule_version,
        source_kind: recommendation.evidence.source_kind,
        source_id: recommendation.evidence.source_id,
        observation_id: recommendation.evidence.observation_id,
        report_id: recommendation.evidence.report_id,
        observed_at: recommendation.evidence.observed_at,
      });
      const [actions, today] = await Promise.all([
        api.healthActions(personId),
        api.assistantToday(personId),
      ]);
      setHealthActions(actions);
      setAssistantToday(today);
      setActionReminders(await loadActionReminders(personId, actions));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not add recommendation to actions",
      );
    } finally {
      setAcceptingRecommendationKeys((current) =>
        current.filter((key) => key !== identityKey),
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
      await refreshRiskSignals();
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

  const displayedRiskAlerts = riskAlerts
    ? [...riskAlerts.alerts].sort(compareRiskAlerts)
    : [];
  const displayedActionRecommendations: ActionRecommendation[] =
    actionRecommendations?.recommendations ?? [];
  const acceptedRecommendationKeys = new Set(
    healthActions
      .map(acceptedActionIdentityKey)
      .filter((key): key is string => key !== null),
  );

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
                  onClick={() => {
                    setSelectedPersonId(person.id);
                    if (person.id !== effectiveSelectedPersonId) {
                      setRiskAlerts(null);
                      setActionRecommendations(null);
                      setActionReminders({});
                      setDueReminders([]);
                    }
                  }}
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
              <div>
                <a
                  className="history-link"
                  data-testid="history-link"
                  href={`/history?person_id=${encodeURIComponent(selectedPerson.id)}`}
                >
                  View Health History
                </a>
                <a
                  className="history-link"
                  data-testid="analytics-link"
                  href={`/analytics?person_id=${encodeURIComponent(selectedPerson.id)}`}
                >
                  View Health Analytics
                </a>
              </div>
            ) : null}
          </article>

          {selectedPerson ? (
            <article className="card" data-testid="health-score-card">
              <div className="session">
                <div>
                  <span className="pill">Deterministic V1</span>
                  <h2>Health signal for {selectedPerson.display_name}</h2>
                </div>
                <button
                  className="secondary"
                  type="button"
                  data-testid="health-score-refresh-button"
                  onClick={refreshHealthScore}
                >
                  Refresh
                </button>
              </div>
              {healthScore ? (
                <>
                  <p className={`score score-${healthScore.status}`}>
                    <strong data-testid="health-score-value">
                      {healthScore.score ?? "—"}
                    </strong>
                    <span data-testid="health-score-status">
                      {healthScore.status.replace("_", " ")}
                    </span>
                  </p>
                  <p>
                    Based on {healthScore.data_points} data point
                    {healthScore.data_points === 1 ? "" : "s"} · rule{" "}
                    {healthScore.rule_version}
                  </p>
                  {healthScore.components.length ? (
                    <ul className="score-components">
                      {healthScore.components.map((component) => (
                        <li key={component.kind}>
                          <span>
                            <strong>{component.label}</strong>
                            <br />
                            {component.rationale}
                          </span>
                          <span>{component.points}/100</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="muted" data-testid="health-score-coverage">
                    <strong>Coverage</strong>
                    <p>
                      Evaluated: {healthScore.coverage.evaluated_inputs.join(", ") || "none"}
                    </p>
                    <p>
                      Missing ordinary data: {healthScore.coverage.missing_inputs.join(", ") || "none"}
                    </p>
                    <p>
                      Unavailable / not evaluated: {healthScore.coverage.unsupported_sources.join(", ")}
                    </p>
                  </div>
                  <p className="muted">{healthScore.limitations}</p>
                </>
              ) : (
                <p>Health signal unavailable.</p>
              )}
            </article>
          ) : null}

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
                    {action.status === "todo" ? (
                      <form
                        key={`${action.id}-${actionReminders[action.id]?.id ?? "none"}-${actionReminders[action.id]?.timezone_name ?? browserTimeZone}-${actionReminders[action.id]?.local_time ?? ""}`}
                        onSubmit={(event) => saveHealthActionReminder(event, action.id)}
                        data-testid="reminder-form"
                      >
                        <strong>Daily in-app reminder</strong>
                        <label>
                          Local reminder time
                          <input
                            name="local_time"
                            type="time"
                            step={60}
                            defaultValue={actionReminders[action.id]?.local_time.slice(0, 5) ?? "09:00"}
                            required
                          />
                        </label>
                        <label>
                          IANA timezone
                          <input
                            name="timezone_name"
                            defaultValue={
                              actionReminders[action.id]?.timezone_name ?? browserTimeZone
                            }
                            maxLength={128}
                            required
                          />
                        </label>
                        <button type="submit">Save reminder</button>
                        {actionReminders[action.id] ? (
                          <button
                            className="secondary"
                            type="button"
                            onClick={() => removeHealthActionReminder(action.id)}
                          >
                            Remove reminder
                          </button>
                        ) : null}
                      </form>
                    ) : actionReminders[action.id] ? (
                      <p>Reminder you scheduled for this action is preserved while it is completed.</p>
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
                  onClick={refreshToday}
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

                  <h3>Reminders due</h3>
                  {dueReminders.length === 0 ? (
                    <p data-testid="today-reminders-empty">No reminders are due.</p>
                  ) : (
                    <ul data-testid="today-reminder-list">
                      {dueReminders.map((reminder) => (
                        <li
                          key={reminder.reminder_id}
                          data-testid="today-reminder-card"
                          data-reminder-action-id={reminder.action_id}
                        >
                          <strong>{reminder.action_title}</strong>
                          <p>Reminder you scheduled for this action.</p>
                          <span>
                            Every day at {reminder.local_time.slice(0, 5)} · {reminder.timezone_name}
                          </span>
                          <button
                            type="button"
                            data-testid="acknowledge-reminder-button"
                            onClick={() => acknowledgeReminder(reminder.action_id)}
                          >
                            Done for today
                          </button>
                          <form
                            onSubmit={(event) => snoozeReminder(event, reminder.action_id)}
                            data-testid="snooze-reminder-form"
                          >
                            <label>
                              Snooze until
                              <input name="snooze_until" type="datetime-local" required />
                            </label>
                            <button type="submit">Snooze reminder</button>
                          </form>
                        </li>
                      ))}
                    </ul>
                  )}

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

                  <h3>Risk alerts</h3>
                  <p data-testid="today-risk-alerts-disclaimer">
                    These are deterministic signals from recorded health data,
                    not a diagnosis or comprehensive medical risk assessment.
                  </p>
                  {riskAlerts ? (
                    <>
                      <p data-testid="today-risk-alert-count">
                        Active alerts: {riskAlerts.active_count}
                      </p>
                      {displayedRiskAlerts.length === 0 ? (
                        <p data-testid="today-risk-alerts-empty">
                          No deterministic risk alerts were found in the health
                          measurements and confirmed lab observations currently
                          supported by Healthy.
                        </p>
                      ) : (
                        <ul data-testid="today-risk-alert-list">
                          {displayedRiskAlerts.map((alert) => (
                            <li
                              key={`${alert.rule_code}-${alert.evidence.source_id}-${alert.evidence.observation_id ?? ""}`}
                              data-testid="today-risk-alert-card"
                              data-risk-alert-severity={alert.severity}
                            >
                              <strong>{alert.rule_code}</strong>
                              <span>Risk: {alert.risk_type}</span>
                              <span>Severity: {alert.severity}</span>
                              <span>Status: {alert.status}</span>
                              <span>
                                Evidence: {alert.evidence.source_kind} ·{" "}
                                {alert.evidence.source_id}
                              </span>
                              {alert.evidence.report_source_name ? (
                                <span>
                                  Report source: {alert.evidence.report_source_name}
                                </span>
                              ) : null}
                              <time dateTime={alert.evidence.observed_at}>
                                Observed: {new Date(alert.evidence.observed_at).toLocaleString()}
                              </time>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    <p>Loading risk alerts&hellip;</p>
                  )}

                  <h3>Recommended next steps</h3>
                  <p data-testid="today-action-recommendations-disclaimer">
                    These are deterministic follow-up suggestions based on Risk
                    Alerts, not a diagnosis, treatment plan, or clinical
                    urgency assessment.
                  </p>
                  {actionRecommendations ? (
                    displayedActionRecommendations.length === 0 ? (
                      <p data-testid="today-action-recommendations-empty">
                        No action recommendations are available from the
                        deterministic risk signals currently supported by
                        Healthy.
                      </p>
                    ) : (
                      <ul data-testid="today-action-recommendation-list">
                        {displayedActionRecommendations.map((recommendation) => {
                          const identityKey = recommendationIdentityKey(recommendation);
                          const isAccepted = acceptedRecommendationKeys.has(identityKey);
                          const isAccepting = acceptingRecommendationKeys.includes(identityKey);
                          return (
                            <li
                              key={`${recommendation.recommendation_code}-${recommendation.evidence.source_id}`}
                              data-testid="today-action-recommendation-card"
                            >
                              <strong>{recommendation.title}</strong>
                              <p>{recommendation.suggested_action}</p>
                              <p>Why it was suggested: {recommendation.rationale}</p>
                              <span>
                                Source Risk Alert: {recommendation.source_rule_code} ·
                                {" "}internal severity: {recommendation.source_severity}
                              </span>
                              <span>
                                Matching alerts for this rule: {recommendation.matching_alert_count}
                              </span>
                              <span>
                                Evidence: {recommendation.evidence.source_kind} ·{" "}
                                {recommendation.evidence.source_id}
                              </span>
                              {recommendation.evidence.report_source_name ? (
                                <span>
                                  Report source: {recommendation.evidence.report_source_name}
                                </span>
                              ) : null}
                              <time dateTime={recommendation.evidence.observed_at}>
                                Observed: {new Date(
                                  recommendation.evidence.observed_at,
                                ).toLocaleString()}
                              </time>
                              <p>{recommendation.limitations}</p>
                              <span>Rule version: {recommendation.rule_version}</span>
                              <button
                                type="button"
                                data-testid="accept-recommendation-button"
                                disabled={isAccepted || isAccepting}
                                onClick={() => acceptActionRecommendation(recommendation)}
                              >
                                {isAccepted
                                  ? "Added to actions"
                                  : isAccepting
                                    ? "Adding…"
                                    : "Add to my actions"}
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    )
                  ) : (
                    <p>Loading action recommendations&hellip;</p>
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
