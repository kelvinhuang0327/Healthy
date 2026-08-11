from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from healthy.application.symptom_duration import build_symptom_duration_input
from healthy.infrastructure.models import SymptomLog

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
PERSON_ID = uuid.UUID(int=1)
OTHER_PERSON_ID = uuid.UUID(int=2)


def _symptom(
    number: int,
    *,
    person_id: uuid.UUID = PERSON_ID,
    days: int | None,
    occurred_at: datetime,
    created_at: datetime | None = None,
    estimated_start_date: date | None = None,
    duration_minutes: int | None = None,
) -> SymptomLog:
    return SymptomLog(
        id=uuid.UUID(int=number),
        person_id=person_id,
        symptom="Headache",
        occurred_at=occurred_at,
        severity=3,
        duration_minutes=duration_minutes,
        estimated_start_date=estimated_start_date,
        estimated_duration_days=days,
        note=None,
        created_at=created_at or occurred_at,
    )


def test_duration_uses_legacy_day_threshold_per_row_without_a_time_window() -> None:
    rows = [
        _symptom(
            1,
            days=179,
            occurred_at=NOW - timedelta(days=365),
        ),
        _symptom(
            2,
            days=180,
            occurred_at=NOW - timedelta(days=2),
            estimated_start_date=date(2026, 1, 1),
        ),
        _symptom(3, days=240, occurred_at=NOW - timedelta(days=1)),
        _symptom(4, days=240, occurred_at=NOW, estimated_start_date=None),
        _symptom(5, person_id=OTHER_PERSON_ID, days=999, occurred_at=NOW),
    ]

    result = build_symptom_duration_input(reversed(rows), person_id=PERSON_ID)

    assert result.status == "available"
    assert result.unit == "days"
    assert result.threshold_days == 180
    assert result.symptom_count == 4
    assert [observation.id for observation in result.observations] == [
        uuid.UUID(int=4),
        uuid.UUID(int=3),
        uuid.UUID(int=2),
        uuid.UUID(int=1),
    ]
    assert result.long_term_symptom_count == 3
    assert [observation.estimated_duration_days for observation in result.long_term_symptoms] == [
        240,
        240,
        180,
    ]


def test_duplicate_and_overlapping_rows_remain_independent_and_ties_are_stable() -> None:
    occurred_at = NOW - timedelta(days=4)
    created_at = NOW - timedelta(days=3)
    first = _symptom(10, days=180, occurred_at=occurred_at, created_at=created_at)
    second = _symptom(11, days=180, occurred_at=occurred_at, created_at=created_at)

    forward = build_symptom_duration_input([first, second], person_id=PERSON_ID)
    reverse = build_symptom_duration_input([second, first], person_id=PERSON_ID)

    assert forward == reverse
    assert forward.long_term_symptom_count == 2
    assert [observation.id for observation in forward.long_term_symptoms] == [
        uuid.UUID(int=11),
        uuid.UUID(int=10),
    ]


def test_missing_duration_is_not_inferred_from_other_timestamps_or_episode_fields() -> None:
    missing = _symptom(
        20,
        days=None,
        occurred_at=NOW,
        duration_minutes=90,
    )

    result = build_symptom_duration_input([missing], person_id=PERSON_ID)

    assert result.status == "missing"
    assert result.symptom_count == 1
    assert result.observations == ()
    assert result.long_term_symptom_count == 0
    assert result.missing_symptom_ids == (missing.id,)
