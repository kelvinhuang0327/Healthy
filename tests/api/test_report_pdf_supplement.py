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
from test_report_pdf_fallback import extracted_payload, mixed_page
from test_report_pdf_ocr import _scanned_pdf


def cross_page_pdf(scanned_lines: list[str], digital_lines: list[str] | None = None) -> bytes:
    writer = PdfWriter()
    writer.append(
        BytesIO(
            _pdf_bytes(
                digital_lines
                or [
                    "Source: Synthetic Cross Page Lab",
                    "Report Date: 2026-10-01",
                    "Glucose: 95 mg/dL",
                ]
            )
        )
    )
    writer.append(BytesIO(mixed_page(["Synthetic image page heading"], scanned_lines)))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_cross_page_draft_confirm_history_and_restart_preserve_both_measurements(client):
    assert register(client).status_code == 201
    person = _person_id(client)
    content = cross_page_pdf(["血糖: 92 mg/dL (65-99)"])
    response = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-cross-page.pdf", content, "application/pdf")},
    )
    assert response.status_code == 201
    intake = response.json()
    assert intake["status"] == "pending_review", intake.get("error_message")
    assert intake["source_name"] == "Synthetic Cross Page Lab"
    assert intake["reported_at"] == "2026-10-01T00:00:00Z"
    assert intake["extraction_method"] == "mixed_pdf"
    assert [o["value_numeric"] for o in intake["observations"]] == [95, 92]
    digital, scanned = intake["observations"]
    assert digital["parser_provenance"] == "digital_pdf:page-1:line-3"
    assert digital["parser_confidence"] is None
    assert scanned["parser_provenance"].startswith("ocr_pdf:page-2:")
    assert scanned["parser_confidence"] >= 0.6
    assert "No confirmed lab report" in client.get(f"/v1/persons/{person}/summary-export").text
    path = f"/v1/persons/{person}/report-intakes/{intake['id']}"
    patched = client.patch(
        path,
        headers=csrf_headers(client),
        json={
            "observations": [
                {
                    "id": o["id"],
                    "code": o["code"],
                    "display_name": o["display_name"],
                    "value_numeric": 93.25 if o["id"] == scanned["id"] else o["value_numeric"],
                    "unit": o["unit"],
                    "reference_range": o["reference_range"],
                    "observed_at": o["observed_at"],
                }
                for o in intake["observations"]
            ]
        },
    )
    assert patched.status_code == 200
    assert patched.json()["observations"][1]["parser_provenance"] == scanned["parser_provenance"]
    assert patched.json()["observations"][1]["parser_confidence"] == scanned["parser_confidence"]
    confirmed = client.post(path + "/confirm", headers=csrf_headers(client))
    assert confirmed.status_code == 200
    report_id = confirmed.json()["report_id"]
    assert (
        client.post(path + "/confirm", headers=csrf_headers(client)).json()["report_id"]
        == report_id
    )
    repeated = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("same-file.pdf", content, "application/pdf")},
    )
    assert repeated.json()["id"] == intake["id"]
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        persisted = restarted.get(path).json()
        assert [o["parser_provenance"] for o in persisted["observations"]] == [
            digital["parser_provenance"],
            scanned["parser_provenance"],
        ]
        assert persisted["observations"][1]["parser_confidence"] == scanned["parser_confidence"]
        report = restarted.get(f"/v1/persons/{person}/reports/{report_id}").json()
        assert [o["value_numeric"] for o in report["observations"]] == [95, 93.25]
        history = restarted.get(f"/v1/persons/{person}/history").json()
        assert sorted(Decimal(o["primary_value"]) for o in history) == [
            Decimal("93.25"),
            Decimal("95"),
        ]
        summary = restarted.get(f"/v1/persons/{person}/summary-export").text
        assert "95.0000" in summary and "93.2500" in summary
        assert row_counts(restarted) == [1, 2, 1]


def test_repeated_codes_and_identical_values_keep_distinct_rows_and_sources(monkeypatch, client):
    monkeypatch.setattr(
        parser,
        "_extract_pil_image_text",
        lambda *a, **kw: (
            "Glucose: 95 mg/dL\nGlucose: 95 mg/dL\nGlucose: 93 mg/dL",
            (Decimal(".91"), Decimal(".86"), Decimal(".95")),
        ),
    )
    content = cross_page_pdf(["合成影像"])
    result = extracted_payload(worker.extract_pdf_pages(content))
    assert [o.value_numeric for o in result.observations] == [95, 95, 95, 93]
    assert [o.parser_provenance for o in result.observations] == [
        "digital_pdf:page-1:line-3",
        "ocr_pdf:page-2:line-1",
        "ocr_pdf:page-2:line-2",
        "ocr_pdf:page-2:line-3",
    ]
    assert [o.parser_confidence for o in result.observations] == [
        None,
        Decimal(".91"),
        Decimal(".86"),
        Decimal(".95"),
    ]
    monkeypatch.setattr(parser, "extract_and_parse", lambda **kw: result)
    assert register(client).status_code == 201
    person = _person_id(client)
    intake = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-repeats.pdf", content, "application/pdf")},
    ).json()
    confirmed = client.post(
        f"/v1/persons/{person}/report-intakes/{intake['id']}/confirm", headers=csrf_headers(client)
    )
    assert confirmed.status_code == 200
    report = client.get(f"/v1/persons/{person}/reports/{confirmed.json()['report_id']}").json()
    assert [o["value_numeric"] for o in report["observations"]] == [95, 95, 95, 93]
    assert len(client.get(f"/v1/persons/{person}/history").json()) == 4


def test_original_metadata_is_not_replaced_by_supplement(monkeypatch):
    monkeypatch.setattr(
        parser,
        "_extract_pil_image_text",
        lambda *a, **kw: (
            "Source: Wrong OCR Lab\nReport Date: 2026-10-02\nGlucose: 92 mg/dL",
            (Decimal(".9"),) * 3,
        ),
    )
    result = extracted_payload(worker.extract_pdf_pages(cross_page_pdf(["合成影像"])))
    assert result.source_name == "Synthetic Cross Page Lab"
    assert result.reported_at.date().isoformat() == "2026-10-01"
    assert all(o.observed_at == result.reported_at for o in result.observations)


def test_pure_digital_cover_and_same_page_candidate_never_trigger_supplement(monkeypatch):
    def unexpected():
        pytest.fail("Existing digital candidate pages must not be re-OCRed")

    monkeypatch.setattr(worker, "_require_pdf_ocr_tools", unexpected)
    writer = PdfWriter()
    writer.append(BytesIO(_pdf_bytes(["Synthetic cover without images"])))
    writer.append(BytesIO(_pdf_bytes(["Glucose: 95 mg/dL"])))
    output = BytesIO()
    writer.write(output)
    result = extracted_payload(worker.extract_pdf_pages(output.getvalue()))
    assert result.extraction_method == "digital_pdf"
    assert [o.value_numeric for o in result.observations] == [95]
    same_page = mixed_page(["Glucose: 95 mg/dL"], ["血糖: 92 mg/dL"])
    result = extracted_payload(worker.extract_pdf_pages(same_page))
    assert result.extraction_method == "digital_pdf"
    assert [o.value_numeric for o in result.observations] == [95]


@pytest.mark.parametrize(
    "text,confidence",
    [
        ("Glucose: 92 mg/dL", (Decimal(".59"),)),
        ("Glucose: 92 mg/dL", (None,)),
        ("Unknown lab: 92 mg/dL", (Decimal(".99"),)),
        ("Glucose: unreadable mg/dL", (Decimal(".99"),)),
    ],
)
def test_unreliable_image_page_rejects_partial_digital_results(monkeypatch, text, confidence):
    monkeypatch.setattr(parser, "_extract_pil_image_text", lambda *a, **kw: (text, confidence))
    with pytest.raises(parser.ReportIntakeParserError, match="No partial candidates"):
        worker.extract_pdf_pages(cross_page_pdf(["合成影像"]))


def test_real_unreliable_supplement_is_failed_intake_with_no_partial_history(client):
    assert register(client).status_code == 201
    person = _person_id(client)
    response = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={
            "file": (
                "noise.pdf",
                cross_page_pdf(["XYZZY: 123", "Synthetic noise"]),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201
    intake = response.json()
    assert intake["status"] == "failed"
    assert (
        "page 2" in intake["error_message"] and "No partial candidates" in intake["error_message"]
    )
    assert intake["observations"] == [] and intake["report_id"] is None
    assert row_counts(client) == [1, 0, 0]
    assert (
        client.post(
            f"/v1/persons/{person}/report-intakes/{intake['id']}/confirm",
            headers=csrf_headers(client),
        ).status_code
        == 409
    )
    assert "No confirmed lab report" in client.get(f"/v1/persons/{person}/summary-export").text


def test_processing_and_combined_output_share_the_character_limit(monkeypatch):
    content = cross_page_pdf(["合成影像"], ["Glucose: 95 mg/dL"])
    texts = [p.extract_text().strip() for p in PdfReader(BytesIO(content)).pages]
    ocr = "Glucose: 92 mg/dL"
    monkeypatch.setattr(parser, "_extract_pil_image_text", lambda *a, **kw: (ocr, (Decimal(".9"),)))
    for budget in [sum(map(len, texts)) + len(ocr), sum(map(len, texts)) + len(ocr) + 2]:
        monkeypatch.setattr(parser, "MAX_EXTRACTED_CHARACTERS", budget)
        with pytest.raises(parser.ReportIntakeParserError, match="too much extracted text"):
            worker.extract_pdf_pages(content)


def test_first_pass_ocr_and_supplement_share_total_pixel_budget(monkeypatch):
    writer = PdfWriter()
    writer.append(BytesIO(_scanned_pdf(["血糖: 92 mg/dL"])))
    writer.append(BytesIO(cross_page_pdf(["血糖: 93 mg/dL"])))
    output = BytesIO()
    writer.write(output)
    calls = []

    def ocr(*a, **kw):
        calls.append(1)
        return "Glucose: 92 mg/dL", (Decimal(".9"),)

    monkeypatch.setattr(parser, "_extract_pil_image_text", ocr)
    monkeypatch.setattr(parser, "MAX_TOTAL_PDF_PIXELS", 3_000_000)
    with pytest.raises(parser.ReportIntakeParserError, match="total OCR limit"):
        worker.extract_pdf_pages(output.getvalue())
    assert len(calls) == 1


def test_merged_candidate_limit_is_not_a_per_page_limit(monkeypatch):
    monkeypatch.setattr(parser, "MAX_CANDIDATE_COUNT", 1)
    monkeypatch.setattr(
        parser, "_extract_pil_image_text", lambda *a, **kw: ("Glucose: 92 mg/dL", (Decimal(".9"),))
    )
    with pytest.raises(parser.ReportIntakeParserError, match="candidate limit"):
        worker.extract_pdf_pages(cross_page_pdf(["合成影像"]))


def test_oversized_supplement_is_rejected_before_ocr(monkeypatch):
    writer = PdfWriter()
    writer.append(BytesIO(cross_page_pdf(["合成影像"])))
    writer.pages[1].mediabox.upper_right = (20000, 20000)
    output = BytesIO()
    writer.write(output)

    def unexpected(*a, **kw):
        pytest.fail("Oversized supplement must not be rendered/OCRed")

    monkeypatch.setattr(parser, "_extract_pil_image_text", unexpected)
    with pytest.raises(parser.ReportIntakeParserError, match="8 million pixel"):
        worker.extract_pdf_pages(output.getvalue())
