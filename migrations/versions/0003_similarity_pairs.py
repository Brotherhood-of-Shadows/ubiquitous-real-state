"""Persist derived similarity evidence separately from automatic duplicate groups."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_similarity_pairs"
down_revision: str | None = "0002_negotiation_identity"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Create rebuildable pair evidence without modifying source listings or personal state."""
    op.create_table(
        "property_matches",
        sa.Column(
            "left_id", sa.Integer(), sa.ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "right_id", sa.Integer(), sa.ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("uncertainties_json", sa.Text(), nullable=False),
        sa.CheckConstraint("left_id < right_id", name="ck_match_order"),
        sa.CheckConstraint("level IN ('probable', 'review')", name="ck_match_level"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_match_score"),
    )
    op.create_index("ix_property_matches_right", "property_matches", ["right_id"])
    op.create_index("ix_property_matches_level", "property_matches", ["level"])


def downgrade() -> None:
    """Drop derived pair evidence; source listings remain intact."""
    op.drop_table("property_matches")
