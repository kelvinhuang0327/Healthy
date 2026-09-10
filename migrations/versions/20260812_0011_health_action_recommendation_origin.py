"""Add structured recommendation provenance to health actions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0011"
down_revision: str | None = "20260811_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("health_actions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "origin_type",
                sa.String(length=32),
                server_default=sa.text("'manual'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("recommendation_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("recommendation_code", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("recommendation_rule_version", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(sa.Column("source_rule_code", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("source_evidence_kind", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("source_evidence_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(
            sa.Column("source_observation_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.add_column(sa.Column("source_report_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(
            sa.Column("source_evidence_observed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "origin_type_allowed",
            "origin_type IN ('manual', 'action_recommendation')",
        )
        batch_op.create_check_constraint(
            "recommendation_fingerprint_length",
            "recommendation_fingerprint IS NULL OR length(recommendation_fingerprint) = 64",
        )
        batch_op.create_check_constraint(
            "recommendation_provenance_consistent",
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
        batch_op.create_index(
            "uq_health_actions_person_recommendation_fingerprint",
            ["person_id", "recommendation_fingerprint"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("health_actions") as batch_op:
        batch_op.drop_index("uq_health_actions_person_recommendation_fingerprint")
        batch_op.drop_constraint("recommendation_provenance_consistent", type_="check")
        batch_op.drop_constraint("recommendation_fingerprint_length", type_="check")
        batch_op.drop_constraint("origin_type_allowed", type_="check")
        batch_op.drop_column("source_evidence_observed_at")
        batch_op.drop_column("source_report_id")
        batch_op.drop_column("source_observation_id")
        batch_op.drop_column("source_evidence_id")
        batch_op.drop_column("source_evidence_kind")
        batch_op.drop_column("source_rule_code")
        batch_op.drop_column("recommendation_rule_version")
        batch_op.drop_column("recommendation_code")
        batch_op.drop_column("recommendation_fingerprint")
        batch_op.drop_column("origin_type")
