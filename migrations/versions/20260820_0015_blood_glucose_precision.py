"""Expand blood glucose storage precision to two decimal places."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0015"
down_revision: str | None = "20260818_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "health_metrics",
        "blood_glucose_mg_dl",
        existing_type=sa.Numeric(precision=5, scale=1),
        type_=sa.Numeric(precision=6, scale=2),
        existing_nullable=True,
    )


def downgrade() -> None:
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

    op.alter_column(
        "health_metrics",
        "blood_glucose_mg_dl",
        existing_type=sa.Numeric(precision=6, scale=2),
        type_=sa.Numeric(precision=5, scale=1),
        existing_nullable=True,
    )
