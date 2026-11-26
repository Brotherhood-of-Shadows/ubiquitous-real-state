/**
 * Interactive dashboard controller for searching, filtering, and locally curating
 * real-estate listings across configured Pato Branco agencies (read-only API).
 * @module app
 */

import {
  ApiError,
  buildSearchBody,
  fetchAgencies,
  fetchFacets,
  fetchMapCatalog,
  fetchPropertyDetail,
  fetchStats,
  fetchSyncStatus,
  searchProperties,
} from "./api.js";
import { createPersonalStore, listenForStorageChanges, LIMITS, resolveStorage } from "./personal_state.js";

const brlFormatter = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const numberFormatter = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 1,
});

const dateTimeFormatter = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short",
  timeStyle: "short",
});

/** @type {Record<string, string>} */
const TYPE_LABELS = {
  apartamento: "Apartamento",
  kitnet_studio: "Kitnet / Studio",
  cobertura: "Cobertura",
  casa: "Casa",
  sobrado: "Sobrado",
  casa_condominio: "Casa em Condomínio",
  terreno: "Terreno",
  sala_comercial: "Sala Comercial",
  ponto_loja: "Ponto / Loja",
  barracao: "Barracão / Galpão",
  predio: "Prédio / Corporativo",
  chacara_rural: "Chácara / Rural",
  outro: "Outro",
};

/** @type {Record<string, string>} */
const PURPOSE_LABELS = {
  aluguel: "Aluguel",
  venda: "Venda",
  venda_e_aluguel: "Venda / Aluguel",
};

/** @type {Record<string, string>} */
const FURNISHED_LABELS = {
  mobiliado: "Mobiliado",
  semimobiliado: "Semimobiliado",
  nao_mobiliado: "Sem mobília",
  nao_informado: "Não informado",
};

/**
 * Normalize an external or app-relative URL, permitting only `http:` or `https:` schemes.
 *
 * App-local paths are resolved relative to the current application path so a stripped reverse-proxy
 * prefix is preserved, while external agency URLs remain independent of that prefix.
 *
 * @param {string|null|undefined} rawUrl
 * @returns {string|null}
 */
export function safeHttpUrl(rawUrl) {
  if (!rawUrl) {
    return null;
  }
  try {
    const trimmed = rawUrl.trim();
    if (!trimmed) {
      return null;
    }
    const normalized =
      trimmed.startsWith("/") && !trimmed.startsWith("//")
        ? trimmed.replace(/^\/+/, "")
        : trimmed;
    const base = new URL(
      typeof window !== "undefined" && window.location?.href
        ? window.location.href
        : "http://localhost/"
    );
    if (!base.pathname.endsWith("/") && !base.pathname.endsWith(".html")) {
      base.pathname = `${base.pathname}/`;
    }
    const parsed = new URL(normalized, base);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Count active user filters in serialized search query parameters.
 *
 * Ignores pagination/sorting (`page`, `page_size`, `sort`) and the default
 * `only_with_price="false"` parameter.
 *
 * @param {URLSearchParams} params
 * @returns {number}
 */
export function countActiveFilters(params) {
  let count = 0;
  for (const [key, value] of params.entries()) {
    if (key === "page" || key === "page_size" || key === "sort") {
      continue;
    }
    if (key === "only_with_price" && value === "false") {
      continue;
    }
    if (value.trim().length > 0) {
      count += 1;
    }
  }
  return count;
}

/**
 * Format a numeric BRL amount safely.
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
function formatBrl(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "Consulte";
  }
  return brlFormatter.format(value);
}

/**
 * Format an ISO-8601 timestamp in PT-BR local time.
 *
 * @param {string|null|undefined} iso
 * @returns {string}
 */
function formatDate(iso) {
  if (!iso) {
    return "Nunca";
  }
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) {
    return iso;
  }
  return dateTimeFormatter.format(dt);
}

/**
 * Create a DOM element safely using `textContent` and explicit attributes.
 *
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {{
 *   className?: string,
 *   text?: string,
 *   title?: string,
 *   attrs?: Record<string, string>
 * }} [opts]
 * @param {...(Node|string|null|undefined)} children
 * @returns {HTMLElementTagNameMap[K]}
 */
function createEl(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  if (opts.className) {
    node.className = opts.className;
  }
  if (opts.text !== undefined) {
    node.textContent = opts.text;
  }
  if (opts.title) {
    node.title = opts.title;
  }
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      node.setAttribute(k, v);
    }
  }
  for (const child of children) {
    if (child === null || child === undefined) {
      continue;
    }
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(child));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

/**
 * Resolve a required DOM element by ID or throw an Error.
 *
 * @template {HTMLElement} T
 * @param {string} id
 * @returns {T}
 */
function mustGetById(id) {
  const elem = document.getElementById(id);
  if (!elem) {
    throw new Error(`Missing required DOM element #${id}`);
  }
  return /** @type {T} */ (elem);
}

/**
 * Stateful UI controller coordinating filters, results, modals, and browser-local personal state.
 */
export class DashboardController {
  constructor() {
    /** @type {string} */
    this.selectedPurpose = "";
    /** @type {Set<string>} */
    this.selectedTypes = new Set();
    /** @type {Set<string>} */
    this.selectedNeighborhoods = new Set();
    /** @type {Set<string>} */
    this.selectedAgencies = new Set();
    /** @type {number} */
    this.currentPage = 1;
    /** @type {number} */
    this.totalPages = 1;

    /** @type {import("./api.js").SearchFacets} */
    this.facets = {
      property_types: [],
      neighborhoods: [],
      agencies: [],
      purposes: [],
    };

    /** @type {AbortController|null} */
    this.searchAbortController = null;
    /** @type {number|null} */
    this.debounceTimerId = null;
    this.personal = createPersonalStore({ storage: resolveStorage(() => window.localStorage) });
    /** @type {number|null} */
    this.noticeTimerId = null;
    /** @type {boolean} */
    this.totalActiveKnown = false;
    /** @type {number} */
    this.totalActiveCount = 0;
  }

  /**
   * Bind event listeners, hydrate initial state from URL parameters, and load initial data.
   *
   * @returns {Promise<void>}
   */
  async init() {
    this.hydrateFromUrl();
    this.bindEvents();
    this.renderPersonalStateInfo();
    listenForStorageChanges(window, this.personal, () => {
      this.renderPersonalStateInfo();
      void this.refreshProperties();
    });
    await Promise.all([this.refreshStats(), this.refreshFacets()]);
    await this.refreshProperties();
  }

  /**
   * Restore filter selections from `window.location.search` if present.
   *
   * @returns {void}
   */
  hydrateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const purpose = params.get("purpose");
    if (purpose === "aluguel" || purpose === "venda") {
      this.selectedPurpose = purpose;
    }

    for (const raw of params.getAll("types")) {
      for (const piece of raw.split(",")) {
        if (piece.trim()) {
          this.selectedTypes.add(piece.trim());
        }
      }
    }
    for (const raw of params.getAll("neighborhoods")) {
      for (const piece of raw.split(",")) {
        if (piece.trim()) {
          this.selectedNeighborhoods.add(piece.trim());
        }
      }
    }
    for (const raw of params.getAll("agencies")) {
      for (const piece of raw.split(",")) {
        if (piece.trim()) {
          this.selectedAgencies.add(piece.trim());
        }
      }
    }

    const setInput = (id, key) => {
      const val = params.get(key);
      if (val !== null) {
        /** @type {HTMLInputElement|HTMLSelectElement} */ (mustGetById(id)).value = val;
      }
    };

    setInput("f-q", "q");
    setInput("f-min-price", "min_price");
    setInput("f-max-price", "max_price");
    setInput("f-max-monthly", "max_monthly_total");
    setInput("f-max-m2", "max_price_per_m2");
    setInput("f-min-area", "min_area");
    setInput("f-max-area", "max_area");
    setInput("f-beds", "min_bedrooms");
    setInput("f-suites", "min_suites");
    setInput("f-baths", "min_bathrooms");
    setInput("f-parking", "min_parking");
    setInput("f-furnished", "furnished");
    setInput("f-sort", "sort");

    const setCheck = (id, key, defaultVal = false) => {
      const val = params.get(key);
      const input = /** @type {HTMLInputElement} */ (mustGetById(id));
      input.checked = val !== null ? val === "true" : defaultVal;
    };

    setCheck("f-only-price", "only_with_price", false);
    setCheck("f-dedup", "deduplicate", false);
    setCheck("f-drops", "only_price_drops", false);
    setCheck("f-favs", "only_favorites", false);
    setCheck("f-pets", "accepts_pets", false);
    setCheck("f-bbq", "has_barbecue", false);
    setCheck("f-balcony", "has_balcony", false);
    setCheck("f-elevator", "has_elevator", false);
    setCheck("f-pool", "has_pool", false);
    setCheck("f-yard", "has_yard", false);
    setCheck("f-fin", "accepts_financing", false);
    setCheck("f-hidden", "include_hidden", false);
    setCheck("f-inactive", "include_inactive", false);
    setCheck("f-review", "only_similarity_candidates", false);

    const pageVal = Number.parseInt(params.get("page") ?? "1", 10);
    if (Number.isFinite(pageVal) && pageVal >= 1) {
      this.currentPage = pageVal;
    }

    this.syncPurposeTabClasses();
  }

  /**
   * Update visual `.active` state on the purpose tab buttons.
   *
   * @returns {void}
   */
  syncPurposeTabClasses() {
    const tabs = document.querySelectorAll(".purpose-tab");
    for (const tab of tabs) {
      const btn = /** @type {HTMLButtonElement} */ (tab);
      const tabPurpose = btn.dataset.purpose ?? "";
      btn.classList.toggle("active", tabPurpose === this.selectedPurpose);
    }
  }

  /**
   * Expand or collapse the mobile filter disclosure panel.
   *
   * @param {boolean} expanded
   * @returns {void}
   */
  setFiltersExpanded(expanded) {
    const panel = mustGetById("filter-panel");
    const toggle = mustGetById("btn-toggle-filters");
    panel.classList.toggle("is-collapsed", !expanded);
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  /**
   * Attach DOM event handlers for filters, buttons, and modals.
   *
   * @returns {void}
   */
  bindEvents() {
    const toggleBtn = mustGetById("btn-toggle-filters");
    toggleBtn.addEventListener("click", () => {
      const expanded = toggleBtn.getAttribute("aria-expanded") === "true";
      this.setFiltersExpanded(!expanded);
    });

    mustGetById("btn-show-results").addEventListener("click", () => {
      this.setFiltersExpanded(false);
      mustGetById("btn-toggle-filters").focus();
    });

    const form = /** @type {HTMLFormElement} */ (mustGetById("filter-form"));
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      this.currentPage = 1;
      void this.refreshProperties();
    });

    for (const tab of document.querySelectorAll(".purpose-tab")) {
      tab.addEventListener("click", () => {
        const btn = /** @type {HTMLButtonElement} */ (tab);
        this.selectedPurpose = btn.dataset.purpose ?? "";
        this.currentPage = 1;
        this.syncPurposeTabClasses();
        void Promise.all([this.refreshFacets(), this.refreshProperties()]);
      });
    }

    const debouncedIds = [
      "f-q",
      "f-min-price",
      "f-max-price",
      "f-max-monthly",
      "f-max-m2",
      "f-min-area",
      "f-max-area",
    ];
    for (const id of debouncedIds) {
      mustGetById(id).addEventListener("input", () => this.scheduleSearch());
    }

    const immediateIds = [
      "f-beds",
      "f-suites",
      "f-baths",
      "f-parking",
      "f-furnished",
      "f-only-price",
      "f-dedup",
      "f-drops",
      "f-favs",
      "f-pets",
      "f-bbq",
      "f-balcony",
      "f-elevator",
      "f-pool",
      "f-yard",
      "f-fin",
      "f-hidden",
      "f-inactive",
      "f-review",
      "f-sort",
    ];
    for (const id of immediateIds) {
      mustGetById(id).addEventListener("change", () => {
        this.currentPage = 1;
        void this.refreshProperties();
      });
    }

    mustGetById("nb-search").addEventListener("input", () => this.renderNeighborhoodFacet());
    mustGetById("ag-search").addEventListener("input", () => this.renderAgencyFacet());

    mustGetById("btn-reset-filters").addEventListener("click", () => {
      this.resetAllFilters();
    });

    mustGetById("btn-clear-local").addEventListener("click", () => {
      if (!window.confirm("Apagar favoritos, descartados e anotações salvos neste navegador?")) {
        return;
      }
      const result = this.personal.clearAll();
      this.showPersonalNotice(result.ok ? "Dados deste navegador apagados." : result.message);
      this.renderPersonalStateInfo();
      void this.refreshProperties();
    });

    const mapsModal = /** @type {HTMLDialogElement} */ (mustGetById("maps-modal"));
    mustGetById("btn-open-maps").addEventListener("click", () => {
      mapsModal.showModal();
      void this.loadMaps();
    });
    mustGetById("btn-close-maps").addEventListener("click", () => mapsModal.close());
    mapsModal.addEventListener("click", (event) => {
      if (event.target === mapsModal) mapsModal.close();
    });
    mustGetById("maps-form").addEventListener("submit", (event) => {
      event.preventDefault();
      void this.loadMaps();
    });

    const detailModal = /** @type {HTMLDialogElement} */ (mustGetById("detail-modal"));
    mustGetById("btn-close-detail").addEventListener("click", () => detailModal.close());
    detailModal.addEventListener("click", (ev) => {
      if (ev.target === detailModal) {
        detailModal.close();
      }
    });

    const agenciesModal = /** @type {HTMLDialogElement} */ (mustGetById("agencies-modal"));
    mustGetById("btn-open-agencies").addEventListener("click", () => {
      void this.openAgenciesModal();
    });
    mustGetById("btn-close-agencies").addEventListener("click", () => agenciesModal.close());
    agenciesModal.addEventListener("click", (ev) => {
      if (ev.target === agenciesModal) {
        agenciesModal.close();
      }
    });
  }

  /**
   * Debounce text and numeric filter changes before refreshing the listings.
   *
   * @returns {void}
   */
  scheduleSearch() {
    if (this.debounceTimerId !== null) {
      window.clearTimeout(this.debounceTimerId);
    }
    this.debounceTimerId = window.setTimeout(() => {
      this.debounceTimerId = null;
      this.currentPage = 1;
      void this.refreshProperties();
    }, 260);
  }

  /**
   * Reset all UI controls and selections back to defaults.
   *
   * @returns {void}
   */
  resetAllFilters() {
    this.selectedPurpose = "";
    this.selectedTypes.clear();
    this.selectedNeighborhoods.clear();
    this.selectedAgencies.clear();
    this.currentPage = 1;

    const form = /** @type {HTMLFormElement} */ (mustGetById("filter-form"));
    form.reset();
    /** @type {HTMLInputElement} */ (mustGetById("f-only-price")).checked = false;
    /** @type {HTMLSelectElement} */ (mustGetById("f-sort")).value = "menor_preco";

    this.syncPurposeTabClasses();
    void Promise.all([this.refreshFacets(), this.refreshProperties()]);
  }

  /**
   * Serialize current filter UI state into `URLSearchParams`.
   *
   * @returns {URLSearchParams}
   */
  buildQueryParams() {
    const params = new URLSearchParams();
    if (this.selectedPurpose) {
      params.set("purpose", this.selectedPurpose);
    }
    if (this.selectedTypes.size > 0) {
      params.set("types", Array.from(this.selectedTypes).join(","));
    }
    if (this.selectedNeighborhoods.size > 0) {
      params.set("neighborhoods", Array.from(this.selectedNeighborhoods).join(","));
    }
    if (this.selectedAgencies.size > 0) {
      params.set("agencies", Array.from(this.selectedAgencies).join(","));
    }

    const addInput = (id, key) => {
      const val = /** @type {HTMLInputElement|HTMLSelectElement} */ (mustGetById(id)).value.trim();
      if (val.length > 0) {
        params.set(key, val);
      }
    };

    addInput("f-q", "q");
    addInput("f-min-price", "min_price");
    addInput("f-max-price", "max_price");
    addInput("f-max-monthly", "max_monthly_total");
    addInput("f-max-m2", "max_price_per_m2");
    addInput("f-min-area", "min_area");
    addInput("f-max-area", "max_area");
    addInput("f-beds", "min_bedrooms");
    addInput("f-suites", "min_suites");
    addInput("f-baths", "min_bathrooms");
    addInput("f-parking", "min_parking");
    addInput("f-furnished", "furnished");
    addInput("f-sort", "sort");

    const addBool = (id, key, onlyWhenTrue = true) => {
      const checked = /** @type {HTMLInputElement} */ (mustGetById(id)).checked;
      if (onlyWhenTrue) {
        if (checked) {
          params.set(key, "true");
        }
      } else {
        params.set(key, checked ? "true" : "false");
      }
    };

    addBool("f-only-price", "only_with_price", false);
    addBool("f-dedup", "deduplicate");
    addBool("f-drops", "only_price_drops");
    addBool("f-favs", "only_favorites");
    addBool("f-pets", "accepts_pets");
    addBool("f-bbq", "has_barbecue");
    addBool("f-balcony", "has_balcony");
    addBool("f-elevator", "has_elevator");
    addBool("f-pool", "has_pool");
    addBool("f-yard", "has_yard");
    addBool("f-fin", "accepts_financing");
    addBool("f-hidden", "include_hidden");
    addBool("f-inactive", "include_inactive");
    addBool("f-review", "only_similarity_candidates");

    params.set("page", String(this.currentPage));
    params.set("page_size", "30");
    return params;
  }

  /**
   * Load global statistics into the topbar pills.
   *
   * @returns {Promise<void>}
   */
  async refreshStats() {
    try {
      const stats = await fetchStats();
      this.totalActiveKnown = true;
      this.totalActiveCount = stats.active_properties;
      mustGetById("stat-active").textContent = String(stats.active_properties);
      mustGetById("stat-rent").textContent = String(stats.rent_count + stats.both_count);
      mustGetById("stat-sale").textContent = String(stats.sale_count + stats.both_count);
      mustGetById("stat-drops").textContent = String(stats.price_drops_count);
      mustGetById("stat-dups").textContent = String(stats.duplicate_groups_count);
      mustGetById("stat-review").textContent = String(stats.similarity_review_pairs_count);
      mustGetById("stat-agencies").textContent = `${stats.agencies_synced}/${stats.agencies_total}`;
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    }
  }

  /**
   * Load facet buckets and re-render sidebar chips and checklists.
   *
   * @returns {Promise<void>}
   */
  async refreshFacets() {
    try {
      this.facets = await fetchFacets(this.selectedPurpose);
      this.renderTypeFacet();
      this.renderNeighborhoodFacet();
      this.renderAgencyFacet();
    } catch (err) {
      console.error("Failed to fetch facets:", err);
    }
  }

  /**
   * Render property-type multi-select chips.
   *
   * @returns {void}
   */
  renderTypeFacet() {
    const container = mustGetById("facet-types");
    container.replaceChildren();

    for (const bucket of this.facets.property_types) {
      const isSelected = this.selectedTypes.has(bucket.value);
      const labelText = TYPE_LABELS[bucket.value] ?? bucket.label;
      const chip = createEl(
        "button",
        {
          className: isSelected ? "chip selected" : "chip",
          attrs: { type: "button" },
        },
        `${labelText} `,
        createEl("span", { className: "cnt", text: `(${bucket.count})` })
      );
      chip.addEventListener("click", () => {
        if (this.selectedTypes.has(bucket.value)) {
          this.selectedTypes.delete(bucket.value);
        } else {
          this.selectedTypes.add(bucket.value);
        }
        this.currentPage = 1;
        this.renderTypeFacet();
        void this.refreshProperties();
      });
      container.appendChild(chip);
    }
  }

  /**
   * Render neighborhood checklist filtered by `#nb-search`.
   *
   * @returns {void}
   */
  renderNeighborhoodFacet() {
    const container = mustGetById("facet-neighborhoods");
    const needle = /** @type {HTMLInputElement} */ (mustGetById("nb-search")).value
      .trim()
      .toLowerCase();
    container.replaceChildren();

    const filtered = this.facets.neighborhoods.filter(
      (b) =>
        this.selectedNeighborhoods.has(b.value) ||
        b.label.toLowerCase().includes(needle) ||
        b.value.toLowerCase().includes(needle)
    );

    if (filtered.length === 0) {
      container.appendChild(createEl("div", { className: "subtitle", text: "Nenhum bairro encontrado." }));
      return;
    }

    for (const bucket of filtered) {
      const checkbox = createEl("input", { attrs: { type: "checkbox" } });
      checkbox.checked = this.selectedNeighborhoods.has(bucket.value);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          this.selectedNeighborhoods.add(bucket.value);
        } else {
          this.selectedNeighborhoods.delete(bucket.value);
        }
        this.currentPage = 1;
        void this.refreshProperties();
      });

      const row = createEl(
        "label",
        { className: "check-row" },
        checkbox,
        createEl("span", { text: bucket.label }),
        createEl("span", { className: "cnt", text: String(bucket.count) })
      );
      container.appendChild(row);
    }
  }

  /**
   * Render agency checklist filtered by `#ag-search`.
   *
   * @returns {void}
   */
  renderAgencyFacet() {
    const container = mustGetById("facet-agencies");
    const needle = /** @type {HTMLInputElement} */ (mustGetById("ag-search")).value
      .trim()
      .toLowerCase();
    container.replaceChildren();

    const filtered = this.facets.agencies.filter(
      (b) =>
        this.selectedAgencies.has(b.value) ||
        b.label.toLowerCase().includes(needle) ||
        b.value.toLowerCase().includes(needle)
    );

    if (filtered.length === 0) {
      container.appendChild(
        createEl("div", { className: "subtitle", text: "Nenhuma imobiliária com anúncios ativos." })
      );
      return;
    }

    for (const bucket of filtered) {
      const checkbox = createEl("input", { attrs: { type: "checkbox" } });
      checkbox.checked = this.selectedAgencies.has(bucket.value);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          this.selectedAgencies.add(bucket.value);
        } else {
          this.selectedAgencies.delete(bucket.value);
        }
        this.currentPage = 1;
        void this.refreshProperties();
      });

      const row = createEl(
        "label",
        { className: "check-row" },
        checkbox,
        createEl("span", { text: bucket.label }),
        createEl("span", { className: "cnt", text: String(bucket.count) })
      );
      container.appendChild(row);
    }
  }

  /**
   * Build the read-only search body, adding local favorites/discarded listing keys (never notes).
   *
   * @param {URLSearchParams} params
   * @returns {Record<string, unknown>}
   */
  buildSearchBody(params) {
    const onlyFavorites = params.get("only_favorites") === "true";
    const includeHidden = params.get("include_hidden") === "true";
    return buildSearchBody(params, {
      includeKeys: onlyFavorites ? this.personal.favoriteKeys() : null,
      // As before, the favorites view also shows favorites that were discarded.
      excludeKeys: includeHidden || onlyFavorites ? [] : this.personal.hiddenKeys(),
    });
  }

  /**
   * Show a non-blocking status message about browser-local personal data.
   *
   * @param {string} message
   * @returns {void}
   */
  showPersonalNotice(message) {
    const notice = mustGetById("personal-notice");
    mustGetById("personal-notice-text").textContent = message;
    notice.classList.remove("hidden");
    if (this.noticeTimerId !== null) {
      window.clearTimeout(this.noticeTimerId);
    }
    this.noticeTimerId = window.setTimeout(() => {
      this.noticeTimerId = null;
      notice.classList.add("hidden");
    }, 6000);
  }

  /**
   * Refresh the favorites counter and the storage-mode label.
   *
   * @returns {void}
   */
  renderPersonalStateInfo() {
    const counts = this.personal.counts();
    mustGetById("stat-favs").textContent = String(counts.favorites);
    const info = mustGetById("local-state-info");
    const parts = ["Seus favoritos e anotações ficam apenas neste navegador."];
    if (this.personal.mode === "memory") {
      parts.push("Armazenamento do navegador indisponível: os dados valem só até fechar esta aba.");
    }
    const issues = this.personal.loadIssues;
    if (issues.length > 0) {
      parts.push(`Alguns dados salvos foram ignorados (${issues.join("; ")}).`);
    }
    info.textContent = parts.join(" ");
    info.classList.toggle("local-state-warning", this.personal.mode === "memory" || issues.length > 0);
  }

  /**
   * Fetch and render paginated property cards, canceling any superseded request.
   *
   * @returns {Promise<void>}
   */
  async refreshProperties() {
    if (this.searchAbortController) {
      this.searchAbortController.abort();
    }
    const controller = new AbortController();
    this.searchAbortController = controller;

    const params = this.buildQueryParams();
    const activeCount = countActiveFilters(params);
    const countEl = mustGetById("active-filter-count");
    countEl.textContent = String(activeCount);
    countEl.classList.toggle("hidden", activeCount === 0);
    mustGetById("btn-toggle-filters").setAttribute("aria-label", `Filtros, ${activeCount} ativos`);

    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, "", nextUrl);

    const summaryEl = mustGetById("results-summary");
    summaryEl.textContent = "Filtrando imóveis...";

    try {
      const pageData = await searchProperties(this.buildSearchBody(params), controller.signal);
      this.totalPages = pageData.total_pages;
      this.currentPage = pageData.page;

      summaryEl.textContent = `${pageData.total.toLocaleString("pt-BR")} imóveis encontrados em Pato Branco (página ${pageData.page} de ${Math.max(1, pageData.total_pages)})`;

      this.renderCards(pageData.items);
      this.renderPagination(pageData.page, pageData.total_pages);
    } catch (err) {
      if (err instanceof ApiError && err.code === "aborted") {
        return;
      }
      const message = err instanceof Error ? err.message : "Erro inesperado ao buscar imóveis.";
      summaryEl.textContent = "Falha na consulta";
      const grid = mustGetById("results-grid");
      grid.replaceChildren(createEl("div", { className: "empty-state", text: message }));
    }
  }

  /**
   * Render property summary cards into `#results-grid`.
   *
   * @param {import("./api.js").PropertySummary[]} items
   * @returns {void}
   */
  renderCards(items) {
    const grid = mustGetById("results-grid");
    grid.replaceChildren();

    if (items.length === 0) {
      if (this.totalActiveKnown && this.totalActiveCount === 0) {
        grid.appendChild(
          createEl(
            "div",
            { className: "empty-state" },
            createEl("h3", { text: "O catálogo ainda está vazio" }),
            createEl("p", {
              text: "Os catálogos das imobiliárias são coletados automaticamente pelo servidor. Volte mais tarde.",
            })
          )
        );
      } else {
        grid.appendChild(
          createEl(
            "div",
            { className: "empty-state" },
            createEl("h3", { text: "Nenhum imóvel corresponde aos filtros atuais" }),
            createEl("p", {
              text: "Experimente ampliar a faixa de valores, desmarcar filtros restritivos ou limpar tudo.",
            })
          )
        );
      }
      return;
    }

    for (const item of items) {
      grid.appendChild(this.buildPropertyCard(item));
    }
  }

  /**
   * Build a single property card element.
   *
   * @param {import("./api.js").PropertySummary} item
   * @returns {HTMLElement}
   */
  buildPropertyCard(item) {
    const isFavorite = this.personal.isFavorite(item);
    const isHidden = this.personal.isHidden(item);
    const card = createEl("article", {
      className: isHidden ? "prop-card is-hidden-card" : "prop-card",
    });

    // Media / cover image
    const media = createEl("div", { className: "card-media" });
    media.addEventListener("click", () => void this.openPropertyDetailModal(item.id));

    const imgUrl = safeHttpUrl(item.cover_image_url);
    if (imgUrl) {
      const img = createEl("img", {
        attrs: { src: imgUrl, alt: item.title, loading: "lazy" },
      });
      img.addEventListener(
        "error",
        () => {
          img.replaceWith(createEl("div", { className: "card-no-photo", text: "Sem foto disponível" }));
        },
        { once: true }
      );
      media.appendChild(img);
    } else {
      media.appendChild(createEl("div", { className: "card-no-photo", text: "Sem foto de capa" }));
    }

    const topBadges = createEl("div", { className: "card-badges-top" });
    const purposeClass =
      item.purpose === "aluguel"
        ? "badge badge-rent"
        : item.purpose === "venda_e_aluguel"
          ? "badge badge-both"
          : "badge badge-sale";
    topBadges.appendChild(
      createEl("span", {
        className: purposeClass,
        text: PURPOSE_LABELS[item.purpose] ?? item.purpose,
      })
    );

    const typeLabel = TYPE_LABELS[item.property_type] ?? item.property_type;
    topBadges.appendChild(
      createEl("span", {
        className: "badge badge-type",
        text: item.type_reconciled ? `${typeLabel} ✓` : typeLabel,
        title: item.type_reconciled
          ? `Categoria corrigida automaticamente (original da imobiliária: "${item.raw_type}")`
          : `Categoria: ${typeLabel}`,
      })
    );

    if (typeof item.price_delta_pct === "number" && item.price_delta_pct < 0) {
      topBadges.appendChild(
        createEl("span", {
          className: "badge badge-drop",
          text: `${numberFormatter.format(item.price_delta_pct)}%`,
          title: `Baixou de ${formatBrl(item.previous_price)}`,
        })
      );
    }

    if (item.duplicate_count > 1) {
      topBadges.appendChild(
        createEl("span", {
          className: "badge badge-dup",
          text: `${item.duplicate_count} imob.`,
          title: `Possível anúncio em ${item.duplicate_count} imobiliárias (menor valor: ${formatBrl(item.min_group_price)})`,
        })
      );
    }
    media.appendChild(topBadges);

    if (item.image_count > 0) {
      media.appendChild(
        createEl("span", {
          className: "photo-count-badge",
          text: `${item.image_count} foto${item.image_count > 1 ? "s" : ""}`,
        })
      );
    }

    card.appendChild(media);

    // Body
    const body = createEl("div", { className: "card-body" });

    body.appendChild(
      createEl(
        "div",
        { className: "card-agency-line" },
        createEl("strong", { text: item.agency_name }),
        createEl("span", { text: item.ref_code ? `Ref: ${item.ref_code}` : `#${item.external_id}` })
      )
    );

    const titleEl = createEl("h3", { className: "card-title", text: item.title });
    titleEl.addEventListener("click", () => void this.openPropertyDetailModal(item.id));
    body.appendChild(titleEl);

    const locParts = [item.neighborhood ?? "Bairro não inf.", item.street, item.building_name].filter(
      Boolean
    );
    body.appendChild(createEl("div", { className: "card-location", text: locParts.join(" • ") }));

    // Price summary block
    const priceBlock = createEl("div", { className: "card-price-block" });
    let mainPriceText = "Consulte";
    if (item.purpose === "aluguel" && item.rent_price !== null) {
      mainPriceText = `${formatBrl(item.rent_price)}/mês`;
    } else if (item.purpose === "venda" && item.sale_price !== null) {
      mainPriceText = formatBrl(item.sale_price);
    } else if (item.purpose === "venda_e_aluguel") {
      const bothParts = [];
      if (item.rent_price !== null) {
        bothParts.push(`Aluguel ${formatBrl(item.rent_price)}`);
      }
      if (item.sale_price !== null) {
        bothParts.push(`Venda ${formatBrl(item.sale_price)}`);
      }
      mainPriceText = bothParts.join(" | ") || "Consulte";
    } else if (item.rent_price !== null) {
      mainPriceText = `${formatBrl(item.rent_price)}/mês`;
    } else if (item.sale_price !== null) {
      mainPriceText = formatBrl(item.sale_price);
    }

    priceBlock.appendChild(createEl("div", { className: "price-main", text: mainPriceText }));

    const subPrice = createEl("div", { className: "price-sub" });
    if (item.condo_fee !== null && item.condo_fee > 0) {
      subPrice.appendChild(createEl("span", { text: `Cond: ${formatBrl(item.condo_fee)}` }));
    }
    if (item.iptu_fee !== null && item.iptu_fee > 0) {
      subPrice.appendChild(createEl("span", { text: `IPTU: ${formatBrl(item.iptu_fee)}` }));
    }
    if (
      item.effective_monthly_total !== null &&
      item.rent_price !== null &&
      item.effective_monthly_total > item.rent_price
    ) {
      subPrice.appendChild(
        createEl("strong", { text: `Total: ${formatBrl(item.effective_monthly_total)}/mês` })
      );
    }
    if (item.price_per_m2 !== null) {
      subPrice.appendChild(createEl("span", { text: `${formatBrl(item.price_per_m2)}/m²` }));
    }
    if (subPrice.childNodes.length > 0) {
      priceBlock.appendChild(subPrice);
    }
    body.appendChild(priceBlock);

    // Specs pills
    const specsRow = createEl("div", { className: "specs-row" });
    if (item.usable_area_m2 !== null) {
      specsRow.appendChild(
        createEl("span", {
          className: "spec-pill",
          text: `${numberFormatter.format(item.usable_area_m2)} m²`,
        })
      );
    }
    if (item.bedrooms !== null) {
      specsRow.appendChild(
        createEl("span", { className: "spec-pill", text: `${item.bedrooms} dorm.` })
      );
    }
    if (item.suites !== null && item.suites > 0) {
      specsRow.appendChild(
        createEl("span", {
          className: "spec-pill",
          text: `${item.suites} suíte${item.suites > 1 ? "s" : ""}`,
        })
      );
    }
    if (item.bathrooms !== null) {
      specsRow.appendChild(
        createEl("span", { className: "spec-pill", text: `${item.bathrooms} banh.` })
      );
    }
    if (item.parking_spots !== null) {
      specsRow.appendChild(
        createEl("span", {
          className: "spec-pill",
          text: `${item.parking_spots} vaga${item.parking_spots > 1 ? "s" : ""}`,
        })
      );
    }
    if (specsRow.childNodes.length > 0) {
      body.appendChild(specsRow);
    }

    // Extracted amenities
    const amenities = createEl("div", { className: "amenity-row" });
    if (item.furnished_status === "mobiliado" || item.furnished_status === "semimobiliado") {
      amenities.appendChild(
        createEl("span", {
          className: "amenity-tag",
          text: FURNISHED_LABELS[item.furnished_status],
        })
      );
    }
    if (item.accepts_pets) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Aceita Pet" }));
    }
    if (item.has_barbecue) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Churrasqueira" }));
    }
    if (item.has_balcony) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Sacada" }));
    }
    if (item.has_elevator) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Elevador" }));
    }
    if (item.has_pool) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Piscina" }));
    }
    if (item.has_yard) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Quintal/Terreno" }));
    }
    if (item.accepts_financing) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Financiável" }));
    }
    if (item.accepts_exchange) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Aceita permuta" }));
    }
    if (item.accepts_proposals) {
      amenities.appendChild(createEl("span", { className: "amenity-tag", text: "Estuda proposta" }));
    }
    if (amenities.childNodes.length > 0) {
      body.appendChild(amenities);
    }

    // Footer buttons
    const footer = createEl("div", { className: "card-footer" });
    const leftGroup = createEl("div", { className: "icon-actions" });

    const btnDetails = createEl("button", {
      className: "btn btn-outline btn-sm",
      text: "Detalhes",
      attrs: { type: "button" },
    });
    btnDetails.addEventListener("click", () => void this.openPropertyDetailModal(item.id));
    leftGroup.appendChild(btnDetails);

    const extHref = safeHttpUrl(item.source_url);
    if (extHref) {
      leftGroup.appendChild(
        createEl("a", {
          className: "btn btn-outline btn-sm",
          text: "Abrir ↗",
          title: "Abrir anúncio no site da imobiliária",
          attrs: { href: extHref, target: "_blank", rel: "noopener noreferrer" },
        })
      );
    }

    const rightGroup = createEl("div", { className: "icon-actions" });
    const favBtn = createEl("button", {
      className: isFavorite ? "btn-icon active-fav" : "btn-icon",
      text: isFavorite ? "★" : "☆",
      title: isFavorite ? "Remover dos favoritos" : "Salvar nos favoritos (neste navegador)",
      attrs: { type: "button" },
    });
    favBtn.addEventListener("click", () => {
      this.toggleLocalFlag(item, "favorite", !isFavorite);
    });

    const hideBtn = createEl("button", {
      className: isHidden ? "btn-icon active-hide" : "btn-icon",
      text: "✕",
      title: isHidden ? "Restaurar imóvel descartado" : "Descartar este imóvel da busca (neste navegador)",
      attrs: { type: "button" },
    });
    hideBtn.addEventListener("click", () => {
      this.toggleLocalFlag(item, "hidden", !isHidden);
    });

    rightGroup.append(favBtn, hideBtn);
    footer.append(leftGroup, rightGroup);
    body.appendChild(footer);

    card.appendChild(body);
    return card;
  }

  /**
   * Render pagination controls below `#results-grid`.
   *
   * @param {number} page
   * @param {number} totalPages
   * @returns {void}
   */
  renderPagination(page, totalPages) {
    const nav = mustGetById("pagination");
    nav.replaceChildren();
    if (totalPages <= 1) {
      return;
    }

    const goTo = (targetPage) => {
      this.currentPage = targetPage;
      window.scrollTo({ top: 0, behavior: "smooth" });
      void this.refreshProperties();
    };

    const prevBtn = createEl("button", {
      className: "btn btn-outline btn-sm",
      text: "← Anterior",
      attrs: { type: "button" },
    });
    prevBtn.disabled = page <= 1;
    prevBtn.addEventListener("click", () => goTo(page - 1));
    nav.appendChild(prevBtn);

    const start = Math.max(1, page - 3);
    const end = Math.min(totalPages, page + 3);
    for (let p = start; p <= end; p += 1) {
      const pBtn = createEl("button", {
        className: p === page ? "btn btn-primary btn-sm" : "btn btn-outline btn-sm",
        text: String(p),
        attrs: { type: "button" },
      });
      pBtn.addEventListener("click", () => goTo(p));
      nav.appendChild(pBtn);
    }

    const nextBtn = createEl("button", {
      className: "btn btn-outline btn-sm",
      text: "Próxima →",
      attrs: { type: "button" },
    });
    nextBtn.disabled = page >= totalPages;
    nextBtn.addEventListener("click", () => goTo(page + 1));
    nav.appendChild(nextBtn);
  }

  /**
   * Save a favorite or discarded flag in this browser and refresh the view.
   *
   * @param {import("./api.js").PropertySummary} item
   * @param {"favorite"|"hidden"} flag
   * @param {boolean} enabled
   * @returns {void}
   */
  toggleLocalFlag(item, flag, enabled) {
    const result =
      flag === "favorite" ? this.personal.setFavorite(item, enabled) : this.personal.setHidden(item, enabled);
    if (!result.ok) {
      this.showPersonalNotice(result.message);
      return;
    }
    this.renderPersonalStateInfo();
    void this.refreshProperties();
  }

  /**
   * Open `#detail-modal` and render stored property details, peers, and price history.
   *
   * @param {number} propertyId
   * @returns {Promise<void>}
   */
  async openPropertyDetailModal(propertyId) {
    const dialog = /** @type {HTMLDialogElement} */ (mustGetById("detail-modal"));
    const bodyEl = mustGetById("modal-body");
    const titleEl = mustGetById("modal-title");
    const badgesEl = mustGetById("modal-badges");

    titleEl.textContent = "Carregando imóvel...";
    badgesEl.replaceChildren();
    bodyEl.replaceChildren(createEl("p", { text: "Consultando banco local..." }));
    if (!dialog.open) {
      dialog.showModal();
    }

    try {
      const detail = await fetchPropertyDetail(propertyId);
      this.renderPropertyDetailContent(detail);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Erro ao carregar imóvel.";
      bodyEl.replaceChildren(createEl("p", { text: msg }));
    }
  }

  /**
   * Open a modal photo gallery with keyboard navigation and native Escape support.
   * @param {string[]} photos
   * @param {string} selected
   * @param {string} title
   * @returns {void}
   */
  openPhotoViewer(photos, selected, title) {
    const urls = photos.map(safeHttpUrl).filter(Boolean);
    if (!urls.length) return;
    let index = Math.max(0, urls.indexOf(selected));
    const dialog = createEl("dialog", { className: "photo-viewer", attrs: { "aria-label": "Fotos do imóvel" } });
    const photo = createEl("img", { attrs: { alt: title } });
    const count = createEl("span", { attrs: { "aria-live": "polite" } });
    const show = (offset) => {
      index = (index + offset + urls.length) % urls.length;
      photo.src = urls[index];
      count.textContent = `${index + 1} / ${urls.length}`;
    };
    const controls = createEl("div", { className: "photo-controls" });
    for (const [label, offset] of [["← Anterior", -1], ["Próxima →", 1]]) {
      const button = createEl("button", { className: "btn btn-outline", text: label, attrs: { type: "button" } });
      button.addEventListener("click", () => show(offset));
      controls.appendChild(button);
    }
    const close = createEl("button", { className: "btn btn-primary", text: "Fechar", attrs: { type: "button" } });
    close.addEventListener("click", () => dialog.close());
    controls.append(count, close);
    dialog.append(controls, photo);
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        show(event.key === "ArrowRight" ? 1 : -1);
      }
    });
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    document.body.appendChild(dialog);
    show(0);
    dialog.showModal();
  }

  /**
   * Populate `#detail-modal` DOM with a loaded `PropertyDetail`.
   *
   * @param {import("./api.js").PropertyDetail} detail
   * @returns {void}
   */
  renderPropertyDetailContent(detail) {
    const titleEl = mustGetById("modal-title");
    const badgesEl = mustGetById("modal-badges");
    const bodyEl = mustGetById("modal-body");

    titleEl.textContent = detail.title;
    badgesEl.replaceChildren(
      createEl("span", {
        className: detail.purpose === "aluguel" ? "badge badge-rent" : "badge badge-sale",
        text: PURPOSE_LABELS[detail.purpose] ?? detail.purpose,
      }),
      " ",
      createEl("span", {
        className: "badge badge-type",
        text: TYPE_LABELS[detail.property_type] ?? detail.property_type,
      }),
      " ",
      createEl("span", {
        className: "subtitle",
        text: `${detail.agency_name} • Ref: ${detail.ref_code ?? detail.external_id}`,
      })
    );

    bodyEl.replaceChildren();

    // Action bar at top of modal
    const actionBar = createEl("div", { className: "topbar-actions detail-action-bar" });
    const srcHref = safeHttpUrl(detail.source_url);
    if (srcHref) {
      actionBar.appendChild(
        createEl("a", {
          className: "btn btn-primary btn-sm",
          text: "Abrir Anúncio Original ↗",
          attrs: { href: srcHref, target: "_blank", rel: "noopener noreferrer" },
        })
      );
    }
    const waHref = safeHttpUrl(detail.whatsapp_url);
    if (waHref) {
      actionBar.appendChild(
        createEl("a", {
          className: "btn btn-outline btn-sm",
          text: "WhatsApp da Imobiliária ↗",
          attrs: { href: waHref, target: "_blank", rel: "noopener noreferrer" },
        })
      );
    }
    if (detail.latitude !== null && detail.longitude !== null) {
      const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${detail.latitude},${detail.longitude}`)}`;
      actionBar.appendChild(
        createEl("a", {
          className: "btn btn-outline btn-sm",
          text: "Ver no Google Maps ↗",
          attrs: { href: mapsUrl, target: "_blank", rel: "noopener noreferrer" },
        })
      );
    }

    bodyEl.appendChild(actionBar);
    bodyEl.appendChild(
      createEl("p", {
        className: "subtitle",
        text: detail.detail_enriched_at
          ? `Dados do catálogo coletado em ${formatDate(detail.last_seen_at)}, com página do anúncio lida em ${formatDate(detail.detail_enriched_at)}.`
          : `Dados do catálogo coletado em ${formatDate(detail.last_seen_at)}. Para descrição completa e taxas, abra o anúncio original.`,
      })
    );

    // Gallery
    const photos =
      detail.image_urls.length > 0
        ? detail.image_urls
        : detail.cover_image_url
          ? [detail.cover_image_url]
          : [];
    if (photos.length > 0) {
      const strip = createEl("div", { className: "gallery-strip" });
      for (const rawPhoto of photos) {
        const cleanUrl = safeHttpUrl(rawPhoto);
        if (cleanUrl) {
          const photoButton = createEl("button", {
            className: "gallery-photo", attrs: { type: "button", "aria-label": "Ampliar foto" },
          }, createEl("img", { attrs: { src: cleanUrl, alt: detail.title, loading: "lazy" } }));
          photoButton.addEventListener("click", () => this.openPhotoViewer(photos, rawPhoto, detail.title));
          strip.appendChild(photoButton);
        }
      }
      bodyEl.appendChild(strip);
    }

    // Two-column Financial & Technical panels
    const detailGrid = createEl("div", { className: "detail-grid" });

    const finPanel = createEl("div", { className: "detail-panel" }, createEl("h3", { text: "Valores & Custos" }));
    const finLines = [
      detail.sale_price !== null ? `Venda: ${formatBrl(detail.sale_price)}` : null,
      detail.rent_price !== null ? `Aluguel: ${formatBrl(detail.rent_price)}/mês` : null,
      detail.condo_fee !== null ? `Condomínio: ${formatBrl(detail.condo_fee)}/mês` : "Condomínio: não informado",
      detail.iptu_fee !== null ? `IPTU: ${formatBrl(detail.iptu_fee)}` : "IPTU: não informado",
      detail.insurance_fee !== null ? `Seguro: ${formatBrl(detail.insurance_fee)}` : null,
      detail.effective_monthly_total !== null
        ? `Custo mensal estimado: ${formatBrl(detail.effective_monthly_total)}/mês`
        : null,
      detail.price_per_m2 !== null ? `Valor por m²: ${formatBrl(detail.price_per_m2)}/m²` : null,
    ].filter(Boolean);
    for (const line of finLines) {
      finPanel.appendChild(createEl("div", { text: /** @type {string} */ (line) }));
    }

    const specPanel = createEl(
      "div",
      { className: "detail-panel" },
      createEl("h3", { text: "Localização & Ficha Técnica" })
    );
    const specLines = [
      `Bairro: ${detail.neighborhood ?? "Não informado"} (${detail.city} - ${detail.state})`,
      detail.street
        ? `Endereço: ${detail.street}${detail.street_number ? `, ${detail.street_number}` : ""}`
        : null,
      detail.building_name ? `Edifício / Condomínio: ${detail.building_name}` : null,
      detail.area_private_m2 !== null ? `Área privativa: ${numberFormatter.format(detail.area_private_m2)} m²` : null,
      detail.area_built_m2 !== null ? `Área construída: ${numberFormatter.format(detail.area_built_m2)} m²` : null,
      detail.area_total_m2 !== null ? `Área total: ${numberFormatter.format(detail.area_total_m2)} m²` : null,
      `Quartos: ${detail.bedrooms ?? "-"} | Suítes: ${detail.suites ?? "-"} | Banheiros: ${detail.bathrooms ?? "-"} | Vagas: ${detail.parking_spots ?? "-"}`,
      `Mobília: ${FURNISHED_LABELS[detail.furnished_status] ?? detail.furnished_status}`,
      detail.accepts_exchange ? "Negociação: aceita permuta" : null,
      detail.accepts_proposals ? "Negociação: estuda proposta" : null,
      `Tipo original no CRM: "${detail.raw_type || "-"}"`,
      `Primeira coleta: ${formatDate(detail.first_seen_at)} | Última verificação: ${formatDate(detail.last_seen_at)}`,
    ].filter(Boolean);
    for (const line of specLines) {
      specPanel.appendChild(createEl("div", { text: /** @type {string} */ (line) }));
    }

    detailGrid.append(finPanel, specPanel);
    bodyEl.appendChild(detailGrid);

    // Description
    bodyEl.appendChild(createEl("h3", { text: "Descrição do Imóvel" }));
    bodyEl.appendChild(
      createEl("div", {
        className: "description-box",
        text:
          detail.description ??
          'Descrição completa ainda não carregada do anúncio individual. Clique em "Buscar descrição completa, condomínio/IPTU e galeria no site" acima para consultar sob demanda.',
      })
    );

    // Cross-agency duplicate peers comparison
    if (detail.duplicate_peers.length > 0) {
      const peerPanel = createEl(
        "div",
        { className: "detail-panel detail-section" },
        createEl("h3", {
          text: `Possível mesmo imóvel — evidências de similaridade (${detail.duplicate_peers.length})`,
        })
      );
      for (const peer of detail.duplicate_peers) {
        const peerPrice =
          peer.rent_price !== null
            ? `${formatBrl(peer.rent_price)}/mês`
            : formatBrl(peer.sale_price);
        const peerLink = safeHttpUrl(peer.source_url);
        const row = createEl(
          "div",
          { className: "duplicate-peer-row" },
          createEl("span", {
            text: `${peer.agency_name} (Ref: ${peer.ref_code ?? peer.id}) — ${peerPrice}. ${peer.match_reasons.join("; ")}. ${peer.match_uncertainties.join("; ")}`,
          }),
          peerLink
            ? createEl("a", {
                className: "btn-link",
                text: "Abrir na outra imobiliária ↗",
                attrs: { href: peerLink, target: "_blank", rel: "noopener noreferrer" },
              })
            : null
        );
        peerPanel.appendChild(row);
      }
      bodyEl.appendChild(peerPanel);
    }

    if (detail.similarity_candidates.length > 0) {
      const panel = createEl("div", { className: "detail-panel similarity-review" },
        createEl("h3", { text: `Pares para revisar (${detail.similarity_candidates_total})` }),
        createEl("p", { text: "Estes anúncios compartilham características, mas ainda não há evidência suficiente para agrupá-los. Veja os motivos e as divergências abaixo." })
      );
      for (const candidate of detail.similarity_candidates) {
        const item = createEl("div", { className: "similarity-candidate" },
          createEl("strong", { text: `${candidate.agency_name} — Ref. ${candidate.ref_code ?? candidate.id}` }),
          createEl("p", { text: candidate.match_reasons.join("; ") }),
          createEl("p", { className: "similarity-uncertainties", text: candidate.match_uncertainties.join("; ") })
        );
        const button = createEl("button", { className: "btn btn-outline btn-sm", text: "Ver detalhes e fotos", attrs: { type: "button" } });
        button.addEventListener("click", () => void this.openPropertyDetailModal(candidate.id));
        item.appendChild(button);
        panel.appendChild(item);
      }
      if (detail.similarity_candidates_total > detail.similarity_candidates.length) {
        panel.appendChild(createEl("p", { text: `Mostrando os ${detail.similarity_candidates.length} pares com mais evidências.` }));
      }
      bodyEl.appendChild(panel);
    }

    // Price History
    if (detail.price_history.length > 0) {
      const histPanel = createEl(
        "div",
        { className: "detail-panel detail-section" },
        createEl("h3", { text: "Histórico de Preços Registrado" })
      );
      for (const h of detail.price_history) {
        const parts = [formatDate(h.recorded_at)];
        if (h.rent_price !== null) {
          parts.push(`Aluguel: ${formatBrl(h.rent_price)}`);
        }
        if (h.sale_price !== null) {
          parts.push(`Venda: ${formatBrl(h.sale_price)}`);
        }
        if (h.effective_monthly_total !== null) {
          parts.push(`Mensal total: ${formatBrl(h.effective_monthly_total)}`);
        }
        histPanel.appendChild(createEl("div", { text: parts.join(" • ") }));
      }
      bodyEl.appendChild(histPanel);
    }

    // Personal Notes & Curation
    const notesPanel = createEl(
      "div",
      { className: "detail-panel" },
      createEl("h3", { text: "Suas anotações pessoais (salvas apenas neste navegador)" })
    );
    const notesInput = createEl("textarea", {
      attrs: {
        rows: "3",
        maxlength: String(LIMITS.noteChars),
        placeholder: "Ex: Visitar sábado às 10h, perguntar sobre vaga coberta e sol da manhã...",
      },
    });
    notesInput.value = this.personal.getNote(detail);
    const notesStatus = createEl("p", { className: "subtitle", attrs: { "aria-live": "polite" } });

    const saveNotesBtn = createEl("button", {
      className: "btn btn-primary btn-sm mt-6",
      text: "Salvar Anotações",
      attrs: { type: "button" },
    });
    saveNotesBtn.addEventListener("click", () => {
      const result = this.personal.setNote(detail, notesInput.value);
      notesStatus.textContent = result.ok ? "Salvo neste navegador ✓" : result.message;
      if (!result.ok) {
        this.showPersonalNotice(result.message);
      }
    });

    notesPanel.append(notesInput, saveNotesBtn, notesStatus);
    bodyEl.appendChild(notesPanel);
  }

  /**
   * Open `#agencies-modal` showing catalog sources and their read-only last-sync status.
   *
   * @returns {Promise<void>}
   */
  async openAgenciesModal() {
    const dialog = /** @type {HTMLDialogElement} */ (mustGetById("agencies-modal"));
    const bodyEl = mustGetById("agencies-body");
    const timerInfoEl = mustGetById("agencies-timer-info");

    bodyEl.replaceChildren(createEl("p", { text: "Carregando status das imobiliárias..." }));
    if (!dialog.open) {
      dialog.showModal();
    }

    try {
      const [agencies, syncStatus] = await Promise.all([fetchAgencies(), fetchSyncStatus()]);
      const running = syncStatus.is_running
        ? ` Atualização em andamento: ${syncStatus.completed_agencies}/${syncStatus.total_agencies} imobiliárias concluídas.`
        : "";
      const scheduleLabel =
        syncStatus.sync_schedule.length > 0 ? syncStatus.sync_schedule.join(" e ") : "06:00 e 18:00";
      timerInfoEl.textContent =
        (syncStatus.auto_sync_enabled
          ? `Catálogos sincronizados automaticamente às ${scheduleLabel} (${syncStatus.sync_timezone}; próxima execução: ${formatDate(syncStatus.next_scheduled_run_at)}).`
          : "Atualização automática desativada neste servidor.") + running;

      const table = createEl("table", { className: "agencies-table" });
      const thead = createEl(
        "thead",
        {},
        createEl(
          "tr",
          {},
          createEl("th", { text: "Imobiliária" }),
          createEl("th", { text: "Plataforma" }),
          createEl("th", { text: "Ativos" }),
          createEl("th", { text: "Último Sync" }),
          createEl("th", { text: "Status" })
        )
      );
      const tbody = createEl("tbody");

      for (const ag of agencies) {
        const siteHref = safeHttpUrl(ag.base_url);
        const nameCell = createEl("td");
        if (siteHref) {
          nameCell.appendChild(
            createEl("a", {
              className: "btn-link",
              text: `${ag.name} ↗`,
              attrs: { href: siteHref, target: "_blank", rel: "noopener noreferrer" },
            })
          );
        } else {
          nameCell.textContent = ag.name;
        }

        const statusLabel = ag.last_error
          ? `${ag.last_sync_status ?? "erro"}: ${ag.last_error}`
          : (ag.last_sync_status ?? "pendente");

        tbody.appendChild(
          createEl(
            "tr",
            {},
            nameCell,
            createEl("td", {
              text: ag.platform === "public_html" ? "Catálogo HTML" : ag.platform,
            }),
            createEl("td", { text: `${ag.active_properties} (${ag.total_properties} total)` }),
            createEl("td", { text: formatDate(ag.last_synced_at) }),
            createEl("td", { text: statusLabel })
          )
        );
      }

      table.append(thead, tbody);
      bodyEl.replaceChildren(table);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Falha ao listar imobiliárias.";
      bodyEl.replaceChildren(createEl("p", { text: msg }));
    }
  }

  /**
   * Load and render public map sources and an optionally selected quadra PDF.
   * @returns {Promise<void>}
   */
  async loadMaps() {
    const body = mustGetById("maps-body");
    const field = /** @type {HTMLInputElement} */ (mustGetById("maps-quadra"));
    if (!field.reportValidity()) return;
    const quadra = field.value.trim() ? Number(field.value) : null;
    body.replaceChildren(createEl("p", { text: "Carregando mapas..." }));
    try {
      const catalog = await fetchMapCatalog(quadra);
      body.replaceChildren();
      body.appendChild(createEl("p", { text: `${catalog.local_quadras.length} quadras com PDF disponível neste servidor.` }));
      const addLink = (parent, title, rawUrl) => {
        const href = safeHttpUrl(rawUrl);
        if (href) parent.appendChild(createEl("p", {}, createEl("a", {
          className: "btn-link", text: title,
          attrs: { href, target: "_blank", rel: "noopener noreferrer" },
        })));
      };
      if (catalog.selected) {
        const selected = catalog.selected;
        body.appendChild(createEl("h3", { text: `Quadra ${selected.label}` }));
        addLink(body, "Abrir a pasta oficial desta faixa de quadras ↗", selected.folder_url);
        if (selected.local_pdf) {
          const url = `api/maps/quadras/${selected.quadra}/pdf?v=${Date.now()}`;
          body.appendChild(createEl("a", { className: "btn btn-outline btn-sm", text: "Abrir PDF em outra aba", attrs: { href: url, target: "_blank", rel: "noopener noreferrer" } }));
          body.appendChild(createEl("iframe", { className: "quadra-pdf", attrs: { src: url, title: `Mapa da quadra ${selected.label}` } }));
        } else {
          body.appendChild(createEl("p", { text: "Esta quadra ainda não tem PDF neste servidor. Abra a pasta oficial para consultar o mapa." }));
        }
      }
      body.appendChild(createEl("h3", { text: "Acervo municipal de quadras" }));
      for (const group of catalog.ranges) addLink(body, group.title + " ↗", group.url);
      body.appendChild(createEl("h3", { text: "Mapas gerais e arquivos cartográficos" }));
      for (const resource of catalog.resources) addLink(body, resource.title + " ↗", resource.url);
      addLink(body, "Fonte: Prefeitura de Pato Branco ↗", catalog.source_url);
    } catch (error) {
      body.replaceChildren(createEl("p", { text: error instanceof Error ? error.message : "Não foi possível carregar os mapas." }));
    }
  }

}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  const dashboard = new DashboardController();
  window.addEventListener("DOMContentLoaded", () => {
    void dashboard.init();
  });
}
