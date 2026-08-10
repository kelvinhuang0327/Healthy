from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import HealthMetric
from sqlalchemy import func, select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_metric(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "systolic_bp_mm_hg": None,
        "diastolic_bp_mm_hg": None,
        "heart_rate_bpm": None,
        "weight_kg": None,
        "blood_glucose_mg_dl": None,
        "sleep_hours": None,
        "note": None,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def test_metric_lifecycle_create_list_get_with_ordering_and_decimal_json_numbers(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    older = _create_metric(
        client,
        person_id,
        recorded_at="2026-01-01T00:00:00+00:00",
        heart_rate_bpm=60,
    )
    newer = _create_metric(
        client,
        person_id,
        recorded_at="2026-06-01T00:00:00+00:00",
        systolic_bp_mm_hg=120,
        diastolic_bp_mm_hg=80,
        weight_kg=70.25,
        blood_glucose_mg_dl=95.5,
        sleep_hours=7.25,
        note="After breakfast",
    )
    assert older.status_code == 201
    assert newer.status_code == 201

    newer_body = newer.json()
    assert newer_body["weight_kg"] == 70.25
    assert newer_body["blood_glucose_mg_dl"] == 95.5
    assert newer_body["sleep_hours"] == 7.25
    assert isinstance(newer_body["weight_kg"], float)
    assert isinstance(newer_body["blood_glucose_mg_dl"], float)
    assert "70.25" not in newer.text or isinstance(newer_body["weight_kg"], float)

    listing = client.get(f"/v1/persons/{person_id}/metrics")
    assert listing.status_code == 200
    rows = listing.json()
    assert [row["id"] for row in rows] == [newer_body["id"], older.json()["id"]]

    single = client.get(f"/v1/persons/{person_id}/metrics/{newer_body['id']}")
    assert single.status_code == 200
    assert single.json() == newer_body


def test_create_requires_at_least_one_metric_value(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = _create_metric(client, person_id)
    assert response.status_code == 422


def test_create_requires_paired_blood_pressure(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = _create_metric(client, person_id, systolic_bp_mm_hg=120)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"systolic_bp_mm_hg": 400, "diastolic_bp_mm_hg": 80},
        {"systolic_bp_mm_hg": 120, "diastolic_bp_mm_hg": 5},
        {"heart_rate_bpm": 10},
        {"weight_kg": 0.5},
        {"weight_kg": 70.123},
        {"blood_glucose_mg_dl": 5.0},
        {"blood_glucose_mg_dl": 95.55},
        {"sleep_hours": 100.00},
        {"sleep_hours": 7.123},
    ],
)
def test_create_rejects_out_of_range_or_imprecise_values(
    client: TestClient,
    overrides: dict[str, object],
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = _create_metric(client, person_id, **overrides)
    assert response.status_code == 422


def test_create_rejects_recorded_at_more_than_five_minutes_in_future(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    response = _create_metric(client, person_id, recorded_at=future, heart_rate_bpm=70)
    assert response.status_code == 422


def test_create_requires_timezone_aware_recorded_at(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = _create_metric(
        client,
        person_id,
        recorded_at="2026-01-01T00:00:00",
        heart_rate_bpm=70,
    )
    assert response.status_code == 422


def test_endpoints_require_authentication(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    client.cookies.clear()
    assert (
        client.post(
            f"/v1/persons/{person_id}/metrics",
            headers={"Origin": ORIGIN},
            json={"recorded_at": datetime.now(UTC).isoformat(), "heart_rate_bpm": 70},
        ).status_code
        == 401
    )
    assert client.get(f"/v1/persons/{person_id}/metrics").status_code == 401
    assert client.get(f"/v1/persons/{person_id}/metrics/{uuid.uuid4()}").status_code == 401


def test_create_requires_valid_csrf(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    payload = {"recorded_at": datetime.now(UTC).isoformat(), "heart_rate_bpm": 70}

    missing = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers={"Origin": ORIGIN},
        json=payload,
    )
    invalid = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"},
        json=payload,
    )
    assert missing.status_code == invalid.status_code == 403


def test_metrics_are_owner_scoped_and_foreign_access_is_404(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    created = _create_metric(client, person_a, sleep_hours=7.25)
    assert created.status_code == 201
    metric_a_id = created.json()["id"]

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="owner-b@example.com").status_code == 201
    person_b = _person_id(other)

    foreign_post = _create_metric(other, person_a, sleep_hours=7.25)
    assert foreign_post.status_code == 404

    foreign_list = other.get(f"/v1/persons/{person_a}/metrics")
    assert foreign_list.status_code == 404

    foreign_get = other.get(f"/v1/persons/{person_a}/metrics/{metric_a_id}")
    assert foreign_get.status_code == 404
    assert metric_a_id not in foreign_get.text

    assert person_b != person_a


def test_missing_metric_id_returns_404(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = client.get(f"/v1/persons/{person_id}/metrics/{uuid.uuid4()}")
    assert response.status_code == 404


def test_missing_person_id_returns_404(client: TestClient) -> None:
    assert register(client).status_code == 201
    missing_person = uuid.uuid4()
    payload = {"recorded_at": datetime.now(UTC).isoformat(), "heart_rate_bpm": 70}
    assert (
        client.post(
            f"/v1/persons/{missing_person}/metrics",
            headers=csrf_headers(client),
            json=payload,
        ).status_code
        == 404
    )
    assert client.get(f"/v1/persons/{missing_person}/metrics").status_code == 404
    assert client.get(f"/v1/persons/{missing_person}/metrics/{uuid.uuid4()}").status_code == 404


def test_repeated_gets_do_not_write_health_metrics(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_metric(client, person_id, heart_rate_bpm=72)
    assert created.status_code == 201
    metric_id = created.json()["id"]

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[int, list[tuple[object, ...]]]:
        with next(database.sessions()) as database_session:
            rows = list(
                database_session.execute(
                    select(
                        HealthMetric.id,
                        HealthMetric.recorded_at,
                        HealthMetric.created_at,
                        HealthMetric.heart_rate_bpm,
                    ).order_by(HealthMetric.id)
                ).tuples()
            )
            count = database_session.scalar(select(func.count()).select_from(HealthMetric)) or 0
            return count, rows

    before = snapshot()
    for _ in range(3):
        assert client.get(f"/v1/persons/{person_id}/metrics").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/metrics/{metric_id}").status_code == 200
    after = snapshot()
    assert after == before


def test_service_layer_maps_integrity_error_without_leaking_sql(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        with pytest.raises(services.HealthMetricIntegrityError):
            services.create_health_metric(
                database_session,
                person_id=person_id,
                recorded_at=datetime.now(UTC),
                systolic_bp_mm_hg=15,
                diastolic_bp_mm_hg=15,
                heart_rate_bpm=None,
                weight_kg=None,
                blood_glucose_mg_dl=None,
                sleep_hours=None,
                note=None,
            )
        assert database_session.scalar(select(func.count()).select_from(HealthMetric)) == 0


def test_create_route_returns_generic_422_on_integrity_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    def raise_integrity_error(*_args: object, **_kwargs: object) -> HealthMetric:
        raise services.HealthMetricIntegrityError

    monkeypatch.setattr(services, "create_health_metric", raise_integrity_error)
    response = _create_metric(client, person_id, heart_rate_bpm=72)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "constraint" not in response.text.casefold()
    assert "sql" not in response.text.casefold()


def test_weight_and_glucose_reject_string_precision_drift(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    response = _create_metric(client, person_id, weight_kg=str(Decimal("70.999")))
    assert response.status_code == 422
