from __future__ import annotations

from datetime import timedelta

NOTE_MAX_LENGTH = 2000
OBSERVED_AT_MAX_FUTURE_SKEW = timedelta(minutes=5)


def normalize_note(value: str) -> str:
    """Return a trimmed, nonblank HealthActionOutcome note."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("note must not be blank")
    if len(normalized) > NOTE_MAX_LENGTH:
        raise ValueError(f"note must be at most {NOTE_MAX_LENGTH} characters")
    return normalized
