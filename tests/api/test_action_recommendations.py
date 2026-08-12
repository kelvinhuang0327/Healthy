from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from conftest import csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application.risk_alert_inputs import (
    RiskAlertEvidence,
    RiskAlertInput,
)
from healthy.domain.action_recommendations import build_action_recommendations
from sqlalchemy import event
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _risk_alert(
    *,
    rule_code: str,
    severity: str = "medium",
    observed_at: datetime = NOW,
    source_id: UUID | None = None,
    source_kind: str = "health_metric",
    risk_type: str | None = None,
    observation_id: UUID | None = None,
    report_id: UUID | None = None,
    report_source_name: str | None = None,
) -> RiskAlertInput:
    person_id = UUID("00000000-0000-0000-0000-000000000001")
    return RiskAlertInput(
        rule_code=rule_code,
        risk_type=risk_type or rule_code.lower(),
        severity=severity,  # type: ignore[arg-type]
        evidence=RiskAlertEvidence(
            source_kind=source_kind,  # type: ignore[arg-type]
            source_id=source_id or uuid4(),
            person_id=person_id,
            observed_at=observed_at,
            observation_id=observation_id,
            report_id=report_id,
            report_source_name=report_source_name,
        ),
    )


def test_evaluator_returns_empty_for_empty_alerts() -> None:
    assert build_action_recommendations([]).recommendations == ()


def test_evaluator_deduplicates_rule_and_uses_newest_metric_evidence() -> None:
    older_source_id = UUID("00000000-0000-0000-0000-000000000010")
    newer_source_id = UUID("00000000-0000-0000-0000-000000000020")

    result = build_action_recommendations(
        [
            _risk_alert(
                rule_code="BP_HIGH",
                severity="high",
                observed_at=NOW - timedelta(days=1),
                source_id=older_source_id,
            ),
            _risk_alert(
                rule_code="BP_HIGH",
                severity="high",
                observed_at=NOW,
                source_id=newer_source_id,
            ),
        ]
    )

    assert len(result.recommendations) == 1
    recommendation = result.recommendations[0]
    assert recommendation.recommendation_code == "REVIEW_BP_HIGH"
    assert recommendation.title == "Blood pressure signal"
    assert recommendation.matching_alert_count == 2
    assert recommendation.evidence.source_id == newer_source_id
    assert recommendation.evidence.source_kind == "health_metric"
    assert recommendation.rule_version == "risk-action-recommendations-v1"
    assert "not a diagnosis" in recommendation.limitations


def test_evaluator_orders_high_before_medium_then_rule_and_source() -> None:
    result = build_action_recommendations(
        [
            _risk_alert(
                rule_code="LIVER_ALT_HIGH",
                source_id=UUID("00000000-0000-0000-0000-000000000030"),
            ),
            _risk_alert(
                rule_code="GLUCOSE_HIGH",
                severity="high",
                source_id=UUID("00000000-0000-0000-0000-000000000040"),
            ),
        ]
    )

    assert [item.source_rule_code for item in result.recommendations] == [
        "GLUCOSE_HIGH",
        "LIVER_ALT_HIGH",
    ]


def test_evaluator_preserves_lab_provenance() -> None:
    report_id = UUID("00000000-0000-0000-0000-000000000050")
    observation_id = UUID("00000000-0000-0000-0000-000000000051")

    recommendation = build_action_recommendations(
        [
            _risk_alert(
                rule_code="LIVER_ALT_HIGH",
                source_id=report_id,
                source_kind="lab_report",
                observation_id=observation_id,
                report_id=report_id,
                report_source_name="Confirmed Risk Lab",
            )
        ]
    ).recommendations[0]

    assert recommendation.evidence.source_kind == "lab_report"
    assert recommendation.evidence.source_id == report_id
    assert recommendation.evidence.observation_id == observation_id
    assert recommendation.evidence.report_id == report_id
    assert recommendation.evidence.report_source_name == "Confirmed Risk Lab"


def test_evaluator_uses_safe_generic_policy_for_unknown_rule() -> None:
    recommendation = build_action_recommendations(
        [_risk_alert(rule_code="FUTURE_SIGNAL")]
    ).recommendations[0]

    assert recommendation.recommendation_code == "REVIEW_FUTURE_SIGNAL"
    assert recommendation.title == "Review this health signal"
    assert "FUTURE_SIGNAL" in recommendation.rationale
    assert "diagnosis" not in recommendation.suggested_action.casefold()


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _recommendations_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/action-recommendations"


def _create_metric(
    client: TestClient,
    person_id: str,
    *,
    recorded_at: datetime = NOW,
    **overrides: object,
):
    payload: dict[str, object] = {
        "recorded_at": recorded_at.isoformat(),
        "heart_rate_bpm": 72,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_confirmed_report(client: TestClient, person_id: str) -> tuple[dict, dict]:
    imported = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Confirmed Risk Lab",
            "reported_at": NOW.isoformat(),
            "observations": [
                {
                    "code": "ALT",
                    "display_name": "ALT",
                    "value_numeric": 50,
                    "unit": "U/L",
                    "observed_at": NOW.isoformat(),
                }
            ],
        },
    )
    assert imported.status_code == 201
    imported_body = imported.json()
    confirmed = client.post(
        f"/v1/persons/{person_id}/reports/{imported_body['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    return imported_body, confirmed.json()


def test_empty_recommendation_response_is_explicit_and_safe(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = client.get(_recommendations_url(person_id))

    assert response.status_code == 200
    assert response.json() == {"recommendations": []}


def test_metric_recommendation_is_person_scoped_and_preserves_provenance(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(
        client,
        person_id,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    response = client.get(_recommendations_url(person_id))

    assert response.status_code == 200
    recommendation = response.json()["recommendations"][0]
    assert recommendation["recommendation_code"] == "REVIEW_BP_HIGH"
    assert recommendation["source_rule_code"] == "BP_HIGH"
    assert recommendation["source_risk_type"] == "bp_high"
    assert recommendation["source_severity"] == "high"
    assert recommendation["title"] == "Blood pressure signal"
    assert recommendation["matching_alert_count"] == 1
    assert recommendation["rule_version"] == "risk-action-recommendations-v1"
    assert recommendation["evidence"] == {
        "source_kind": "health_metric",
        "source_id": metric.json()["id"],
        "person_id": person_id,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "observation_id": None,
        "report_id": None,
        "report_source_name": None,
    }


def test_lab_recommendation_preserves_report_provenance(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    report, _confirmed = _create_confirmed_report(client, person_id)

    response = client.get(_recommendations_url(person_id))

    assert response.status_code == 200
    recommendation = response.json()["recommendations"][0]
    assert recommendation["source_rule_code"] == "LIVER_ALT_HIGH"
    assert recommendation["title"] == "ALT lab signal"
    assert recommendation["evidence"] == {
        "source_kind": "lab_report",
        "source_id": report["id"],
        "person_id": person_id,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "observation_id": report["observations"][0]["id"],
        "report_id": report["id"],
        "report_source_name": "Confirmed Risk Lab",
    }


def test_recommendation_deduplicates_same_rule_and_uses_newest_evidence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    older = _create_metric(
        client,
        person_id,
        recorded_at=NOW - timedelta(days=1),
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    newer = _create_metric(
        client,
        person_id,
        recorded_at=NOW,
        systolic_bp_mm_hg=150,
        diastolic_bp_mm_hg=96,
    )
    assert older.status_code == newer.status_code == 201

    response = client.get(_recommendations_url(person_id))

    assert response.status_code == 200
    recommendations = response.json()["recommendations"]
    assert len(recommendations) == 1
    assert recommendations[0]["matching_alert_count"] == 2
    assert recommendations[0]["evidence"]["source_id"] == newer.json()["id"]
    assert recommendations[0]["evidence"]["observed_at"] == NOW.isoformat().replace("+00:00", "Z")


def test_foreign_person_cannot_read_action_recommendations(client: TestClient) -> None:
    assert register(client, email="recommendation-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    metric = _create_metric(
        client,
        person_a,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="recommendation-owner-b@example.com").status_code == 201

    response = other.get(_recommendations_url(person_a))

    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found"}
    assert person_a not in response.text
    assert metric.json()["id"] not in response.text


def test_get_action_recommendations_performs_zero_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(
        client,
        person_id,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    commit_sessions: list[Session] = []

    def record_commit(session: Session) -> None:
        commit_sessions.append(session)

    event.listen(Session, "after_commit", record_commit)
    try:
        first = client.get(_recommendations_url(person_id))
        second = client.get(_recommendations_url(person_id))
    finally:
        event.remove(Session, "after_commit", record_commit)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert commit_sessions == []
