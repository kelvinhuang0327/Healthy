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
  weight_kg: number | null;
  blood_glucose_mg_dl: number | null;
  note: string | null;
  created_at: string;
};

export type SymptomLog = {
  id: string;
  person_id: string;
  symptom: string;
  occurred_at: string;
  severity: number;
  duration_minutes: number | null;
  note: string | null;
  created_at: string;
};

export type HealthAction = {
  id: string;
  person_id: string;
  title: string;
  description: string | null;
  due_at: string | null;
  status: "todo" | "done";
  completed_at: string | null;
  created_at: string;
  updated_at: string;
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

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body) {
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
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
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
  createHealthMetric: (
    personId: string,
    payload: {
      recorded_at: string;
      systolic_bp_mm_hg: number | null;
      diastolic_bp_mm_hg: number | null;
      heart_rate_bpm: number | null;
      weight_kg: number | null;
      blood_glucose_mg_dl: number | null;
      note: string | null;
    },
  ) =>
    request<HealthMetric>(`/persons/${personId}/metrics`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
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
};
