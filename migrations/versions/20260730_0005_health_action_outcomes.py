"""Create the health_action_outcomes table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0005"
down_revision: str | None = "20260729_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_action_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(note) BETWEEN 1 AND 2000",
            name="note_length",
        ),
        sa.CheckConstraint("note = btrim(note)", name="note_trimmed"),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["health_actions.id"],
            name="fk_health_action_outcomes_action_id_health_actions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_action_outcomes"),
    )
    op.create_index(
        "ix_health_action_outcomes_action_id",
        "health_action_outcomes",
        ["action_id"],
    )
    op.create_index(
        "ix_health_action_outcomes_action_timeline",
        "health_action_outcomes",
        [
            "action_id",
            sa.text("observed_at DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_health_action_outcomes_action_timeline",
        table_name="health_action_outcomes",
    )
    op.drop_index(
        "ix_health_action_outcomes_action_id",
        table_name="health_action_outcomes",
    )
    op.drop_table("health_action_outcomes")
