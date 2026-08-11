"""Add legacy-compatible symptom duration facts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0010"
down_revision: str | None = "20260810_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("symptom_logs", sa.Column("estimated_start_date", sa.Date(), nullable=True))
    op.add_column("symptom_logs", sa.Column("estimated_duration_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "estimated_duration_days_bounds",
        "symptom_logs",
        "estimated_duration_days IS NULL OR estimated_duration_days BETWEEN 1 AND 36500",
    )


def downgrade() -> None:
    op.drop_constraint(
        "estimated_duration_days_bounds",
        "symptom_logs",
        type_="check",
    )
    op.drop_column("symptom_logs", "estimated_duration_days")
    op.drop_column("symptom_logs", "estimated_start_date")
