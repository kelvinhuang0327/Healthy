from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import HealthMetric
from sqlalchemy import func, select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _import_csv(
    client: TestClient,
    person_id: str,
    csv_content: str | bytes,
    *,
    headers: dict[str, str] | None = None,
):
    req_headers = {"Content-Type": "text/csv", **csrf_headers(client)}
    if headers:
        req_headers.update(headers)
    payload = csv_content.encode("utf-8") if isinstance(csv_content, str) else csv_content
    return client.post(
        f"/v1/persons/{person_id}/metrics/imports/csv",
        headers=req_headers,
        content=payload,
    )


def test_owned_person_can_import_valid_csv_and_view_in_history(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = (
        "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg,heart_rate_bpm,steps,weight_kg,blood_glucose_mg_dl,sleep_hours,note\n"
        "2026-08-01T08:00:00+08:00,120,80,72,8000,70.50,95.5,7.50,Morning check\n"
        "2026-08-01T12:00:00+08:00,,,,,70.40,,,\n"
    )

    response = _import_csv(client, person_id, csv_data)
    assert response.status_code == 200
    summary = response.json()
    assert summary == {
        "source_type": "external_csv",
        "total_rows": 2,
        "imported_count": 2,
        "duplicate_count": 0,
    }

    listing = client.get(f"/v1/persons/{person_id}/metrics")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 2

    # Check that imported rows report source_type = external_csv
    assert all(row["source_type"] == "external_csv" for row in rows)
    # Check that fingerprint is not exposed in public schema
    assert all("source_record_fingerprint" not in row for row in rows)
    assert all("fingerprint" not in row for row in rows)

    # Check history endpoint contains imported metrics
    history_res = client.get(f"/v1/persons/{person_id}/history")
    assert history_res.status_code == 200
    history_items = history_res.json()
    metric_history = [item for item in history_items if item["kind"] == "metric"]
    assert len(metric_history) == 2


def test_foreign_person_import_is_inaccessible_and_unauthorized_fails(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)

    csv_data = "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,72\n"

    # Unauthenticated
    unauth_client = TestClient(client.app, base_url=ORIGIN)
    unauth_res = unauth_client.post(
        f"/v1/persons/{person_a}/metrics/imports/csv",
        headers={"Content-Type": "text/csv", "Origin": ORIGIN},
        content=csv_data.encode("utf-8"),
    )
    assert unauth_res.status_code == 401

    # Foreign owner
    other_client = TestClient(client.app, base_url=ORIGIN)
    assert register(other_client, email="owner-b@example.com").status_code == 201
    foreign_res = _import_csv(other_client, person_a, csv_data)
    assert foreign_res.status_code == 404


def test_manual_vs_imported_provenance_distinction(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Manual create
    manual_res = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={"recorded_at": "2026-08-01T08:00:00Z", "heart_rate_bpm": 70},
    )
    assert manual_res.status_code == 201
    manual_body = manual_res.json()
    assert manual_body["source_type"] == "manual"

    # External CSV import
    import_res = _import_csv(
        client,
        person_id,
        "recorded_at,heart_rate_bpm\n2026-08-01T09:00:00Z,75\n",
    )
    assert import_res.status_code == 200

    listing = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(listing) == 2
    sources = {item["heart_rate_bpm"]: item["source_type"] for item in listing}
    assert sources == {70: "manual", 75: "external_csv"}


def test_all_supported_metric_fields_imported_and_utc_normalized(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = (
        "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg,heart_rate_bpm,steps,weight_kg,blood_glucose_mg_dl,sleep_hours,note\n"
        "2026-08-01T16:30:00+08:00,125,82,68,10500,68.75,102.3,8.25,Full measurement\n"
    )

    response = _import_csv(client, person_id, csv_data)
    assert response.status_code == 200

    rows = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(rows) == 1
    metric = rows[0]
    assert metric["systolic_bp_mm_hg"] == 125
    assert metric["diastolic_bp_mm_hg"] == 82
    assert metric["heart_rate_bpm"] == 68
    assert metric["steps"] == 10500
    assert metric["weight_kg"] == 68.75
    assert metric["blood_glucose_mg_dl"] == 102.3
    assert metric["sleep_hours"] == 8.25
    assert metric["note"] == "Full measurement"
    # UTC normalized from +08:00 (16:30 +08:00 -> 08:30 UTC)
    assert metric["recorded_at"].startswith("2026-08-01T08:30:00")


def test_unpaired_blood_pressure_rejects_entire_import(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = (
        "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg\n"
        "2026-08-01T08:00:00Z,120,80\n"
        "2026-08-01T09:00:00Z,120,\n"  # unpaired
    )

    response = _import_csv(client, person_id, csv_data)
    assert response.status_code == 422
    err = response.json()
    assert err["detail"]["code"] == "UNPAIRED_BLOOD_PRESSURE"
    assert err["detail"]["row"] == 2

    # Zero writes
    assert len(client.get(f"/v1/persons/{person_id}/metrics").json()) == 0


@pytest.mark.parametrize(
    ("csv_content", "expected_code", "expected_row", "expected_field"),
    [
        (
            "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,10\n",
            "OUT_OF_RANGE",
            1,
            "heart_rate_bpm",
        ),
        (
            "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,350\n",
            "OUT_OF_RANGE",
            1,
            "heart_rate_bpm",
        ),
        ("recorded_at,steps\n2026-08-01T08:00:00Z,-5\n", "OUT_OF_RANGE", 1, "steps"),
        ("recorded_at,weight_kg\n2026-08-01T08:00:00Z,0.5\n", "OUT_OF_RANGE", 1, "weight_kg"),
        (
            "recorded_at,weight_kg\n2026-08-01T08:00:00Z,70.123\n",
            "INVALID_PRECISION",
            1,
            "weight_kg",
        ),
        (
            "recorded_at,blood_glucose_mg_dl\n2026-08-01T08:00:00Z,5.0\n",
            "OUT_OF_RANGE",
            1,
            "blood_glucose_mg_dl",
        ),
        (
            "recorded_at,blood_glucose_mg_dl\n2026-08-01T08:00:00Z,95.55\n",
            "INVALID_PRECISION",
            1,
            "blood_glucose_mg_dl",
        ),
        (
            "recorded_at,sleep_hours\n2026-08-01T08:00:00Z,105.00\n",
            "OUT_OF_RANGE",
            1,
            "sleep_hours",
        ),
        (
            "recorded_at,sleep_hours\n2026-08-01T08:00:00Z,7.123\n",
            "INVALID_PRECISION",
            1,
            "sleep_hours",
        ),
        (
            "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,abc\n",
            "INVALID_INTEGER",
            1,
            "heart_rate_bpm",
        ),
        (
            "recorded_at,weight_kg\n2026-08-01T08:00:00Z,not_a_num\n",
            "INVALID_DECIMAL",
            1,
            "weight_kg",
        ),
    ],
)
def test_out_of_range_or_imprecise_metrics_reject_import(
    client: TestClient,
    csv_content: str,
    expected_code: str,
    expected_row: int,
    expected_field: str,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = _import_csv(client, person_id, csv_content)
    assert response.status_code == 422
    err = response.json()
    assert err["detail"]["code"] == expected_code
    assert err["detail"]["row"] == expected_row
    assert err["detail"]["field"] == expected_field

    # Zero writes
    assert len(client.get(f"/v1/persons/{person_id}/metrics").json()) == 0


def test_malformed_timestamp_and_missing_timezone_rejects(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Malformed
    res1 = _import_csv(client, person_id, "recorded_at,heart_rate_bpm\ninvalid_time,72\n")
    assert res1.status_code == 422
    assert res1.json()["detail"]["code"] == "INVALID_TIMESTAMP"

    # Missing timezone
    res2 = _import_csv(client, person_id, "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00,72\n")
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "TIMESTAMP_REQUIRED_TZ"

    # Future skew > 5 min
    future = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
    res3 = _import_csv(client, person_id, f"recorded_at,heart_rate_bpm\n{future},72\n")
    assert res3.status_code == 422
    assert res3.json()["detail"]["code"] == "TIMESTAMP_TOO_FUTURE"


def test_header_validation_unknown_duplicate_missing_and_no_metric(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Unknown header
    res1 = _import_csv(
        client, person_id, "recorded_at,heart_rate_bpm,unknown_col\n2026-08-01T08:00:00Z,72,foo\n"
    )
    assert res1.status_code == 422
    assert res1.json()["detail"]["code"] == "UNKNOWN_HEADER"

    # Duplicate header
    res2 = _import_csv(
        client, person_id, "recorded_at,heart_rate_bpm,heart_rate_bpm\n2026-08-01T08:00:00Z,72,72\n"
    )
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "DUPLICATE_HEADER"

    # Missing required recorded_at header
    res3 = _import_csv(client, person_id, "heart_rate_bpm,steps\n72,5000\n")
    assert res3.status_code == 422
    assert res3.json()["detail"]["code"] == "MISSING_REQUIRED_HEADER"

    # No metric header (only note)
    res4 = _import_csv(client, person_id, "recorded_at,note\n2026-08-01T08:00:00Z,just a note\n")
    assert res4.status_code == 422
    assert res4.json()["detail"]["code"] == "NO_METRIC_HEADER"


def test_blank_metric_row_rejects_entire_import(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = (
        "recorded_at,steps,heart_rate_bpm\n"
        "2026-08-01T08:00:00Z,8000,72\n"
        "2026-08-01T09:00:00Z,,\n"  # blank metric values
    )
    res = _import_csv(client, person_id, csv_data)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "BLANK_METRIC_ROW"
    assert res.json()["detail"]["row"] == 2

    # Zero writes
    assert len(client.get(f"/v1/persons/{person_id}/metrics").json()) == 0


def test_oversized_payload_and_too_many_rows_rejected_before_persistence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Oversized payload (> 1 MiB)
    big_note = "x" * 1500
    oversized_data = (
        "recorded_at,heart_rate_bpm,note\n" + f"2026-08-01T08:00:00Z,72,{big_note}\n" * 800
    )
    assert len(oversized_data.encode("utf-8")) > 1_048_576
    res1 = _import_csv(client, person_id, oversized_data)
    assert res1.status_code == 422
    assert res1.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"

    # Too many rows (> 5000 rows)
    rows_data = "recorded_at,heart_rate_bpm\n" + "2026-08-01T08:00:00Z,72\n" * 5001
    res2 = _import_csv(client, person_id, rows_data)
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "TOO_MANY_ROWS"


def test_repeat_import_is_idempotent_and_internal_duplicates_collapse(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # CSV with internal duplicate row
    csv_data = (
        "recorded_at,heart_rate_bpm,steps\n"
        "2026-08-01T08:00:00Z,72,5000\n"
        "2026-08-01T08:00:00Z,72,5000\n"  # identical to row 1
        "2026-08-01T09:00:00Z,75,6000\n"
    )

    first_res = _import_csv(client, person_id, csv_data)
    assert first_res.status_code == 200
    assert first_res.json() == {
        "source_type": "external_csv",
        "total_rows": 3,
        "imported_count": 2,
        "duplicate_count": 1,
    }

    # Stored rows in DB
    metrics_1 = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(metrics_1) == 2

    # Exact repeat import of same CSV
    second_res = _import_csv(client, person_id, csv_data)
    assert second_res.status_code == 200
    assert second_res.json() == {
        "source_type": "external_csv",
        "total_rows": 3,
        "imported_count": 0,
        "duplicate_count": 3,
    }

    # Still exactly 2 rows
    metrics_2 = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(metrics_2) == 2
    assert [m["id"] for m in metrics_2] == [m["id"] for m in metrics_1]


def test_same_canonical_record_for_different_persons_is_independent(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)

    other_client = TestClient(client.app, base_url=ORIGIN)
    assert register(other_client, email="owner-b@example.com").status_code == 201
    person_b = _person_id(other_client)

    csv_data = "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,72\n"

    res_a = _import_csv(client, person_a, csv_data)
    assert res_a.status_code == 200
    assert res_a.json()["imported_count"] == 1

    res_b = _import_csv(other_client, person_b, csv_data)
    assert res_b.status_code == 200
    assert res_b.json()["imported_count"] == 1

    assert len(client.get(f"/v1/persons/{person_a}/metrics").json()) == 1
    assert len(other_client.get(f"/v1/persons/{person_b}/metrics").json()) == 1


def test_concurrent_identical_imports_produce_one_logical_metric(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = "recorded_at,heart_rate_bpm,steps\n2026-08-01T08:00:00Z,72,5000\n"

    def do_import() -> int:
        c = TestClient(client.app, base_url=ORIGIN, cookies=client.cookies)
        res = _import_csv(c, person_id, csv_data)
        return res.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_import) for _ in range(5)]
        statuses = [f.result() for f in futures]

    assert all(status == 200 for status in statuses)

    # Exactly 1 row in database
    metrics = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(metrics) == 1


def test_atomic_zero_writes_on_any_invalid_row(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = (
        "recorded_at,heart_rate_bpm\n"
        "2026-08-01T08:00:00Z,70\n"
        "2026-08-01T09:00:00Z,72\n"
        "2026-08-01T10:00:00Z,999\n"  # out of range
    )

    response = _import_csv(client, person_id, csv_data)
    assert response.status_code == 422

    # Verify zero rows in database
    metrics = client.get(f"/v1/persons/{person_id}/metrics").json()
    assert len(metrics) == 0


def test_imported_metrics_participate_in_health_score_and_risk_alerts(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Import high blood pressure metrics
    csv_data = (
        "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg,heart_rate_bpm\n"
        "2026-08-01T08:00:00Z,160,100,80\n"
    )
    import_res = _import_csv(client, person_id, csv_data)
    assert import_res.status_code == 200

    # Health score evaluation
    score_res = client.get(f"/v1/persons/{person_id}/health-score")
    assert score_res.status_code == 200
    score_data = score_res.json()
    assert score_data["data_points"] >= 1

    # Risk alerts evaluation
    alerts_res = client.get(f"/v1/persons/{person_id}/risk-alerts")
    assert alerts_res.status_code == 200
    alerts_data = alerts_res.json()
    assert alerts_data["active_count"] >= 1
    assert any(
        alert["evidence"]["source_kind"] == "health_metric" for alert in alerts_data["alerts"]
    )


def test_repeated_gets_after_import_are_zero_write(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    csv_data = "recorded_at,heart_rate_bpm\n2026-08-01T08:00:00Z,72\n"
    assert _import_csv(client, person_id, csv_data).status_code == 200

    database = Database(DATABASE_URL)

    def count_metrics() -> int:
        with next(database.sessions()) as session:
            return session.scalar(select(func.count()).select_from(HealthMetric)) or 0

    count_before = count_metrics()
    assert count_before == 1

    for _ in range(3):
        assert client.get(f"/v1/persons/{person_id}/metrics").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/health-score").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/risk-alerts").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/history").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/analytics").status_code == 200

    count_after = count_metrics()
    assert count_after == count_before
