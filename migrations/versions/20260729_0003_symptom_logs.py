"""Create the symptom_logs table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0003"
down_revision: str | None = "20260727_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symptom_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symptom", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(symptom) BETWEEN 1 AND 120",
            name="symptom_length",
        ),
        sa.CheckConstraint("symptom = btrim(symptom)", name="symptom_trimmed"),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="severity_bounds"),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes >= 1",
            name="duration_minutes_minimum",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) <= 2000",
            name="note_length",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_symptom_logs_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_symptom_logs"),
    )
    op.create_index("ix_symptom_logs_person_id", "symptom_logs", ["person_id"])
    op.create_index(
        "ix_symptom_logs_person_timeline",
        "symptom_logs",
        [
            "person_id",
            sa.text("occurred_at DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_symptom_logs_person_timeline", table_name="symptom_logs")
    op.drop_index("ix_symptom_logs_person_id", table_name="symptom_logs")
    op.drop_table("symptom_logs")
