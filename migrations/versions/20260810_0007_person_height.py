"""Add nullable current height to persons."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0007"
down_revision: str | None = "20260803_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("persons", sa.Column("height_cm", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("persons", "height_cm")
