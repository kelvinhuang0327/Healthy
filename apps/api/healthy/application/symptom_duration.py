from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from healthy.infrastructure.models import SymptomLog

LEGACY_LONG_TERM_DURATION_DAYS = 180
SymptomDurationStatus = Literal["available", "missing"]


@dataclass(frozen=True, slots=True)
class SymptomDurationObservation:
    id: uuid.UUID
    person_id: uuid.UUID
    symptom: str
    occurred_at: datetime
    estimated_start_date: date | None
    estimated_duration_days: int


@dataclass(frozen=True, slots=True)
class SymptomDurationInput:
    """Person-scoped legacy duration facts without derived writes.

    Legacy duration is an ongoing, user-estimated day count. Each persisted
    symptom row is evaluated independently; no episode merge or time window is
    applied. Rows without the timing fact remain explicitly missing.
    """

    status: SymptomDurationStatus
    unit: Literal["days"]
    threshold_days: int
    symptom_count: int
    observations: tuple[SymptomDurationObservation, ...]
    missing_symptom_ids: tuple[uuid.UUID, ...]

    @property
    def long_term_symptoms(self) -> tuple[SymptomDurationObservation, ...]:
        return tuple(
            observation
            for observation in self.observations
            if observation.estimated_duration_days >= self.threshold_days
        )

    @property
    def long_term_symptom_count(self) -> int:
        return len(self.long_term_symptoms)


def build_symptom_duration_input(
    symptoms: Iterable[SymptomLog],
    *,
    person_id: uuid.UUID,
) -> SymptomDurationInput:
    """Evaluate persisted Healthy symptom facts with legacy selection semantics."""
    person_symptoms = [symptom for symptom in symptoms if symptom.person_id == person_id]
    person_symptoms.sort(key=_symptom_sort_key, reverse=True)

    observations = tuple(
        SymptomDurationObservation(
            id=symptom.id,
            person_id=symptom.person_id,
            symptom=symptom.symptom,
            occurred_at=symptom.occurred_at,
            estimated_start_date=symptom.estimated_start_date,
            estimated_duration_days=symptom.estimated_duration_days,
        )
        for symptom in person_symptoms
        if symptom.estimated_duration_days is not None
    )
    missing_symptom_ids = tuple(
        symptom.id for symptom in person_symptoms if symptom.estimated_duration_days is None
    )
    return SymptomDurationInput(
        status="available" if observations else "missing",
        unit="days",
        threshold_days=LEGACY_LONG_TERM_DURATION_DAYS,
        symptom_count=len(person_symptoms),
        observations=observations,
        missing_symptom_ids=missing_symptom_ids,
    )


def _symptom_sort_key(symptom: SymptomLog) -> tuple[datetime, datetime, str]:
    return (
        _as_utc(symptom.occurred_at),
        _as_utc(symptom.created_at),
        str(symptom.id),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
