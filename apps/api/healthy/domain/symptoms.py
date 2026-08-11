from __future__ import annotations

from datetime import timedelta

SYMPTOM_MAX_LENGTH = 120
SEVERITY_MIN = 1
SEVERITY_MAX = 5
DURATION_MINUTES_MIN = 1
ESTIMATED_DURATION_DAYS_MIN = 1
ESTIMATED_DURATION_DAYS_MAX = 36_500
NOTE_MAX_LENGTH = 2000
OCCURRED_AT_MAX_FUTURE_SKEW = timedelta(minutes=5)


def normalize_symptom(value: str) -> str:
    """Return the canonical symptom label, rejecting blank trimmed input."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("symptom must not be blank")
    if len(normalized) > SYMPTOM_MAX_LENGTH:
        raise ValueError(f"symptom must be at most {SYMPTOM_MAX_LENGTH} characters")
    return normalized
