from enum import StrEnum


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PersonRelationship(StrEnum):
    SELF = "self"
    FAMILY = "family"
    CHILD = "child"
    PARENT = "parent"
    SPOUSE = "spouse"
    CAREGIVER = "caregiver"


def normalize_email(value: str) -> str:
    """Return the canonical account identifier."""
    return value.strip().casefold()
