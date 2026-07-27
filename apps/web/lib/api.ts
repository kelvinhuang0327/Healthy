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
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type Registration = {
  account: Account;
  default_person: Person;
  session: SessionSummary;
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
  createPerson: (payload: { display_name: string; relationship: string }) =>
    request<Person>("/persons", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
