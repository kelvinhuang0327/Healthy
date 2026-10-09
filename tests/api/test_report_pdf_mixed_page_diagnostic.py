"""Partially recognized documents remain outside zero-candidate fallback scope."""

import os
from io import BytesIO
from pathlib import Path

import pytest
from healthy.domain import report_intakes as parser
from healthy.domain import report_pdf_worker as worker
from pypdf import PdfReader, PdfWriter
from test_health_report_intakes import _pdf_bytes
from test_report_pdf_ocr import _scanned_pdf


def test_partially_recognized_document_does_not_supplement_the_hidden_image(monkeypatch):
    writer = PdfWriter()
    writer.append(BytesIO(_pdf_bytes(["Synthetic digital report heading"])))
    scan = PdfReader(BytesIO(_scanned_pdf(["血糖: 92 mg/dL (65-99)"])))
    writer.pages[0].merge_page(scan.pages[0])
    writer.append(BytesIO(_pdf_bytes(["Glucose: 95 mg/dL"])))
    output = BytesIO()
    writer.write(output)
    content = output.getvalue()
    assert len(PdfReader(BytesIO(content)).pages) == 2
    if target := os.environ.get("HEALTHY_SYNTHETIC_PDF_DIAGNOSTIC_OUT"):
        Path(target).write_bytes(content)

    def unexpected_ocr():
        pytest.fail("Current worker must select digital extraction for this text-bearing page.")

    monkeypatch.setattr(worker, "_require_pdf_ocr_tools", unexpected_ocr)
    extracted = worker.extract_pdf_pages(content)
    assert extracted["extraction_method"] == "digital_pdf"
    assert "Synthetic digital report heading" in extracted["text"]
    assert "92" not in extracted["text"]
    parsed = parser.parse_extracted_text(
        text=extracted["text"],
        filename="synthetic-mixed-page.pdf",
        extraction_method="digital_pdf",
        page_count=2,
    )
    assert len(parsed.observations) == 1
    assert parsed.observations[0].value_numeric == 95
