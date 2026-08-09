"""Create the health_reports and health_report_observations tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0006"
down_revision: str | None = "20260730_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed')",
            name="ck_health_reports_status",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_health_reports_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_reports"),
        sa.UniqueConstraint(
            "person_id",
            "canonical_sha256",
            name="uq_health_reports_person_sha256",
        ),
    )
    op.create_index(
        "ix_health_reports_person_timeline",
        "health_reports",
        [
            "person_id",
            sa.text("reported_at DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )

    op.create_table(
        "health_report_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("value_numeric", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("reference_range", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["health_reports.id"],
            name="fk_health_report_observations_report_id_health_reports",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_health_report_observations_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_report_observations"),
    )
    op.create_index(
        "ix_health_report_observations_report_id",
        "health_report_observations",
        ["report_id"],
    )
    op.create_index(
        "ix_health_report_observations_person_code",
        "health_report_observations",
        ["person_id", "code", sa.text("observed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_health_report_observations_person_code",
        table_name="health_report_observations",
    )
    op.drop_index(
        "ix_health_report_observations_report_id",
        table_name="health_report_observations",
    )
    op.drop_table("health_report_observations")

    op.drop_index(
        "ix_health_reports_person_timeline",
        table_name="health_reports",
    )
    op.drop_table("health_reports")
