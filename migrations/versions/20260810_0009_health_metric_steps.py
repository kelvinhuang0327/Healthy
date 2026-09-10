"""Add the legacy-compatible Steps count to health metrics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0009"
down_revision: str | None = "20260810_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.add_column(sa.Column("steps", sa.Integer(), nullable=True))
        batch_op.drop_constraint("at_least_one_value", type_="check")
        batch_op.create_check_constraint(
            "at_least_one_value",
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL"
            " OR sleep_hours IS NOT NULL"
            " OR steps IS NOT NULL",
        )
        batch_op.create_check_constraint(
            "steps_bounds",
            "steps IS NULL OR steps BETWEEN 0 AND 200000",
        )


def downgrade() -> None:
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.drop_constraint("at_least_one_value", type_="check")
        batch_op.drop_constraint("steps_bounds", type_="check")
        batch_op.create_check_constraint(
            "at_least_one_value",
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL"
            " OR sleep_hours IS NOT NULL",
        )
        batch_op.drop_column("steps")
