from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from conftest import ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.domain import report_intakes as parser
from healthy.infrastructure.models import (
    HealthReportModel,
    ReportIntakeModel,
    ReportIntakeObservationModel,
)
from healthy.infrastructure.repositories import ReportIntakeRepository
from healthy.main import create_app
from sqlalchemy import func, select
from test_health_report_intakes import _pdf_bytes, _person_id
from test_report_pdf_ocr import _scanned_pdf


def upload(client: TestClient, person_id: str, content: bytes):
    return client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-retry.pdf", content, "application/pdf")},
    )


def retry(client: TestClient, person_id: str, intake_id: str, content: bytes):
    return client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}/retry",
        headers=csrf_headers(client),
        files={"file": ("synthetic-retry.pdf", content, "application/pdf")},
    )


def fail_upload(client, monkeypatch, content: bytes):
    original = parser.extract_and_parse

    def unavailable(**kwargs):
        raise parser.ReportIntakeParserError("Synthetic local OCR tool is unavailable.")

    monkeypatch.setattr(parser, "extract_and_parse", unavailable)
    person_id = _person_id(client)
    response = upload(client, person_id, content)
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    monkeypatch.setattr(parser, "extract_and_parse", original)
    return person_id, response.json()


def row_counts(client):
    with next(client.app.state.database.sessions()) as db:
        return [
            db.scalar(select(func.count()).select_from(model))
            for model in [ReportIntakeModel, ReportIntakeObservationModel, HealthReportModel]
        ]


def confirm_with_correction(client, person_id: str, intake: dict):
    observation = intake["observations"][0]
    patched = client.patch(
        f"/v1/persons/{person_id}/report-intakes/{intake['id']}",
        headers=csrf_headers(client),
        json={
            "observations": [
                {
                    "id": observation["id"],
                    "code": observation["code"],
                    "display_name": observation["display_name"],
                    "value_numeric": 93.25,
                    "unit": observation["unit"],
                    "reference_range": observation["reference_range"],
                    "observed_at": observation["observed_at"],
                }
            ]
        },
    )
    assert patched.status_code == 200
    confirmed = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    return confirmed.json()


def test_recovered_tool_can_explicitly_retry_the_same_failed_file(client, monkeypatch):
    assert register(client).status_code == 201
    person_id = _person_id(client)
    content = _pdf_bytes(["Report Date: 2026-10-01", "Glucose: 92 mg/dL"])
    original = parser.extract_and_parse

    def unavailable(**kwargs):
        raise parser.ReportIntakeParserError("Synthetic local OCR tool is unavailable.")

    monkeypatch.setattr(parser, "extract_and_parse", unavailable)
    failed = upload(client, person_id, content)
    assert failed.status_code == 201
    intake_id = failed.json()["id"]
    assert failed.json()["status"] == "failed"
    monkeypatch.setattr(parser, "extract_and_parse", original)
    duplicate = upload(client, person_id, content)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == intake_id
    assert duplicate.json()["status"] == "failed"
    recovered = retry(client, person_id, intake_id, content)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["id"] == intake_id
    assert recovered.json()["status"] == "pending_review"


def test_real_chinese_ocr_recovers_after_restart_and_exports_only_human_corrected_data(
    client, monkeypatch, tmp_path
):
    assert register(client).status_code == 201
    person_id = _person_id(client)
    content = _scanned_pdf(
        [
            "Source: Synthetic Retry Lab",
            "Report Date: 2026-10-01",
            "血糖: 92 mg/dL (65-99)",
        ]
    )
    tools = os.environ["HEALTHY_OCR_TESSDATA_DIR"]
    assert (Path(tools) / "chi_tra.traineddata").is_file()
    monkeypatch.setenv("HEALTHY_OCR_TESSDATA_DIR", str(tmp_path))
    failed = upload(client, person_id, content)
    assert failed.status_code == 201
    original = failed.json()
    assert original["status"] == "failed"
    assert "requires local eng and chi_tra" in original["error_message"]
    monkeypatch.setenv("HEALTHY_OCR_TESSDATA_DIR", tools)
    # A fresh app/engine reads the existing SQLite file, not in-memory state.
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        recovered = retry(restarted, person_id, original["id"], content)
        assert recovered.status_code == 200
        intake = recovered.json()
        assert intake["id"] == original["id"]
        assert intake["created_at"] == original["created_at"]
        assert intake["extraction_method"] == "ocr_pdf"
        assert intake["status"] == "pending_review"
        assert intake["error_message"] is None
        assert intake["report_id"] is None
        assert intake["observations"][0]["value_numeric"] == 92
        assert (
            "No confirmed lab report"
            in restarted.get(f"/v1/persons/{person_id}/summary-export").text
        )
        assert retry(restarted, person_id, intake["id"], content).status_code == 409
        confirmed = confirm_with_correction(restarted, person_id, intake)
        repeated_confirm = restarted.post(
            f"/v1/persons/{person_id}/report-intakes/{intake['id']}/confirm",
            headers=csrf_headers(restarted),
        )
        assert repeated_confirm.json()["report_id"] == confirmed["report_id"]
        assert retry(restarted, person_id, intake["id"], content).status_code == 409
        assert upload(restarted, person_id, content).json()["id"] == intake["id"]
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as persisted:
        persisted.cookies.update(client.cookies)
        report = persisted.get(f"/v1/persons/{person_id}/reports/{confirmed['report_id']}").json()
        assert report["observations"][0]["value_numeric"] == 93.25
        assert report["observations"][0]["unit"] == "mg/dL"
        assert report["observations"][0]["reference_range"] == "65-99"
        history = persisted.get(f"/v1/persons/{person_id}/history").json()
        assert any(row["primary_value"] == "93.25" for row in history)
        summary = persisted.get(f"/v1/persons/{person_id}/summary-export")
        assert summary.status_code == 200
        assert "血糖" in summary.text
        assert "93.2500" in summary.text
        assert "Synthetic Retry Lab" in summary.text
        assert "65-99" in summary.text
        assert row_counts(persisted) == [1, 1, 1]


@pytest.mark.parametrize("late_failure", [False, True])
def test_concurrent_retries_cannot_overwrite_winners_review_or_confirmation(
    client, monkeypatch, late_failure
):
    assert register(client).status_code == 201
    content = _pdf_bytes(["Glucose: 92 mg/dL"])
    person_id, failed = fail_upload(client, monkeypatch, content)
    parsed = parser.parse_extracted_text(
        text="Report Date: 2026-10-01\nGlucose: 92 mg/dL",
        filename="synthetic-retry.pdf",
        extraction_method="digital_pdf",
        page_count=1,
    )
    barrier = threading.Barrier(2)
    reviewed = threading.Event()
    counter_lock = threading.Lock()
    attempts = 0

    def racing_extract(**kwargs):
        nonlocal attempts
        with counter_lock:
            ordinal = attempts
            attempts += 1
        barrier.wait(timeout=10)
        if ordinal == 1:
            assert reviewed.wait(timeout=10)
            if late_failure:
                raise parser.ReportIntakeParserError("Synthetic delayed tool failure.")
        return parsed

    monkeypatch.setattr(parser, "extract_and_parse", racing_extract)

    def request_retry():
        with TestClient(client.app, base_url=ORIGIN) as peer:
            peer.cookies.update(client.cookies)
            response = retry(peer, person_id, failed["id"], content)
            assert response.status_code == 200
            if response.json()["status"] == "pending_review":
                confirm_with_correction(peer, person_id, response.json())
                reviewed.set()
            return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(request_retry) for _ in range(2)]
        results = [future.result(timeout=15) for future in responses]
    assert {row["status"] for row in results} == {"pending_review", "confirmed"}
    assert {row["id"] for row in results} == {failed["id"]}
    latest = client.get(f"/v1/persons/{person_id}/report-intakes/{failed['id']}").json()
    assert latest["status"] == "confirmed"
    assert latest["observations"][0]["value_numeric"] == 93.25
    assert row_counts(client) == [1, 1, 1]


def test_failed_retry_is_atomic_and_remains_recoverable(client, monkeypatch):
    registered = register(client).json()
    content = _pdf_bytes(["Glucose: 92 mg/dL"])
    person_id, failed = fail_upload(client, monkeypatch, content)
    original = ReportIntakeRepository._add_observations
    parsed = parser.parse_extracted_text(
        text="Glucose: 92 mg/dL",
        filename="synthetic-retry.pdf",
        extraction_method="digital_pdf",
        page_count=1,
    )
    monkeypatch.setattr(parser, "extract_and_parse", lambda **kwargs: parsed)

    def interrupt(*args, **kwargs):
        raise RuntimeError("Synthetic interruption after state update.")

    monkeypatch.setattr(ReportIntakeRepository, "_add_observations", interrupt)
    with next(client.app.state.database.sessions()) as db:
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            services.retry_report_intake(
                db,
                owner_account_id=UUID(registered["account"]["id"]),
                person_id=UUID(person_id),
                intake_id=UUID(failed["id"]),
                filename="synthetic-retry.pdf",
                media_type="application/pdf",
                file_bytes=content,
                now=datetime.now(UTC),
            )
    assert client.get(f"/v1/persons/{person_id}/report-intakes/{failed['id']}").json() == failed
    assert row_counts(client) == [1, 0, 0]
    monkeypatch.setattr(ReportIntakeRepository, "_add_observations", original)
    assert retry(client, person_id, failed["id"], content).json()["status"] == "pending_review"


def test_late_failure_cannot_replace_a_newer_failed_attempt(client, monkeypatch):
    assert register(client).status_code == 201
    content = _pdf_bytes(["Glucose: 92 mg/dL"])
    person_id, failed = fail_upload(client, monkeypatch, content)
    fired = False

    with TestClient(client.app, base_url=ORIGIN) as winner:
        winner.cookies.update(client.cookies)

        def delayed_extract(**kwargs):
            nonlocal fired
            if not fired:
                fired = True
                newest = retry(winner, person_id, failed["id"], content)
                assert newest.status_code == 200
                assert newest.json()["error_message"] == "Synthetic newest failure."
                raise parser.ReportIntakeParserError("Synthetic stale failure.")
            raise parser.ReportIntakeParserError("Synthetic newest failure.")

        monkeypatch.setattr(parser, "extract_and_parse", delayed_extract)
        stale = retry(client, person_id, failed["id"], content)
        assert stale.status_code == 200
        assert stale.json()["error_message"] == "Synthetic newest failure."
        assert stale.json()["id"] == failed["id"]
        assert stale.json()["updated_at"] > failed["updated_at"]
        assert row_counts(client) == [1, 0, 0]


def test_retry_denies_wrong_file_person_account_and_missing_command_authority(client, monkeypatch):
    assert register(client).status_code == 201
    content = _pdf_bytes(["Glucose: 92 mg/dL"])
    person_id, failed = fail_upload(client, monkeypatch, content)
    child = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "Synthetic Child", "relationship": "child"},
    )
    assert child.status_code == 201

    def forbidden_extract(**kwargs):
        pytest.fail("Rejected requests must not invoke the decoder or OCR.")

    monkeypatch.setattr(parser, "extract_and_parse", forbidden_extract)
    assert (
        retry(client, person_id, failed["id"], _pdf_bytes(["Glucose: 95 mg/dL"])).status_code == 422
    )
    assert retry(client, child.json()["id"], failed["id"], content).status_code == 404
    url = f"/v1/persons/{person_id}/report-intakes/{failed['id']}/retry"
    for headers in [{}, {"Origin": "https://foreign.invalid"}, {"Origin": ORIGIN}]:
        response = client.post(
            url, headers=headers, files={"file": ("synthetic.pdf", content, "application/pdf")}
        )
        assert response.status_code == 403
    with TestClient(client.app, base_url=ORIGIN) as stranger:
        assert (
            stranger.post(
                url,
                headers={"Origin": ORIGIN},
                files={"file": ("synthetic.pdf", content, "application/pdf")},
            ).status_code
            == 401
        )
        assert register(stranger, email="synthetic-stranger@example.com").status_code == 201
        assert retry(stranger, person_id, failed["id"], content).status_code == 404
        assert retry(stranger, _person_id(stranger), failed["id"], content).status_code == 404
    assert client.get(f"/v1/persons/{person_id}/report-intakes/{failed['id']}").json() == failed


def test_retry_retains_upload_limits_and_failed_state(client, monkeypatch):
    assert register(client).status_code == 201
    content = _pdf_bytes(["Glucose: 92 mg/dL"])
    person_id, failed = fail_upload(client, monkeypatch, content)
    assert retry(client, person_id, failed["id"], b"not a PDF").status_code == 415
    assert (
        retry(
            client, person_id, failed["id"], b"%PDF-" + b"x" * parser.MAX_UPLOAD_BYTES
        ).status_code
        == 413
    )
    assert client.get(f"/v1/persons/{person_id}/report-intakes/{failed['id']}").json() == failed
    assert row_counts(client) == [1, 0, 0]
