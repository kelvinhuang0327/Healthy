from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Literal, cast

import pytesseract  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pytesseract import Output

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CANDIDATE_COUNT = 100
PARSER_VERSION = "report-intake-parser-v1"

ALLOWED_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
ExtractionMethod = Literal["digital_pdf", "ocr_image"]
ReportIntakeStatus = Literal["pending_review", "confirmed", "failed"]


class ReportIntakeUploadError(ValueError):
    """Raised when an upload does not satisfy the intake boundary."""


class ReportIntakeUnsupportedTypeError(ReportIntakeUploadError):
    """Raised when the declared type or file signature is not supported."""


class ReportIntakeTooLargeError(ReportIntakeUploadError):
    """Raised when an upload exceeds the bounded intake size."""


class ReportIntakeParserError(ValueError):
    """Raised when a supported file cannot be safely converted to candidates."""


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    code: str
    display_name: str
    value_numeric: Decimal | None
    value_text: str | None
    unit: str | None
    reference_range: str | None
    observed_at: datetime | None
    parser_provenance: str
    parser_confidence: Decimal | None


@dataclass(frozen=True, slots=True)
class ParsedReportIntake:
    source_name: str
    reported_at: datetime | None
    extraction_method: ExtractionMethod
    page_count: int
    extracted_character_count: int
    observations: tuple[CandidateObservation, ...]


def sanitize_filename(filename: str | None) -> str:
    """Return a bounded display filename without path or control characters."""
    raw = (filename or "health-report").replace("\\", "/")
    basename = PurePath(raw).name
    normalized = unicodedata.normalize("NFKC", basename)
    safe = "".join(
        character if character.isalnum() or character in {".", "-", "_", " "} else "_"
        for character in normalized
        if not unicodedata.category(character).startswith("C")
    )
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    return (safe or "health-report")[:255]


def sanitize_source_name(source_name: str | None) -> str:
    """Return a bounded, visible source label without path/control characters."""
    normalized = unicodedata.normalize("NFKC", (source_name or "").strip())
    safe = "".join(
        "_"
        if character in {"/", "\\"} or unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    safe = re.sub(r"\s+", " ", safe).strip(" ._")
    return safe[:128]


def source_name_from_filename(filename: str | None) -> str:
    filename_clean = sanitize_filename(filename)
    stem = PurePath(filename_clean).stem.strip(" .")
    return sanitize_source_name(stem) or "Uploaded health report"


def sha256_for_bytes(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def validate_upload(
    *,
    filename: str | None,
    media_type: str | None,
    file_bytes: bytes,
) -> tuple[str, ExtractionMethod]:
    del filename
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ReportIntakeTooLargeError("The report file is larger than the 10 MB limit.")

    normalized_media_type = (media_type or "").split(";", 1)[0].strip().lower()
    if normalized_media_type not in ALLOWED_MEDIA_TYPES:
        raise ReportIntakeUnsupportedTypeError(
            "Only PDF, JPEG, and PNG report files are supported."
        )

    if normalized_media_type == "application/pdf":
        valid_signature = file_bytes.startswith(b"%PDF-")
        extraction_method: ExtractionMethod = "digital_pdf"
    elif normalized_media_type == "image/jpeg":
        valid_signature = file_bytes.startswith(b"\xff\xd8\xff")
        extraction_method = "ocr_image"
    else:
        valid_signature = file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        extraction_method = "ocr_image"

    if not valid_signature:
        raise ReportIntakeUnsupportedTypeError("The file content does not match its declared type.")
    return normalized_media_type, extraction_method


def _parse_datetime(value: str) -> datetime | None:
    normalized = value.strip().replace("/", "-")
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
            return datetime.fromisoformat(normalized).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_DATE_LINE_RE = re.compile(
    r"^\s*(?:report\s+date|date\s+of\s+report|collection\s+date|collected\s+on)"
    r"\s*[:=]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:source|source\s+name|laboratory|lab|facility)\s*[:=]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_FIELD_LINE_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 _/%()\-]{0,126}?)"
    r"(?:\s*[:=]\s*|\t+|\s{2,})(?P<value>.+?)\s*$"
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
_EXPLICIT_REFERENCE_RE = re.compile(
    r"(?:reference|ref(?:erence)?(?:\s+range)?|normal(?:\s+range)?)"
    r"\s*[:=]\s*(?P<value>[^,;|)]+)",
    re.IGNORECASE,
)
_REFERENCE_SHAPE_RE = re.compile(
    r"^(?:[<>]=?\s*)?-?\d+(?:[.,]\d+)?\s*"
    r"(?:-|–|to|/)\s*(?:[<>]=?\s*)?-?\d+(?:[.,]\d+)?$"
    r"|^[<>]=?\s*-?\d+(?:[.,]\d+)?$"
    r"|^(?:negative|positive|non[- ]?reactive|reactive)$",
    re.IGNORECASE,
)

_ALIASES = {
    "a1c": "HEMOGLOBIN_A1C",
    "blood glucose": "GLUCOSE",
    "fasting blood glucose": "GLUCOSE",
    "glucose": "GLUCOSE",
    "hemoglobin a1c": "HEMOGLOBIN_A1C",
    "hba1c": "HEMOGLOBIN_A1C",
}
_IGNORED_LABELS = {
    "accession",
    "address",
    "comments",
    "collection date",
    "collected on",
    "date of birth",
    "date of report",
    "dob",
    "facility",
    "lab",
    "laboratory",
    "medical record number",
    "mrn",
    "name",
    "notes",
    "ordering physician",
    "patient",
    "patient name",
    "physician",
    "report date",
    "source",
    "source name",
    "specimen",
}


def _clean_reference(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value.strip())
    cleaned = re.sub(
        r"^(?:reference|ref(?:erence)?(?:\s+range)?|normal(?:\s+range)?)\s*[:=]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned[:128] if _REFERENCE_SHAPE_RE.fullmatch(cleaned) else None


def _reference_from_value(value: str) -> str | None:
    explicit = _EXPLICIT_REFERENCE_RE.search(value)
    if explicit:
        reference = _clean_reference(explicit.group("value"))
        if reference:
            return reference

    for parenthetical in re.findall(r"\(([^)]{1,128})\)", value):
        reference = _clean_reference(parenthetical)
        if reference:
            return reference
    return None


def _remove_reference_text(value: str) -> str:
    without_parenthetical = re.sub(r"\([^)]{1,128}\)", "", value)
    without_explicit = _EXPLICIT_REFERENCE_RE.sub("", without_parenthetical)
    return re.sub(r"\s+", " ", without_explicit).strip(" ;|")


def _normalise_code(label: str) -> str:
    clean_label = re.sub(r"\s+", " ", label.strip(" :")).strip()
    alias = _ALIASES.get(clean_label.casefold())
    if alias:
        return alias
    code = re.sub(r"[^A-Za-z0-9]+", "_", clean_label).strip("_").upper()
    return code[:64]


def _parse_candidate_value(value: str) -> tuple[Decimal | None, str | None, str | None]:
    reference_removed = _remove_reference_text(value)
    number_match = _NUMBER_RE.search(reference_removed)
    if number_match is None:
        text_value = reference_removed.strip()
        return None, text_value[:2000] or None, None

    try:
        numeric_value = Decimal(number_match.group(0).replace(",", ""))
    except InvalidOperation:
        return None, reference_removed[:2000] or None, None
    if not numeric_value.is_finite():
        return None, reference_removed[:2000] or None, None

    unit_tail = reference_removed[number_match.end() :]
    unit_match = re.match(r"\s*([A-Za-z%µμ][A-Za-z0-9%µμ/.*^()\- ]{0,31}?)(?:\s|$)", unit_tail)
    unit = unit_match.group(1).strip(" :,-") if unit_match else None
    return numeric_value, None, unit or None


def _line_confidence(line_confidences: tuple[Decimal | None, ...], index: int) -> Decimal | None:
    if index >= len(line_confidences):
        return None
    confidence = line_confidences[index]
    if confidence is None:
        return None
    return confidence.quantize(Decimal("0.0001"))


def parse_extracted_text(
    *,
    text: str,
    filename: str | None,
    extraction_method: ExtractionMethod,
    page_count: int,
    line_confidences: tuple[Decimal | None, ...] = (),
) -> ParsedReportIntake:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    source_name = source_name_from_filename(filename)
    reported_at: datetime | None = None

    for line in lines:
        source_match = _SOURCE_LINE_RE.match(line)
        if source_match:
            visible_source = sanitize_source_name(source_match.group("value"))
            if visible_source:
                source_name = visible_source
            continue
        date_match = _DATE_LINE_RE.match(line)
        if date_match:
            reported_at = _parse_datetime(date_match.group("value"))
            continue

    observations: list[CandidateObservation] = []
    for line_index, line in enumerate(lines):
        field_match = _FIELD_LINE_RE.match(line)
        if field_match is None:
            continue
        label = re.sub(r"\s+", " ", field_match.group("label")).strip()
        if label.casefold() in _IGNORED_LABELS:
            continue
        code = _normalise_code(label)
        if not code:
            continue
        raw_value = field_match.group("value").strip()
        if not raw_value:
            continue
        reference_range = _reference_from_value(raw_value)
        value_numeric, value_text, unit = _parse_candidate_value(raw_value)
        if value_numeric is None and value_text is None:
            continue
        observations.append(
            CandidateObservation(
                code=code,
                display_name=label[:128],
                value_numeric=value_numeric,
                value_text=value_text,
                unit=unit,
                reference_range=reference_range,
                observed_at=reported_at,
                parser_provenance=f"{extraction_method}:line-{line_index + 1}",
                parser_confidence=_line_confidence(line_confidences, line_index),
            )
        )
        if len(observations) >= MAX_CANDIDATE_COUNT:
            break

    if not observations:
        raise ReportIntakeParserError("No candidate observations were found in the report.")

    return ParsedReportIntake(
        source_name=source_name,
        reported_at=reported_at,
        extraction_method=extraction_method,
        page_count=page_count,
        extracted_character_count=len(text),
        observations=tuple(observations),
    )


def _extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(file_bytes), strict=False)
        page_text = [(page.extract_text() or "") for page in reader.pages]
    except Exception as error:
        raise ReportIntakeParserError("The PDF could not be read safely.") from error
    text = "\n".join(page_text).strip()
    if not text:
        raise ReportIntakeParserError(
            "No digital text was found. Scanned PDFs are not supported in this version."
        )
    return text, len(page_text)


def _extract_image_text(file_bytes: bytes) -> tuple[str, tuple[Decimal | None, ...]]:
    try:
        with Image.open(io.BytesIO(file_bytes)) as image:
            image.load()
            ocr_data = cast(
                dict[str, list[object]],
                pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 6"),
            )
    except pytesseract.TesseractNotFoundError as error:
        raise ReportIntakeParserError("Image OCR is unavailable on this server.") from error
    except (UnidentifiedImageError, OSError) as error:
        raise ReportIntakeParserError("The image could not be read safely.") from error
    except Exception as error:
        raise ReportIntakeParserError("The image could not be processed safely.") from error

    texts = ocr_data.get("text", [])
    blocks = ocr_data.get("block_num", [])
    paragraphs = ocr_data.get("par_num", [])
    lines = ocr_data.get("line_num", [])
    confidences = ocr_data.get("conf", [])
    grouped: OrderedDict[tuple[str, str, str], list[tuple[str, Decimal | None]]] = OrderedDict()
    for index, raw_text in enumerate(texts):
        text_piece = str(raw_text).strip()
        if not text_piece:
            continue
        key = (
            str(blocks[index]) if index < len(blocks) else "0",
            str(paragraphs[index]) if index < len(paragraphs) else "0",
            str(lines[index]) if index < len(lines) else str(index),
        )
        confidence: Decimal | None = None
        if index < len(confidences):
            try:
                parsed_confidence = Decimal(str(confidences[index]))
            except InvalidOperation:
                parsed_confidence = Decimal("-1")
            if parsed_confidence.is_finite() and parsed_confidence >= 0:
                confidence = max(Decimal("0"), min(parsed_confidence, Decimal("100"))) / Decimal(
                    "100"
                )
        grouped.setdefault(key, []).append((text_piece, confidence))

    text_lines: list[str] = []
    line_confidences: list[Decimal | None] = []
    for words in grouped.values():
        text_lines.append(" ".join(word for word, _confidence in words))
        known_confidences = [confidence for _word, confidence in words if confidence is not None]
        if known_confidences:
            line_confidences.append(sum(known_confidences, Decimal("0")) / len(known_confidences))
        else:
            line_confidences.append(None)
    return "\n".join(text_lines).strip(), tuple(line_confidences)


def extract_and_parse(
    *,
    filename: str | None,
    media_type: str | None,
    file_bytes: bytes,
) -> ParsedReportIntake:
    normalized_media_type, extraction_method = validate_upload(
        filename=filename,
        media_type=media_type,
        file_bytes=file_bytes,
    )
    del normalized_media_type
    if extraction_method == "digital_pdf":
        text, page_count = _extract_pdf_text(file_bytes)
        return parse_extracted_text(
            text=text,
            filename=filename,
            extraction_method=extraction_method,
            page_count=page_count,
        )

    text, line_confidences = _extract_image_text(file_bytes)
    if not text:
        raise ReportIntakeParserError("No readable text was found in the image.")
    return parse_extracted_text(
        text=text,
        filename=filename,
        extraction_method=extraction_method,
        page_count=1,
        line_confidences=line_confidences,
    )


def is_safe_numeric(value: Decimal | None) -> bool:
    return value is None or (value.is_finite() and not math.isnan(float(value)))
