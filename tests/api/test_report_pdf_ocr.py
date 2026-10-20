from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from conftest import csrf_headers, register
from fastapi.testclient import TestClient
from healthy.domain import report_intakes as reports
from healthy.domain import report_pdf_worker as worker
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from test_health_report_intakes import _pdf_bytes, _person_id


@pytest.fixture
def scanned_pdf() -> bytes:
    if importlib.util.find_spec("pypdfium2") is None or shutil.which("tesseract") is None:
        pytest.skip("Real scanned-PDF test requires the optional private PDFium/Tesseract tools")
    tessdata = Path(os.environ.get("HEALTHY_OCR_TESSDATA_DIR", "/opt/homebrew/share/tessdata"))
    if not (tessdata / "chi_tra.traineddata").is_file():
        pytest.skip("Real Chinese OCR test requires the chi_tra model")
    return _scanned_pdf(
        [
            # Separate the acronym clearly with either supported CJK font.
            "Source: Synthetic Local   OCR Lab",
            "Report Date: 2026-10-01",
            "血糖: 92 mg/dL (65-99)",
            "糖化血紅素: 5.8 %",
        ]
    )


def _scanned_pdf(lines: list[str]) -> bytes:
    fonts = (
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    font_path = next((path for path in fonts if path.exists()), None)
    if font_path is None:
        pytest.skip("Synthetic Chinese image fixture needs an installed Chinese font")
    image = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 60)
    for index, line in enumerate(lines):
        draw.text((60, 80 + index * 160), line, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PDF", resolution=150)
    image.close()
    return output.getvalue()


def test_real_chinese_scanned_pdf_requires_review_and_keeps_page_source(
    client: TestClient, scanned_pdf: bytes
) -> None:
    assert not PdfReader(BytesIO(scanned_pdf)).pages[0].extract_text()
    assert register(client).status_code == 201
    person_id = _person_id(client)
    upload = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-scan.pdf", scanned_pdf, "application/pdf")},
    )
    assert upload.status_code == 201
    intake = upload.json()
    assert intake["status"] == "pending_review", intake.get("error_message")
    assert intake["extraction_method"] == "ocr_pdf"
    assert intake["report_id"] is None
    assert intake["source_name"] == "Synthetic Local OCR Lab"
    glucose = next(item for item in intake["observations"] if item["code"] == "GLUCOSE")
    a1c = next(item for item in intake["observations"] if item["code"] == "HEMOGLOBIN_A1C")
    assert glucose["value_numeric"] == 92
    assert glucose["reference_range"] == "65-99"
    assert glucose["parser_provenance"] == "ocr_pdf:page-1:line-3"
    assert glucose["parser_confidence"] >= 0.6
    assert a1c["value_numeric"] == 5.8
    patch = client.patch(
        f"/v1/persons/{person_id}/report-intakes/{intake['id']}",
        headers=csrf_headers(client),
        json={
            "observations": [
                {
                    "id": glucose["id"],
                    "code": "GLUCOSE",
                    "display_name": glucose["display_name"],
                    "value_numeric": 93.25,
                    "unit": "mg/dL",
                    "reference_range": "65-99",
                    "observed_at": glucose["observed_at"],
                }
            ]
        },
    )
    assert patch.status_code == 200
    assert patch.json()["observations"][0]["parser_provenance"] == glucose["parser_provenance"]
    confirmed = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    report = client.get(f"/v1/persons/{person_id}/reports/{confirmed.json()['report_id']}").json()
    assert report["observations"][0]["value_numeric"] == 93.25
    duplicate = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-scan.pdf", scanned_pdf, "application/pdf")},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == intake["id"]


def test_real_mixed_pdf_keeps_digital_and_ocr_pages(scanned_pdf: bytes) -> None:
    writer = PdfWriter()
    writer.append(BytesIO(_pdf_bytes(["Glucose: 91 mg/dL"])))
    writer.append(BytesIO(scanned_pdf))
    output = BytesIO()
    writer.write(output)
    parsed = reports.extract_and_parse(
        filename="mixed-synthetic.pdf", media_type="application/pdf", file_bytes=output.getvalue()
    )
    assert parsed.extraction_method == "mixed_pdf"
    assert parsed.page_count == 2
    digital = next(item for item in parsed.observations if item.value_numeric == Decimal("91"))
    scanned = next(item for item in parsed.observations if item.value_numeric == Decimal("92"))
    assert digital.parser_provenance == "digital_pdf:page-1:line-1"
    assert digital.parser_confidence is None
    assert scanned.parser_provenance == "ocr_pdf:page-2:line-3"


def test_real_unreadable_scan_is_explicit_failure_not_a_partial_report(
    client: TestClient, scanned_pdf: bytes
) -> None:
    del scanned_pdf  # Ensures the real tool prerequisites have been checked.
    writer = PdfWriter()
    writer.append(BytesIO(_pdf_bytes(["Glucose: 91 mg/dL"])))
    writer.append(BytesIO(_scanned_pdf(["XYZZY: 123", "Unreadable synthetic noise"])))
    output = BytesIO()
    writer.write(output)
    assert register(client).status_code == 201
    person_id = _person_id(client)
    uploaded = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-noise.pdf", output.getvalue(), "application/pdf")},
    )
    assert uploaded.status_code == 201
    intake = uploaded.json()
    assert intake["status"] == "failed"
    assert intake["observations"] == []
    assert intake["report_id"] is None
    assert "page 2" in intake["error_message"]
    assert "No partial candidates" in intake["error_message"]
    confirm = client.post(
        f"/v1/persons/{person_id}/report-intakes/{intake['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirm.status_code == 409


def test_pdf_page_count_limit_is_checked_before_decoding() -> None:
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(612, 792)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(reports.ReportIntakeParserError, match="between 1 and 10 pages"):
        reports._extract_pdf_text(output.getvalue())


def test_corrupt_and_encrypted_pdfs_return_safe_errors() -> None:
    with pytest.raises(reports.ReportIntakeParserError, match="could not be read safely"):
        reports._extract_pdf_text(b"%PDF-1.4\nsynthetic-invalid")
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt("synthetic-only-password")
    output = BytesIO()
    writer.write(output)
    with pytest.raises(reports.ReportIntakeParserError, match="Encrypted PDF"):
        reports._extract_pdf_text(output.getvalue())


def test_extracted_text_budget_rejects_whole_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reports, "MAX_EXTRACTED_CHARACTERS", 10)
    with pytest.raises(reports.ReportIntakeParserError, match="too much extracted text"):
        worker.extract_pdf_pages(_pdf_bytes(["Glucose: 92 mg/dL"]))


def test_missing_pdf_renderer_has_actionable_local_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for language in ("eng", "chi_tra"):
        (tmp_path / f"{language}.traineddata").touch()
    monkeypatch.setenv("HEALTHY_OCR_TESSDATA_DIR", str(tmp_path))

    def unavailable(_name: str) -> None:
        raise ImportError("synthetic module failure")

    monkeypatch.setattr(worker.importlib, "import_module", unavailable)
    with pytest.raises(reports.ReportIntakeParserError, match="optional local pypdfium2"):
        worker._require_pdf_ocr_tools()


def test_ocr_timeout_does_not_expose_document_or_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic document text and tool internals")

    monkeypatch.setattr(reports.pytesseract, "image_to_data", timeout)
    with Image.new("RGB", (100, 100), "white") as image:
        with pytest.raises(reports.ReportIntakeParserError, match="8 second page limit") as error:
            reports._extract_pil_image_text(image)
    assert "document text" not in str(error.value)


def test_oversized_pdf_page_is_rejected_before_render(scanned_pdf: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_page(PdfReader(BytesIO(scanned_pdf)).pages[0])
    page.mediabox.upper_right = (20000, 20000)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(reports.ReportIntakeParserError, match="8 million pixel"):
        reports._extract_pdf_text(output.getvalue())


def test_total_render_pixel_budget(scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = PdfWriter()
    writer.append(BytesIO(scanned_pdf))
    writer.append(BytesIO(scanned_pdf))
    output = BytesIO()
    writer.write(output)
    monkeypatch.setattr(reports, "MAX_TOTAL_PDF_PIXELS", 2_000_000)
    monkeypatch.setattr(
        reports,
        "_extract_pil_image_text",
        lambda _image, **_kwargs: ("Glucose: 92", (Decimal("0.99"),)),
    )
    with pytest.raises(reports.ReportIntakeParserError, match="total OCR limit"):
        worker.extract_pdf_pages(output.getvalue())


def test_pdf_timeout_kills_worker_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []

    class StalledWorker:
        pid = 123456789
        returncode = 0
        attempts = 0

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired("synthetic-worker", 30)
            return b"", b""

    monkeypatch.setattr(reports.subprocess, "Popen", lambda *_args, **_kwargs: StalledWorker())
    monkeypatch.setattr(reports.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(reports.ReportIntakeParserError, match="30 second limit"):
        reports._extract_pdf_text(b"synthetic")
    assert calls == [(123456789, signal.SIGKILL)]


def test_missing_private_language_model_fails_safely(
    scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEALTHY_OCR_TESSDATA_DIR", str(tmp_path))
    with pytest.raises(reports.ReportIntakeParserError, match="eng and chi_tra"):
        reports._extract_pdf_text(scanned_pdf)


@pytest.mark.parametrize(
    ("text", "confidence"),
    [("XYZZY: 123", "0.99"), ("血糖: 92", "0.20"), ("Glucose: unreadable", "0.99")],
)
def test_ocr_gibberish_or_low_confidence_is_not_success(text: str, confidence: str) -> None:
    with pytest.raises(reports.ReportIntakeParserError, match="No candidate"):
        reports.parse_extracted_text(
            text=text,
            filename=None,
            extraction_method="ocr_pdf",
            page_count=1,
            line_confidences=(Decimal(confidence),),
        )


def test_ocr_chinese_character_spacing_retains_exact_alias() -> None:
    parsed = reports.parse_extracted_text(
        text="血 糖 : 92 mg/dL",
        filename=None,
        extraction_method="ocr_pdf",
        page_count=1,
        line_confidences=(Decimal("0.95"),),
    )
    assert parsed.observations[0].code == "GLUCOSE"


@pytest.mark.parametrize("method", ["digital_pdf", "ocr_pdf"])
def test_candidate_limit_rejects_overflow_instead_of_truncating(
    method: reports.ExtractionMethod,
) -> None:
    arguments = {
        "filename": None,
        "extraction_method": method,
        "page_count": 1,
        "line_confidences": (Decimal("0.99"),) * 101,
    }
    exactly_100 = reports.parse_extracted_text(text="\n".join(["Glucose: 92"] * 100), **arguments)
    assert len(exactly_100.observations) == 100
    with pytest.raises(reports.ReportIntakeParserError, match="No partial candidates"):
        reports.parse_extracted_text(text="\n".join(["Glucose: 92"] * 101), **arguments)
