from datetime import UTC, datetime
from decimal import Decimal

import pytest
from healthy.domain.report_intakes import parse_extracted_text


@pytest.mark.parametrize(
    ("label", "english", "code"),
    [
        ("血糖", "Glucose", "GLUCOSE"),
        ("空腹血糖", "Fasting blood glucose", "GLUCOSE"),
        ("糖化血紅素", "HbA1c", "HEMOGLOBIN_A1C"),
        ("糖化血色素", "Hemoglobin A1c", "HEMOGLOBIN_A1C"),
        ("尿酸", "Uric Acid", "URIC_ACID"),
        ("膽固醇", "Total Cholesterol", "TOTAL_CHOLESTEROL"),
        ("總膽固醇", "Total Cholesterol", "TOTAL_CHOLESTEROL"),
        ("低密度脂蛋白膽固醇", "LDL", "LDL"),
        ("高密度脂蛋白膽固醇", "HDL", "HDL"),
        ("三酸甘油脂", "Triglycerides", "TRIGLYCERIDES"),
        ("三酸甘油酯", "Triglycerides", "TRIGLYCERIDES"),
        ("血紅素", "Hemoglobin", "HEMOGLOBIN"),
        ("丙胺酸轉氨酶", "GPT", "ALT"),
        ("天門冬胺酸轉氨酶", "GOT", "AST"),
    ],
)
def test_chinese_whitelist_maps_to_exact_english_canonical_code(
    label: str, english: str, code: str
) -> None:
    parsed = parse_extracted_text(
        text=f"{label} １２．５ mg/dL\n{english}: 12.5 mg/dL",
        filename="synthetic.pdf",
        extraction_method="digital_pdf",
        page_count=1,
    )
    assert len(parsed.observations) == 2
    for observation in parsed.observations:
        assert observation.code == code
        assert observation.value_numeric == Decimal("12.5")
        assert observation.unit == "mg/dL"
        assert observation.reference_range is None  # No inferred medical thresholds.
    assert parsed.observations[0].display_name == label


def test_fullwidth_metadata_values_and_extracted_reference_keep_line_source() -> None:
    text = (
        "來源：合成檢驗所\r\n"
        "報告日期：２０２６／１０／０１\r\n"
        "血糖：９２．２５ ｍｇ／ｄＬ （參考範圍：６５－９９）\r\n"
        "ＨｂＡ１ｃ＝５．８ ％\r\n"
        "三酸甘油脂：１３５ mg/dL"
    )
    parsed = parse_extracted_text(
        text=text,
        filename="synthetic.pdf",
        extraction_method="digital_pdf",
        page_count=1,
        line_confidences=(None, None, Decimal("0.91"), None, None),
    )
    assert parsed.source_name == "合成檢驗所"
    assert parsed.reported_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert parsed.extracted_character_count == len(text)
    glucose, a1c, triglycerides = parsed.observations
    assert glucose.value_numeric == Decimal("92.25")
    assert glucose.unit == "mg/dL"
    assert glucose.reference_range == "65-99"
    assert glucose.observed_at == parsed.reported_at
    assert glucose.parser_provenance == "digital_pdf:line-3"
    assert glucose.parser_confidence == Decimal("0.9100")
    assert a1c.code == "HEMOGLOBIN_A1C"
    assert a1c.value_numeric == Decimal("5.8")
    assert a1c.unit == "%"
    assert triglycerides.reference_range is None


def test_unknown_chinese_or_mixed_labels_are_not_stripped_into_lab_codes() -> None:
    parsed = parse_extracted_text(
        text=(
            "姓名：Synthetic Patient\n"
            "地址Glucose：92\n"
            "血糖說明：180\n"
            "未辨識檢驗：12\n"
            "Glucose: 92\n"
            "GPT comments: 42"
        ),
        filename="synthetic.pdf",
        extraction_method="digital_pdf",
        page_count=1,
    )
    assert [item.code for item in parsed.observations] == ["GLUCOSE", "GPT_COMMENTS"]
    assert parsed.observations[0].parser_provenance == "digital_pdf:line-5"
