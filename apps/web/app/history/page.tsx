"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  api,
  type HealthHistoryItem,
  type HealthHistoryKind,
  type Person,
  type SessionSummary,
} from "../../lib/api";

type HistoryFilter = "all" | HealthHistoryKind;

const filterLabels: Record<HistoryFilter, string> = {
  all: "All",
  symptom: "Symptoms",
  metric: "Metrics",
  report_observation: "Reports",
};

const kindLabels: Record<HealthHistoryKind, string> = {
  symptom: "Symptom",
  metric: "Metric",
  report_observation: "Report observation",
};

export default function HealthHistoryPage() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [selectedPerson, setSelectedPerson] = useState<Person | null>(null);
  const [history, setHistory] = useState<HealthHistoryItem[]>([]);
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadHistory() {
      try {
        const [current, persons] = await Promise.all([
          api.session(),
          api.persons(),
        ]);
        const requestedPersonId = new URLSearchParams(window.location.search).get(
          "person_id",
        );
        const person =
          persons.find((candidate) => candidate.id === requestedPersonId) ??
          persons.find((candidate) => candidate.is_default) ??
          persons[0] ??
          null;
        if (cancelled) {
          return;
        }
        setSession(current);
        setSelectedPerson(person);
        if (!person) {
          setLoading(false);
          return;
        }
        const rows = await api.healthHistory(person.id);
        if (!cancelled) {
          setHistory(rows);
          setLoading(false);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load Health History",
          );
          setLoading(false);
        }
      }
    }

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleHistory = useMemo(
    () =>
      filter === "all"
        ? history
        : history.filter((item) => item.kind === filter),
    [filter, history],
  );

  return (
    <main>
      <header>
        <Link className="back-link" href="/">
          Healthy
        </Link>
        <h1>Health History</h1>
        <p className="lede">
          A read-only timeline of symptoms, metrics, and confirmed report
          observations for the selected Person.
        </p>
      </header>

      {error ? (
        <p className="error" role="alert" data-testid="history-error">
          {error}
        </p>
      ) : null}

      {loading ? (
        <p data-testid="history-loading">Loading Health History&hellip;</p>
      ) : session && selectedPerson ? (
        <section className="history-page" data-testid="history-page">
          <div className="history-toolbar">
            <p>
              For <strong>{selectedPerson.display_name}</strong>
            </p>
            <div className="history-filters" role="group" aria-label="History type filter">
              {(Object.keys(filterLabels) as HistoryFilter[]).map((option) => (
                <button
                  className="history-filter"
                  key={option}
                  type="button"
                  aria-pressed={filter === option}
                  data-testid={`history-filter-${option}`}
                  onClick={() => setFilter(option)}
                >
                  {filterLabels[option]}
                </button>
              ))}
            </div>
          </div>

          {history.length === 0 ? (
            <p className="history-empty" data-testid="history-empty">
              No health history yet.
            </p>
          ) : visibleHistory.length === 0 ? (
            <p className="history-empty" data-testid="history-filter-empty">
              No {filterLabels[filter].toLowerCase()} in this history.
            </p>
          ) : (
            <ol className="history-list" data-testid="history-list">
              {visibleHistory.map((item) => (
                <li
                  className="history-item"
                  key={item.id}
                  data-testid="history-item"
                  data-history-kind={item.kind}
                  data-source-id={item.source.id}
                >
                  <div className="history-item-heading">
                    <span className="history-kind">{kindLabels[item.kind]}</span>
                    <time dateTime={item.occurred_at}>
                      {new Date(item.occurred_at).toLocaleString()}
                    </time>
                  </div>
                  <h2>{item.title}</h2>
                  {item.primary_value ? (
                    <p className="history-value">
                      {item.primary_value}
                      {item.unit ? ` ${item.unit}` : ""}
                    </p>
                  ) : null}
                  {item.detail ? <p>{item.detail}</p> : null}
                  {item.source.report_source_name ? (
                    <p className="history-provenance">
                      Source: {item.source.report_source_name}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : !error ? (
        <p data-testid="history-no-person">No Person is available for History.</p>
      ) : null}
    </main>
  );
}
