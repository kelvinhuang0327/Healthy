"""Add idempotent external-import identity to health metrics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0013"
down_revision: str | None = "20260812_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_type",
                sa.String(length=32),
                server_default=sa.text("'manual'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("source_record_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "source_type_allowed",
            "source_type IN ('manual', 'external_csv')",
        )
        batch_op.create_check_constraint(
            "source_record_consistent",
            "(source_type = 'manual' AND source_record_fingerprint IS NULL)"
            " OR (source_type = 'external_csv'"
            " AND source_record_fingerprint IS NOT NULL"
            " AND length(source_record_fingerprint) = 64)",
        )
        batch_op.create_unique_constraint(
            "uq_health_metrics_person_source_record_fingerprint",
            ["person_id", "source_type", "source_record_fingerprint"],
        )


def downgrade() -> None:
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.drop_constraint(
            "uq_health_metrics_person_source_record_fingerprint",
            type_="unique",
        )
        batch_op.drop_constraint("source_record_consistent", type_="check")
        batch_op.drop_constraint("source_type_allowed", type_="check")
        batch_op.drop_column("source_record_fingerprint")
        batch_op.drop_column("source_type")
