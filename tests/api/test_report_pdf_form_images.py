from __future__ import annotations

from io import BytesIO

import pytest
from conftest import csrf_headers, register
from healthy.domain import report_intakes as parser
from healthy.domain import report_pdf_worker as worker
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)
from test_health_report_intakes import _pdf_bytes, _person_id
from test_report_pdf_fallback import extracted_payload
from test_report_pdf_ocr import _scanned_pdf


def form_pdf(depth: int = 2) -> bytes:
    """A real /Form -> /Form -> /Image graph painted on a text-bearing page."""
    writer = PdfWriter()
    writer.append(
        BytesIO(
            _pdf_bytes(
                [
                    "Source: Synthetic Form Lab",
                    "Report Date: 2026-10-01",
                    "Glucose: 95 mg/dL",
                ]
            )
        )
    )
    writer.append(BytesIO(_pdf_bytes(["Synthetic Form image page heading"])))
    scan = PdfReader(BytesIO(_scanned_pdf(["血糖: 92 mg/dL (65-99)"]))).pages[0]
    resources = scan["/Resources"]
    commands = scan.get_contents().get_data()
    for _ in range(depth):
        form = DecodedStreamObject()
        form.set_data(commands)
        form.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject(scan.mediabox),
                NameObject("/Resources"): resources,
            }
        )
        # Clone the image/resource references into this writer before replacing
        # the next Form's resource dictionary with the wrapper reference.
        reference = writer._add_object(form.clone(writer))
        resources = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/LabForm"): reference})}
        )
        commands = b"q /LabForm Do Q"
    page = writer.pages[1]
    page["/Resources"][NameObject("/XObject")] = resources["/XObject"]
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b"\n" + commands)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("depth", [1, 3])
def test_real_nested_form_keeps_both_measurements_and_page_sources(depth, tmp_path):
    content = form_pdf(depth)
    (tmp_path / "synthetic-nested-form.pdf").write_bytes(content)
    page = PdfReader(BytesIO(content)).pages[1]
    assert "92" not in page.extract_text()
    assert all(
        ref.get_object()["/Subtype"] == "/Form" for ref in page["/Resources"]["/XObject"].values()
    )
    parsed = extracted_payload(worker.extract_pdf_pages(content))
    assert parsed.extraction_method == "mixed_pdf"
    assert [o.value_numeric for o in parsed.observations] == [95, 92]
    assert parsed.source_name == "Synthetic Form Lab"
    assert parsed.reported_at.date().isoformat() == "2026-10-01"
    digital, scanned = parsed.observations
    assert digital.parser_provenance == "digital_pdf:page-1:line-3"
    assert digital.parser_confidence is None
    assert scanned.parser_provenance.startswith("ocr_pdf:page-2:")
    assert scanned.parser_confidence >= parser.MIN_OCR_LINE_CONFIDENCE


def test_api_nested_form_requires_confirmation_and_keeps_history_values(client):
    assert register(client).status_code == 201
    person = _person_id(client)
    response = client.post(
        f"/v1/persons/{person}/report-intakes",
        headers=csrf_headers(client),
        files={"file": ("synthetic-form.pdf", form_pdf(), "application/pdf")},
    )
    assert response.status_code == 201
    intake = response.json()
    assert intake["status"] == "pending_review", intake.get("error_message")
    assert intake["report_id"] is None
    assert [o["value_numeric"] for o in intake["observations"]] == [95, 92]
    assert client.get(f"/v1/persons/{person}/history").json() == []
    confirmed = client.post(
        f"/v1/persons/{person}/report-intakes/{intake['id']}/confirm", headers=csrf_headers(client)
    )
    assert confirmed.status_code == 200
    report = client.get(f"/v1/persons/{person}/reports/{confirmed.json()['report_id']}").json()
    assert [o["value_numeric"] for o in report["observations"]] == [95, 92]
    history = client.get(f"/v1/persons/{person}/history").json()
    assert sorted(float(item["primary_value"]) for item in history) == [92, 95]


def resource_graph(depth: int = 1, *, cycle: bool = False, breadth: int = 1):
    """Serialized resource graph: the selector must never decode Form streams."""
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    root = DictionaryObject({NameObject("/XObject"): DictionaryObject()})
    page[NameObject("/Resources")] = root
    current = root
    for index in range(depth):
        child = DictionaryObject({NameObject("/XObject"): DictionaryObject()})
        for sibling in range(breadth):
            form = DecodedStreamObject()
            form.set_data(b"q Q")
            form.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Form"),
                    NameObject("/BBox"): ArrayObject(
                        [NumberObject(0), NumberObject(0), NumberObject(612), NumberObject(792)]
                    ),
                    NameObject("/Resources"): child,
                }
            )
            current["/XObject"][NameObject(f"/Form{index}_{sibling}")] = writer._add_object(form)
        current = child
    if cycle:
        current[NameObject("/XObject")] = root["/XObject"]
    output = BytesIO()
    writer.write(output)
    return PdfReader(BytesIO(output.getvalue())).pages[0]


def test_form_without_images_and_shared_resources_do_not_trigger_ocr():
    assert not worker._has_raster_image(resource_graph(depth=3, breadth=2))


@pytest.mark.parametrize(
    "shape,message",
    [
        ({"depth": 2, "cycle": True}, "Cyclic"),
        ({"depth": 10}, "depth"),
        ({"depth": 3, "breadth": 100}, "inspection limit"),
    ],
)
def test_form_resource_cycles_depth_and_total_budget_reject_safely(shape, message):
    with pytest.raises(parser.ReportIntakeParserError, match=message):
        worker._has_raster_image(resource_graph(**shape))
