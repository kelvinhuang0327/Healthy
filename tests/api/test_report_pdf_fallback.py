from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from conftest import ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.domain import report_intakes as parser
from healthy.domain import report_pdf_worker as worker
from healthy.main import create_app
from pypdf import PdfReader, PdfWriter
from test_health_report_intakes import _pdf_bytes, _person_id
from test_report_intake_retry import row_counts
from test_report_pdf_ocr import _scanned_pdf


def mixed_page(headers: list[str], scanned_lines: list[str]) -> bytes:
    writer = PdfWriter()
    writer.append(BytesIO(_pdf_bytes(headers)))
    image_page = PdfReader(BytesIO(_scanned_pdf(scanned_lines)))
    writer.pages[0].merge_page(image_page.pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_title_and_chinese_image_table_is_a_reviewable_draft(client):
    assert register(client).status_code == 201
    person_id = _person_id(client)
    content = mixed_page(["Synthetic digital heading"], ["血糖: 92 mg/dL (65-99)"])
    assert "92" not in PdfReader(BytesIO(content)).pages[0].extract_text()
    uploaded = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-mixed-page.pdf", content, "application/pdf")},
    )
    assert uploaded.status_code == 201
    intake = uploaded.json()
    assert intake["status"] == "pending_review", intake.get("error_message")
    assert intake["report_id"] is None
    assert intake["reported_at"] is None
    assert len(intake["observations"]) == 1
    observation = intake["observations"][0]
    assert observation["code"] == "GLUCOSE"
    assert observation["value_numeric"] == 92
    assert observation["unit"] == "mg/dL"
    assert observation["parser_provenance"].startswith("ocr_pdf:page-1:")
    assert observation["parser_confidence"] >= float(parser.MIN_OCR_LINE_CONFIDENCE)
    path = f"/v1/persons/{person_id}/report-intakes/{intake['id']}"
    assert "No confirmed lab report" in client.get(f"/v1/persons/{person_id}/summary-export").text
    assert client.post(path + "/confirm", headers=csrf_headers(client)).status_code == 422
    corrected = client.patch(
        path,
        headers=csrf_headers(client),
        json={
            "source_name": "Synthetic Reviewed Lab",
            "reported_at": "2026-10-01T00:00:00Z",
            "observations": [
                {
                    "id": observation["id"],
                    "code": "GLUCOSE",
                    "display_name": observation["display_name"],
                    "value_numeric": 93.25,
                    "unit": "mg/dL",
                    "reference_range": "65-99",
                    "observed_at": "2026-10-01T00:00:00Z",
                }
            ],
        },
    )
    assert corrected.status_code == 200
    confirmed = client.post(path + "/confirm", headers=csrf_headers(client))
    assert confirmed.status_code == 200
    report_id = confirmed.json()["report_id"]
    assert (
        client.post(path + "/confirm", headers=csrf_headers(client)).json()["report_id"]
        == report_id
    )
    duplicate = client.post(
        f"/v1/persons/{person_id}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("renamed.pdf", content, "application/pdf")},
    )
    assert duplicate.status_code == 200 and duplicate.json()["id"] == intake["id"]
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        report = restarted.get(f"/v1/persons/{person_id}/reports/{report_id}").json()
        assert report["observations"][0]["value_numeric"] == 93.25
        summary = restarted.get(f"/v1/persons/{person_id}/summary-export").text
        assert "93.2500" in summary and "65-99" in summary and "Synthetic Reviewed Lab" in summary
        assert row_counts(restarted) == [1, 1, 1]


def extracted_payload(payload):
    return parser.parse_extracted_text(
        text=payload["text"],
        filename=None,
        extraction_method=payload["extraction_method"],
        page_count=payload["page_count"],
        line_provenances=tuple(payload["line_provenances"]),
        line_confidences=tuple(
            Decimal(value) if value is not None else None for value in payload["line_confidences"]
        ),
        metadata_text=payload["metadata_text"],
    )


@pytest.mark.parametrize(
    "headers,source,date",
    [
        (
            ["Source: Original Synthetic Lab", "Report Date: 2026-09-01"],
            "Original Synthetic Lab",
            "2026-09-01",
        ),
        (["Synthetic heading"], "health-report", None),
    ],
)
def test_fallback_preserves_only_original_metadata(monkeypatch, headers, source, date):
    monkeypatch.setattr(
        parser,
        "_extract_pil_image_text",
        lambda *a, **kw: (
            "Source: OCR Other Lab\nReport Date: 2026-09-02\nGlucose: 92 mg/dL",
            (Decimal(".9"),) * 3,
        ),
    )
    result = extracted_payload(worker.extract_pdf_pages(_pdf_bytes(headers)))
    assert result.source_name == source
    assert (result.reported_at.date().isoformat() if result.reported_at else None) == date
    assert result.observations[0].parser_provenance == "ocr_pdf:page-1:line-3"
    assert result.extraction_method == "mixed_pdf"


def test_digital_success_and_candidate_overflow_never_enter_fallback(monkeypatch):
    def unexpected():
        pytest.fail("Digital candidates must not trigger OCR")

    monkeypatch.setattr(worker, "_require_pdf_ocr_tools", unexpected)
    result = extracted_payload(worker.extract_pdf_pages(_pdf_bytes(["Glucose: 92 mg/dL"])))
    assert result.extraction_method == "digital_pdf"
    assert result.observations[0].parser_confidence is None
    monkeypatch.setattr(parser, "MAX_CANDIDATE_COUNT", 1)
    with pytest.raises(parser.ReportIntakeParserError, match="candidate limit"):
        worker.extract_pdf_pages(_pdf_bytes(["Glucose: 92 mg/dL", "Glucose: 93 mg/dL"]))


@pytest.mark.parametrize(
    "text,confidence",
    [
        ("Glucose: 92 mg/dL", (Decimal(".59"),)),
        ("Glucose: 92 mg/dL", (None,)),
        ("Unknown lab: 92 mg/dL", (Decimal(".99"),)),
        ("Glucose: unreadable mg/dL", (Decimal(".99"),)),
    ],
)
def test_unreliable_fallback_rejects_whole_document(monkeypatch, text, confidence):
    monkeypatch.setattr(parser, "_extract_pil_image_text", lambda *a, **kw: (text, confidence))
    with pytest.raises(parser.ReportIntakeParserError, match="No partial candidates"):
        worker.extract_pdf_pages(_pdf_bytes(["Synthetic heading"]))


def test_both_passes_share_character_budget(monkeypatch):
    monkeypatch.setattr(parser, "MAX_EXTRACTED_CHARACTERS", 40)
    monkeypatch.setattr(
        parser, "_extract_pil_image_text", lambda *a, **kw: ("Glucose: 92 mg/dL", (Decimal(".9"),))
    )
    with pytest.raises(parser.ReportIntakeParserError, match="too much extracted text"):
        worker.extract_pdf_pages(_pdf_bytes(["Synthetic heading without lab"]))


def test_fallback_total_pixel_budget_and_no_duplicate_extraction(monkeypatch):
    calls = []

    def ocr(*a, **kw):
        calls.append(1)
        return "Glucose: 92 mg/dL\nGlucose: 93 mg/dL", (Decimal(".9"),) * 2

    monkeypatch.setattr(parser, "_extract_pil_image_text", ocr)
    content = _pdf_bytes(["Synthetic heading"])
    result = extracted_payload(worker.extract_pdf_pages(content))
    assert len(calls) == 1
    assert [o.value_numeric for o in result.observations] == [Decimal("92"), Decimal("93")]
    writer = PdfWriter()
    writer.append(BytesIO(content))
    writer.append(BytesIO(content))
    output = BytesIO()
    writer.write(output)
    calls.clear()
    monkeypatch.setattr(parser, "MAX_TOTAL_PDF_PIXELS", 2_500_000)
    with pytest.raises(parser.ReportIntakeParserError, match="total OCR limit"):
        worker.extract_pdf_pages(output.getvalue())
    assert len(calls) == 1
