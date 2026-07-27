"""Create the health_metrics table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0002"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("systolic_bp_mm_hg", sa.Integer(), nullable=True),
        sa.Column("diastolic_bp_mm_hg", sa.Integer(), nullable=True),
        sa.Column("heart_rate_bpm", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("blood_glucose_mg_dl", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(systolic_bp_mm_hg IS NULL) = (diastolic_bp_mm_hg IS NULL)",
            name="bp_pairing",
        ),
        sa.CheckConstraint(
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL",
            name="at_least_one_value",
        ),
        sa.CheckConstraint(
            "systolic_bp_mm_hg IS NULL OR systolic_bp_mm_hg BETWEEN 30 AND 300",
            name="systolic_bp_mm_hg_bounds",
        ),
        sa.CheckConstraint(
            "diastolic_bp_mm_hg IS NULL OR diastolic_bp_mm_hg BETWEEN 20 AND 200",
            name="diastolic_bp_mm_hg_bounds",
        ),
        sa.CheckConstraint(
            "heart_rate_bpm IS NULL OR heart_rate_bpm BETWEEN 20 AND 300",
            name="heart_rate_bpm_bounds",
        ),
        sa.CheckConstraint(
            "weight_kg IS NULL OR weight_kg BETWEEN 1.00 AND 500.00",
            name="weight_kg_bounds",
        ),
        sa.CheckConstraint(
            "blood_glucose_mg_dl IS NULL OR blood_glucose_mg_dl BETWEEN 10.0 AND 1000.0",
            name="blood_glucose_mg_dl_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_health_metrics_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_metrics"),
    )
    op.create_index("ix_health_metrics_person_id", "health_metrics", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_health_metrics_person_id", table_name="health_metrics")
    op.drop_table("health_metrics")
