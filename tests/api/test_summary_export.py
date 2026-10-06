from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthMetric,
    HealthReportModel,
    Person,
    SessionRecord,
    SymptomLog,
)
from sqlalchemy import func, select
from test_health_history import _create_metric, _create_symptom, _person_id, _report_payload


class HtmlShape(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend(attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def export(client: TestClient, person_id: str):
    return client.get(f"/v1/persons/{person_id}/summary-export")


def row_counts() -> list[int | None]:
    with next(Database(DATABASE_URL).sessions()) as db:
        return [
            db.scalar(select(func.count()).select_from(model))
            for model in [
                Account,
                Person,
                SessionRecord,
                HealthMetric,
                SymptomLog,
                HealthAction,
                HealthReportModel,
            ]
        ]


def test_empty_export_has_explicit_missing_data_and_download_headers(client: TestClient) -> None:
    assert register(client, display_name="合成測試人物").status_code == 201
    person_id = _person_id(client)
    before = row_counts()
    response = export(client, person_id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="healthy-summary-{person_id}-'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "合成測試人物" in response.content.decode("utf-8")
    for message in [
        "No recorded metrics",
        "No confirmed lab report",
        "No recorded symptoms",
        "No open actions",
        "insufficient data",
        "not a diagnosis",
        "not a complete data backup",
    ]:
        assert message in response.text
    assert "owner@example.com" not in response.text
    assert client.cookies.get("healthy_session") not in response.text
    assert "Synthetic-Password-42" not in response.text
    assert export(client, person_id).status_code == 200
    assert row_counts() == before


def test_summary_keeps_values_provenance_and_only_latest_confirmed_report(
    client: TestClient,
) -> None:
    assert register(client, display_name="中文人物 <script>bad()</script>").status_code == 201
    person_id = _person_id(client)
    now = datetime.now(UTC)
    assert (
        _create_metric(
            client,
            person_id,
            recorded_at=(now - timedelta(days=2)).isoformat(),
            weight_kg=55,
            note="OLD-METRIC-SENTINEL",
        ).status_code
        == 201
    )
    assert (
        _create_metric(
            client,
            person_id,
            recorded_at=now.isoformat(),
            weight_kg=61.25,
            sleep_hours=7.5,
            note='<img src="https://external.invalid">\n合成備註',
        ).status_code
        == 201
    )
    assert (
        _create_symptom(
            client, person_id, symptom="合成頭痛", duration_minutes=30, severity=3, note="症狀備註"
        ).status_code
        == 201
    )
    created = client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json={"title": "未完成行動", "description": "合成行動備註"},
    )
    assert created.status_code == 201
    payload = _report_payload(
        source_name="已確認合成檢驗所",
        reported_at=now.isoformat(),
        code="GLUCOSE",
        display_name="血糖",
        observed_at=now.isoformat(),
        value_numeric=0,
    )
    confirmed = client.post(
        f"/v1/persons/{person_id}/reports", headers=csrf_headers(client), json=payload
    )
    assert confirmed.status_code == 201
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports/{confirmed.json()['id']}/confirm",
            headers=csrf_headers(client),
        ).status_code
        == 200
    )
    payload["source_name"] = "UNREVIEWED-SENTINEL"
    payload["reported_at"] = (now + timedelta(hours=1)).isoformat()
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports", headers=csrf_headers(client), json=payload
        ).status_code
        == 201
    )
    response = export(client, person_id)
    shape = HtmlShape()
    shape.feed(response.text)
    plain = " ".join(shape.text)
    for value in [
        "中文人物 <script>bad()</script>",
        "61.25",
        "7.50",
        "Blood glucose (mg/dL)",
        "kg",
        "hours",
        "合成備註",
        "合成頭痛",
        "30",
        "3",
        "症狀備註",
        "未完成行動",
        "已確認合成檢驗所",
        "血糖",
        "0.0000",
        "70-99",
        "UTC",
        "Limitations",
    ]:
        assert value in plain
    assert "OLD-METRIC-SENTINEL" not in response.text and "UNREVIEWED-SENTINEL" not in response.text
    assert "script" not in shape.tags and "img" not in shape.tags
    assert not any(key in ["src", "href", "onerror", "onclick"] for key, _value in shape.attrs)
    assert any(
        key == "http-equiv" and value == "Content-Security-Policy" for key, value in shape.attrs
    )


def test_summary_never_exports_another_person_or_owner(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    other_person = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "OTHER-PERSON-SENTINEL", "relationship": "family"},
    ).json()["id"]
    assert (
        _create_metric(
            client, other_person, note="FOREIGN-METRIC-SENTINEL", weight_kg=80
        ).status_code
        == 201
    )
    assert "OTHER-PERSON-SENTINEL" not in export(client, person_id).text
    assert "FOREIGN-METRIC-SENTINEL" not in export(client, person_id).text
    assert "FOREIGN-METRIC-SENTINEL" in export(client, other_person).text
    outsider = TestClient(client.app)
    assert register(outsider, email="outsider@example.com").status_code == 201
    denied = export(outsider, person_id)
    missing = export(outsider, str(uuid.uuid4()))
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json() == {"detail": "Person not found"}
    assert export(TestClient(client.app), person_id).status_code == 401


def test_summary_declares_and_applies_recent_item_limits(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    now = datetime.now(UTC)
    for i in range(6):
        assert (
            _create_symptom(
                client,
                person_id,
                symptom=f"SYNTHETIC-SYMPTOM-{i}",
                occurred_at=(now - timedelta(hours=i)).isoformat(),
            ).status_code
            == 201
        )
    response = export(client, person_id)
    assert "Recent symptoms (up to 5)" in response.text
    for i in range(5):
        assert f"SYNTHETIC-SYMPTOM-{i}" in response.text
    assert "SYNTHETIC-SYMPTOM-5" not in response.text
