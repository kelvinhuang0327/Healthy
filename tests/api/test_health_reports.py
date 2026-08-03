from __future__ import annotations

from conftest import ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _valid_report_payload() -> dict:
    return {
        "schema_version": "healthy.health-report.v1",
        "source_name": "LabCorp Diagnostics",
        "reported_at": "2026-08-01T08:00:00Z",
        "observations": [
            {
                "code": "GLUCOSE",
                "display_name": "Fasting Blood Glucose",
                "value_numeric": 95.5,
                "unit": "mg/dL",
                "reference_range": "70-99",
                "observed_at": "2026-08-01T08:00:00Z",
            },
            {
                "code": "HEMOGLOBIN_A1C",
                "display_name": "Hemoglobin A1c",
                "value_numeric": 5.4,
                "unit": "%",
                "reference_range": "<5.7",
                "observed_at": "2026-08-01T08:00:00Z",
            },
        ],
    }


def test_import_health_report_lifecycle_and_idempotency(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    payload = _valid_report_payload()

    # 1. Import new report -> 201 Created, status='pending'
    resp = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=payload,
    )
    assert resp.status_code == 201
    report = resp.json()
    report_id = report["id"]
    assert report["person_id"] == person_id
    assert report["schema_version"] == "healthy.health-report.v1"
    assert report["source_name"] == "LabCorp Diagnostics"
    assert report["status"] == "pending"
    assert report["confirmed_at"] is None
    assert len(report["observations"]) == 2
    assert report["canonical_sha256"] is not None

    # 2. Re-import exact same report -> 200 OK (idempotent, return same report)
    re_resp = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=payload,
    )
    assert re_resp.status_code == 200
    assert re_resp.json()["id"] == report_id

    # 3. List reports -> 200 OK
    list_resp = client.get(f"/v1/persons/{person_id}/reports")
    assert list_resp.status_code == 200
    reports_list = list_resp.json()
    assert len(reports_list) == 1
    assert reports_list[0]["id"] == report_id

    # 4. Detail report -> 200 OK
    detail_resp = client.get(f"/v1/persons/{person_id}/reports/{report_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == report_id

    # 5. Confirm report -> 200 OK, status='confirmed'
    confirm_resp = client.post(
        f"/v1/persons/{person_id}/reports/{report_id}/confirm",
        headers=csrf_headers(client),
    )
    assert confirm_resp.status_code == 200
    confirmed_data = confirm_resp.json()
    assert confirmed_data["status"] == "confirmed"
    assert confirmed_data["confirmed_at"] is not None

    # 6. Re-confirm report -> 200 OK (idempotent)
    re_confirm = client.post(
        f"/v1/persons/{person_id}/reports/{report_id}/confirm",
        headers=csrf_headers(client),
    )
    assert re_confirm.status_code == 200
    assert re_confirm.json()["confirmed_at"] == confirmed_data["confirmed_at"]


def test_zero_write_get_operations(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    payload = _valid_report_payload()

    resp = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=payload,
    )
    assert resp.status_code == 201
    report_id = resp.json()["id"]

    # Multiple GETs should succeed zero-write
    for _ in range(3):
        assert client.get(f"/v1/persons/{person_id}/reports").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/reports/{report_id}").status_code == 200


def test_pending_reports_excluded_and_confirmed_included_in_today_guidance(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    payload = _valid_report_payload()

    # Import pending report
    import_resp = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=payload,
    )
    assert import_resp.status_code == 201
    report_id = import_resp.json()["id"]

    # Check Today view: pending report must NOT create a recent_report_imported item
    today_pending = client.get(f"/v1/persons/{person_id}/assistant/today")
    assert today_pending.status_code == 200
    today_data = today_pending.json()
    attention_kinds = [item["kind"] for item in today_data["daily_attention"]]
    assert "recent_report_imported" not in attention_kinds

    # Confirm report
    confirm_resp = client.post(
        f"/v1/persons/{person_id}/reports/{report_id}/confirm",
        headers=csrf_headers(client),
    )
    assert confirm_resp.status_code == 200

    # Check Today view: confirmed report MUST generate recent_report_imported guidance item
    today_confirmed = client.get(f"/v1/persons/{person_id}/assistant/today")
    assert today_confirmed.status_code == 200
    confirmed_kinds = [item["kind"] for item in today_confirmed.json()["daily_attention"]]
    assert "recent_report_imported" in confirmed_kinds


def test_schema_validation_rejections(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    # Missing schema_version -> 422
    bad_schema = _valid_report_payload()
    bad_schema["schema_version"] = "invalid.v1"
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports",
            headers=csrf_headers(client),
            json=bad_schema,
        ).status_code
        == 422
    )

    # Empty observations -> 422
    empty_obs = _valid_report_payload()
    empty_obs["observations"] = []
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports",
            headers=csrf_headers(client),
            json=empty_obs,
        ).status_code
        == 422
    )

    # Observation without value_numeric and value_text -> 422
    no_value_obs = _valid_report_payload()
    no_value_obs["observations"] = [{"code": "NO_VAL", "display_name": "No Value"}]
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports",
            headers=csrf_headers(client),
            json=no_value_obs,
        ).status_code
        == 422
    )


def test_owner_isolation_and_404(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a_id = _person_id(client)

    # Register second user
    client.post("/v1/sessions/current", headers={"Origin": ORIGIN})  # logout
    assert register(client, email="owner-b@example.com").status_code == 201
    person_b_id = _person_id(client)

    # Owner B imports a report for Person B
    import_b = client.post(
        f"/v1/persons/{person_b_id}/reports",
        headers=csrf_headers(client),
        json=_valid_report_payload(),
    )
    assert import_b.status_code == 201
    report_b_id = import_b.json()["id"]

    # Owner B attempting to access Person A's endpoint returns 404
    assert client.get(f"/v1/persons/{person_a_id}/reports").status_code == 404
    assert client.get(f"/v1/persons/{person_a_id}/reports/{report_b_id}").status_code == 404
    assert (
        client.post(
            f"/v1/persons/{person_a_id}/reports",
            headers=csrf_headers(client),
            json=_valid_report_payload(),
        ).status_code
        == 404
    )
