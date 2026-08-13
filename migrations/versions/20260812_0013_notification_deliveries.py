"""Add privacy-minimized email notification delivery state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0013"
down_revision: str | None = "20260812_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "health_action_reminders",
        sa.Column(
            "email_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reminder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "channel", sa.String(length=20), server_default=sa.text("'email'"), nullable=False
        ),
        sa.Column("reminder_local_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
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
            "channel IN ('email')",
            name="channel_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'cancelled', 'failed', 'unknown')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL",
            name="sent_requires_sent_at",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name="failed_requires_failed_at",
        ),
        sa.CheckConstraint(
            "status <> 'sending' OR claimed_at IS NOT NULL",
            name="sending_requires_claimed_at",
        ),
        sa.ForeignKeyConstraint(
            ["reminder_id"],
            ["health_action_reminders.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reminder_id",
            "channel",
            "reminder_local_date",
            name="uq_notification_deliveries_reminder_channel_local_date",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_reminder_id",
        "notification_deliveries",
        ["reminder_id"],
    )
    op.create_index(
        "ix_notification_deliveries_status_claimed_at",
        "notification_deliveries",
        ["status", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_status_claimed_at",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_reminder_id",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_column("health_action_reminders", "email_enabled")
