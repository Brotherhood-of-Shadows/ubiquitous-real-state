"""Initial relational schema and SQLite FTS5 search index for Imob PB."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Apply initial tables, constraints, indexes, and FTS5 triggers."""
    op.create_table(
        "agencies",
        sa.Column("slug", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=24), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("slug"),
    )

    op.create_table(
        "properties",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agency_slug", sa.String(length=48), nullable=False),
        sa.Column("external_id", sa.String(length=96), nullable=False),
        sa.Column("ref_code", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("whatsapp_url", sa.String(length=512), nullable=True),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("property_type", sa.String(length=32), nullable=False),
        sa.Column("raw_type", sa.String(length=96), nullable=False),
        sa.Column("raw_purpose", sa.String(length=64), nullable=False),
        sa.Column("type_reconciled", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("neighborhood", sa.String(length=100), nullable=True),
        sa.Column("neighborhood_slug", sa.String(length=100), nullable=True),
        sa.Column("street", sa.String(length=180), nullable=True),
        sa.Column("street_number", sa.String(length=32), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("building_name", sa.String(length=120), nullable=True),
        sa.Column("sale_price", sa.Float(), nullable=True),
        sa.Column("rent_price", sa.Float(), nullable=True),
        sa.Column("condo_fee", sa.Float(), nullable=True),
        sa.Column("iptu_fee", sa.Float(), nullable=True),
        sa.Column("insurance_fee", sa.Float(), nullable=True),
        sa.Column("effective_monthly_total", sa.Float(), nullable=True),
        sa.Column("price_per_m2", sa.Float(), nullable=True),
        sa.Column("price_on_request", sa.Boolean(), nullable=False),
        sa.Column("previous_price", sa.Float(), nullable=True),
        sa.Column("price_delta_pct", sa.Float(), nullable=True),
        sa.Column("area_private_m2", sa.Float(), nullable=True),
        sa.Column("area_built_m2", sa.Float(), nullable=True),
        sa.Column("area_total_m2", sa.Float(), nullable=True),
        sa.Column("usable_area_m2", sa.Float(), nullable=True),
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
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("cover_image_url", sa.String(length=1024), nullable=True),
        sa.Column("image_urls_json", sa.Text(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("dedup_group_key", sa.String(length=160), nullable=True),
        sa.Column("fingerprint_hash", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), nullable=False),
        sa.Column("user_notes", sa.Text(), nullable=True),
        sa.Column("detail_enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sale_price IS NULL OR sale_price > 0", name="ck_properties_sale_pos"),
        sa.CheckConstraint("rent_price IS NULL OR rent_price > 0", name="ck_properties_rent_pos"),
        sa.CheckConstraint("usable_area_m2 IS NULL OR usable_area_m2 > 0", name="ck_properties_area_pos"),
        sa.ForeignKeyConstraint(["agency_slug"], ["agencies.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agency_slug", "external_id", name="uq_properties_agency_external"),
    )
    op.create_index(
        "ix_properties_active_purpose_type",
        "properties",
        ["is_active", "is_hidden", "purpose", "property_type"],
    )
    op.create_index("ix_properties_neighborhood", "properties", ["is_active", "neighborhood_slug"])
    op.create_index("ix_properties_sale_price", "properties", ["is_active", "sale_price"])
    op.create_index("ix_properties_rent_price", "properties", ["is_active", "rent_price"])
    op.create_index("ix_properties_monthly_total", "properties", ["is_active", "effective_monthly_total"])
    op.create_index("ix_properties_dedup_key", "properties", ["is_active", "dedup_group_key"])
    op.create_index("ix_properties_favorite", "properties", ["is_favorite"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sale_price", sa.Float(), nullable=True),
        sa.Column("rent_price", sa.Float(), nullable=True),
        sa.Column("condo_fee", sa.Float(), nullable=True),
        sa.Column("iptu_fee", sa.Float(), nullable=True),
        sa.Column("effective_monthly_total", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_property_time", "price_history", ["property_id", "recorded_at"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agency_slug", sa.String(length=48), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requests_made", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("price_changes_count", sa.Integer(), nullable=False),
        sa.Column("deactivated_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agency_slug"], ["agencies.slug"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_agency_started", "sync_runs", ["agency_slug", "started_at"])

    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS properties_fts USING fts5(
            title,
            neighborhood,
            street,
            building_name,
            ref_code,
            description,
            content='properties',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_properties_fts_ai AFTER INSERT ON properties BEGIN
            INSERT INTO properties_fts(rowid, title, neighborhood, street, building_name, ref_code, description)
            VALUES (new.id, coalesce(new.title, ''), coalesce(new.neighborhood, ''), coalesce(new.street, ''),
                    coalesce(new.building_name, ''), coalesce(new.ref_code, ''), coalesce(new.description, ''));
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_properties_fts_ad AFTER DELETE ON properties BEGIN
            INSERT INTO properties_fts(properties_fts, rowid, title, neighborhood, street, building_name, ref_code, description)
            VALUES ('delete', old.id, coalesce(old.title, ''), coalesce(old.neighborhood, ''), coalesce(old.street, ''),
                    coalesce(old.building_name, ''), coalesce(old.ref_code, ''), coalesce(old.description, ''));
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_properties_fts_au AFTER UPDATE ON properties BEGIN
            INSERT INTO properties_fts(properties_fts, rowid, title, neighborhood, street, building_name, ref_code, description)
            VALUES ('delete', old.id, coalesce(old.title, ''), coalesce(old.neighborhood, ''), coalesce(old.street, ''),
                    coalesce(old.building_name, ''), coalesce(old.ref_code, ''), coalesce(old.description, ''));
            INSERT INTO properties_fts(rowid, title, neighborhood, street, building_name, ref_code, description)
            VALUES (new.id, coalesce(new.title, ''), coalesce(new.neighborhood, ''), coalesce(new.street, ''),
                    coalesce(new.building_name, ''), coalesce(new.ref_code, ''), coalesce(new.description, ''));
        END;
        """
    )


def downgrade() -> None:
    """Drop FTS5 triggers, virtual table, and relational tables cleanly."""
    op.execute("DROP TRIGGER IF EXISTS trg_properties_fts_au")
    op.execute("DROP TRIGGER IF EXISTS trg_properties_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS trg_properties_fts_ai")
    op.execute("DROP TABLE IF EXISTS properties_fts")
    op.drop_index("ix_sync_runs_agency_started", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index("ix_price_history_property_time", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_properties_favorite", table_name="properties")
    op.drop_index("ix_properties_dedup_key", table_name="properties")
    op.drop_index("ix_properties_monthly_total", table_name="properties")
    op.drop_index("ix_properties_rent_price", table_name="properties")
    op.drop_index("ix_properties_sale_price", table_name="properties")
    op.drop_index("ix_properties_neighborhood", table_name="properties")
    op.drop_index("ix_properties_active_purpose_type", table_name="properties")
    op.drop_table("properties")
    op.drop_table("agencies")
