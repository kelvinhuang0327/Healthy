"""Add structured recommendation provenance to health actions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0011"
down_revision: str | None = "20260811_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "health_actions",
        sa.Column(
            "origin_type",
            sa.String(length=32),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )
    op.add_column(
        "health_actions",
        sa.Column("recommendation_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "health_actions",
        sa.Column("recommendation_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "health_actions",
        sa.Column("recommendation_rule_version", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "health_actions",
        sa.Column("source_rule_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "health_actions",
        sa.Column("source_evidence_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "health_actions",
        sa.Column(
            "source_evidence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "health_actions",
        sa.Column(
            "source_observation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "health_actions",
        sa.Column(
            "source_report_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "health_actions",
        sa.Column(
            "source_evidence_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "origin_type_allowed",
        "health_actions",
        "origin_type IN ('manual', 'action_recommendation')",
    )
    op.create_check_constraint(
        "recommendation_fingerprint_length",
        "health_actions",
        "recommendation_fingerprint IS NULL OR char_length(recommendation_fingerprint) = 64",
    )
    op.create_check_constraint(
        "recommendation_provenance_consistent",
        "health_actions",
        "(origin_type = 'manual'"
        " AND recommendation_fingerprint IS NULL"
        " AND recommendation_code IS NULL"
        " AND recommendation_rule_version IS NULL"
        " AND source_rule_code IS NULL"
        " AND source_evidence_kind IS NULL"
        " AND source_evidence_id IS NULL"
        " AND source_observation_id IS NULL"
        " AND source_report_id IS NULL"
        " AND source_evidence_observed_at IS NULL)"
        " OR (origin_type = 'action_recommendation'"
        " AND recommendation_fingerprint IS NOT NULL"
        " AND recommendation_code IS NOT NULL"
        " AND recommendation_rule_version IS NOT NULL"
        " AND source_rule_code IS NOT NULL"
        " AND source_evidence_kind IS NOT NULL"
        " AND source_evidence_id IS NOT NULL"
        " AND source_evidence_observed_at IS NOT NULL)",
    )
    op.create_index(
        "uq_health_actions_person_recommendation_fingerprint",
        "health_actions",
        ["person_id", "recommendation_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_health_actions_person_recommendation_fingerprint",
        table_name="health_actions",
    )
    op.drop_constraint(
        "recommendation_provenance_consistent",
        "health_actions",
        type_="check",
    )
    op.drop_constraint(
        "recommendation_fingerprint_length",
        "health_actions",
        type_="check",
    )
    op.drop_constraint(
        "origin_type_allowed",
        "health_actions",
        type_="check",
    )
    op.drop_column("health_actions", "source_evidence_observed_at")
    op.drop_column("health_actions", "source_report_id")
    op.drop_column("health_actions", "source_observation_id")
    op.drop_column("health_actions", "source_evidence_id")
    op.drop_column("health_actions", "source_evidence_kind")
    op.drop_column("health_actions", "source_rule_code")
    op.drop_column("health_actions", "recommendation_rule_version")
    op.drop_column("health_actions", "recommendation_code")
    op.drop_column("health_actions", "recommendation_fingerprint")
    op.drop_column("health_actions", "origin_type")
