"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
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

type SelectionContext = {
  personId: string | null;
  intakeId: string | null;
  version: number;
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

function repeatedMeasurementNotices(observations: ReviewObservation[]): string[] {
  const groups = new Map<string, ReviewObservation[]>();
  for (const observation of observations) {
    const code = observation.code.trim().toUpperCase();
    if (code) groups.set(code, [...(groups.get(code) ?? []), observation]);
  }
  return [...groups.entries()].filter(([, rows]) => rows.length > 1).map(([code, rows]) => {
    const signatures = new Set(rows.map((row) => JSON.stringify([
      row.value_numeric.trim() ? Number(row.value_numeric) : null,
      row.value_text.trim(), row.unit.trim(), row.observed_at, row.reference_range.trim(),
    ])));
    return signatures.size > 1
      ? `${code} has differing values, units, dates, or reference ranges. These may be separate measurements or conflicting extraction. All rows are retained; compare their source page and line before confirming.`
      : `${code} appears ${rows.length} times. Repeated measurements may be valid. All rows are retained; check each source before confirming.`;
  });
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

function extractionMethodLabel(method: ReportIntake["extraction_method"]): string {
  switch (method) {
    case "digital_pdf": return "Digital PDF text";
    case "ocr_image": return "Image OCR";
    case "ocr_pdf": return "Scanned PDF OCR";
    case "mixed_pdf": return "Digital text + scanned PDF OCR";
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
  const repeatedNotices = useMemo(() => repeatedMeasurementNotices(observations), [observations]);
  const [authLoading, setAuthLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const selectionContextRef = useRef<SelectionContext>({
    personId: null,
    intakeId: null,
    version: 0,
  });

  function setSelectionContext(
    personId: string | null,
    intakeId: string | null,
  ) {
    selectionContextRef.current = {
      personId,
      intakeId,
      version: selectionContextRef.current.version + 1,
    };
  }

  function isCurrentSelection(context: SelectionContext): boolean {
    const current = selectionContextRef.current;
    return (
      current.personId === context.personId &&
      current.intakeId === context.intakeId &&
      current.version === context.version
    );
  }

  function clearReviewState() {
    setSelectedIntake(null);
    setConfirmedReport(null);
    setLoadedDetailId(null);
    setSourceName("");
    setReportedAt("");
    setObservations([]);
    setDirty(false);
  }

  function showReviewDetail(detail: ReportIntakeDetail) {
    setSelectedIntake(detail);
    setConfirmedReport(null);
    setSourceName(detail.source_name);
    setReportedAt(dateInputValue(detail.reported_at));
    setObservations(detail.observations.map(observationForReview));
    setDirty(false);
    setLoadedDetailId(detail.id);
  }

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
        setSelectionContext(person?.id ?? null, null);
        setSession(current);
        setPersons(rows);
        setSelectedPersonId(person?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setSession(null);
          setPersons([]);
          setSelectionContext(null, null);
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
    const requestPersonId = selectedPersonId;
    let cancelled = false;
    api
      .reportIntakes(requestPersonId)
      .then((rows) => {
        if (
          cancelled ||
          selectionContextRef.current.personId !== requestPersonId
        ) {
          return;
        }
        setIntakes(rows);
        const currentIntakeId = selectionContextRef.current.intakeId;
        const nextIntakeId = rows.some(
          (intake) => intake.id === currentIntakeId,
        )
          ? currentIntakeId
          : (rows[0]?.id ?? null);
        if (nextIntakeId !== currentIntakeId) {
          setSelectionContext(requestPersonId, nextIntakeId);
          setSelectedIntakeId(nextIntakeId);
          clearReviewState();
          setUploading(false);
          setSaving(false);
          setConfirming(false);
        } else {
          setSelectedIntakeId(nextIntakeId);
        }
        setLoadedIntakePersonId(requestPersonId);
      })
      .catch((reason) => {
        if (
          !cancelled &&
          selectionContextRef.current.personId === requestPersonId
        ) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load report intakes.",
          );
          setIntakes([]);
          setSelectionContext(requestPersonId, null);
          setSelectedIntakeId(null);
          clearReviewState();
          setUploading(false);
          setSaving(false);
          setConfirming(false);
          setLoadedIntakePersonId(requestPersonId);
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
    const requestPersonId = selectedPersonId;
    const requestIntakeId = selectedIntakeId;
    const requestContext = { ...selectionContextRef.current };
    if (
      requestContext.personId !== requestPersonId ||
      requestContext.intakeId !== requestIntakeId
    ) {
      return;
    }
    let cancelled = false;
    api
      .reportIntake(requestPersonId, requestIntakeId)
      .then((detail) => {
        if (cancelled || !isCurrentSelection(requestContext)) {
          return;
        }
        showReviewDetail(detail);
        if (detail.status === "confirmed" && detail.report_id) {
          api
            .healthReport(requestPersonId, detail.report_id)
            .then((report) => {
              if (!cancelled && isCurrentSelection(requestContext)) {
                setConfirmedReport(report);
              }
            })
            .catch(() => {
              if (!cancelled && isCurrentSelection(requestContext)) {
                setConfirmedReport(null);
              }
            });
        }
      })
      .catch((reason) => {
        if (!cancelled && isCurrentSelection(requestContext)) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the report review.",
          );
          setSelectedIntake(null);
          setLoadedDetailId(requestIntakeId);
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

  function selectIntake(intakeId: string | null) {
    if (
      selectionContextRef.current.personId === selectedPersonId &&
      selectionContextRef.current.intakeId === intakeId
    ) {
      setSelectedIntakeId(intakeId);
      return;
    }
    setSelectionContext(selectedPersonId, intakeId);
    setSelectedIntakeId(intakeId);
    clearReviewState();
    setUploading(false);
    setSaving(false);
    setConfirming(false);
    setError("");
    setMessage("");
  }

  function selectPerson(personId: string) {
    setSelectionContext(personId, null);
    setSelectedPersonId(personId);
    setIntakes([]);
    setSelectedIntakeId(null);
    clearReviewState();
    setLoadedIntakePersonId(null);
    setSelectedFile(null);
    setUploading(false);
    setSaving(false);
    setConfirming(false);
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
    await extractReport();
  }

  async function extractReport(retryIntakeId?: string) {
    if (uploading) return;
    const requestPersonId = selectedPersonId;
    const requestFile = selectedFile;
    const requestContext = { ...selectionContextRef.current };
    if (
      !requestPersonId ||
      !requestFile ||
      requestContext.personId !== requestPersonId
    ) {
      setError("Choose a PDF, JPEG, or PNG report file first.");
      return;
    }
    if (retryIntakeId && (
      requestContext.intakeId !== retryIntakeId || selectedIntake?.status !== "failed"
    )) return;
    setUploading(true);
    setError("");
    setMessage("");
    try {
      const detail = retryIntakeId
        ? await api.retryReportIntake(requestPersonId, retryIntakeId, requestFile)
        : await api.createReportIntake(requestPersonId, requestFile);
      if (!isCurrentSelection(requestContext)) {
        return;
      }
      setUploading(false);
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
      setSelectionContext(requestPersonId, detail.id);
      setSelectedIntakeId(detail.id);
      // Retry and duplicate uploads preserve the ID, so the selection effect
      // will not run again. Hydrate from the server response immediately.
      showReviewDetail(detail);
      setMessage(
        detail.status === "pending_review"
          ? "Report extracted. Review every candidate before confirming."
          : detail.status === "confirmed"
            ? "This report is already confirmed. Its reviewed values were preserved."
            : (detail.error_message ?? "The report could not be extracted."),
      );
      if (detail.status === "confirmed" && detail.report_id) {
        const loadedContext = { ...selectionContextRef.current };
        try {
          const report = await api.healthReport(requestPersonId, detail.report_id);
          if (isCurrentSelection(loadedContext)) setConfirmedReport(report);
        } catch {
          if (isCurrentSelection(loadedContext)) {
            setError("The confirmed report could not be loaded. Reload this page to retry.");
          }
        }
      }
    } catch (reason) {
      if (isCurrentSelection(requestContext)) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not upload the report.",
        );
      }
    } finally {
      if (isCurrentSelection(requestContext)) {
        setUploading(false);
      }
    }
  }

  async function saveReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !selectedPersonId ||
      !selectedIntake ||
      selectionContextRef.current.personId !== selectedPersonId ||
      selectionContextRef.current.intakeId !== selectedIntake.id ||
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
    const requestPersonId = selectedPersonId;
    const requestIntakeId = selectedIntake.id;
    const requestContext = { ...selectionContextRef.current };
    try {
      const candidateEdits = observations.map(toApiObservation);
      setSaving(true);
      setError("");
      const detail = await api.updateReportIntake(
        requestPersonId,
        requestIntakeId,
        {
          source_name: sourceName.trim(),
          reported_at: `${reportedAt}T00:00:00Z`,
          observations: candidateEdits,
        },
      );
      if (!isCurrentSelection(requestContext)) {
        return;
      }
      setSelectedIntake(detail);
      setIntakes((current) =>
        current.map((intake) => (intake.id === detail.id ? detail : intake)),
      );
      setMessage(
        "Review changes saved. Confirm only when every value is correct.",
      );
      setDirty(false);
    } catch (reason) {
      if (isCurrentSelection(requestContext)) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not save the review.",
        );
      }
    } finally {
      if (isCurrentSelection(requestContext)) {
        setSaving(false);
      }
    }
  }

  async function confirmReview() {
    if (
      !selectedPersonId ||
      !selectedIntake ||
      selectionContextRef.current.personId !== selectedPersonId ||
      selectionContextRef.current.intakeId !== selectedIntake.id ||
      dirty ||
      selectedIntake.status !== "pending_review"
    ) {
      return;
    }
    const requestPersonId = selectedPersonId;
    const requestIntakeId = selectedIntake.id;
    const requestContext = { ...selectionContextRef.current };
    setConfirming(true);
    setError("");
    try {
      const detail = await api.confirmReportIntake(
        requestPersonId,
        requestIntakeId,
      );
      if (!isCurrentSelection(requestContext)) {
        return;
      }
      setSelectedIntake(detail);
      setIntakes((current) =>
        current.map((intake) => (intake.id === detail.id ? detail : intake)),
      );
      setMessage("Report confirmed and added to Health History.");
      if (detail.report_id) {
        try {
          const report = await api.healthReport(requestPersonId, detail.report_id);
          if (isCurrentSelection(requestContext)) {
            setConfirmedReport(report);
          }
        } catch {
          if (isCurrentSelection(requestContext)) {
            setError("Report confirmed, but its details could not be loaded. Reload this page to retry.");
          }
        }
      }
    } catch (reason) {
      if (isCurrentSelection(requestContext)) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not confirm the report.",
        );
      }
    } finally {
      if (isCurrentSelection(requestContext)) {
        setConfirming(false);
      }
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
          onClick={() => selectIntake(intake.id)}
          aria-pressed={selectedIntakeId === intake.id}
        >
          <span>
            <strong>{intake.source_name}</strong>
            <small>{intake.source_filename}</small>
          </span>
          <span className="pill">{intakeStatusLabel(intake)}</span>
        </button>
        <p>
          {extractionMethodLabel(intake.extraction_method)}
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
              Supported files: PDF, JPEG, and PNG, up to 10 MB. PDF reports are
              limited to 10 pages and 30 seconds of processing. Scanned pages use
              local OCR tools; unavailable tools or unreadable pages show a failure.
              Temporary OCR images are deleted after processing. Raw uploads are not saved.
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
            {selectedIntake?.status === "failed" ? (
              <div data-testid="report-retry-section">
                <p>
                  To retry <strong>{selectedIntake.source_filename}</strong>, choose
                  the original file above, then retry after resolving the failure.
                  A successful retry still requires your review and confirmation.
                </p>
                <button
                  type="button"
                  disabled={uploading || !selectedFile}
                  onClick={() => void extractReport(selectedIntake.id)}
                  data-testid="report-retry-button"
                >
                  {uploading ? "Retrying report…" : "Retry this failed report"}
                </button>
              </div>
            ) : null}
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
                  {extractionMethodLabel(selectedIntake.extraction_method)}
                </span>
              </div>
              <p className="review-warning" data-testid="report-review-warning">
                Extraction is a draft and may omit results. Compare every page
                with the source document; only recognized numeric lab labels are
                accepted from OCR. Correct every candidate before saving and confirming. No report is added
                to Health History until you confirm it. Image results may still be
                omitted when the same page also contains recognized text results.
              </p>
              {repeatedNotices.length > 0 ? (
                <ul className="review-warning" data-testid="report-repeat-warning">
                  {repeatedNotices.map((notice) => <li key={notice}>{notice}</li>)}
                </ul>
              ) : null}
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
