"""Add legacy-compatible symptom duration facts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0010"
down_revision: str | None = "20260810_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("symptom_logs") as batch_op:
        batch_op.add_column(sa.Column("estimated_start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("estimated_duration_days", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "estimated_duration_days_bounds",
            "estimated_duration_days IS NULL OR estimated_duration_days BETWEEN 1 AND 36500",
        )


def downgrade() -> None:
    with op.batch_alter_table("symptom_logs") as batch_op:
        batch_op.drop_constraint("estimated_duration_days_bounds", type_="check")
        batch_op.drop_column("estimated_duration_days")
        batch_op.drop_column("estimated_start_date")
