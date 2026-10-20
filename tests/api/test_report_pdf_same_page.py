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
from test_health_report_intakes import _person_id
from test_report_intake_retry import row_counts
from test_report_pdf_fallback import extracted_payload, mixed_page
from test_report_pdf_form_images import form_pdf


def same_page_pdf(*, overlap=False, value=92):
    return mixed_page(
        ["Source: Synthetic Same Page Lab", "Report Date: 2026-10-01"]
        + (["Synthetic heading"] * 8 if overlap else [])
        + ["Glucose: 95 mg/dL"],
        [f"血糖: {value} mg/dL (65-99)"],
    )


@pytest.mark.parametrize("value", [92, 95])
def test_real_same_page_keeps_digital_and_image_rows_without_value_dedup(value):
    content = same_page_pdf(value=value)
    assert str(value) not in PdfReader(BytesIO(content)).pages[0].extract_text() or value == 95
    payload = worker.extract_pdf_pages(content)
    result = extracted_payload(payload)
    assert result.page_count == payload["page_count"] == 1
    assert result.extraction_method == "mixed_pdf"
    assert [o.value_numeric for o in result.observations] == [95, value]
    assert result.source_name == "Synthetic Same Page Lab"
    assert result.reported_at.date().isoformat() == "2026-10-01"
    assert result.observations[0].parser_provenance == "digital_pdf:page-1:line-3"
    assert result.observations[1].parser_provenance.startswith("ocr_pdf:page-1:image-region:")
    assert result.observations[1].parser_confidence >= parser.MIN_OCR_LINE_CONFIDENCE


def test_same_page_api_review_edit_confirm_retry_and_restart(client):
    assert register(client).status_code == 201
    person = _person_id(client)
    content = same_page_pdf()
    uploaded = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("same-page.pdf", content, "application/pdf")},
    )
    intake = uploaded.json()
    assert uploaded.status_code == 201
    assert intake["status"] == "pending_review", intake.get("error_message")
    assert [o["value_numeric"] for o in intake["observations"]] == [95, 92]
    assert client.get(f"/v1/persons/{person}/history").json() == []
    path = f"/v1/persons/{person}/report-intakes/{intake['id']}"
    updated = client.patch(
        path,
        headers=csrf_headers(client),
        json={
            "observations": [
                {
                    k: o[k]
                    for k in (
                        "id",
                        "code",
                        "display_name",
                        "unit",
                        "reference_range",
                        "observed_at",
                    )
                }
                | {"value_numeric": 93.25 if index else 95}
                for index, o in enumerate(intake["observations"])
            ]
        },
    )
    assert updated.status_code == 200
    confirmed = client.post(path + "/confirm", headers=csrf_headers(client))
    assert confirmed.status_code == 200
    report_id = confirmed.json()["report_id"]
    assert (
        client.post(path + "/confirm", headers=csrf_headers(client)).json()["report_id"]
        == report_id
    )
    duplicate = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("renamed.pdf", content, "application/pdf")},
    )
    assert duplicate.json()["id"] == intake["id"]
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        persisted = restarted.get(path).json()
        assert (
            persisted["observations"][1]["parser_provenance"]
            == intake["observations"][1]["parser_provenance"]
        )
        assert (
            persisted["observations"][1]["parser_confidence"]
            == intake["observations"][1]["parser_confidence"]
        )
        history = restarted.get(f"/v1/persons/{person}/history").json()
        assert sorted(Decimal(o["primary_value"]) for o in history) == [
            Decimal("93.25"),
            Decimal("95"),
        ]
        assert row_counts(restarted) == [1, 2, 1]


def test_overlap_fails_before_ocr_and_api_saves_no_partial_rows(monkeypatch, client):
    def unexpected(*a, **kw):
        pytest.fail("Overlapping text/image regions must never reach OCR")

    monkeypatch.setattr(parser, "_extract_pil_image_text", unexpected)
    content = same_page_pdf(overlap=True)
    with pytest.raises(parser.ReportIntakeParserError, match="cannot be separated safely"):
        worker.extract_pdf_pages(content)
    assert register(client).status_code == 201
    person = _person_id(client)
    uploaded = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("overlap.pdf", content, "application/pdf")},
    ).json()
    assert uploaded["status"] == "failed"
    assert "No partial candidates" in uploaded["error_message"]
    assert uploaded["observations"] == []
    assert row_counts(client) == [1, 0, 0]
    assert client.get(f"/v1/persons/{person}/history").json() == []


@pytest.mark.parametrize(
    "text,confidence", [("Glucose: 92 mg/dL", Decimal(".59")), ("Unknown: 92", Decimal(".99"))]
)
def test_unreliable_same_page_image_does_not_return_partial_digital_rows(
    monkeypatch, text, confidence
):
    monkeypatch.setattr(parser, "_extract_pil_image_text", lambda *a, **kw: (text, (confidence,)))
    with pytest.raises(parser.ReportIntakeParserError, match="No partial candidates"):
        worker.extract_pdf_pages(same_page_pdf())


def test_same_page_candidates_share_limits(monkeypatch):
    monkeypatch.setattr(parser, "MAX_CANDIDATE_COUNT", 1)
    with pytest.raises(parser.ReportIntakeParserError, match="candidate limit"):
        worker.extract_pdf_pages(same_page_pdf())


@pytest.mark.parametrize("layout", ["rotated", "offset", "form"])
def test_ambiguous_same_page_requires_manual_entry(layout):
    writer = PdfWriter()
    if layout == "form":
        writer.append(BytesIO(form_pdf()))
        writer.pages[1].merge_page(PdfReader(BytesIO(same_page_pdf())).pages[0])
    else:
        writer.append(BytesIO(same_page_pdf()))
        if layout == "rotated":
            writer.pages[0].rotate(90)
        else:
            writer.pages[0].cropbox.lower_left = (20, 20)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(parser.ReportIntakeParserError, match="cannot be separated safely"):
        worker.extract_pdf_pages(output.getvalue())


@pytest.mark.parametrize("limit", ["pixels", "characters", "objects"])
def test_same_page_supplement_has_bounded_work(monkeypatch, limit):
    content = same_page_pdf()
    if limit == "pixels":
        monkeypatch.setattr(parser, "MAX_TOTAL_PDF_PIXELS", 1)
        message = "total OCR limit"
    elif limit == "characters":
        original = PdfReader(BytesIO(content)).pages[0].extract_text().strip()
        monkeypatch.setattr(parser, "MAX_EXTRACTED_CHARACTERS", len(original) + 1)
        message = "too much extracted text"
    else:
        content = mixed_page(["Glucose: 95 mg/dL"] + ["Heading"] * 256, ["血糖: 92 mg/dL"])
        message = "cannot be separated safely"
    monkeypatch.setattr(
        parser, "_extract_pil_image_text", lambda *a, **kw: ("Glucose: 92 mg/dL", (Decimal(".9"),))
    )
    with pytest.raises(parser.ReportIntakeParserError, match=message):
        worker.extract_pdf_pages(content)
