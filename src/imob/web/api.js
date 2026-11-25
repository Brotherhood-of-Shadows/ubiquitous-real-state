/**
 * HTTP boundary adapter and runtime payload validators for the Imob REST API.
 * @module api
 */

const DEFAULT_TIMEOUT_MS = 20_000;

/**
 * @typedef {Object} CatalogStats
 * @property {number} active_properties
 * @property {number} inactive_properties
 * @property {number} rent_count
 * @property {number} sale_count
 * @property {number} both_count
 * @property {number} agencies_total
 * @property {number} agencies_synced
 * @property {number} price_drops_count
 * @property {number} similarity_review_pairs_count
 * @property {number} duplicate_groups_count
 * @property {string|null} last_global_sync_at
 */

/**
 * @typedef {Object} FacetBucket
 * @property {string} value
 * @property {string} label
 * @property {number} count
 */

/**
 * @typedef {Object} SearchFacets
 * @property {FacetBucket[]} property_types
 * @property {FacetBucket[]} neighborhoods
 * @property {FacetBucket[]} agencies
 * @property {FacetBucket[]} purposes
 */

/**
 * @typedef {Object} AgencyStatusView
 * @property {string} slug
 * @property {string} name
 * @property {string} base_url
 * @property {string} platform
 * @property {string|null} last_synced_at
 * @property {string|null} last_sync_status
 * @property {string|null} last_error
 * @property {number} active_properties
 * @property {number} total_properties
 */

/**
 * @typedef {Object} PropertySummary
 * @property {number} id
 * @property {string} agency_slug
 * @property {string} agency_name
 * @property {string} external_id
 * @property {string|null} ref_code
 * @property {string} source_url
 * @property {string|null} whatsapp_url
 * @property {string} purpose
 * @property {string} property_type
 * @property {string} raw_type
 * @property {boolean} type_reconciled
 * @property {string} title
 * @property {string} city
 * @property {string} state
 * @property {string|null} neighborhood
 * @property {string|null} street
 * @property {string|null} building_name
 * @property {number|null} sale_price
 * @property {number|null} rent_price
 * @property {number|null} condo_fee
 * @property {number|null} iptu_fee
 * @property {number|null} effective_monthly_total
 * @property {number|null} price_per_m2
 * @property {boolean} price_on_request
 * @property {number|null} previous_price
 * @property {number|null} price_delta_pct
 * @property {number|null} area_private_m2
 * @property {number|null} area_built_m2
 * @property {number|null} area_total_m2
 * @property {number|null} usable_area_m2
 * @property {number|null} bedrooms
 * @property {number|null} suites
 * @property {number|null} bathrooms
 * @property {number|null} parking_spots
 * @property {string} furnished_status
 * @property {boolean} accepts_pets
 * @property {boolean} has_elevator
 * @property {boolean} has_pool
 * @property {boolean} has_barbecue
 * @property {boolean} has_balcony
 * @property {boolean} has_air_conditioning
 * @property {boolean} has_solar
 * @property {boolean} has_yard
 * @property {boolean} accepts_financing
 * @property {boolean} accepts_proposals
 * @property {boolean} accepts_exchange
 * @property {number|null} latitude
 * @property {number|null} longitude
 * @property {string|null} cover_image_url
 * @property {number} image_count
 * @property {string|null} dedup_group_key
 * @property {number} duplicate_count
 * @property {number|null} min_group_price
 * @property {boolean} is_active
 * @property {string|null} detail_enriched_at
 * @property {string} first_seen_at
 * @property {string} last_seen_at
 */

/**
 * @typedef {Object} PriceHistoryEntry
 * @property {string} recorded_at
 * @property {number|null} sale_price
 * @property {number|null} rent_price
 * @property {number|null} condo_fee
 * @property {number|null} iptu_fee
 * @property {number|null} effective_monthly_total
 */

/**
 * @typedef {Object} DuplicatePeer
 * @property {number} id
 * @property {string} agency_slug
 * @property {string} agency_name
 * @property {string|null} ref_code
 * @property {number|null} sale_price
 * @property {number|null} rent_price
 * @property {number|null} effective_monthly_total
 * @property {number} match_score
 * @property {string[]} match_uncertainties
 * @property {string[]} match_reasons
 * @property {string} source_url
 */

/**
 * @typedef {PropertySummary & {
 *   description: string|null,
 *   street_number: string|null,
 *   postal_code: string|null,
 *   insurance_fee: number|null,
 *   image_urls: string[],
 *   price_history: PriceHistoryEntry[],
 *   similarity_candidates: DuplicatePeer[],
 *   similarity_candidates_total: number,
 *   duplicate_peers: DuplicatePeer[]
 * }} PropertyDetail
 */

/**
 * @typedef {Object} PaginatedProperties
 * @property {number} total
 * @property {number} page
 * @property {number} page_size
 * @property {number} total_pages
 * @property {PropertySummary[]} items
 */

/**
 * @typedef {Object} SyncRuntimeStatus
 * @property {boolean} is_running
 * @property {number} completed_agencies
 * @property {number} total_agencies
 * @property {string[]} active_agencies
 * @property {boolean} auto_sync_enabled
 * @property {string[]} sync_schedule
 * @property {string} sync_timezone
 * @property {string|null} next_scheduled_run_at
 */

/**
 * Domain error representing failed HTTP communication or malformed API payloads.
 */
export class ApiError extends Error {
  /**
   * @param {string} message Safe human-readable failure description.
   * @param {{ code?: string, status?: number, cause?: unknown }} [options]
   */
  constructor(message, options = {}) {
    super(message, options.cause !== undefined ? { cause: options.cause } : undefined);
    this.name = "ApiError";
    this.code = options.code ?? "api_error";
    this.status = options.status ?? 0;
  }
}

/**
 * @param {unknown} value
 * @param {string} label
 * @returns {Record<string, unknown>}
 */
function asRecord(value, label) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ApiError(`Invalid object payload for ${label}`, { code: "invalid_payload" });
  }
  return /** @type {Record<string, unknown>} */ (value);
}

/**
 * @param {unknown} value
 * @param {string} field
 * @returns {string}
 */
function asString(value, field) {
  if (typeof value !== "string") {
    throw new ApiError(`Expected string in field '${field}'`, { code: "invalid_payload" });
  }
  return value;
}

/**
 * @param {unknown} value
 * @returns {string|null}
 */
function asNullableString(value) {
  return typeof value === "string" ? value : null;
}

/**
 * @param {unknown} value
 * @param {string} field
 * @returns {number}
 */
function asNumber(value, field) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ApiError(`Expected finite number in field '${field}'`, { code: "invalid_payload" });
  }
  return value;
}

/**
 * @param {unknown} value
 * @returns {number|null}
 */
function asNullableNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * @param {unknown} value
 * @returns {boolean}
 */
function asBoolean(value) {
  return Boolean(value);
}

/**
 * @template T
 * @param {unknown} value
 * @param {(item: unknown) => T} mapper
 * @param {string} field
 * @returns {T[]}
 */
function asArray(value, mapper, field) {
  if (!Array.isArray(value)) {
    throw new ApiError(`Expected array in field '${field}'`, { code: "invalid_payload" });
  }
  return value.map(mapper);
}

/**
 * @param {unknown} raw
 * @returns {FacetBucket}
 */
function parseFacetBucket(raw) {
  const rec = asRecord(raw, "FacetBucket");
  return {
    value: asString(rec.value, "FacetBucket.value"),
    label: asString(rec.label, "FacetBucket.label"),
    count: asNumber(rec.count, "FacetBucket.count"),
  };
}

/**
 * @param {unknown} raw
 * @returns {PropertySummary}
 */
function parsePropertySummary(raw) {
  const r = asRecord(raw, "PropertySummary");
  return {
    id: asNumber(r.id, "PropertySummary.id"),
    agency_slug: asString(r.agency_slug, "PropertySummary.agency_slug"),
    agency_name: asString(r.agency_name, "PropertySummary.agency_name"),
    external_id: asString(r.external_id, "PropertySummary.external_id"),
    ref_code: asNullableString(r.ref_code),
    source_url: asString(r.source_url, "PropertySummary.source_url"),
    whatsapp_url: asNullableString(r.whatsapp_url),
    purpose: asString(r.purpose, "PropertySummary.purpose"),
    property_type: asString(r.property_type, "PropertySummary.property_type"),
    raw_type: asString(r.raw_type, "PropertySummary.raw_type"),
    type_reconciled: asBoolean(r.type_reconciled),
    title: asString(r.title, "PropertySummary.title"),
    city: asString(r.city, "PropertySummary.city"),
    state: asString(r.state, "PropertySummary.state"),
    neighborhood: asNullableString(r.neighborhood),
    street: asNullableString(r.street),
    building_name: asNullableString(r.building_name),
    sale_price: asNullableNumber(r.sale_price),
    rent_price: asNullableNumber(r.rent_price),
    condo_fee: asNullableNumber(r.condo_fee),
    iptu_fee: asNullableNumber(r.iptu_fee),
    effective_monthly_total: asNullableNumber(r.effective_monthly_total),
    price_per_m2: asNullableNumber(r.price_per_m2),
    price_on_request: asBoolean(r.price_on_request),
    previous_price: asNullableNumber(r.previous_price),
    price_delta_pct: asNullableNumber(r.price_delta_pct),
    area_private_m2: asNullableNumber(r.area_private_m2),
    area_built_m2: asNullableNumber(r.area_built_m2),
    area_total_m2: asNullableNumber(r.area_total_m2),
    usable_area_m2: asNullableNumber(r.usable_area_m2),
    bedrooms: asNullableNumber(r.bedrooms),
    suites: asNullableNumber(r.suites),
    bathrooms: asNullableNumber(r.bathrooms),
    parking_spots: asNullableNumber(r.parking_spots),
    furnished_status: asString(r.furnished_status, "PropertySummary.furnished_status"),
    accepts_pets: asBoolean(r.accepts_pets),
    has_elevator: asBoolean(r.has_elevator),
    has_pool: asBoolean(r.has_pool),
    has_barbecue: asBoolean(r.has_barbecue),
    has_balcony: asBoolean(r.has_balcony),
    has_air_conditioning: asBoolean(r.has_air_conditioning),
    has_solar: asBoolean(r.has_solar),
    has_yard: asBoolean(r.has_yard),
    accepts_financing: asBoolean(r.accepts_financing),
    accepts_exchange: asBoolean(r.accepts_exchange),
    accepts_proposals: asBoolean(r.accepts_proposals),
    latitude: asNullableNumber(r.latitude),
    longitude: asNullableNumber(r.longitude),
    cover_image_url: asNullableString(r.cover_image_url),
    image_count: asNumber(r.image_count, "PropertySummary.image_count"),
    dedup_group_key: asNullableString(r.dedup_group_key),
    duplicate_count: asNullableNumber(r.duplicate_count) ?? 1,
    min_group_price: asNullableNumber(r.min_group_price),
    is_active: asBoolean(r.is_active),
    detail_enriched_at: asNullableString(r.detail_enriched_at),
    first_seen_at: asString(r.first_seen_at, "PropertySummary.first_seen_at"),
    last_seen_at: asString(r.last_seen_at, "PropertySummary.last_seen_at"),
  };
}

/**
 * Parse one probable or review candidate and its supporting evidence.
 * @param {unknown} peer
 * @returns {DuplicatePeer}
 */
function parseDuplicatePeer(peer) {
  const pr = asRecord(peer, "DuplicatePeer");
  return {
    id: asNumber(pr.id, "DuplicatePeer.id"),
    agency_slug: asString(pr.agency_slug, "DuplicatePeer.agency_slug"),
    agency_name: asString(pr.agency_name, "DuplicatePeer.agency_name"),
    ref_code: asNullableString(pr.ref_code),
    sale_price: asNullableNumber(pr.sale_price),
    rent_price: asNullableNumber(pr.rent_price),
    effective_monthly_total: asNullableNumber(pr.effective_monthly_total),
    source_url: asString(pr.source_url, "DuplicatePeer.source_url"),
    match_score: asNullableNumber(pr.match_score) ?? 0,
    match_uncertainties: asArray(pr.match_uncertainties ?? [],
      (reason) => asString(reason, "uncertainty"), "match_uncertainties"),
    match_reasons: asArray(pr.match_reasons ?? [],
      (reason) => asString(reason, "match reason"), "match_reasons"),
  };
}

/**
 * @param {unknown} raw
 * @returns {PropertyDetail}
 */
function parsePropertyDetail(raw) {
  const base = parsePropertySummary(raw);
  const r = asRecord(raw, "PropertyDetail");
  return {
    ...base,
    description: asNullableString(r.description),
    street_number: asNullableString(r.street_number),
    postal_code: asNullableString(r.postal_code),
    insurance_fee: asNullableNumber(r.insurance_fee),
    image_urls: asArray(r.image_urls ?? [], (item) => asString(item, "image_url"), "image_urls"),
    price_history: asArray(
      r.price_history ?? [],
      (entry) => {
        const er = asRecord(entry, "PriceHistoryEntry");
        return {
          recorded_at: asString(er.recorded_at, "PriceHistoryEntry.recorded_at"),
          sale_price: asNullableNumber(er.sale_price),
          rent_price: asNullableNumber(er.rent_price),
          condo_fee: asNullableNumber(er.condo_fee),
          iptu_fee: asNullableNumber(er.iptu_fee),
          effective_monthly_total: asNullableNumber(er.effective_monthly_total),
        };
      },
      "price_history"
    ),
    duplicate_peers: asArray(r.duplicate_peers ?? [], parseDuplicatePeer, "duplicate_peers"),
    similarity_candidates: asArray(r.similarity_candidates ?? [], parseDuplicatePeer, "similarity_candidates"),
    similarity_candidates_total: asNullableNumber(r.similarity_candidates_total) ?? 0,
  };
}

/**
 * Execute a JSON HTTP request with finite timeout and explicit error translation.
 *
 * @param {string} path Relative `api/...` path.
 * @param {{
 *   method?: string,
 *   body?: unknown,
 *   timeoutMs?: number,
 *   signal?: AbortSignal
 * }} [options]
 * @returns {Promise<unknown>}
 */
async function requestJson(path, options = {}) {
  const requestPath = path.replace(/^(\.\/|\/)+/, "");
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timerId = window.setTimeout(() => controller.abort(), timeoutMs);

  const onExternalAbort = () => controller.abort();
  if (options.signal) {
    if (options.signal.aborted) {
      window.clearTimeout(timerId);
      throw new ApiError("Request cancelled", { code: "aborted" });
    }
    options.signal.addEventListener("abort", onExternalAbort, { once: true });
  }

  try {
    /** @type {Record<string, string>} */
    const headers = { Accept: "application/json" };
    let serializedBody;
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      serializedBody = JSON.stringify(options.body);
    }

    const response = await window.fetch(requestPath, {
      method: options.method ?? "GET",
      headers,
      body: serializedBody,
      signal: controller.signal,
    });

    const rawText = await response.text();
    /** @type {unknown} */
    let parsed = null;
    if (rawText.trim().length > 0) {
      try {
        parsed = JSON.parse(rawText);
      } catch (err) {
        throw new ApiError(`Malformed JSON response from ${requestPath}`, {
          code: "invalid_json",
          status: response.status,
          cause: err,
        });
      }
    }

    if (!response.ok) {
      let message = `HTTP ${response.status} ao consultar ${requestPath}`;
      let code = `http_${response.status}`;
      if (typeof parsed === "object" && parsed !== null) {
        const errObj = /** @type {Record<string, unknown>} */ (parsed);
        if (typeof errObj.message === "string") {
          message = errObj.message;
        } else if (typeof errObj.detail === "string") {
          message = errObj.detail;
        }
        if (typeof errObj.error === "string") {
          code = errObj.error;
        }
      }
      throw new ApiError(message, { code, status: response.status });
    }

    return parsed;
  } catch (err) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      if (options.signal?.aborted) {
        throw new ApiError("Request cancelled", { code: "aborted", cause: err });
      }
      throw new ApiError("Tempo limite excedido ao comunicar com a API local.", {
        code: "timeout",
        cause: err,
      });
    }
    throw new ApiError("Falha de rede ao comunicar com o servidor local.", {
      code: "network_error",
      cause: err,
    });
  } finally {
    window.clearTimeout(timerId);
    if (options.signal) {
      options.signal.removeEventListener("abort", onExternalAbort);
    }
  }
}

/**
 * Fetch global catalog metrics from `api/stats`.
 *
 * @returns {Promise<CatalogStats>}
 */
export async function fetchStats() {
  const raw = await requestJson("api/stats");
  const r = asRecord(raw, "CatalogStats");
  return {
    active_properties: asNumber(r.active_properties, "active_properties"),
    inactive_properties: asNumber(r.inactive_properties, "inactive_properties"),
    rent_count: asNumber(r.rent_count, "rent_count"),
    sale_count: asNumber(r.sale_count, "sale_count"),
    both_count: asNumber(r.both_count, "both_count"),
    agencies_total: asNumber(r.agencies_total, "agencies_total"),
    agencies_synced: asNumber(r.agencies_synced, "agencies_synced"),
    price_drops_count: asNumber(r.price_drops_count, "price_drops_count"),
    duplicate_groups_count: asNumber(r.duplicate_groups_count, "duplicate_groups_count"),
    similarity_review_pairs_count: asNullableNumber(r.similarity_review_pairs_count) ?? 0,
    last_global_sync_at: asNullableString(r.last_global_sync_at),
  };
}

/**
 * Fetch dynamic filter facets from `api/facets`.
 *
 * @param {string} [purpose] Optional purpose (`aluguel` or `venda`).
 * @returns {Promise<SearchFacets>}
 */
export async function fetchFacets(purpose = "") {
  const qs = purpose ? `?purpose=${encodeURIComponent(purpose)}` : "";
  const raw = await requestJson(`api/facets${qs}`);
  const r = asRecord(raw, "SearchFacets");
  return {
    property_types: asArray(r.property_types, parseFacetBucket, "property_types"),
    neighborhoods: asArray(r.neighborhoods, parseFacetBucket, "neighborhoods"),
    agencies: asArray(r.agencies, parseFacetBucket, "agencies"),
    purposes: asArray(r.purposes, parseFacetBucket, "purposes"),
  };
}

/**
 * Fetch registered agencies and their sync statuses.
 *
 * @returns {Promise<AgencyStatusView[]>}
 */
export async function fetchAgencies() {
  const raw = await requestJson("api/agencies");
  return asArray(
    raw,
    (item) => {
      const r = asRecord(item, "AgencyStatusView");
      return {
        slug: asString(r.slug, "slug"),
        name: asString(r.name, "name"),
        base_url: asString(r.base_url, "base_url"),
        platform: asString(r.platform, "platform"),
        last_synced_at: asNullableString(r.last_synced_at),
        last_sync_status: asNullableString(r.last_sync_status),
        last_error: asNullableString(r.last_error),
        active_properties: asNumber(r.active_properties, "active_properties"),
        total_properties: asNumber(r.total_properties, "total_properties"),
      };
    },
    "agencies"
  );
}

/**
 * Query paginated properties with rich filters.
 *
 * @param {URLSearchParams} searchParams Serialized query parameters.
 * @param {AbortSignal} [signal] Optional cancellation signal.
 * @returns {Promise<PaginatedProperties>}
 */
export async function fetchProperties(searchParams, signal) {
  const qs = searchParams.toString();
  const path = qs ? `api/properties?${qs}` : "api/properties";
  const raw = await requestJson(path, { signal });
  return parsePaginatedProperties(raw);
}

/**
 * @param {unknown} raw
 * @returns {PaginatedProperties}
 */
function parsePaginatedProperties(raw) {
  const r = asRecord(raw, "PaginatedProperties");
  return {
    total: asNumber(r.total, "total"),
    page: asNumber(r.page, "page"),
    page_size: asNumber(r.page_size, "page_size"),
    total_pages: asNumber(r.total_pages, "total_pages"),
    items: asArray(r.items, parsePropertySummary, "items"),
  };
}

const NUMBER_FIELDS = [
  "min_price",
  "max_price",
  "max_monthly_total",
  "max_price_per_m2",
  "min_area",
  "max_area",
];
const INTEGER_FIELDS = ["min_bedrooms", "min_suites", "min_bathrooms", "min_parking", "page", "page_size"];
const STRING_FIELDS = ["q", "purpose", "furnished", "sort"];
const LIST_FIELDS = ["types", "neighborhoods", "agencies"];
const BOOLEAN_FIELDS = [
  "accepts_pets",
  "has_barbecue",
  "has_balcony",
  "has_elevator",
  "has_pool",
  "has_yard",
  "accepts_financing",
  "only_with_price",
  "only_price_drops",
  "include_inactive",
  "only_similarity_candidates",
  "deduplicate",
];

/**
 * @typedef {{ agency_slug: string, external_id: string }} ListingKey
 */

/**
 * Convert UI filter query parameters into a `POST /api/properties/search` JSON body.
 *
 * Unknown parameters (e.g. the UI-only `only_favorites` / `include_hidden` toggles) are dropped,
 * because the server rejects extra fields. Only listing keys are added, never notes.
 *
 * @param {URLSearchParams} params UI filter state.
 * @param {{ includeKeys?: ListingKey[]|null, excludeKeys?: ListingKey[] }} [keys]
 * @returns {Record<string, unknown>}
 */
export function buildSearchBody(params, keys = {}) {
  /** @type {Record<string, unknown>} */
  const body = {};
  for (const field of STRING_FIELDS) {
    const val = params.get(field)?.trim();
    if (val) body[field] = val;
  }
  for (const field of LIST_FIELDS) {
    const values = params
      .getAll(field)
      .flatMap((raw) => raw.split(","))
      .map((v) => v.trim())
      .filter(Boolean);
    if (values.length > 0) body[field] = Array.from(new Set(values));
  }
  for (const field of NUMBER_FIELDS) {
    const num = Number.parseFloat(params.get(field) ?? "");
    if (Number.isFinite(num)) body[field] = num;
  }
  for (const field of INTEGER_FIELDS) {
    const num = Number.parseInt(params.get(field) ?? "", 10);
    if (Number.isFinite(num)) body[field] = num;
  }
  for (const field of BOOLEAN_FIELDS) {
    const val = params.get(field);
    if (val === "true" || val === "false") body[field] = val === "true";
  }
  if (keys.includeKeys) {
    body.include_keys = keys.includeKeys.map(({ agency_slug, external_id }) => ({ agency_slug, external_id }));
  }
  if (keys.excludeKeys && keys.excludeKeys.length > 0) {
    body.exclude_keys = keys.excludeKeys.map(({ agency_slug, external_id }) => ({ agency_slug, external_id }));
  }
  return body;
}

/**
 * Read-only property search with a JSON body, used when sending local favorites/discarded keys.
 *
 * @param {Record<string, unknown>} body Built by `buildSearchBody`.
 * @param {AbortSignal} [signal] Optional cancellation signal.
 * @returns {Promise<PaginatedProperties>}
 */
export async function searchProperties(body, signal) {
  const raw = await requestJson("api/properties/search", { method: "POST", body, signal });
  return parsePaginatedProperties(raw);
}

/**
 * Fetch complete stored details, price history, and duplicate peers for one property.
 *
 * @param {number} propertyId Internal property ID.
 * @returns {Promise<PropertyDetail>}
 */
export async function fetchPropertyDetail(propertyId) {
  const raw = await requestJson(`api/properties/${encodeURIComponent(String(propertyId))}`);
  return parsePropertyDetail(raw);
}

/**
 * Poll live runtime synchronization progress and fixed schedule metadata.
 *
 * @returns {Promise<SyncRuntimeStatus>}
 */
export async function fetchSyncStatus() {
  const raw = await requestJson("api/sync/status");
  const r = asRecord(raw, "SyncRuntimeStatus");
  return {
    is_running: asBoolean(r.is_running),
    completed_agencies: asNumber(r.completed_agencies, "completed_agencies"),
    total_agencies: asNumber(r.total_agencies, "total_agencies"),
    active_agencies: asArray(r.active_agencies ?? [], (s) => asString(s, "slug"), "active_agencies"),
    auto_sync_enabled: asBoolean(r.auto_sync_enabled),
    sync_schedule: asArray(r.sync_schedule ?? [], (s) => asString(s, "sync_schedule"), "sync_schedule"),
    sync_timezone: asString(r.sync_timezone, "sync_timezone"),
    next_scheduled_run_at: asNullableString(r.next_scheduled_run_at),
  };
}

/**
 * @typedef {{title: string, url: string}} MapResource
 * @typedef {MapResource & {start: number, end: number}} MapRange
 * @typedef {{quadra: number, label: string, folder_url: string, local_pdf: boolean}} QuadraMap
 * @typedef {{source_url: string, ranges: MapRange[], resources: MapResource[], local_quadras: number[], selected: QuadraMap|null}} MapCatalog
 */

/**
 * Read official map folders and availability of operator-imported quadra PDFs.
 * @param {number|null} [quadra]
 * @returns {Promise<MapCatalog>}
 */
export async function fetchMapCatalog(quadra = null) {
  const raw = await requestJson(`api/maps${quadra === null ? "" : `?quadra=${quadra}`}`);
  const r = asRecord(raw, "MapCatalog");
  const resource = (value) => {
    const item = asRecord(value, "MapResource");
    return { title: asString(item.title, "title"), url: asString(item.url, "url") };
  };
  let selected = null;
  if (r.selected !== null && r.selected !== undefined) {
    const item = asRecord(r.selected, "QuadraMap");
    selected = {
      quadra: asNumber(item.quadra, "quadra"), label: asString(item.label, "label"),
      folder_url: asString(item.folder_url, "folder_url"), local_pdf: asBoolean(item.local_pdf),
    };
  }
  return {
    source_url: asString(r.source_url, "source_url"),
    resources: asArray(r.resources, resource, "resources"),
    ranges: asArray(r.ranges, (value) => {
      const item = asRecord(value, "MapRange");
      return { ...resource(value), start: asNumber(item.start, "start"), end: asNumber(item.end, "end") };
    }, "ranges"),
    local_quadras: asArray(r.local_quadras, (value) => asNumber(value, "quadra"), "local_quadras"),
    selected,
  };
}
