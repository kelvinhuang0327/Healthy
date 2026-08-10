"""Add nullable legacy-compatible sleep duration to health metrics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0008"
down_revision: str | None = "20260810_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("health_metrics", sa.Column("sleep_hours", sa.Numeric(4, 2), nullable=True))
    op.drop_constraint(
        "at_least_one_value",
        "health_metrics",
        type_="check",
    )
    op.create_check_constraint(
        "at_least_one_value",
        "health_metrics",
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
    op.drop_constraint(
        "at_least_one_value",
        "health_metrics",
        type_="check",
    )
    op.create_check_constraint(
        "at_least_one_value",
        "health_metrics",
        "systolic_bp_mm_hg IS NOT NULL"
        " OR diastolic_bp_mm_hg IS NOT NULL"
        " OR heart_rate_bpm IS NOT NULL"
        " OR weight_kg IS NOT NULL"
        " OR blood_glucose_mg_dl IS NOT NULL",
    )
    op.drop_column("health_metrics", "sleep_hours")
