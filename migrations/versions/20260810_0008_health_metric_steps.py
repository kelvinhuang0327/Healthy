"""Add the legacy-compatible Steps count to health metrics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0008"
down_revision: str | None = "20260810_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("health_metrics", sa.Column("steps", sa.Integer(), nullable=True))
    op.drop_constraint("at_least_one_value", "health_metrics", type_="check")
    op.create_check_constraint(
        "at_least_one_value",
        "health_metrics",
        "systolic_bp_mm_hg IS NOT NULL"
        " OR diastolic_bp_mm_hg IS NOT NULL"
        " OR heart_rate_bpm IS NOT NULL"
        " OR steps IS NOT NULL"
        " OR weight_kg IS NOT NULL"
        " OR blood_glucose_mg_dl IS NOT NULL",
    )
    op.create_check_constraint(
        "steps_bounds",
        "health_metrics",
        "steps IS NULL OR steps BETWEEN 0 AND 200000",
    )


def downgrade() -> None:
    op.drop_constraint("at_least_one_value", "health_metrics", type_="check")
    op.drop_constraint("steps_bounds", "health_metrics", type_="check")
    op.create_check_constraint(
        "at_least_one_value",
        "health_metrics",
        "systolic_bp_mm_hg IS NOT NULL"
        " OR diastolic_bp_mm_hg IS NOT NULL"
        " OR heart_rate_bpm IS NOT NULL"
        " OR weight_kg IS NOT NULL"
        " OR blood_glucose_mg_dl IS NOT NULL",
    )
    op.drop_column("health_metrics", "steps")
