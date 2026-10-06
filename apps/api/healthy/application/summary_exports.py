from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthy.application.services import get_health_score
from healthy.infrastructure.models import (
    HealthAction,
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    Person,
    SymptomLog,
)


def _text(value: object) -> str:
    if value is None:
        return "Not recorded"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _field(label: str, value: object) -> str:
    return f"<p><strong>{escape(label)}:</strong> {escape(_text(value))}</p>"


def render_person_summary(database_session: Session, person: Person, now: datetime) -> str:
    """Render an owned person's bounded, read-only snapshot without saving an export."""
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Healthy health summary</title>",
        "<style>body{font:16px system-ui,sans-serif;max-width:800px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.6;overflow-wrap:anywhere}p{white-space:pre-wrap}"
        "section{border-top:1px solid #ccc;margin-top:2rem}li{margin-bottom:1rem}"
        "@media print{body{margin:0}h2{break-after:avoid}li{break-inside:avoid}}</style>",
        "</head><body><h1>Healthy health summary</h1>",
        _field("Person", person.display_name),
        _field("Person ID", person.id),
        _field("Relationship", person.relationship),
        _field("Height (cm)", person.height_cm),
        _field("Generated at (UTC)", now),
        "<p>A snapshot of recorded information, not a diagnosis. Missing values are not "
        "normal results. Dates are UTC. This summary is not a complete data backup.</p>",
    ]
    metric = database_session.scalar(
        select(HealthMetric)
        .where(HealthMetric.person_id == person.id)
        .order_by(
            HealthMetric.recorded_at.desc(), HealthMetric.created_at.desc(), HealthMetric.id.desc()
        )
        .limit(1)
    )
    parts.append("<section><h2>Latest recorded metrics</h2>")
    if metric is None:
        parts.append("<p>No recorded metrics.</p>")
    else:
        for label, value in (
            ("Recorded at", metric.recorded_at),
            ("Record ID", metric.id),
            ("Source", metric.source_type),
            ("Systolic blood pressure (mmHg)", metric.systolic_bp_mm_hg),
            ("Diastolic blood pressure (mmHg)", metric.diastolic_bp_mm_hg),
            ("Heart rate (bpm)", metric.heart_rate_bpm),
            ("Steps", metric.steps),
            ("Weight (kg)", metric.weight_kg),
            ("Blood glucose (mg/dL)", metric.blood_glucose_mg_dl),
            ("Sleep (hours)", metric.sleep_hours),
            ("Notes", metric.note),
        ):
            parts.append(_field(label, value))
    parts.append("</section><section><h2>Latest confirmed lab report (up to 100 observations)</h2>")
    report = database_session.scalar(
        select(HealthReportModel)
        .where(HealthReportModel.person_id == person.id, HealthReportModel.status == "confirmed")
        .order_by(
            HealthReportModel.reported_at.desc(),
            HealthReportModel.created_at.desc(),
            HealthReportModel.id.desc(),
        )
        .limit(1)
    )
    if report is None:
        parts.append("<p>No confirmed lab report. Unreviewed imports are excluded.</p>")
    else:
        parts.extend(
            [_field("Source", report.source_name), _field("Report date", report.reported_at)]
        )
        observations = database_session.scalars(
            select(HealthReportObservationModel)
            .where(
                HealthReportObservationModel.report_id == report.id,
                HealthReportObservationModel.person_id == person.id,
            )
            .order_by(
                HealthReportObservationModel.observed_at.desc(), HealthReportObservationModel.id
            )
            .limit(100)
        )
        parts.append("<ul>")
        for observation in observations:
            parts.extend(
                [
                    "<li>",
                    _field("Observation", observation.display_name),
                    _field("Code", observation.code),
                    _field(
                        "Value",
                        observation.value_numeric
                        if observation.value_numeric is not None
                        else observation.value_text,
                    ),
                    _field("Unit", observation.unit),
                    _field("Source reference range", observation.reference_range),
                    _field("Observed at", observation.observed_at),
                    "</li>",
                ]
            )
        parts.append("</ul>")
    parts.append("</section><section><h2>Recent symptoms (up to 5)</h2><ul>")
    symptoms = list(
        database_session.scalars(
            select(SymptomLog)
            .where(SymptomLog.person_id == person.id)
            .order_by(
                SymptomLog.occurred_at.desc(), SymptomLog.created_at.desc(), SymptomLog.id.desc()
            )
            .limit(5)
        )
    )
    if not symptoms:
        parts.append("<li>No recorded symptoms.</li>")
    for symptom in symptoms:
        parts.extend(
            [
                "<li>",
                _field("Symptom", symptom.symptom),
                _field("Occurred at", symptom.occurred_at),
                _field("Severity (1–5)", symptom.severity),
                _field("Estimated duration (minutes)", symptom.duration_minutes),
                _field("Notes", symptom.note),
                "</li>",
            ]
        )
    parts.append("</ul></section><section><h2>Open actions (up to 5, newest first)</h2><ul>")
    actions = list(
        database_session.scalars(
            select(HealthAction)
            .where(HealthAction.person_id == person.id, HealthAction.status == "todo")
            .order_by(HealthAction.created_at.desc(), HealthAction.id.desc())
            .limit(5)
        )
    )
    if not actions:
        parts.append("<li>No open actions.</li>")
    for action in actions:
        parts.extend(
            [
                "<li>",
                _field("Action", action.title),
                _field("Details", action.description),
                _field("Due at", action.due_at),
                "</li>",
            ]
        )
    parts.append("</ul></section><section><h2>Existing heuristic health signal</h2>")
    score = get_health_score(
        database_session, owner_account_id=person.owner_account_id, person_id=person.id
    )
    if score is not None:
        parts.extend(
            [
                _field(
                    "Score",
                    "Unavailable: insufficient data"
                    if score.anchor_at is None or score.status == "insufficient_data"
                    else score.score,
                ),
                _field(
                    "Status",
                    "Unavailable: insufficient data" if score.anchor_at is None else score.status,
                ),
                _field("Rule version", score.rule_version),
                _field("Latest signal input at", score.anchor_at),
                _field("Data points", score.data_points),
                _field("Evaluated inputs", ", ".join(score.coverage.evaluated_inputs) or "None"),
                _field("Missing inputs", ", ".join(score.coverage.missing_inputs) or "None"),
                _field(
                    "Unsupported sources", ", ".join(score.coverage.unsupported_sources) or "None"
                ),
                _field("Limitations", score.limitations),
            ]
        )
    else:
        parts.append("<p>Health signal unavailable.</p>")
    parts.append(
        "<p>Heuristic signals are not a medical diagnosis or proof of clinical accuracy.</p>"
        "</section></body></html>"
    )
    return "".join(parts)
