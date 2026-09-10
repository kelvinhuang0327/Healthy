"""Finalize cross-dialect external metric import identity constraints."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260910_0016"
down_revision: str | None = "20260820_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SOURCE_RECORD_CONSISTENT = (
    "(source_type = 'manual' AND source_record_fingerprint IS NULL)"
    " OR (source_type = 'external_csv'"
    " AND source_record_fingerprint IS NOT NULL"
    " AND length(source_record_fingerprint) = 64)"
)


def upgrade() -> None:
    with op.batch_alter_table("health_metrics", recreate="always") as batch_op:
        batch_op.drop_constraint("source_record_fingerprint_length", type_="check")
        batch_op.create_check_constraint(
            "source_record_fingerprint_length",
            "source_record_fingerprint IS NULL OR length(source_record_fingerprint) = 64",
        )
        batch_op.create_check_constraint(
            "source_record_consistent",
            _SOURCE_RECORD_CONSISTENT,
        )
        batch_op.create_unique_constraint(
            "uq_health_metrics_person_source_record_fingerprint",
            ["person_id", "source_type", "source_record_fingerprint"],
        )


def _assert_precision_downgrade_safe() -> None:
    precision_loss = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 "
                "FROM health_metrics "
                "WHERE blood_glucose_mg_dl IS NOT NULL "
                "AND MOD(blood_glucose_mg_dl * 10, 1) <> 0 "
                "LIMIT 1"
            )
        )
        .scalar_one_or_none()
    )
    if precision_loss is not None:
        raise RuntimeError("BLOOD_GLUCOSE_DOWNGRADE_PRECISION_LOSS")


def downgrade() -> None:
    if context.get_revision_argument() != down_revision:
        _assert_precision_downgrade_safe()

    with op.batch_alter_table("health_metrics", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "uq_health_metrics_person_source_record_fingerprint",
            type_="unique",
        )
        batch_op.drop_constraint("source_record_consistent", type_="check")
        batch_op.drop_constraint("source_record_fingerprint_length", type_="check")
        batch_op.create_check_constraint(
            "source_record_fingerprint_length",
            "source_record_fingerprint IS NULL OR length(source_record_fingerprint) = 64",
        )
