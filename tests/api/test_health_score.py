from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import csrf_headers, register
from fastapi.testclient import TestClient
from healthy.domain.health_score import (
    MetricSnapshot,
    NamedLabSnapshot,
    RiskAlertSnapshot,
    SymptomDurationSnapshot,
    build_health_score,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _uuid(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _metric(
    number: int,
    *,
    recorded_at: datetime = NOW,
    systolic: int | None = None,
    diastolic: int | None = None,
    steps: int | None = None,
    weight: Decimal | None = None,
    glucose: Decimal | None = None,
    sleep: Decimal | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        id=_uuid(number),
        recorded_at=recorded_at,
        systolic_bp_mm_hg=systolic,
        diastolic_bp_mm_hg=diastolic,
        heart_rate_bpm=None,
        weight_kg=weight,
        blood_glucose_mg_dl=glucose,
        created_at=recorded_at,
        steps=steps,
        sleep_hours=sleep,
    )


def test_supported_fixture_preserves_formula_weights_rules_and_evidence() -> None:
    result = build_health_score(
        metrics=[
            _metric(
                1,
                recorded_at=NOW - timedelta(days=2),
                systolic=145,
                diastolic=90,
                steps=0,
                weight=Decimal("88"),
                glucose=Decimal("130"),
                sleep=Decimal("6"),
            ),
            _metric(
                2,
                recorded_at=NOW - timedelta(days=10),
                systolic=135,
                diastolic=85,
                steps=6000,
                weight=Decimal("80"),
                glucose=Decimal("110"),
                sleep=Decimal("7"),
            ),
        ],
        named_labs={
            "Total Cholesterol": NamedLabSnapshot(
                value=Decimal("220"), evidence_ids=(_uuid(10),), observed_at=NOW
            ),
            "LDL": NamedLabSnapshot(
                value=Decimal("140"), evidence_ids=(_uuid(11),), observed_at=NOW
            ),
            "ALT": NamedLabSnapshot(
                value=Decimal("50"), evidence_ids=(_uuid(12),), observed_at=NOW
            ),
        },
        risk_alerts=[
            RiskAlertSnapshot(evidence_ids=(_uuid(20),), observed_at=NOW),
            RiskAlertSnapshot(evidence_ids=(_uuid(21),), observed_at=NOW),
            RiskAlertSnapshot(evidence_ids=(_uuid(22),), observed_at=NOW),
        ],
        symptom_durations=[
            SymptomDurationSnapshot(
                id=_uuid(30),
                occurred_at=NOW - timedelta(days=1),
                estimated_duration_days=180,
            )
        ],
        height_cm=Decimal("170"),
        now=NOW,
    )

    assert result.score == 54
    assert result.status == "attention"
    assert [component.kind for component in result.components] == [
        "cardiovascular",
        "metabolic",
        "activity",
        "weight",
        "overall",
    ]
    assert [component.points for component in result.components] == [76, 66, 71, 74, 83]
    assert result.components[-1].penalty == 17
    assert set(result.components[-1].evidence_ids) == {
        _uuid(20),
        _uuid(21),
        _uuid(22),
        _uuid(30),
    }
    assert result.data_points == 6


def test_missing_inputs_receive_no_penalty_and_zero_steps_is_not_missing() -> None:
    missing = build_health_score(metrics=[_metric(1)], now=NOW)
    zero_steps = build_health_score(metrics=[_metric(2, steps=0)], now=NOW)

    assert missing.score == 100
    assert [component.points for component in missing.components] == [100] * 5
    assert zero_steps.score == 94
    assert zero_steps.components[2].points == 77
    assert zero_steps.components[2].evidence_ids == (_uuid(2),)


def test_duration_boundary_risk_alert_and_height_semantics_are_deterministic() -> None:
    below_boundary = build_health_score(
        metrics=[_metric(1, weight=Decimal("80"))],
        symptom_durations=[
            SymptomDurationSnapshot(_uuid(2), NOW, 179),
        ],
        height_cm=Decimal("170"),
        now=NOW,
    )
    at_boundary = build_health_score(
        metrics=[_metric(1, weight=Decimal("80"))],
        symptom_durations=[
            SymptomDurationSnapshot(_uuid(2), NOW, 180),
        ],
        height_cm=Decimal("170"),
        now=NOW,
    )
    with_alert = build_health_score(
        metrics=[_metric(1, weight=Decimal("80"))],
        risk_alerts=[RiskAlertSnapshot((_uuid(3),), NOW)],
        height_cm=Decimal("170"),
        now=NOW,
    )

    assert below_boundary.score == 98
    assert at_boundary.score == 90
    assert with_alert.score == 94
    assert below_boundary.components[3].points == 85
    assert below_boundary.components[2].points == 90
    assert at_boundary == build_health_score(
        metrics=[_metric(1, weight=Decimal("80"))],
        symptom_durations=[SymptomDurationSnapshot(_uuid(2), NOW, 180)],
        height_cm=Decimal("170"),
        now=NOW,
    )


def test_health_score_endpoint_uses_supported_missing_behavior(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]

    response = client.get(f"/v1/persons/{person_id}/health-score")

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 100
    assert body["status"] == "stable"
    assert body["data_points"] == 0
    assert [component["kind"] for component in body["components"]] == [
        "cardiovascular",
        "metabolic",
        "activity",
        "weight",
        "overall",
    ]
    assert all(component["points"] == 100 for component in body["components"])
    assert body["coverage"]["evaluated_inputs"] == ["safe_route_b_risk_alerts"]
    assert set(body["coverage"]["missing_inputs"]) == {
        "blood_pressure",
        "blood_glucose",
        "steps",
        "sleep_hours",
        "weight_kg",
        "height_cm",
        "named_labs",
        "symptom_duration_days",
    }
    assert body["coverage"]["unsupported_sources"] == [
        "ai_summary",
        "ai_generated_alerts",
        "external_metric_alerts",
        "unsupported_chronic_risk_alerts",
    ]


def test_health_score_endpoint_wires_healthy_inputs_and_provenance(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]
    assert (
        client.patch(
            f"/v1/persons/{person_id}/profile",
            headers=csrf_headers(client),
            json={"height_cm": 170},
        ).status_code
        == 200
    )

    metric = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={
            "recorded_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "systolic_bp_mm_hg": 120,
            "diastolic_bp_mm_hg": 80,
            "steps": 6000,
            "weight_kg": 80,
            "blood_glucose_mg_dl": 100,
            "sleep_hours": 7,
        },
    )
    assert metric.status_code == 201
    metric_id = metric.json()["id"]

    report = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Score fixture",
            "reported_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "observations": [
                {
                    "code": "LDL",
                    "display_name": "LDL",
                    "value_numeric": 140,
                    "unit": "mg/dL",
                    "observed_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                }
            ],
        },
    )
    assert report.status_code == 201
    report_id = report.json()["id"]
    observation_id = report.json()["observations"][0]["id"]
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports/{report_id}/confirm",
            headers=csrf_headers(client),
        ).status_code
        == 200
    )

    symptom = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json={
            "symptom": "Persistent headache",
            "occurred_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "severity": 3,
            "estimated_start_date": "2026-01-01",
            "estimated_duration_days": 180,
        },
    )
    assert symptom.status_code == 201
    symptom_id = symptom.json()["id"]

    body = client.get(f"/v1/persons/{person_id}/health-score").json()
    components = {component["kind"]: component for component in body["components"]}

    assert body["score"] == 79
    assert body["status"] == "monitor"
    assert body["data_points"] == 3
    assert [
        components[kind]["points"]
        for kind in (
            "cardiovascular",
            "metabolic",
            "activity",
            "weight",
            "overall",
        )
    ] == [100, 97, 90, 85, 83]
    assert observation_id in components["metabolic"]["evidence_ids"]
    assert metric_id in components["overall"]["evidence_ids"]
    assert observation_id in components["overall"]["evidence_ids"]
    assert symptom_id in components["overall"]["evidence_ids"]
    assert body["coverage"]["missing_inputs"] == []
    assert "safe_route_b_risk_alerts" in body["coverage"]["evaluated_inputs"]


def test_366_day_symptom_uses_healthy_v1_without_legacy_parity_adjustment() -> None:
    result = build_health_score(
        metrics=[],
        symptom_durations=[
            SymptomDurationSnapshot(
                id=_uuid(366),
                occurred_at=NOW,
                estimated_duration_days=366,
            )
        ],
        now=NOW,
    )

    # The excluded full legacy pipeline would contribute a separate -3 alert
    # in this fixture; Healthy V1 intentionally does not fabricate that source.
    assert result.score == 92
    assert result.components[-1].penalty == 8
    assert result.coverage.evaluated_inputs == (
        "symptom_duration_days",
        "safe_route_b_risk_alerts",
    )
    assert result.coverage.unsupported_sources == (
        "ai_summary",
        "ai_generated_alerts",
        "external_metric_alerts",
        "unsupported_chronic_risk_alerts",
    )


def test_health_score_is_deterministic_and_owner_scoped(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]

    first = client.get(f"/v1/persons/{person_id}/health-score")
    second = client.get(f"/v1/persons/{person_id}/health-score")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="owner-b@example.com").status_code == 201
    foreign = other.get(f"/v1/persons/{person_id}/health-score")
    assert foreign.status_code == 404
