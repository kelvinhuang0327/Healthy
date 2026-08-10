from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

SYSTOLIC_BP_MM_HG_MIN = 30
SYSTOLIC_BP_MM_HG_MAX = 300
DIASTOLIC_BP_MM_HG_MIN = 20
DIASTOLIC_BP_MM_HG_MAX = 200
HEART_RATE_BPM_MIN = 20
HEART_RATE_BPM_MAX = 300
WEIGHT_KG_MIN = Decimal("1.00")
WEIGHT_KG_MAX = Decimal("500.00")
WEIGHT_KG_DECIMAL_PLACES = 2
BLOOD_GLUCOSE_MG_DL_MIN = Decimal("10.0")
BLOOD_GLUCOSE_MG_DL_MAX = Decimal("1000.0")
BLOOD_GLUCOSE_MG_DL_DECIMAL_PLACES = 1
SLEEP_HOURS_MIN = Decimal("0.00")
SLEEP_HOURS_MAX = Decimal("24.00")
SLEEP_HOURS_DECIMAL_PLACES = 2
SLEEP_HOURS_MAX_DIGITS = 4
NOTE_MAX_LENGTH = 2000
RECORDED_AT_MAX_FUTURE_SKEW = timedelta(minutes=5)


def has_at_least_one_metric_value(
    *,
    systolic_bp_mm_hg: int | None,
    diastolic_bp_mm_hg: int | None,
    heart_rate_bpm: int | None,
    weight_kg: Decimal | None,
    blood_glucose_mg_dl: Decimal | None,
    sleep_hours: Decimal | None,
) -> bool:
    """A HealthMetric is a bundled measurement event; it must carry a value."""
    return any(
        value is not None
        for value in (
            systolic_bp_mm_hg,
            diastolic_bp_mm_hg,
            heart_rate_bpm,
            weight_kg,
            blood_glucose_mg_dl,
            sleep_hours,
        )
    )


def blood_pressure_is_paired(
    systolic_bp_mm_hg: int | None,
    diastolic_bp_mm_hg: int | None,
) -> bool:
    return (systolic_bp_mm_hg is None) == (diastolic_bp_mm_hg is None)
