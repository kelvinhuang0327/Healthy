"""Add nullable legacy-compatible sleep duration to health metrics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0008"
down_revision: str | None = "20260810_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.add_column(sa.Column("sleep_hours", sa.Numeric(4, 2), nullable=True))
        batch_op.drop_constraint("at_least_one_value", type_="check")
        batch_op.create_check_constraint(
            "at_least_one_value",
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL"
            " OR sleep_hours IS NOT NULL",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM health_metrics "
            "WHERE sleep_hours IS NOT NULL "
            "AND systolic_bp_mm_hg IS NULL "
            "AND diastolic_bp_mm_hg IS NULL "
            "AND heart_rate_bpm IS NULL "
            "AND weight_kg IS NULL "
            "AND blood_glucose_mg_dl IS NULL"
        )
    )
    with op.batch_alter_table("health_metrics") as batch_op:
        batch_op.drop_constraint("at_least_one_value", type_="check")
        batch_op.create_check_constraint(
            "at_least_one_value",
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL",
        )
        batch_op.drop_column("sleep_hours")
