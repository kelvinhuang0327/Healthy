"""Synthetic reproducer for a text-bearing image page beside digital results."""

import os
from io import BytesIO
from pathlib import Path

from healthy.domain import report_pdf_worker as worker
from pypdf import PdfReader, PdfWriter
from test_health_report_intakes import _pdf_bytes
from test_report_pdf_fallback import extracted_payload
from test_report_pdf_ocr import _scanned_pdf


def test_cross_page_supplement_recovers_the_hidden_image():
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

    extracted = worker.extract_pdf_pages(content)
    assert extracted["extraction_method"] == "mixed_pdf"
    parsed = extracted_payload(extracted)
    assert [o.value_numeric for o in parsed.observations] == [92, 95]
    assert parsed.observations[0].parser_provenance.startswith("ocr_pdf:page-1:")
    assert parsed.observations[0].parser_confidence >= 0.6
    assert parsed.observations[1].parser_provenance == "digital_pdf:page-2:line-1"
