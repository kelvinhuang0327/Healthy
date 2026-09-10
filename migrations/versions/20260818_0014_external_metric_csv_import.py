"""Add external health metric CSV import provenance and idempotency."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0014"
down_revision: str | None = "20260812_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_sqlite() -> None:
    with op.batch_alter_table("health_metrics", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_type",
                sa.String(length=32),
                server_default=sa.text("'manual'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("source_record_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "source_type_allowed",
            "source_type IN ('manual', 'external_csv')",
        )
        batch_op.create_check_constraint(
            "source_record_fingerprint_length",
            "source_record_fingerprint IS NULL OR length(source_record_fingerprint) = 64",
        )
        batch_op.create_index(
            "uq_health_metrics_person_source_fingerprint",
            ["person_id", "source_type", "source_record_fingerprint"],
            unique=True,
            sqlite_where=sa.text("source_record_fingerprint IS NOT NULL"),
        )


def _upgrade_postgresql() -> None:
    op.add_column(
        "health_metrics",
        sa.Column(
            "source_type",
            sa.String(length=32),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )
    op.add_column(
        "health_metrics",
        sa.Column("source_record_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "source_type_allowed",
        "health_metrics",
        "source_type IN ('manual', 'external_csv')",
    )
    op.create_check_constraint(
        "source_record_fingerprint_length",
        "health_metrics",
        "source_record_fingerprint IS NULL OR char_length(source_record_fingerprint) = 64",
    )
    op.create_index(
        "uq_health_metrics_person_source_fingerprint",
        "health_metrics",
        ["person_id", "source_type", "source_record_fingerprint"],
        unique=True,
        postgresql_where=sa.text("source_record_fingerprint IS NOT NULL"),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgresql()


def _downgrade_sqlite() -> None:
    with op.batch_alter_table("health_metrics", recreate="always") as batch_op:
        batch_op.drop_index("uq_health_metrics_person_source_fingerprint")
        batch_op.drop_constraint("source_record_fingerprint_length", type_="check")
        batch_op.drop_constraint("source_type_allowed", type_="check")
        batch_op.drop_column("source_record_fingerprint")
        batch_op.drop_column("source_type")


def _downgrade_postgresql() -> None:
    op.drop_index(
        "uq_health_metrics_person_source_fingerprint",
        table_name="health_metrics",
    )
    op.drop_constraint(
        "source_record_fingerprint_length",
        "health_metrics",
        type_="check",
    )
    op.drop_constraint(
        "source_type_allowed",
        "health_metrics",
        type_="check",
    )
    op.drop_column("health_metrics", "source_record_fingerprint")
    op.drop_column("health_metrics", "source_type")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _downgrade_sqlite()
    else:
        _downgrade_postgresql()
