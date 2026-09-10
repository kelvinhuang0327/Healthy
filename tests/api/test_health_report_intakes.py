from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from conftest import ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.domain.report_intakes import MAX_UPLOAD_BYTES
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    HealthReportModel,
    ReportIntakeModel,
    ReportIntakeObservationModel,
)
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _pdf_bytes(lines: list[str]) -> bytes:
    commands: list[bytes] = []
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"BT /F1 18 Tf 60 {730 - index * 28} Td ({escaped}) Tj ET".encode())
    stream = b"\n".join(commands)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return bytes(output)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (1800, 500), "white")
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ):
        if candidate.exists():
            font = ImageFont.truetype(str(candidate), 54)
            break
    if font is None:
        font = ImageFont.load_default()
    for index, line in enumerate(
        [
            "Source: Synthetic OCR Lab",
            "Report Date: 2026-09-01",
            "Glucose: 92 mg/dL (65-99)",
        ]
    ):
        draw.text((40, 40 + index * 110), line, fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_pdf(
    client: TestClient,
    person_id: str,
    lines: list[str],
    *,
    filename: str = "synthetic-lab.pdf",
):
    return client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": (filename, _pdf_bytes(lines), "application/pdf")},
    )


def test_digital_pdf_review_confirmation_idempotency_and_sqlite_persistence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    source_marker = "SYNTHETIC_SOURCE_MARKER_DO_NOT_LOG"
    response = _upload_pdf(
        client,
        person_id,
        [
            f"Source: {source_marker}",
            "Report Date: 2026-09-01",
            "Glucose: 92 mg/dL (65-99)",
            "Weight: 70 kg",
        ],
    )
    assert response.status_code == 201
    intake = response.json()
    intake_id = intake["id"]
    assert intake["status"] == "pending_review"
    assert intake["pending_review"] is True
    assert intake["source_name"] == source_marker
    assert intake["extraction_method"] == "digital_pdf"
    assert intake["parser_metadata"]["page_count"] == 1
    assert source_marker not in (intake.get("error_message") or "")
    assert len(intake["observations"]) == 2
    glucose = next(item for item in intake["observations"] if item["code"] == "GLUCOSE")
    weight = next(item for item in intake["observations"] if item["code"] == "WEIGHT")
    assert glucose["value_numeric"] == 92
    assert glucose["unit"] == "mg/dL"
    assert glucose["reference_range"] == "65-99"
    assert glucose["parser_confidence"] is None
    assert weight["reference_range"] is None

    duplicate = _upload_pdf(
        client,
        person_id,
        [
            f"Source: {source_marker}",
            "Report Date: 2026-09-01",
            "Glucose: 92 mg/dL (65-99)",
            "Weight: 70 kg",
        ],
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == intake_id

    database: Database = client.app.state.database
    with next(database.sessions()) as database_session:
        stored_intake = database_session.get(ReportIntakeModel, UUID(intake_id))
        assert stored_intake is not None
        assert not hasattr(stored_intake, "raw_bytes")
        assert not hasattr(stored_intake, "extracted_text")
        assert "raw_bytes" not in [column.name for column in ReportIntakeModel.__table__.columns]
        assert "extracted_text" not in [
            column.name for column in ReportIntakeModel.__table__.columns
        ]
        stored_observations = list(
            database_session.scalars(
                select(ReportIntakeObservationModel).where(
                    ReportIntakeObservationModel.intake_id == UUID(intake_id)
                )
            )
        )
        assert len(stored_observations) == 2
        assert all(
            source_marker not in (observation.value_text or "")
            for observation in stored_observations
        )

    detail = client.get(f"/v1/persons/{person_id}/report-intakes/{intake_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == intake_id

    patched = client.patch(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}",
        headers=csrf_headers(client),
        json={
            "source_name": "Corrected Synthetic Lab",
            "reported_at": "2026-09-02T00:00:00Z",
            "observations": [
                {
                    "id": glucose["id"],
                    "code": "GLUCOSE",
                    "display_name": "Corrected Glucose",
                    "value_numeric": 91.25,
                    "value_text": None,
                    "unit": "mg/dL",
                    "reference_range": "65-99",
                    "observed_at": "2026-09-02T00:00:00Z",
                },
                {
                    "code": "NOTE",
                    "display_name": "Synthetic note",
                    "value_numeric": None,
                    "value_text": "reviewed by user",
                    "unit": None,
                    "reference_range": None,
                    "observed_at": None,
                },
            ],
        },
    )
    assert patched.status_code == 200, patched.text
    patched_data = patched.json()
    assert patched_data["source_name"] == "Corrected Synthetic Lab"
    assert len(patched_data["observations"]) == 2
    assert patched_data["observations"][0]["id"] == glucose["id"]
    assert patched_data["observations"][0]["parser_provenance"] == glucose["parser_provenance"]
    assert patched_data["observations"][1]["parser_provenance"] == "human_added"

    reloaded = client.get(f"/v1/persons/{person_id}/report-intakes/{intake_id}").json()
    assert reloaded["source_name"] == "Corrected Synthetic Lab"
    assert reloaded["observations"][0]["value_numeric"] == 91.25
    assert all(item["code"] != "WEIGHT" for item in reloaded["observations"])

    unsafe_source = client.patch(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}",
        headers=csrf_headers(client),
        json={"source_name": "../../\\\\\u0000evil"},
    )
    assert unsafe_source.status_code == 200, unsafe_source.text
    safe_source = unsafe_source.json()["source_name"]
    assert safe_source == "evil"
    assert "/" not in safe_source
    assert "\\" not in safe_source
    assert "\x00" not in safe_source

    confirmation = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmation.status_code == 200
    confirmed_intake = confirmation.json()
    assert confirmed_intake["status"] == "confirmed"
    assert confirmed_intake["pending_review"] is False
    report_id = confirmed_intake["report_id"]
    assert report_id

    canonical = client.get(f"/v1/persons/{person_id}/reports/{report_id}")
    assert canonical.status_code == 200
    canonical_data = canonical.json()
    assert canonical_data["schema_version"] == "healthy.health-report.v1"
    assert canonical_data["status"] == "confirmed"
    canonical_glucose = next(
        item for item in canonical_data["observations"] if item["code"] == "GLUCOSE"
    )
    assert canonical_glucose["value_numeric"] == 91.25
    assert canonical_glucose["reference_range"] == "65-99"

    repeated_confirmation = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}/confirm",
        headers=csrf_headers(client),
    )
    assert repeated_confirmation.status_code == 200
    assert repeated_confirmation.json()["report_id"] == report_id
    with next(database.sessions()) as database_session:
        assert (
            database_session.scalar(
                select(func.count())
                .select_from(HealthReportModel)
                .where(HealthReportModel.person_id == UUID(person_id))
            )
            == 1
        )
        assert database_session.get(HealthReportModel, UUID(report_id)) is not None


def test_intake_upload_boundary_parser_failure_and_zero_candidate_confirmation(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    unauthorized = TestClient(client.app, base_url=ORIGIN).post(
        f"/v1/persons/{person_id}/report-intakes",
        files={"file": ("report.pdf", _pdf_bytes(["Glucose: 92"]), "application/pdf")},
    )
    assert unauthorized.status_code == 403

    unsupported = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("report.txt", b"Glucose: 92", "text/plain")},
    )
    assert unsupported.status_code == 415

    oversized = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={
            "file": (
                "report.pdf",
                b"%PDF-" + b"x" * MAX_UPLOAD_BYTES,
                "application/pdf",
            )
        },
    )
    assert oversized.status_code == 413

    failed = _upload_pdf(client, person_id, ["This synthetic file has no observation fields."])
    assert failed.status_code == 201
    failed_data = failed.json()
    assert failed_data["status"] == "failed"
    assert failed_data["pending_review"] is False
    assert failed_data["observations"] == []
    assert "This synthetic file" not in failed.text

    pending = _upload_pdf(
        client,
        person_id,
        ["Report Date: 2026-09-01", "Glucose: 92 mg/dL"],
        filename="zero-candidate.pdf",
    )
    assert pending.status_code == 201
    pending_data = pending.json()
    intake_id = pending_data["id"]
    emptied = client.patch(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}",
        headers=csrf_headers(client),
        json={"observations": []},
    )
    assert emptied.status_code == 200
    assert emptied.json()["observations"] == []
    rejected = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}/confirm",
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 422
    assert "observation" in rejected.text.casefold()


def test_report_intake_owner_isolation_and_pending_command_protection(
    client: TestClient,
) -> None:
    assert register(client, email="intake-owner-a@example.com").status_code == 201
    person_a_id = _person_id(client)
    intake_a = _upload_pdf(client, person_a_id, ["Report Date: 2026-09-01", "Glucose: 92"])
    assert intake_a.status_code == 201
    intake_id = intake_a.json()["id"]

    logout = client.delete("/v1/sessions/current", headers=csrf_headers(client))
    assert logout.status_code == 204
    assert register(client, email="intake-owner-b@example.com").status_code == 201
    person_b_id = _person_id(client)

    assert client.get(f"/v1/persons/{person_a_id}/report-intakes").status_code == 404
    assert client.get(f"/v1/persons/{person_a_id}/report-intakes/{intake_id}").status_code == 404
    assert (
        client.patch(
            f"/v1/persons/{person_a_id}/report-intakes/{intake_id}",
            headers=csrf_headers(client),
            json={"observations": []},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/persons/{person_a_id}/report-intakes/{intake_id}/confirm",
            headers=csrf_headers(client),
        ).status_code
        == 404
    )

    own_upload = _upload_pdf(client, person_b_id, ["Report Date: 2026-09-01", "Glucose: 88"])
    assert own_upload.status_code == 201
    own_id = own_upload.json()["id"]
    missing_csrf = client.patch(
        f"/v1/persons/{person_b_id}/report-intakes/{own_id}",
        headers={"Origin": ORIGIN},
        json={"observations": []},
    )
    assert missing_csrf.status_code == 403


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_image_ocr_creates_candidates_with_engine_confidence(client: TestClient) -> None:
    assert register(client, email="ocr-owner@example.com").status_code == 201
    person_id = _person_id(client)
    response = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-ocr.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201
    intake = response.json()
    assert intake["status"] == "pending_review"
    assert intake["extraction_method"] == "ocr_image"
    glucose = next(item for item in intake["observations"] if item["code"] == "GLUCOSE")
    assert glucose["value_numeric"] == 92
    assert glucose["reference_range"] == "65-99"
    assert glucose["parser_confidence"] is not None
    assert 0 <= glucose["parser_confidence"] <= 1


def test_report_intake_requires_a_report_date_before_canonical_confirmation(
    client: TestClient,
) -> None:
    assert register(client, email="missing-date-owner@example.com").status_code == 201
    person_id = _person_id(client)
    response = _upload_pdf(client, person_id, ["Source: Synthetic Lab", "Glucose: 92 mg/dL"])
    assert response.status_code == 201
    intake_id = response.json()["id"]
    rejected = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake_id}/confirm",
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 422
    assert "report date" in rejected.text.casefold()
