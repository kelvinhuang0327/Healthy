"""Create the health_actions table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0004"
down_revision: str | None = "20260729_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'todo'"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 240",
            name="title_length",
        ),
        sa.CheckConstraint("title = btrim(title)", name="title_trimmed"),
        sa.CheckConstraint("status IN ('todo', 'done')", name="status_allowed"),
        sa.CheckConstraint(
            "(status = 'todo' AND completed_at IS NULL)"
            " OR (status = 'done' AND completed_at IS NOT NULL)",
            name="status_completion_consistent",
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 2000",
            name="description_length",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_health_actions_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_actions"),
    )
    op.create_index("ix_health_actions_person_id", "health_actions", ["person_id"])
    op.create_index(
        "ix_health_actions_person_timeline",
        "health_actions",
        [
            "person_id",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_health_actions_person_timeline", table_name="health_actions")
    op.drop_index("ix_health_actions_person_id", table_name="health_actions")
    op.drop_table("health_actions")
