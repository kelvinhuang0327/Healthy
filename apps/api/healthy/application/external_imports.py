from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthy.domain.external_imports import (
    ExternalMetricCsvImportSummary,
    build_import_summary,
    parse_health_metric_rows,
)
from healthy.infrastructure.repositories import HealthMetricRepository


class HealthMetricImportError(Exception):
    """Base application exception for health metric import errors."""


class HealthMetricImportIntegrityError(HealthMetricImportError):
    """Raised when an import violates database integrity constraints."""


def import_external_metrics_csv(
    database_session: Session,
    *,
    person_id: uuid.UUID,
    csv_payload: bytes,
) -> ExternalMetricCsvImportSummary:
    rows = parse_health_metric_rows(csv_payload)
    try:
        inserted_count = HealthMetricRepository.import_external_metrics(
            database_session,
            person_id=person_id,
            rows=rows,
        )
        database_session.commit()
    except IntegrityError as exc:
        database_session.rollback()
        raise HealthMetricImportIntegrityError("Import violates integrity constraints") from exc

    return build_import_summary(
        rows=rows,
        inserted_count=inserted_count,
    )
