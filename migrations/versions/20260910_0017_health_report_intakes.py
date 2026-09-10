"""Create bounded health report file intake and review candidates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0017"
down_revision: str | None = "20260910_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_intakes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("person_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("extraction_method", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extracted_character_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending_review'"),
            nullable=False,
        ),
        sa.Column(
            "pending_review",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=256), nullable=True),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_review', 'confirmed', 'failed')",
            name="ck_report_intakes_status",
        ),
        sa.CheckConstraint(
            "pending_review = (status = 'pending_review')",
            name="ck_report_intakes_pending_review_consistent",
        ),
        sa.CheckConstraint(
            "status <> 'confirmed' OR report_id IS NOT NULL",
            name="ck_report_intakes_confirmed_requires_report",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_report_intakes_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["health_reports.id"],
            name="fk_report_intakes_report_id_health_reports",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_intakes"),
        sa.UniqueConstraint(
            "person_id",
            "file_sha256",
            name="uq_report_intakes_person_file_sha256",
        ),
    )
    op.create_index("ix_report_intakes_person_id", "report_intakes", ["person_id"])
    op.create_index("ix_report_intakes_report_id", "report_intakes", ["report_id"])
    op.create_index(
        "ix_report_intakes_person_timeline",
        "report_intakes",
        ["person_id", "created_at", "id"],
    )

    op.create_table(
        "report_intake_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("value_numeric", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("reference_range", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parser_provenance", sa.String(length=128), nullable=False),
        sa.Column("parser_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "value_numeric IS NOT NULL OR "
            "(value_text IS NOT NULL AND length(trim(value_text)) > 0)",
            name="ck_report_intake_observations_at_least_one_value",
        ),
        sa.CheckConstraint(
            "parser_confidence IS NULL OR parser_confidence BETWEEN 0 AND 1",
            name="ck_report_intake_observations_confidence_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["intake_id"],
            ["report_intakes.id"],
            name="fk_report_intake_observations_intake_id_report_intakes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_intake_observations"),
        sa.UniqueConstraint(
            "intake_id",
            "ordinal",
            name="uq_report_intake_observations_intake_ordinal",
        ),
    )
    op.create_index(
        "ix_report_intake_observations_intake_id",
        "report_intake_observations",
        ["intake_id"],
    )
    op.create_index(
        "ix_report_intake_observations_intake_order",
        "report_intake_observations",
        ["intake_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_intake_observations_intake_order",
        table_name="report_intake_observations",
    )
    op.drop_index(
        "ix_report_intake_observations_intake_id",
        table_name="report_intake_observations",
    )
    op.drop_table("report_intake_observations")

    op.drop_index("ix_report_intakes_person_timeline", table_name="report_intakes")
    op.drop_index("ix_report_intakes_report_id", table_name="report_intakes")
    op.drop_index("ix_report_intakes_person_id", table_name="report_intakes")
    op.drop_table("report_intakes")
