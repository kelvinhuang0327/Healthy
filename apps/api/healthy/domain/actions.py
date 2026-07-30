from __future__ import annotations

from enum import StrEnum

TITLE_MAX_LENGTH = 240
DESCRIPTION_MAX_LENGTH = 2000


class HealthActionStatus(StrEnum):
    TODO = "todo"
    DONE = "done"


def normalize_title(value: str) -> str:
    """Return a trimmed, nonblank HealthAction title."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("title must not be blank")
    if len(normalized) > TITLE_MAX_LENGTH:
        raise ValueError(f"title must be at most {TITLE_MAX_LENGTH} characters")
    return normalized


def normalize_description(value: str | None) -> str | None:
    """Trim an optional description and canonicalize blank text to null."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > DESCRIPTION_MAX_LENGTH:
        raise ValueError(f"description must be at most {DESCRIPTION_MAX_LENGTH} characters")
    return normalized
