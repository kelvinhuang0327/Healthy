export type Account = {
  id: string;
  normalized_email: string;
  status: string;
  created_at: string;
};

export type SessionSummary = {
  id: string;
  account: Account;
  expires_at: string;
};

export type Person = {
  id: string;
  owner_account_id: string;
  display_name: string;
  relationship: string;
  height_cm: number | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type Registration = {
  account: Account;
  default_person: Person;
  session: SessionSummary;
};

export type HealthMetric = {
  id: string;
  person_id: string;
  recorded_at: string;
  systolic_bp_mm_hg: number | null;
  diastolic_bp_mm_hg: number | null;
  heart_rate_bpm: number | null;
  steps: number | null;
  weight_kg: number | null;
  blood_glucose_mg_dl: number | null;
  sleep_hours: number | null;
  note: string | null;
  source_type: "manual" | "external_csv" | string;
  created_at: string;
};

export type ExternalMetricCsvImportSummary = {
  source_type: "external_csv";
  total_rows: number;
  imported_count: number;
  duplicate_count: number;
};

export type HealthAnalyticsMetric = {
  metric: string;
  label: string;
  unit: string;
  points: number;
  first_value: number | null;
  last_value: number | null;
  change_percent: number | null;
  slope_per_day: number | null;
  direction: "up" | "down" | "stable" | "no_data";
};

export type HealthAnalytics = {
  period_days: number;
  summaries: HealthAnalyticsMetric[];
};

export type HealthScoreComponent = {
  kind: "cardiovascular" | "metabolic" | "activity" | "weight" | "overall";
  label: string;
  points: number;
  penalty: number;
  evidence_ids: string[];
  rationale: string;
};

export type HealthScoreCoverage = {
  evaluated_inputs: string[];
  missing_inputs: string[];
  unsupported_sources: string[];
};

export type HealthScore = {
  score: number;
  status: "stable" | "monitor" | "attention" | "insufficient_data";
  rule_version: string;
  anchor_at: string | null;
  data_points: number;
  components: HealthScoreComponent[];
  coverage: HealthScoreCoverage;
  limitations: string;
};

export type RiskAlert = {
  rule_code: string;
  risk_type: string;
  severity: "medium" | "high";
  status: "active";
  evidence: {
    source_kind: "health_metric" | "lab_report";
    source_id: string;
    person_id: string;
    observed_at: string;
    observation_id: string | null;
    report_id: string | null;
    report_source_name: string | null;
  };
};

export type RiskAlerts = {
  active_count: number;
  alerts: RiskAlert[];
};

export type ActionRecommendation = {
  recommendation_code: string;
  source_rule_code: string;
  source_risk_type: string;
  source_severity: "medium" | "high";
  title: string;
  rationale: string;
  suggested_action: string;
  matching_alert_count: number;
  rule_version: string;
  limitations: string;
  evidence: RiskAlert["evidence"];
};

export type ActionRecommendations = {
  recommendations: ActionRecommendation[];
};

export type SymptomLog = {
  id: string;
  person_id: string;
  symptom: string;
  occurred_at: string;
  severity: number;
  duration_minutes: number | null;
  estimated_start_date: string | null;
  estimated_duration_days: number | null;
  note: string | null;
  created_at: string;
};

export type HealthAction = {
  id: string;
  person_id: string;
  title: string;
  description: string | null;
  due_at: string | null;
  origin_type: "manual" | "action_recommendation";
  recommendation_code: string | null;
  recommendation_rule_version: string | null;
  source_rule_code: string | null;
  source_evidence_kind: "health_metric" | "lab_report" | null;
  source_evidence_id: string | null;
  source_observation_id: string | null;
  source_report_id: string | null;
  source_evidence_observed_at: string | null;
  status: "todo" | "done";
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type HealthActionReminder = {
  id: string;
  action_id: string;
  timezone_name: string;
  local_time: string;
  email_enabled: boolean;
  snoozed_until: string | null;
  last_acknowledged_local_date: string | null;
  created_at: string;
  updated_at: string;
};

export type NotificationCapabilities = {
  email_available: boolean;
};

export type DueHealthActionReminder = {
  reminder_id: string;
  action_id: string;
  action_title: string;
  action_origin_type: "manual" | "action_recommendation";
  timezone_name: string;
  local_time: string;
  local_date: string;
  snoozed_until: string | null;
  last_acknowledged_local_date: string | null;
};

export type ActionRecommendationAcceptance = {
  action: HealthAction;
  created: boolean;
};

export type HealthActionOutcome = {
  id: string;
  action_id: string;
  note: string;
  observed_at: string;
  created_at: string;
};

export type DailyAttentionItem = {
  kind: string;
  title: string;
  rationale: string;
  evidence_ids: string[];
  confidence: "low" | "medium" | "high";
  limitations: string;
  rule_version: string;
};

export type InsightEvidence = {
  source_kind: "metric" | "symptom" | "report_observation";
  source_record_id: string;
  occurred_at: string;
  role: string | null;
  report_id: string | null;
  report_source_name: string | null;
};

export type Insight = {
  id: string;
  insight_type: "metric_change" | "symptom_pattern" | "report_observation_update";
  headline: string;
  observed_at: string;
  evidence: InsightEvidence[];
};

export type AssistantToday = {
  generated_at: string;
  lookback_days: number;
  latest_metric: HealthMetric | null;
  recent_symptoms: SymptomLog[];
  open_or_recent_actions: HealthAction[];
  recent_outcomes: HealthActionOutcome[];
  daily_attention: DailyAttentionItem[];
  insights: Insight[];
};

export type HealthHistoryKind = "symptom" | "metric" | "report_observation";

export type HealthHistoryItem = {
  id: string;
  kind: HealthHistoryKind;
  occurred_at: string;
  title: string;
  primary_value: string | null;
  unit: string | null;
  detail: string | null;
  source: {
    type: HealthHistoryKind;
    id: string;
    report_id: string | null;
    report_source_name: string | null;
  };
};

function csrfToken(): string {
  const row = document.cookie
    .split("; ")
    .find((cookie) => cookie.startsWith("healthy_csrf="));
  return row ? decodeURIComponent(row.slice("healthy_csrf=".length)) : "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) {
      headers.set("X-CSRF-Token", csrf);
    }
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string; code?: string; row?: number; field?: string };
    } | null;
    let message = `Request failed (${response.status})`;
    if (body?.detail) {
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (typeof body.detail === "object") {
        const d = body.detail;
        message = d.message || d.code ? `${d.message || "Error"}${d.code ? ` (${d.code})` : ""}${d.row ? ` at row ${d.row}` : ""}${d.field ? `, field ${d.field}` : ""}` : message;
      }
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export type HealthReportObservation = {
  id: string;
  report_id: string;
  person_id: string;
  code: string;
  display_name: string;
  value_numeric: number | null;
  value_text: string | null;
  unit: string | null;
  reference_range: string | null;
  observed_at: string;
  created_at: string;
};

export type HealthReportSummary = {
  id: string;
  person_id: string;
  schema_version: string;
  source_name: string;
  reported_at: string;
  canonical_sha256: string;
  status: "pending" | "confirmed";
  created_at: string;
  confirmed_at: string | null;
};

export type HealthReportDetail = {
  id: string;
  person_id: string;
  schema_version: string;
  source_name: string;
  reported_at: string;
  canonical_sha256: string;
  status: "pending" | "confirmed";
  created_at: string;
  confirmed_at: string | null;
  observations: HealthReportObservation[];
};

export const api = {
  register: (payload: {
    email: string;
    password: string;
    display_name: string;
  }) =>
    request<Registration>("/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  login: (payload: { email: string; password: string }) =>
    request<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logout: () =>
    request<void>("/sessions/current", {
      method: "DELETE",
    }),
  session: () => request<SessionSummary>("/session"),
  persons: () => request<Person[]>("/persons"),
  notificationCapabilities: () =>
    request<NotificationCapabilities>("/notification-capabilities"),
  person: (personId: string) => request<Person>(`/persons/${personId}`),
  updatePersonHeight: (personId: string, heightCm: number | null) =>
    request<Person>(`/persons/${personId}/profile`, {
      method: "PATCH",
      body: JSON.stringify({ height_cm: heightCm }),
    }),
  createPerson: (payload: { display_name: string; relationship: string }) =>
    request<Person>("/persons", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  healthMetrics: (personId: string) =>
    request<HealthMetric[]>(`/persons/${personId}/metrics`),
  healthScore: (personId: string) =>
    request<HealthScore>(`/persons/${personId}/health-score`),
  riskAlerts: (personId: string) =>
    request<RiskAlerts>(`/persons/${personId}/risk-alerts`),
  actionRecommendations: (personId: string) =>
    request<ActionRecommendations>(`/persons/${personId}/action-recommendations`),
  acceptActionRecommendation: (
    personId: string,
    recommendationCode: string,
    payload: {
      rule_version: string;
      source_kind: "health_metric" | "lab_report";
      source_id: string;
      observation_id: string | null;
      report_id: string | null;
      observed_at: string;
    },
  ) =>
    request<ActionRecommendationAcceptance>(
      `/persons/${personId}/action-recommendations/${recommendationCode}/accept`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  createHealthMetric: (
    personId: string,
    payload: {
      recorded_at: string;
      systolic_bp_mm_hg: number | null;
      diastolic_bp_mm_hg: number | null;
      heart_rate_bpm: number | null;
      steps: number | null;
      weight_kg: number | null;
      blood_glucose_mg_dl: number | null;
      sleep_hours: number | null;
      note: string | null;
    },
  ) =>
    request<HealthMetric>(`/persons/${personId}/metrics`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importMetricCsv: (personId: string, csvPayload: string | Blob) =>
    request<ExternalMetricCsvImportSummary>(
      `/persons/${personId}/metrics/imports/csv`,
      {
        method: "POST",
        headers: {
          "Content-Type": "text/csv",
        },
        body: csvPayload,
      },
    ),
  symptomLogs: (personId: string) =>
    request<SymptomLog[]>(`/persons/${personId}/symptoms`),
  symptomLog: (personId: string, symptomId: string) =>
    request<SymptomLog>(`/persons/${personId}/symptoms/${symptomId}`),
  createSymptomLog: (
    personId: string,
    payload: {
      symptom: string;
      occurred_at: string;
      severity: number;
      duration_minutes: number | null;
      estimated_start_date: string | null;
      estimated_duration_days: number | null;
      note: string | null;
    },
  ) =>
    request<SymptomLog>(`/persons/${personId}/symptoms`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  healthActions: (personId: string) =>
    request<HealthAction[]>(`/persons/${personId}/actions`),
  healthAction: (personId: string, actionId: string) =>
    request<HealthAction>(`/persons/${personId}/actions/${actionId}`),
  createHealthAction: (
    personId: string,
    payload: {
      title: string;
      description: string | null;
      due_at: string | null;
    },
  ) =>
    request<HealthAction>(`/persons/${personId}/actions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  completeHealthAction: (personId: string, actionId: string) =>
    request<HealthAction>(
      `/persons/${personId}/actions/${actionId}/complete`,
      { method: "POST" },
    ),
  healthActionReminder: (personId: string, actionId: string) =>
    request<HealthActionReminder>(
      `/persons/${personId}/actions/${actionId}/reminder`,
    ),
  upsertHealthActionReminder: (
    personId: string,
    actionId: string,
    payload: { timezone_name: string; local_time: string },
  ) =>
    request<HealthActionReminder>(
      `/persons/${personId}/actions/${actionId}/reminder`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  setHealthActionEmailNotification: (
    personId: string,
    actionId: string,
    enabled: boolean,
  ) =>
    request<HealthActionReminder>(
      `/persons/${personId}/actions/${actionId}/reminder/channels/email`,
      { method: "PUT", body: JSON.stringify({ enabled }) },
    ),
  deleteHealthActionReminder: (personId: string, actionId: string) =>
    request<void>(
      `/persons/${personId}/actions/${actionId}/reminder`,
      { method: "DELETE" },
    ),
  acknowledgeHealthActionReminder: (personId: string, actionId: string) =>
    request<HealthActionReminder>(
      `/persons/${personId}/actions/${actionId}/reminder/acknowledge`,
      { method: "POST" },
    ),
  snoozeHealthActionReminder: (
    personId: string,
    actionId: string,
    until: string,
  ) =>
    request<HealthActionReminder>(
      `/persons/${personId}/actions/${actionId}/reminder/snooze`,
      { method: "POST", body: JSON.stringify({ until }) },
    ),
  dueHealthActionReminders: (personId: string) =>
    request<DueHealthActionReminder[]>(`/persons/${personId}/reminders/due`),
  createHealthActionOutcome: (
    personId: string,
    actionId: string,
    payload: { note: string; observed_at: string },
  ) =>
    request<HealthActionOutcome>(
      `/persons/${personId}/actions/${actionId}/outcomes`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  importReport: (personId: string, payload: unknown) =>
    request<HealthReportDetail>(`/persons/${personId}/reports`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  healthReports: (personId: string) =>
    request<HealthReportSummary[]>(`/persons/${personId}/reports`),
  healthReport: (personId: string, reportId: string) =>
    request<HealthReportDetail>(`/persons/${personId}/reports/${reportId}`),
  confirmReport: (personId: string, reportId: string) =>
    request<HealthReportDetail>(
      `/persons/${personId}/reports/${reportId}/confirm`,
      { method: "POST" },
    ),
  assistantToday: (personId: string) =>
    request<AssistantToday>(`/persons/${personId}/assistant/today`),
  healthHistory: (personId: string) =>
    request<HealthHistoryItem[]>(`/persons/${personId}/history`),
  healthAnalytics: (personId: string, days = 90) =>
    request<HealthAnalytics>(`/persons/${personId}/analytics?days=${days}`),
};
