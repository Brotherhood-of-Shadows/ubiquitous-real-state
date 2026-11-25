"""SQLAlchemy 2.0 typed ORM declarations for agencies, properties, history, and sync logs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all relational tables."""


class AgencyRow(Base):
    """Persisted real-estate agency metadata and synchronization state."""

    __tablename__ = "agencies"

    slug: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    properties: Mapped[list[PropertyRow]] = relationship(
        back_populates="agency",
        cascade="all, delete-orphan",
    )


class PropertyRow(Base):
    """Persisted normalized property listing in Pato Branco."""

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("agency_slug", "external_id", name="uq_properties_agency_external"),
        CheckConstraint("sale_price IS NULL OR sale_price > 0", name="ck_properties_sale_pos"),
        CheckConstraint("rent_price IS NULL OR rent_price > 0", name="ck_properties_rent_pos"),
        CheckConstraint("usable_area_m2 IS NULL OR usable_area_m2 > 0", name="ck_properties_area_pos"),
        Index("ix_properties_active_purpose_type", "is_active", "is_hidden", "purpose", "property_type"),
        Index("ix_properties_neighborhood", "is_active", "neighborhood_slug"),
        Index("ix_properties_sale_price", "is_active", "sale_price"),
        Index("ix_properties_rent_price", "is_active", "rent_price"),
        Index("ix_properties_monthly_total", "is_active", "effective_monthly_total"),
        Index("ix_properties_dedup_key", "is_active", "dedup_group_key"),
        Index("ix_properties_favorite", "is_favorite"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agency_slug: Mapped[str] = mapped_column(
        String(48),
        ForeignKey("agencies.slug", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(96), nullable=False)
    ref_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    whatsapp_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    property_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_type: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    raw_purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    type_reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    city: Mapped[str] = mapped_column(String(80), nullable=False, default="Pato Branco")
    state: Mapped[str] = mapped_column(String(8), nullable=False, default="PR")
    neighborhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    neighborhood_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    street: Mapped[str | None] = mapped_column(String(180), nullable=True)
    street_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    building_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    sale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    condo_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    iptu_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    insurance_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_monthly_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_per_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_on_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    previous_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    area_private_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_built_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_total_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)

    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suites: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_spots: Mapped[int | None] = mapped_column(Integer, nullable=True)

    furnished_status: Mapped[str] = mapped_column(String(24), nullable=False, default="nao_informado")
    accepts_pets: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_elevator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_pool: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_barbecue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_balcony: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_air_conditioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_solar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_yard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_financing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_exchange: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    accepts_proposals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    cover_image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_urls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    dedup_group_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fingerprint_hash: Mapped[str] = mapped_column(String(32), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    detail_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agency: Mapped[AgencyRow] = relationship(back_populates="properties")
    price_history: Mapped[list[PriceHistoryRow]] = relationship(
        back_populates="property",
        cascade="all, delete-orphan",
        order_by="PriceHistoryRow.recorded_at.desc()",
    )


class PriceHistoryRow(Base):
    """Historical snapshot of prices for a property."""

    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_property_time", "property_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    condo_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    iptu_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_monthly_total: Mapped[float | None] = mapped_column(Float, nullable=True)

    property: Mapped[PropertyRow] = relationship(back_populates="price_history")


class SyncRunRow(Base):
    """Audit record of each agency synchronization execution."""

    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_agency_started", "agency_slug", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agency_slug: Mapped[str] = mapped_column(
        String(48),
        ForeignKey("agencies.slug", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    requests_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_changes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deactivated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class PropertyMatchRow(Base):
    """Derived pair evidence rebuilt atomically alongside automatic groups."""

    __tablename__ = "property_matches"
    __table_args__ = (
        CheckConstraint("left_id < right_id", name="ck_match_order"),
        CheckConstraint("level IN ('probable', 'review')", name="ck_match_level"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_match_score"),
        Index("ix_property_matches_right", "right_id"),
        Index("ix_property_matches_level", "level"),
    )
    left_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True)
    right_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainties_json: Mapped[str] = mapped_column(Text, nullable=False)
