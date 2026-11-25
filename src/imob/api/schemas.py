"""HTTP request and response schemas for the Imob REST API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from imob.domain.enums import FurnishedStatus, ListingPurpose, SortOrder
from imob.domain.models import MAX_EXCLUDE_KEYS, MAX_INCLUDE_KEYS, MAX_QUERY_LENGTH, ListingKey

MAX_TYPE_VALUES = 32
MAX_NEIGHBORHOOD_VALUES = 200
MAX_AGENCY_VALUES = 64

_ShortText = Annotated[str, StringConstraints(max_length=120)]


class ErrorResponse(BaseModel):
    """Standardized JSON error envelope."""

    model_config = ConfigDict(frozen=True)

    error: str
    message: str


class PropertySearchRequest(BaseModel):
    """Read-only property search filters shared by `GET /api/properties` and `POST /api/properties/search`.

    Only the POST body may carry `include_keys`/`exclude_keys`, the browser's local favorites and
    discarded listings, identified by stable agency + listing ID keys. Both lists are bounded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    q: str | None = Field(default=None, max_length=MAX_QUERY_LENGTH)
    purpose: ListingPurpose | None = None
    types: tuple[_ShortText, ...] = Field(default=(), max_length=MAX_TYPE_VALUES)
    neighborhoods: tuple[_ShortText, ...] = Field(default=(), max_length=MAX_NEIGHBORHOOD_VALUES)
    agencies: tuple[_ShortText, ...] = Field(default=(), max_length=MAX_AGENCY_VALUES)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    max_monthly_total: float | None = Field(default=None, ge=0)
    max_price_per_m2: float | None = Field(default=None, ge=0)
    min_area: float | None = Field(default=None, ge=0)
    max_area: float | None = Field(default=None, ge=0)
    min_bedrooms: int | None = Field(default=None, ge=0)
    min_suites: int | None = Field(default=None, ge=0)
    min_bathrooms: int | None = Field(default=None, ge=0)
    min_parking: int | None = Field(default=None, ge=0)
    furnished: FurnishedStatus | None = None
    accepts_pets: bool | None = None
    has_barbecue: bool | None = None
    has_balcony: bool | None = None
    has_elevator: bool | None = None
    has_pool: bool | None = None
    has_yard: bool | None = None
    accepts_financing: bool | None = None
    only_with_price: bool = False
    only_price_drops: bool = False
    include_inactive: bool = False
    only_similarity_candidates: bool = False
    deduplicate: bool = False
    sort: SortOrder = SortOrder.PRICE_ASC
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=120)
    include_keys: tuple[ListingKey, ...] | None = Field(default=None, max_length=MAX_INCLUDE_KEYS)
    exclude_keys: tuple[ListingKey, ...] = Field(default=(), max_length=MAX_EXCLUDE_KEYS)
