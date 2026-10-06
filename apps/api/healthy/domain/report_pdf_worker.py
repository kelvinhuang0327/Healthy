"""Local, disposable PDF decoder/OCR worker; communicates only through pipes."""

from __future__ import annotations

import importlib
import io
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytesseract  # type: ignore[import-untyped]
from pypdf import PdfReader

from healthy.domain import report_intakes as reports


def _require_pdf_ocr_tools() -> ModuleType:
    directory = os.environ.get("HEALTHY_OCR_TESSDATA_DIR")
    if directory:
        languages = {path.stem for path in Path(directory).glob("*.traineddata")}
    else:
        try:
            languages = set(pytesseract.get_languages())
        except Exception as error:
            raise reports.ReportIntakeParserError("Local Tesseract OCR is unavailable.") from error
    if not {"eng", "chi_tra"}.issubset(languages):
        raise reports.ReportIntakeParserError(
            "Scanned PDF OCR requires local eng and chi_tra Tesseract models. "
            "Configure HEALTHY_OCR_TESSDATA_DIR or enter the results manually."
        )
    try:
        return importlib.import_module("pypdfium2")
    except ImportError as error:
        raise reports.ReportIntakeParserError(
            "Scanned PDF OCR requires the optional local pypdfium2 tool. "
            "Install the report-ocr extra in a private environment or enter results manually."
        ) from error


def extract_pdf_pages(file_bytes: bytes) -> dict[str, object]:
    try:
        reader = PdfReader(io.BytesIO(file_bytes), strict=False)
        if reader.is_encrypted:
            raise reports.ReportIntakeParserError("Encrypted PDF reports are not supported.")
        page_count = len(reader.pages)
    except reports.ReportIntakeParserError:
        raise
    except Exception as error:
        raise reports.ReportIntakeParserError("The PDF could not be read safely.") from error
    if not 1 <= page_count <= reports.MAX_REPORT_PAGES:
        raise reports.ReportIntakeParserError("PDF reports must contain between 1 and 10 pages.")

    all_lines: list[str] = []
    provenances: list[str] = []
    confidences: list[str | None] = []
    methods: set[str] = set()
    characters = 0
    total_pixels = 0
    document = None
    try:
        for page_index, pdf_page in enumerate(reader.pages):
            text = (pdf_page.extract_text() or "").strip()
            method = "digital_pdf"
            page_confidences: tuple[Decimal | None, ...] = ()
            if not text:
                method = "ocr_pdf"
                if document is None:
                    tools = _require_pdf_ocr_tools()
                    document = tools.PdfDocument(file_bytes)
                page = document[page_index]
                bitmap = None
                image = None
                try:
                    width, height = page.get_size()
                    scale = reports.OCR_RENDER_DPI / 72
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise reports.ReportIntakeParserError(
                            "The PDF page dimensions are invalid."
                        )
                    pixels = math.ceil(width * scale) * math.ceil(height * scale)
                    if pixels > reports.MAX_PAGE_PIXELS:
                        raise reports.ReportIntakeParserError(
                            f"PDF page {page_index + 1} exceeds the 8 million pixel render limit."
                        )
                    total_pixels += pixels
                    if total_pixels > reports.MAX_TOTAL_PDF_PIXELS:
                        raise reports.ReportIntakeParserError(
                            "The PDF exceeds the 40 million pixel total OCR limit."
                        )
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil()
                    if image.width * image.height > reports.MAX_PAGE_PIXELS:
                        raise reports.ReportIntakeParserError("The rendered PDF page is too large.")
                    text, page_confidences = reports._extract_pil_image_text(
                        image, language="chi_tra+eng"
                    )
                finally:
                    if image is not None:
                        image.close()
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
                try:
                    reports.parse_extracted_text(
                        text=text,
                        filename=None,
                        extraction_method="ocr_pdf",
                        page_count=1,
                        line_confidences=page_confidences,
                    )
                except reports.ReportIntakeParserError as error:
                    raise reports.ReportIntakeParserError(
                        f"Scanned PDF page {page_index + 1} could not be parsed reliably. "
                        "No partial candidates were saved; enter results manually."
                    ) from error
            characters += len(text)
            if characters > reports.MAX_EXTRACTED_CHARACTERS:
                raise reports.ReportIntakeParserError("The PDF contains too much extracted text.")
            page_lines = text.replace("\r\n", "\n").split("\n")
            all_lines.extend(page_lines)
            methods.add(method)
            for line_index in range(len(page_lines)):
                provenances.append(f"{method}:page-{page_index + 1}:line-{line_index + 1}")
                confidence = (
                    page_confidences[line_index] if line_index < len(page_confidences) else None
                )
                confidences.append(str(confidence) if confidence is not None else None)
    except reports.ReportIntakeParserError:
        raise
    except Exception as error:
        raise reports.ReportIntakeParserError("The PDF could not be processed safely.") from error
    finally:
        if document is not None:
            document.close()
    method_summary = next(iter(methods)) if len(methods) == 1 else "mixed_pdf"
    return {
        "text": "\n".join(all_lines),
        "page_count": page_count,
        "extraction_method": method_summary,
        "line_provenances": provenances,
        "line_confidences": confidences,
    }


def main() -> int:
    try:
        content = sys.stdin.buffer.read(reports.MAX_UPLOAD_BYTES + 1)
        if len(content) > reports.MAX_UPLOAD_BYTES:
            raise reports.ReportIntakeParserError("The PDF exceeds the 10 MB upload limit.")
        result = extract_pdf_pages(content)
    except reports.ReportIntakeParserError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except Exception:
        print(json.dumps({"error": "The PDF could not be processed safely."}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
