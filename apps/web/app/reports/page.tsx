"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  api,
  type HealthReportDetail,
  type Person,
  type ReportIntake,
  type ReportIntakeDetail,
  type ReportIntakeObservationEdit,
  type SessionSummary,
} from "../../lib/api";

type ReviewObservation = {
  id?: string;
  code: string;
  display_name: string;
  value_numeric: string;
  value_text: string;
  unit: string;
  reference_range: string;
  observed_at: string;
  parser_provenance: string;
  parser_confidence: number | null;
};

function dateInputValue(value: string | null): string {
  return value ? value.slice(0, 10) : "";
}

function observationForReview(
  observation: ReportIntakeDetail["observations"][number],
): ReviewObservation {
  return {
    id: observation.id,
    code: observation.code,
    display_name: observation.display_name,
    value_numeric:
      observation.value_numeric === null
        ? ""
        : String(observation.value_numeric),
    value_text: observation.value_text ?? "",
    unit: observation.unit ?? "",
    reference_range: observation.reference_range ?? "",
    observed_at: dateInputValue(observation.observed_at),
    parser_provenance: observation.parser_provenance,
    parser_confidence: observation.parser_confidence,
  };
}

function emptyObservation(): ReviewObservation {
  return {
    code: "NEW_OBSERVATION",
    display_name: "New observation",
    value_numeric: "",
    value_text: "",
    unit: "",
    reference_range: "",
    observed_at: "",
    parser_provenance: "human_added",
    parser_confidence: null,
  };
}

function toApiObservation(
  observation: ReviewObservation,
): ReportIntakeObservationEdit {
  const numericText = observation.value_numeric.trim();
  const numericValue = numericText ? Number(numericText) : null;
  if (numericText && !Number.isFinite(numericValue)) {
    throw new Error(
      `Enter a finite numeric value for ${observation.display_name}.`,
    );
  }
  return {
    ...(observation.id ? { id: observation.id } : {}),
    code: observation.code,
    display_name: observation.display_name,
    value_numeric: numericValue,
    value_text: observation.value_text.trim() || null,
    unit: observation.unit.trim() || null,
    reference_range: observation.reference_range.trim() || null,
    observed_at: observation.observed_at
      ? `${observation.observed_at}T00:00:00Z`
      : null,
  };
}

function intakeStatusLabel(intake: ReportIntake): string {
  switch (intake.status) {
    case "pending_review":
      return "Needs review";
    case "confirmed":
      return "Confirmed";
    default:
      return "Could not extract";
  }
}

export default function ReportsPage() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [intakes, setIntakes] = useState<ReportIntake[]>([]);
  const [selectedIntakeId, setSelectedIntakeId] = useState<string | null>(null);
  const [selectedIntake, setSelectedIntake] =
    useState<ReportIntakeDetail | null>(null);
  const [confirmedReport, setConfirmedReport] =
    useState<HealthReportDetail | null>(null);
  const [loadedIntakePersonId, setLoadedIntakePersonId] = useState<
    string | null
  >(null);
  const [loadedDetailId, setLoadedDetailId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [reportedAt, setReportedAt] = useState("");
  const [observations, setObservations] = useState<ReviewObservation[]>([]);
  const [authLoading, setAuthLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.session(), api.persons()])
      .then(([current, rows]) => {
        if (cancelled) {
          return;
        }
        const requestedPersonId = new URLSearchParams(
          window.location.search,
        ).get("person_id");
        const person =
          rows.find((candidate) => candidate.id === requestedPersonId) ??
          rows.find((candidate) => candidate.is_default) ??
          rows[0] ??
          null;
        setSession(current);
        setPersons(rows);
        setSelectedPersonId(person?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setSession(null);
          setPersons([]);
          setSelectedPersonId(null);
          setError("Sign in to review health reports.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAuthLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedPersonId) {
      return;
    }
    let cancelled = false;
    api
      .reportIntakes(selectedPersonId)
      .then((rows) => {
        if (cancelled) {
          return;
        }
        setIntakes(rows);
        setSelectedIntakeId((current) =>
          rows.some((intake) => intake.id === current)
            ? current
            : (rows[0]?.id ?? null),
        );
        setLoadedIntakePersonId(selectedPersonId);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load report intakes.",
          );
          setIntakes([]);
          setSelectedIntakeId(null);
          setLoadedIntakePersonId(selectedPersonId);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedPersonId]);

  useEffect(() => {
    if (!selectedPersonId || !selectedIntakeId) {
      return;
    }
    let cancelled = false;
    api
      .reportIntake(selectedPersonId, selectedIntakeId)
      .then((detail) => {
        if (!cancelled) {
          setSelectedIntake(detail);
          setConfirmedReport(null);
          setSourceName(detail.source_name);
          setReportedAt(dateInputValue(detail.reported_at));
          setObservations(detail.observations.map(observationForReview));
          setDirty(false);
          if (detail.status === "confirmed" && detail.report_id) {
            api
              .healthReport(selectedPersonId, detail.report_id)
              .then(setConfirmedReport)
              .catch(() => setConfirmedReport(null));
          }
          setLoadedDetailId(selectedIntakeId);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the report review.",
          );
          setSelectedIntake(null);
          setLoadedDetailId(selectedIntakeId);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIntakeId, selectedPersonId]);

  const selectedPerson = useMemo(
    () => persons.find((person) => person.id === selectedPersonId) ?? null,
    [persons, selectedPersonId],
  );

  function selectPerson(personId: string) {
    setSelectedPersonId(personId);
    setIntakes([]);
    setSelectedIntakeId(null);
    setSelectedIntake(null);
    setConfirmedReport(null);
    setLoadedIntakePersonId(null);
    setLoadedDetailId(null);
    setDirty(false);
    setError("");
    setMessage("");
  }

  const intakesLoading = Boolean(
    selectedPersonId && loadedIntakePersonId !== selectedPersonId,
  );
  const detailLoading = Boolean(
    selectedIntakeId && loadedDetailId !== selectedIntakeId,
  );

  function updateObservation(
    index: number,
    field: keyof ReviewObservation,
    value: string,
  ) {
    setObservations((current) =>
      current.map((observation, candidateIndex) =>
        candidateIndex === index
          ? { ...observation, [field]: value }
          : observation,
      ),
    );
    setDirty(true);
  }

  async function uploadReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedPersonId || !selectedFile) {
      setError("Choose a PDF, JPEG, or PNG report file first.");
      return;
    }
    setUploading(true);
    setError("");
    setMessage("");
    try {
      const detail = await api.createReportIntake(
        selectedPersonId,
        selectedFile,
      );
      setSelectedFile(null);
      const fileInput = document.getElementById(
        "report-file",
      ) as HTMLInputElement | null;
      if (fileInput) {
        fileInput.value = "";
      }
      setIntakes((current) => [
        detail,
        ...current.filter((intake) => intake.id !== detail.id),
      ]);
      setSelectedIntakeId(detail.id);
      setMessage(
        detail.status === "pending_review"
          ? "Report extracted. Review every candidate before confirming."
          : (detail.error_message ?? "The report could not be extracted."),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not upload the report.",
      );
    } finally {
      setUploading(false);
    }
  }

  async function saveReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !selectedPersonId ||
      !selectedIntake ||
      selectedIntake.status !== "pending_review"
    ) {
      return;
    }
    if (!sourceName.trim()) {
      setError("Enter the report source before saving.");
      return;
    }
    if (!reportedAt) {
      setError("Enter the report date before saving.");
      return;
    }
    try {
      const candidateEdits = observations.map(toApiObservation);
      setSaving(true);
      setError("");
      const detail = await api.updateReportIntake(
        selectedPersonId,
        selectedIntake.id,
        {
          source_name: sourceName.trim(),
          reported_at: `${reportedAt}T00:00:00Z`,
          observations: candidateEdits,
        },
      );
      setSelectedIntake(detail);
      setIntakes((current) =>
        current.map((intake) => (intake.id === detail.id ? detail : intake)),
      );
      setMessage(
        "Review changes saved. Confirm only when every value is correct.",
      );
      setDirty(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not save the review.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function confirmReview() {
    if (
      !selectedPersonId ||
      !selectedIntake ||
      dirty ||
      selectedIntake.status !== "pending_review"
    ) {
      return;
    }
    setConfirming(true);
    setError("");
    try {
      const detail = await api.confirmReportIntake(
        selectedPersonId,
        selectedIntake.id,
      );
      setSelectedIntake(detail);
      setIntakes((current) =>
        current.map((intake) => (intake.id === detail.id ? detail : intake)),
      );
      setMessage("Report confirmed and added to Health History.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not confirm the report.",
      );
    } finally {
      setConfirming(false);
    }
  }

  function renderIntakeCard(intake: ReportIntake) {
    return (
      <li
        className={`report-intake-card report-intake-${intake.status}`}
        key={intake.id}
        data-testid="report-intake-card"
        data-intake-id={intake.id}
        data-intake-status={intake.status}
      >
        <button
          className="report-intake-select"
          type="button"
          onClick={() => setSelectedIntakeId(intake.id)}
          aria-pressed={selectedIntake?.id === intake.id}
        >
          <span>
            <strong>{intake.source_name}</strong>
            <small>{intake.source_filename}</small>
          </span>
          <span className="pill">{intakeStatusLabel(intake)}</span>
        </button>
        <p>
          {intake.extraction_method === "digital_pdf"
            ? "Digital PDF text"
            : "Image OCR"}
          {intake.reported_at
            ? ` · ${dateInputValue(intake.reported_at)}`
            : " · Report date needed"}
        </p>
        {intake.status === "failed" && intake.error_message ? (
          <p className="error" data-testid="report-intake-failure">
            {intake.error_message}
          </p>
        ) : null}
        {intake.status === "confirmed" && intake.report_id ? (
          <p className="confirmed-report-reference">
            HealthReport <code>{intake.report_id}</code>
          </p>
        ) : null}
      </li>
    );
  }

  return (
    <main>
      <header>
        <Link className="back-link" href="/">
          Back to Today
        </Link>
        <h1>Report Intake &amp; Review</h1>
        <p className="lede">
          Upload a synthetic health report, inspect the extracted candidates,
          correct them, and explicitly confirm the final report. This screen
          does not diagnose or recommend care.
        </p>
      </header>

      {error ? (
        <p className="error" role="alert" data-testid="report-intake-error">
          {error}
        </p>
      ) : null}
      {message ? (
        <p
          className="import-summary"
          role="status"
          data-testid="report-intake-message"
        >
          {message}
        </p>
      ) : null}

      {authLoading ? (
        <p data-testid="report-intake-loading">Loading Report Intake&hellip;</p>
      ) : session && selectedPerson ? (
        <section className="reports-page" data-testid="reports-page">
          <div className="reports-toolbar">
            <label>
              Person
              <select
                value={selectedPerson.id}
                onChange={(event) => selectPerson(event.target.value)}
                data-testid="reports-person-select"
              >
                {persons.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.display_name} ({person.relationship})
                  </option>
                ))}
              </select>
            </label>
          </div>

          <article className="card report-upload-card">
            <h2>Upload a health report</h2>
            <p className="muted">
              Supported files: PDF, JPEG, and PNG. Files are extracted in memory
              and are not saved as raw uploads.
            </p>
            <form
              onSubmit={uploadReport}
              data-testid="report-intake-upload-form"
            >
              <label htmlFor="report-file">Report file</label>
              <input
                id="report-file"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
                onChange={(event) =>
                  setSelectedFile(event.target.files?.[0] ?? null)
                }
                data-testid="report-file-input"
              />
              <button
                type="submit"
                disabled={uploading || !selectedFile}
                data-testid="report-upload-button"
              >
                {uploading ? "Extracting report…" : "Upload and extract"}
              </button>
            </form>
          </article>

          <section className="card" data-testid="report-intake-list-section">
            <div className="report-section-heading">
              <div>
                <span className="pill">Persistent intake</span>
                <h2>Reports for {selectedPerson.display_name}</h2>
              </div>
              {intakesLoading ? <span className="muted">Loading…</span> : null}
            </div>
            {intakes.length === 0 && !intakesLoading ? (
              <p className="history-empty" data-testid="report-intake-empty">
                No report files have been uploaded for this Person yet.
              </p>
            ) : (
              <ul className="report-intake-list">
                {intakes.map(renderIntakeCard)}
              </ul>
            )}
          </section>

          {detailLoading ? (
            <p data-testid="report-review-loading">Loading review…</p>
          ) : null}
          {selectedIntake && selectedIntake.status === "pending_review" ? (
            <section
              className="card report-review-card"
              data-testid="report-review-section"
            >
              <div className="report-section-heading">
                <div>
                  <span className="pill">Human review required</span>
                  <h2>Review extracted candidates</h2>
                </div>
                <span className="muted">
                  {selectedIntake.extraction_method === "digital_pdf"
                    ? "Digital PDF text"
                    : "Image OCR"}
                </span>
              </div>
              <p className="review-warning" data-testid="report-review-warning">
                Extraction is a draft. Check the source document and correct
                every candidate before saving and confirming. No report is added
                to Health History until you confirm it.
              </p>
              <form onSubmit={saveReview} data-testid="report-review-form">
                <div className="report-review-meta">
                  <label>
                    Source name
                    <input
                      value={sourceName}
                      onChange={(event) => {
                        setSourceName(event.target.value);
                        setDirty(true);
                      }}
                      data-testid="report-source-name"
                    />
                  </label>
                  <label>
                    Report date
                    <input
                      type="date"
                      value={reportedAt}
                      onChange={(event) => {
                        setReportedAt(event.target.value);
                        setDirty(true);
                      }}
                      data-testid="report-reported-at"
                    />
                  </label>
                </div>
                <div className="report-table-wrap">
                  <table
                    className="report-observations"
                    data-testid="report-candidate-table"
                  >
                    <thead>
                      <tr>
                        <th>Code / name</th>
                        <th>Numeric value</th>
                        <th>Text value</th>
                        <th>Unit</th>
                        <th>Reference shown in source</th>
                        <th>Observed date</th>
                        <th>Provenance</th>
                        <th aria-label="Actions" />
                      </tr>
                    </thead>
                    <tbody>
                      {observations.map((observation, index) => (
                        <tr
                          key={observation.id ?? `new-${index}`}
                          data-testid="report-candidate-row"
                          data-observation-id={observation.id ?? "new"}
                        >
                          <td>
                            <label>
                              <span className="sr-only">Observation code</span>
                              <input
                                value={observation.code}
                                onChange={(event) =>
                                  updateObservation(
                                    index,
                                    "code",
                                    event.target.value,
                                  )
                                }
                                data-testid="report-candidate-code"
                              />
                            </label>
                            <label>
                              <span className="sr-only">
                                Observation display name
                              </span>
                              <input
                                value={observation.display_name}
                                onChange={(event) =>
                                  updateObservation(
                                    index,
                                    "display_name",
                                    event.target.value,
                                  )
                                }
                                data-testid="report-candidate-name"
                              />
                            </label>
                          </td>
                          <td>
                            <input
                              type="number"
                              step="any"
                              value={observation.value_numeric}
                              onChange={(event) =>
                                updateObservation(
                                  index,
                                  "value_numeric",
                                  event.target.value,
                                )
                              }
                              data-testid="report-candidate-value"
                            />
                          </td>
                          <td>
                            <input
                              value={observation.value_text}
                              onChange={(event) =>
                                updateObservation(
                                  index,
                                  "value_text",
                                  event.target.value,
                                )
                              }
                              data-testid="report-candidate-text"
                            />
                          </td>
                          <td>
                            <input
                              value={observation.unit}
                              onChange={(event) =>
                                updateObservation(
                                  index,
                                  "unit",
                                  event.target.value,
                                )
                              }
                              data-testid="report-candidate-unit"
                            />
                          </td>
                          <td>
                            <input
                              value={observation.reference_range}
                              onChange={(event) =>
                                updateObservation(
                                  index,
                                  "reference_range",
                                  event.target.value,
                                )
                              }
                              data-testid="report-candidate-reference"
                            />
                          </td>
                          <td>
                            <input
                              type="date"
                              value={observation.observed_at}
                              onChange={(event) =>
                                updateObservation(
                                  index,
                                  "observed_at",
                                  event.target.value,
                                )
                              }
                              data-testid="report-candidate-date"
                            />
                          </td>
                          <td className="report-provenance">
                            <span>{observation.parser_provenance}</span>
                            {observation.parser_confidence === null ? (
                              <small>No OCR confidence</small>
                            ) : (
                              <small>
                                OCR confidence{" "}
                                {(observation.parser_confidence * 100).toFixed(
                                  1,
                                )}
                                %
                              </small>
                            )}
                          </td>
                          <td>
                            <button
                              className="secondary"
                              type="button"
                              onClick={() => {
                                setObservations((current) =>
                                  current.filter(
                                    (_, candidateIndex) =>
                                      candidateIndex !== index,
                                  ),
                                );
                                setDirty(true);
                              }}
                              data-testid="report-candidate-delete"
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="report-review-actions">
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => {
                      setObservations((current) => [
                        ...current,
                        emptyObservation(),
                      ]);
                      setDirty(true);
                    }}
                    data-testid="report-candidate-add"
                  >
                    Add observation
                  </button>
                  <button
                    type="submit"
                    disabled={saving}
                    data-testid="report-review-save"
                  >
                    {saving ? "Saving review…" : "Save review"}
                  </button>
                  <button
                    type="button"
                    disabled={confirming || dirty}
                    onClick={() => void confirmReview()}
                    data-testid="report-review-confirm"
                  >
                    {confirming ? "Confirming…" : "Confirm reviewed report"}
                  </button>
                </div>
                {dirty ? (
                  <p className="muted" data-testid="report-review-unsaved">
                    Save your review changes before confirming.
                  </p>
                ) : null}
              </form>
            </section>
          ) : null}

          {selectedIntake && selectedIntake.status === "confirmed" ? (
            <section
              className="card confirmed-report-card"
              data-testid="confirmed-health-report"
            >
              <span className="pill">Canonical HealthReport confirmed</span>
              <h2>Health History now includes this report</h2>
              <p>
                This intake created HealthReport{" "}
                <code>{selectedIntake.report_id}</code> using schema{" "}
                <code>healthy.health-report.v1</code>.
              </p>
              <p>
                Source: <strong>{selectedIntake.source_name}</strong>
              </p>
              {confirmedReport ? (
                <ul className="confirmed-observations">
                  {confirmedReport.observations.map((observation) => (
                    <li key={observation.id}>
                      <strong>{observation.display_name}</strong>:{" "}
                      {observation.value_numeric ?? observation.value_text}
                      {observation.unit ? ` ${observation.unit}` : ""}
                      {observation.reference_range
                        ? ` · Reference ${observation.reference_range}`
                        : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              <Link
                className="history-link"
                href={`/history?person_id=${encodeURIComponent(selectedPerson.id)}`}
                data-testid="confirmed-report-history-link"
              >
                View this report in Health History
              </Link>
            </section>
          ) : null}
        </section>
      ) : !error ? (
        <p data-testid="report-intake-no-person">
          No Person is available for Report Intake.
        </p>
      ) : null}
    </main>
  );
}
