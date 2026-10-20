"""Local, disposable PDF decoder/OCR worker; communicates only through pipes."""

from __future__ import annotations

import importlib
import io
import json
import math
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytesseract  # type: ignore[import-untyped]
from pypdf import PageObject, PdfReader
from pypdf.generic import DictionaryObject, PdfObject

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


@dataclass(frozen=True, slots=True)
class _PageText:
    page_index: int
    text: str
    method: reports.ExtractionMethod
    confidences: tuple[Decimal | None, ...] = ()
    image_region: bool = False


def _compose(pages: list[_PageText], metadata_text: str | None = None) -> reports.ExtractedPDF:
    lines: list[str] = []
    provenances: list[str] = []
    confidences: list[Decimal | None] = []
    for page in pages:
        page_lines = page.text.replace("\r\n", "\n").split("\n")
        lines.extend(page_lines)
        for index in range(len(page_lines)):
            region = ":image-region" if page.image_region else ""
            provenances.append(f"{page.method}:page-{page.page_index + 1}{region}:line-{index + 1}")
            confidences.append(page.confidences[index] if index < len(page.confidences) else None)
    method = (
        pages[0].method
        if metadata_text is None and all(page.method == pages[0].method for page in pages)
        else "mixed_pdf"
    )
    return reports.ExtractedPDF(
        text="\n".join(lines),
        page_count=len({page.page_index for page in pages}),
        extraction_method=method,
        line_provenances=tuple(provenances),
        line_confidences=tuple(confidences),
        metadata_text=metadata_text,
    )


def _has_raster_image(page: PageObject) -> bool:
    # Inspect dictionaries only; PageObject.images would decode image streams
    # before the existing bounded native rendering path. Forms may reference
    # other Forms, so bound the whole graph and distinguish sharing from cycles.
    resources = page.get("/Resources")
    if resources is None:
        return False
    pending: list[tuple[PdfObject, int, tuple[int, ...]]] = [(resources, 0, ())]
    visited: set[int] = set()
    inspected = 0
    while pending:
        reference, depth, ancestors = pending.pop()
        resolved = reference.get_object()
        if not isinstance(resolved, DictionaryObject):
            raise reports.ReportIntakeParserError("Invalid PDF image resources.")
        identity = id(resolved)
        if identity in ancestors:
            raise reports.ReportIntakeParserError(
                "Cyclic PDF Form resources cannot be inspected safely. "
                "No partial candidates were saved; enter results manually."
            )
        if depth > 8:
            raise reports.ReportIntakeParserError(
                "PDF Form nesting exceeds the depth inspection limit. "
                "No partial candidates were saved; enter results manually."
            )
        if identity in visited:
            continue
        visited.add(identity)
        objects = resolved.get("/XObject")
        if objects is None:
            continue
        objects = objects.get_object()
        if not isinstance(objects, DictionaryObject):
            raise reports.ReportIntakeParserError("Invalid PDF image resources.")
        inspected += len(objects)
        if inspected > 256:
            raise reports.ReportIntakeParserError(
                "PDF image resources exceed the supplement inspection limit. "
                "No partial candidates were saved; enter results manually."
            )
        for entry in objects.values():
            obj = entry.get_object()
            if not isinstance(obj, DictionaryObject):
                raise reports.ReportIntakeParserError("Invalid PDF image resources.")
            if obj.get("/Subtype") == "/Image":
                return True
            if obj.get("/Subtype") == "/Form":
                nested = obj.get("/Resources")
                if nested is not None:
                    pending.append((nested, depth + 1, (*ancestors, identity)))
    return False


def _parse_pdf(extracted: reports.ExtractedPDF) -> reports.ParsedReportIntake:
    return reports.parse_extracted_text(
        text=extracted.text,
        filename=None,
        extraction_method=extracted.extraction_method,
        page_count=extracted.page_count,
        line_provenances=extracted.line_provenances,
        line_confidences=extracted.line_confidences,
        metadata_text=extracted.metadata_text,
    )


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

    pages: list[_PageText] = []
    characters = 0
    charged_chunks = 0
    total_pixels = 0
    document = None

    def charge_text(text: str) -> None:
        nonlocal characters, charged_chunks
        # Both passes share one budget, including original metadata and separators.
        characters += len(text) + (1 if charged_chunks else 0)
        charged_chunks += 1
        if characters > reports.MAX_EXTRACTED_CHARACTERS:
            raise reports.ReportIntakeParserError("The PDF contains too much extracted text.")

    def ocr_page(
        page_index: int, *, fallback: bool = False, image_region: bool = False
    ) -> _PageText:
        nonlocal document, total_pixels
        tools = _require_pdf_ocr_tools()
        if document is None:
            document = tools.PdfDocument(file_bytes)
        page = document[page_index]
        bitmap = None
        image = None
        try:
            width, height = page.get_size()
            scale = reports.OCR_RENDER_DPI / 72
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise reports.ReportIntakeParserError("The PDF page dimensions are invalid.")
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
            crop = (0.0, 0.0, 0.0, 0.0)
            if image_region:
                # Read only a separable image area, never the digital candidates
                # again. A searchable scan or ambiguous layout must be reviewed
                # manually rather than deduplicated by code/value.
                failure = (
                    f"PDF page {page_index + 1} image region cannot be separated safely "
                    "from digital text. No partial candidates were saved; enter results manually."
                )
                if page.get_rotation() != 0 or page.get_bbox() != (0.0, 0.0, width, height):
                    raise reports.ReportIntakeParserError(failure)
                images: list[tuple[float, float, float, float]] = []
                texts: list[tuple[float, float, float, float]] = []
                for index, obj in enumerate(page.get_objects(max_depth=1)):
                    if index >= 256 or obj.type == tools.raw.FPDF_PAGEOBJ_FORM:
                        raise reports.ReportIntakeParserError(failure)
                    if obj.type not in (tools.raw.FPDF_PAGEOBJ_IMAGE, tools.raw.FPDF_PAGEOBJ_TEXT):
                        continue
                    left, bottom, right, top = obj.get_bounds()
                    if not all(math.isfinite(v) for v in (left, bottom, right, top)):
                        raise reports.ReportIntakeParserError(failure)
                    bounds = (max(0.0, left), max(0.0, bottom), min(width, right), min(height, top))
                    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                        continue
                    if obj.type == tools.raw.FPDF_PAGEOBJ_IMAGE:
                        images.append(bounds)
                    else:
                        texts.append(bounds)
                if not images:
                    raise reports.ReportIntakeParserError(failure)
                left = min(b[0] for b in images)
                bottom = min(b[1] for b in images)
                right = max(b[2] for b in images)
                top = max(b[3] for b in images)
                # Include a two-pixel guard for render rounding/antialiasing.
                guard = 2 / scale
                if any(
                    b[0] < right + guard
                    and b[2] > left - guard
                    and b[1] < top + guard
                    and b[3] > bottom - guard
                    for b in texts
                ):
                    raise reports.ReportIntakeParserError(failure)
                crop = (left, bottom, width - right, height - top)
            bitmap = page.render(scale=scale, crop=crop)
            image = bitmap.to_pil()
            if image.width * image.height > reports.MAX_PAGE_PIXELS:
                raise reports.ReportIntakeParserError("The rendered PDF page is too large.")
            text, confidence = reports._extract_pil_image_text(image, language="chi_tra+eng")
        finally:
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            page.close()
        charge_text(text)
        try:
            reports.parse_extracted_text(
                text=text,
                filename=None,
                extraction_method="ocr_pdf",
                page_count=1,
                line_confidences=confidence,
            )
        except reports.ReportIntakeParserError as error:
            label = "PDF OCR fallback" if fallback else "Scanned PDF"
            raise reports.ReportIntakeParserError(
                f"{label} page {page_index + 1} could not be parsed reliably. "
                "No partial candidates were saved; enter results manually."
            ) from error
        return _PageText(page_index, text, "ocr_pdf", confidence, image_region)

    try:
        for page_index, pdf_page in enumerate(reader.pages):
            text = (pdf_page.extract_text() or "").strip()
            if not text:
                pages.append(ocr_page(page_index))
            else:
                charge_text(text)
                pages.append(_PageText(page_index, text, "digital_pdf"))
        extracted = _compose(pages)
        try:
            _parse_pdf(extracted)
        except reports.NoCandidateObservationsError:
            # Only a document-wide absence of candidates permits fallback.
            original_text = extracted.text
            pages = [
                ocr_page(page.page_index, fallback=True) if page.method == "digital_pdf" else page
                for page in pages
            ]
            extracted = _compose(pages, metadata_text=original_text)
        else:
            # Zero-candidate image pages use whole-page OCR; candidate-bearing
            # pages retain their digital rows and supplement only disjoint images.
            original_text = extracted.text
            supplemented = False
            supplemented_pages: list[_PageText] = []
            for page in pages:
                supplemented_pages.append(page)
                if page.method != "digital_pdf":
                    continue
                try:
                    _parse_pdf(_compose([page]))
                except reports.NoCandidateObservationsError:
                    if _has_raster_image(reader.pages[page.page_index]):
                        supplemented_pages[-1] = ocr_page(page.page_index, fallback=True)
                        supplemented = True
                else:
                    if _has_raster_image(reader.pages[page.page_index]):
                        supplemented_pages.append(ocr_page(page.page_index, image_region=True))
                        supplemented = True
            if supplemented:
                extracted = _compose(supplemented_pages, metadata_text=original_text)
        # Metadata and retained digital pages can overlap in a supplement.
        # Bound the actual combined output as well as the shared processing budget.
        if (
            len(extracted.text) + len(extracted.metadata_text or "")
            > reports.MAX_EXTRACTED_CHARACTERS
        ):
            raise reports.ReportIntakeParserError("The PDF contains too much extracted text.")
        _parse_pdf(extracted)
    except reports.ReportIntakeParserError:
        raise
    except Exception as error:
        raise reports.ReportIntakeParserError("The PDF could not be processed safely.") from error
    finally:
        if document is not None:
            document.close()
    return {
        "text": extracted.text,
        "page_count": page_count,
        "extraction_method": extracted.extraction_method,
        "line_provenances": extracted.line_provenances,
        "line_confidences": [
            str(value) if value is not None else None for value in extracted.line_confidences
        ],
        "metadata_text": extracted.metadata_text,
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
