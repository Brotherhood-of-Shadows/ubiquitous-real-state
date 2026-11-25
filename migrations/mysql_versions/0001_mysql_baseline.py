"""MySQL 8.4 baseline schema for Imob PB 0.2.0 (fresh database, no data migration).

Replaces the retired pre-0.2.0 history kept in `migrations/versions/`. Differences from that schema:
no server-side personal state (favorites, hidden flag, notes), DOUBLE money/area/coordinate columns,
UTC `DATETIME(6)` timestamps, byte-exact (`utf8mb4_0900_bin`, NO PAD) identifiers, and an InnoDB
FULLTEXT index in place of the old full-text virtual table. `property_matches` keeps its cascading
foreign keys but not the `left_id < right_id` CHECK, which MySQL rejects (error 3823). The repository
enforces that ordering.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_mysql_baseline"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}
_BIN = "utf8mb4_0900_bin"  # NO PAD binary: "ab", "AB" and "ab " are distinct IDs.


def _money(name: str) -> sa.Column[float]:
    return sa.Column(name, sa.Double(), nullable=True)


def _datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def upgrade() -> None:
    """Create all tables, constraints, secondary indexes, and the FULLTEXT search index."""
    op.create_table(
        "agencies",
        sa.Column("slug", sa.String(length=48, collation=_BIN), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", _datetime(), nullable=True),
        sa.Column("last_sync_status", sa.String(length=24), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("slug"),
        **_TABLE_OPTIONS,
    )

    op.create_table(
        "properties",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agency_slug", sa.String(length=48, collation=_BIN), nullable=False),
        sa.Column("external_id", sa.String(length=96, collation=_BIN), nullable=False),
        sa.Column("ref_code", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("whatsapp_url", sa.String(length=512), nullable=True),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("property_type", sa.String(length=32), nullable=False),
        sa.Column("raw_type", sa.String(length=96), nullable=False),
        sa.Column("raw_purpose", sa.String(length=64), nullable=False),
        sa.Column("type_reconciled", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("neighborhood", sa.String(length=100), nullable=True),
        sa.Column("neighborhood_slug", sa.String(length=100), nullable=True),
        sa.Column("street", sa.String(length=180), nullable=True),
        sa.Column("street_number", sa.String(length=32), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("building_name", sa.String(length=120), nullable=True),
        _money("sale_price"),
        _money("rent_price"),
        _money("condo_fee"),
        _money("iptu_fee"),
        _money("insurance_fee"),
        _money("effective_monthly_total"),
        _money("price_per_m2"),
        sa.Column("price_on_request", sa.Boolean(), nullable=False),
        _money("previous_price"),
        _money("price_delta_pct"),
        _money("area_private_m2"),
        _money("area_built_m2"),
        _money("area_total_m2"),
        _money("usable_area_m2"),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("suites", sa.Integer(), nullable=True),
        sa.Column("bathrooms", sa.Integer(), nullable=True),
        sa.Column("parking_spots", sa.Integer(), nullable=True),
        sa.Column("furnished_status", sa.String(length=24), nullable=False),
        sa.Column("accepts_pets", sa.Boolean(), nullable=False),
        sa.Column("has_elevator", sa.Boolean(), nullable=False),
        sa.Column("has_pool", sa.Boolean(), nullable=False),
        sa.Column("has_barbecue", sa.Boolean(), nullable=False),
        sa.Column("has_balcony", sa.Boolean(), nullable=False),
        sa.Column("has_air_conditioning", sa.Boolean(), nullable=False),
        sa.Column("has_solar", sa.Boolean(), nullable=False),
        sa.Column("has_yard", sa.Boolean(), nullable=False),
        sa.Column("accepts_financing", sa.Boolean(), nullable=False),
        sa.Column("accepts_exchange", sa.Boolean(), nullable=False),
        sa.Column("accepts_proposals", sa.Boolean(), nullable=False),
        _money("latitude"),
        _money("longitude"),
        sa.Column("cover_image_url", sa.String(length=1024), nullable=True),
        sa.Column("image_urls_json", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("dedup_group_key", sa.String(length=160, collation=_BIN), nullable=True),
        sa.Column("fingerprint_hash", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("detail_enriched_at", _datetime(), nullable=True),
        sa.Column("first_seen_at", _datetime(), nullable=False),
        sa.Column("last_seen_at", _datetime(), nullable=False),
        sa.CheckConstraint("sale_price IS NULL OR sale_price > 0", name="ck_properties_sale_pos"),
        sa.CheckConstraint("rent_price IS NULL OR rent_price > 0", name="ck_properties_rent_pos"),
        sa.CheckConstraint("usable_area_m2 IS NULL OR usable_area_m2 > 0", name="ck_properties_area_pos"),
        sa.ForeignKeyConstraint(["agency_slug"], ["agencies.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agency_slug", "external_id", name="uq_properties_agency_external"),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_properties_active_purpose_type", "properties", ["is_active", "purpose", "property_type"]
    )
    op.create_index("ix_properties_neighborhood", "properties", ["is_active", "neighborhood_slug"])
    op.create_index("ix_properties_sale_price", "properties", ["is_active", "sale_price"])
    op.create_index("ix_properties_rent_price", "properties", ["is_active", "rent_price"])
    op.create_index("ix_properties_monthly_total", "properties", ["is_active", "effective_monthly_total"])
    op.create_index("ix_properties_dedup_key", "properties", ["is_active", "dedup_group_key"])
    # Column list must equal the MATCH(...) list in the repository search.
    op.create_index(
        "ft_properties_search",
        "properties",
        ["title", "neighborhood", "street", "building_name", "ref_code", "description"],
        mysql_prefix="FULLTEXT",
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", _datetime(), nullable=False),
        _money("sale_price"),
        _money("rent_price"),
        _money("condo_fee"),
        _money("iptu_fee"),
        _money("effective_monthly_total"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_OPTIONS,
    )
    op.create_index("ix_price_history_property_time", "price_history", ["property_id", "recorded_at"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agency_slug", sa.String(length=48, collation=_BIN), nullable=False),
        sa.Column("started_at", _datetime(), nullable=False),
        sa.Column("finished_at", _datetime(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requests_made", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("price_changes_count", sa.Integer(), nullable=False),
        sa.Column("deactivated_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agency_slug"], ["agencies.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_OPTIONS,
    )
    op.create_index("ix_sync_runs_agency_started", "sync_runs", ["agency_slug", "started_at"])

    op.create_table(
        "property_matches",
        sa.Column("left_id", sa.Integer(), nullable=False),
        sa.Column("right_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("uncertainties_json", sa.Text(), nullable=False),
        sa.CheckConstraint("level IN ('probable', 'review')", name="ck_match_level"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_match_score"),
        sa.ForeignKeyConstraint(["left_id"], ["properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["right_id"], ["properties.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("left_id", "right_id"),
        **_TABLE_OPTIONS,
    )
    op.create_index("ix_property_matches_right", "property_matches", ["right_id"])
    op.create_index("ix_property_matches_level", "property_matches", ["level"])


def downgrade() -> None:
    """Drop every baseline table (children first); the database itself is left in place."""
    op.drop_table("property_matches")
    op.drop_table("sync_runs")
    op.drop_table("price_history")
    op.drop_table("properties")
    op.drop_table("agencies")
