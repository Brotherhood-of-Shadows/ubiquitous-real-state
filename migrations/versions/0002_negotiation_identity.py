"""Add proposal support and invalidate unsafe area-only identity groups."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_negotiation_identity"
down_revision: str | None = "0001_initial_schema"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Preserve listings and curation; obsolete groups are rebuilt on the next sync."""
    op.add_column(
        "properties",
        sa.Column("accepts_proposals", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE properties SET dedup_group_key = NULL")


def downgrade() -> None:
    """Remove proposal support without restoring known incorrect matches."""
    op.drop_column("properties", "accepts_proposals")
