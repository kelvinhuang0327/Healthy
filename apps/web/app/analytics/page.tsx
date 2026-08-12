"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  api,
  type HealthAnalytics,
  type Person,
  type SessionSummary,
} from "../../lib/api";

const periodOptions = [30, 90, 180, 365] as const;

function formatNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(3).replace(/\.?0+$/, "");
}

function formatPercent(value: number | null): string {
  return value === null ? "Not available" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function directionLabel(direction: HealthAnalytics["summaries"][number]["direction"]): string {
  switch (direction) {
    case "up":
      return "Increasing";
    case "down":
      return "Decreasing";
    case "stable":
      return "Stable";
    default:
      return "No data";
  }
}

function HealthAnalyticsContent() {
  const searchParams = useSearchParams();
  const requestedPersonId = searchParams.get("person_id");
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [days, setDays] = useState(90);
  const [analytics, setAnalytics] = useState<HealthAnalytics | null>(null);
  const [loadedAnalyticsKey, setLoadedAnalyticsKey] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.session(), api.persons()])
      .then(([current, rows]) => {
        setSession(current);
        setPersons(rows);
        const requested = rows.find((person) => person.id === requestedPersonId);
        setSelectedPersonId(
          requested?.id ?? rows.find((person) => person.is_default)?.id ?? rows[0]?.id ?? null,
        );
        setError("");
      })
      .catch(() => {
        setSession(null);
        setPersons([]);
        setSelectedPersonId(null);
        setError("Sign in to view Health Analytics.");
      })
      .finally(() => setAuthLoading(false));
  }, [requestedPersonId]);

  const selectedPerson =
    persons.find((person) => person.id === selectedPersonId) ??
    persons.find((person) => person.is_default) ??
    persons[0];
  const effectiveSelectedPersonId = selectedPerson?.id;
  const analyticsKey = effectiveSelectedPersonId
    ? `${effectiveSelectedPersonId}:${days}`
    : null;
  const analyticsLoading = Boolean(analyticsKey && loadedAnalyticsKey !== analyticsKey);

  useEffect(() => {
    if (!effectiveSelectedPersonId) {
      return;
    }
    let cancelled = false;
    api
      .healthAnalytics(effectiveSelectedPersonId, days)
      .then((result) => {
        if (!cancelled) {
          setAnalytics(result);
          setLoadedAnalyticsKey(analyticsKey);
          setError("");
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setAnalytics(null);
          setLoadedAnalyticsKey(analyticsKey);
          setError(reason instanceof Error ? reason.message : "Could not load Health Analytics");
        }
      })
      ;
    return () => {
      cancelled = true;
    };
  }, [analyticsKey, days, effectiveSelectedPersonId]);

  return (
    <main>
      <header>
        <Link className="back-link" href="/">
          Back to Today
        </Link>
        <h1>Health Analytics</h1>
        <p className="lede">
          Review deterministic changes in the health metrics you have recorded. These summaries
          describe the selected period and do not diagnose or predict health outcomes.
        </p>
      </header>

      {authLoading ? <p data-testid="analytics-auth-loading">Loading Health Analytics&hellip;</p> : null}
      {!authLoading && !session ? (
        <p className="error" role="alert" data-testid="analytics-auth-error">
          {error}
        </p>
      ) : null}
      {!authLoading && session && !selectedPerson ? (
        <p data-testid="analytics-no-person">No Person is available for Health Analytics.</p>
      ) : null}

      {!authLoading && session && selectedPerson ? (
        <section className="history-page" data-testid="analytics-page">
          <div className="history-toolbar">
            <label>
              Person
              <select
                value={effectiveSelectedPersonId}
                onChange={(event) => setSelectedPersonId(event.target.value)}
                data-testid="analytics-person-select"
              >
                {persons.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.display_name} ({person.relationship})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Period
              <select
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
                data-testid="analytics-period"
              >
                {periodOptions.map((option) => (
                  <option key={option} value={option}>
                    Last {option} days
                  </option>
                ))}
              </select>
            </label>
          </div>

          {error ? (
            <p className="error" role="alert" data-testid="analytics-error">
              {error}
            </p>
          ) : null}
          {analyticsLoading ? <p data-testid="analytics-loading">Loading trends&hellip;</p> : null}

          {analytics && !analyticsLoading ? (
            <section className="grid" data-testid="analytics-grid">
              {analytics.summaries.map((summary) => (
                <article
                  className="card"
                  key={summary.metric}
                  data-testid="analytics-card"
                  data-analytics-metric={summary.metric}
                >
                  <div className="session">
                    <h2>{summary.label}</h2>
                    <span className="pill">{directionLabel(summary.direction)}</span>
                  </div>
                  {summary.points === 0 ? (
                    <p className="history-empty" data-testid="analytics-no-data">
                      No data recorded in the selected period.
                    </p>
                  ) : (
                    <>
                      <p className="history-value">
                        Latest: {formatNumber(summary.last_value ?? 0)} {summary.unit}
                      </p>
                      <p className="history-provenance">{summary.points} data point(s)</p>
                      <p className="history-provenance">
                        Change from first to latest: {formatPercent(summary.change_percent)}
                      </p>
                      <p className="history-provenance">
                        Daily change: {summary.slope_per_day === null
                          ? "Not available"
                          : `${formatNumber(summary.slope_per_day)} ${summary.unit}/day`}
                      </p>
                    </>
                  )}
                </article>
              ))}
            </section>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}

export default function HealthAnalyticsPage() {
  return (
    <Suspense fallback={<main><p data-testid="analytics-auth-loading">Loading Health Analytics&hellip;</p></main>}>
      <HealthAnalyticsContent />
    </Suspense>
  );
}
