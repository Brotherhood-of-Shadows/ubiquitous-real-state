/**
 * Browser-only personal state (favorites, discarded listings and notes).
 *
 * Pure ES module without DOM access: callers inject the `Storage`-like object, so the store can be
 * unit-tested in Node. Everything lives under one `localStorage` key and never leaves the browser
 * except as bounded listing keys sent to the read-only search endpoint (notes are never sent).
 *
 * Listings are identified by the stable `agency_slug` + `external_id` pair, not the database ID,
 * so a server database rebuild that reuses IDs can never attach state to a different listing.
 * @module personal_state
 */

export const STORAGE_KEY = "imob.personal.v1";
export const STATE_VERSION = 1;

export const LIMITS = Object.freeze({
  favorites: 200,
  hidden: 500,
  notes: 200,
  noteChars: 2000,
  bytes: 256 * 1024,
});

const UNSUPPORTED_VERSION_ISSUE =
  "dados salvos por outra versão do site foram preservados; para usar favoritos aqui, limpe os dados deste navegador";

const SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/;
const MAX_EXTERNAL_ID_CHARS = 96;

/**
 * @typedef {{ agency_slug: string, external_id: string }} ListingKey
 * @typedef {ListingKey & { t: number }} FlagEntry
 * @typedef {ListingKey & { t: number, text: string }} NoteEntry
 * @typedef {{ favorites: Record<string, FlagEntry>, hidden: Record<string, FlagEntry>, notes: Record<string, NoteEntry> }} PersonalState
 * @typedef {"persistent"|"memory"} StorageMode
 * @typedef {"limit"|"too_long"|"quota"|"storage"|"invalid"|"unsupported_version"} FailureReason
 * @typedef {{ ok: true } | { ok: false, reason: FailureReason, message: string }} SaveResult
 * @typedef {{ getItem(key: string): string|null, setItem(key: string, value: string): void, removeItem(key: string): void }} StorageLike
 */

/**
 * Resolve `localStorage` defensively; access itself throws `SecurityError` in some privacy modes.
 *
 * @param {() => unknown} getter Returns the candidate storage, e.g. `() => window.localStorage`.
 * @returns {StorageLike|null}
 */
export function resolveStorage(getter) {
  try {
    const candidate = /** @type {StorageLike|null|undefined} */ (getter());
    if (!candidate || typeof candidate.getItem !== "function") {
      return null;
    }
    candidate.getItem(STORAGE_KEY);
    return candidate;
  } catch {
    return null;
  }
}

/**
 * Build the stable string key used inside the store. Slugs never contain `/`, so it is unambiguous.
 *
 * @param {{ agency_slug: string, external_id: string }} item
 * @returns {string}
 */
export function listingKey(item) {
  return `${item.agency_slug}/${item.external_id}`;
}

/**
 * @param {unknown} item
 * @returns {item is ListingKey}
 */
function isValidListingKey(item) {
  if (typeof item !== "object" || item === null) {
    return false;
  }
  const r = /** @type {Record<string, unknown>} */ (item);
  return (
    typeof r.agency_slug === "string" &&
    SLUG_RE.test(r.agency_slug) &&
    typeof r.external_id === "string" &&
    r.external_id.length > 0 &&
    r.external_id.length <= MAX_EXTERNAL_ID_CHARS
  );
}

/** @returns {PersonalState} */
function emptyState() {
  return { favorites: {}, hidden: {}, notes: {} };
}

/**
 * Keep only valid entries of one section; invalid entries are dropped individually.
 *
 * @param {unknown} raw
 * @param {number} maxEntries
 * @param {boolean} withText
 * @param {string[]} issues
 * @param {string} section
 * @returns {Record<string, any>}
 */
function sanitizeSection(raw, maxEntries, withText, issues, section) {
  /** @type {Record<string, any>} */
  const out = {};
  if (raw === undefined) {
    return out;
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    issues.push(`${section}: formato inválido`);
    return out;
  }
  let dropped = 0;
  let overflow = 0;
  for (const [key, value] of Object.entries(raw)) {
    const v = /** @type {Record<string, unknown>} */ (value);
    const valid =
      isValidListingKey(value) &&
      listingKey(/** @type {ListingKey} */ (value)) === key &&
      typeof v.t === "number" &&
      Number.isFinite(v.t) &&
      (!withText ||
        (typeof v.text === "string" && v.text.trim().length > 0 && v.text.length <= LIMITS.noteChars));
    if (!valid) {
      dropped += 1;
      continue;
    }
    if (Object.keys(out).length >= maxEntries) {
      overflow += 1;
      continue;
    }
    const entry = { agency_slug: v.agency_slug, external_id: v.external_id, t: v.t };
    out[key] = withText ? { ...entry, text: v.text } : entry;
  }
  if (dropped > 0) {
    issues.push(`${section}: ${dropped} registro(s) inválido(s) ignorado(s)`);
  }
  if (overflow > 0) {
    issues.push(`${section}: ${overflow} registro(s) acima do limite ignorado(s)`);
  }
  return out;
}

/**
 * Parse a raw stored string into a valid state, reporting what was dropped.
 *
 * Data written by another (normally newer) format version is not dropped: `unsupported` is set and
 * the caller must leave the stored value untouched until the user explicitly clears it.
 *
 * @param {string|null} raw
 * @returns {{ state: PersonalState, issues: string[], unsupported: boolean }}
 */
export function parseState(raw) {
  /** @type {string[]} */
  const issues = [];
  if (raw === null || raw === "") {
    return { state: emptyState(), issues, unsupported: false };
  }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { state: emptyState(), issues: ["dados salvos corrompidos (JSON inválido)"], unsupported: false };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { state: emptyState(), issues: ["dados salvos em formato inválido"], unsupported: false };
  }
  const r = /** @type {Record<string, unknown>} */ (parsed);
  if (typeof r.v === "number" && r.v !== STATE_VERSION) {
    return { state: emptyState(), issues: [UNSUPPORTED_VERSION_ISSUE], unsupported: true };
  }
  if (r.v !== STATE_VERSION) {
    return { state: emptyState(), issues: ["dados salvos sem versão válida"], unsupported: false };
  }
  return {
    state: {
      favorites: sanitizeSection(r.favorites, LIMITS.favorites, false, issues, "favoritos"),
      hidden: sanitizeSection(r.hidden, LIMITS.hidden, false, issues, "descartados"),
      notes: sanitizeSection(r.notes, LIMITS.notes, true, issues, "anotações"),
    },
    issues,
    unsupported: false,
  };
}

/**
 * @param {PersonalState} state
 * @returns {string}
 */
export function serializeState(state) {
  return JSON.stringify({ v: STATE_VERSION, ...state });
}

/**
 * @param {string} text
 * @returns {number}
 */
function utf8Bytes(text) {
  return new TextEncoder().encode(text).length;
}

/**
 * @param {unknown} err
 * @returns {boolean}
 */
function isQuotaError(err) {
  if (typeof err !== "object" || err === null) {
    return false;
  }
  const e = /** @type {{ name?: unknown, code?: unknown }} */ (err);
  return (
    e.name === "QuotaExceededError" ||
    e.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
    e.code === 22 ||
    e.code === 1014
  );
}

/**
 * @param {FailureReason} reason
 * @param {string} message
 * @returns {SaveResult}
 */
function fail(reason, message) {
  return { ok: false, reason, message };
}

/** @type {SaveResult} */
const OK = Object.freeze({ ok: true });

/**
 * Create the personal-state store.
 *
 * @param {{ storage: StorageLike|null, now?: () => number }} options
 */
export function createPersonalStore({ storage, now = () => Date.now() }) {
  /** @type {StorageMode} */
  const mode = storage ? "persistent" : "memory";
  /** @type {PersonalState} */
  let state = emptyState();
  /** @type {string[]} */
  let loadIssues = [];
  /** True while storage holds another format version that must not be overwritten. */
  let unsupported = false;

  /**
   * Re-read the stored state. On a read failure the last known snapshot is kept unchanged.
   *
   * @returns {boolean} Whether the stored value could be read.
   */
  const reload = () => {
    if (!storage) {
      return true;
    }
    let raw = null;
    try {
      raw = storage.getItem(STORAGE_KEY);
    } catch {
      loadIssues = ["não foi possível ler os dados deste navegador"];
      return false;
    }
    const parsed = parseState(raw);
    state = parsed.state;
    loadIssues = parsed.issues;
    unsupported = parsed.unsupported;
    return true;
  };

  /**
   * Apply a mutation to a fresh copy of the latest stored state and persist it atomically.
   * On any failure the last successfully saved state stays both in storage and in memory.
   *
   * @param {(draft: PersonalState) => SaveResult} mutate
   * @returns {SaveResult}
   */
  const commit = (mutate) => {
    // Pick up writes made by other tabs before changing anything; never save over unread data.
    if (!reload()) {
      return fail("storage", "Não foi possível ler os dados deste navegador; nada foi alterado. Tente novamente.");
    }
    if (unsupported) {
      return fail(
        "unsupported_version",
        "Os dados deste navegador foram salvos por outra versão do site e foram preservados. " +
          "Use \"Limpar dados deste navegador\" para recomeçar."
      );
    }
    /** @type {PersonalState} */
    const draft = {
      favorites: { ...state.favorites },
      hidden: { ...state.hidden },
      notes: { ...state.notes },
    };
    const outcome = mutate(draft);
    if (!outcome.ok) {
      return outcome;
    }
    const serialized = serializeState(draft);
    if (utf8Bytes(serialized) > LIMITS.bytes) {
      return fail("limit", "Limite de armazenamento local atingido (256 KB). Remova anotações antigas.");
    }
    if (storage) {
      try {
        storage.setItem(STORAGE_KEY, serialized);
      } catch (err) {
        if (isQuotaError(err)) {
          return fail("quota", "O navegador recusou salvar: espaço de armazenamento local esgotado.");
        }
        return fail("storage", "O navegador recusou salvar os dados locais.");
      }
    }
    state = draft;
    return OK;
  };

  /**
   * @param {"favorites"|"hidden"} section
   * @param {ListingKey} item
   * @param {boolean} enabled
   * @returns {SaveResult}
   */
  const setFlag = (section, item, enabled) => {
    if (!isValidListingKey(item)) {
      return fail("invalid", "Imóvel sem identificação estável; não foi possível salvar.");
    }
    const key = listingKey(item);
    return commit((draft) => {
      const bucket = draft[section];
      if (!enabled) {
        delete bucket[key];
        return OK;
      }
      if (bucket[key]) {
        return OK;
      }
      const max = LIMITS[section];
      if (Object.keys(bucket).length >= max) {
        const label = section === "favorites" ? "favoritos" : "descartados";
        return fail("limit", `Limite de ${max} ${label} atingido. Remova algum antes de adicionar outro.`);
      }
      bucket[key] = { agency_slug: item.agency_slug, external_id: item.external_id, t: now() };
      return OK;
    });
  };

  /**
   * @param {Record<string, FlagEntry>} bucket
   * @returns {ListingKey[]}
   */
  const keysOf = (bucket) =>
    Object.values(bucket).map((e) => ({ agency_slug: e.agency_slug, external_id: e.external_id }));

  reload();

  return {
    /** @returns {StorageMode} */
    get mode() {
      return mode;
    },
    /** @returns {string[]} Problems found while reading saved data (bad parts were dropped). */
    get loadIssues() {
      return [...loadIssues];
    },
    /** @returns {boolean} Saved data uses another format version; writes are refused until cleared. */
    get unsupportedVersion() {
      return unsupported;
    },
    reload,
    /** @param {ListingKey} item */
    isFavorite: (item) => Object.hasOwn(state.favorites, listingKey(item)),
    /** @param {ListingKey} item */
    isHidden: (item) => Object.hasOwn(state.hidden, listingKey(item)),
    /**
     * @param {ListingKey} item
     * @returns {string}
     */
    getNote: (item) => state.notes[listingKey(item)]?.text ?? "",
    /**
     * @param {ListingKey} item
     * @param {boolean} enabled
     */
    setFavorite: (item, enabled) => setFlag("favorites", item, enabled),
    /**
     * @param {ListingKey} item
     * @param {boolean} enabled
     */
    setHidden: (item, enabled) => setFlag("hidden", item, enabled),
    /**
     * Save or (when blank) remove the note for one listing.
     *
     * @param {ListingKey} item
     * @param {string} text
     * @returns {SaveResult}
     */
    setNote: (item, text) => {
      if (!isValidListingKey(item)) {
        return fail("invalid", "Imóvel sem identificação estável; não foi possível salvar.");
      }
      const cleaned = typeof text === "string" ? text.trim() : "";
      if (cleaned.length > LIMITS.noteChars) {
        return fail("too_long", `A anotação deve ter no máximo ${LIMITS.noteChars} caracteres.`);
      }
      const key = listingKey(item);
      return commit((draft) => {
        if (!cleaned) {
          delete draft.notes[key];
          return OK;
        }
        if (!draft.notes[key] && Object.keys(draft.notes).length >= LIMITS.notes) {
          return fail("limit", `Limite de ${LIMITS.notes} anotações atingido. Apague alguma antes.`);
        }
        draft.notes[key] = {
          agency_slug: item.agency_slug,
          external_id: item.external_id,
          t: now(),
          text: cleaned,
        };
        return OK;
      });
    },
    /** @returns {ListingKey[]} */
    favoriteKeys: () => keysOf(state.favorites),
    /** @returns {ListingKey[]} */
    hiddenKeys: () => keysOf(state.hidden),
    counts: () => ({
      favorites: Object.keys(state.favorites).length,
      hidden: Object.keys(state.hidden).length,
      notes: Object.keys(state.notes).length,
    }),
    /**
     * Remove all personal data of this app from this browser (only this app's key).
     *
     * @returns {SaveResult}
     */
    clearAll: () => {
      if (storage) {
        try {
          storage.removeItem(STORAGE_KEY);
        } catch {
          return fail("storage", "O navegador recusou apagar os dados locais.");
        }
      }
      state = emptyState();
      loadIssues = [];
      unsupported = false;
      return OK;
    },
    /**
     * React to a `storage` event from another tab; returns whether this store was affected.
     *
     * @param {{ key: string|null }} event
     * @returns {boolean}
     */
    handleStorageEvent: (event) => {
      if (event.key !== null && event.key !== STORAGE_KEY) {
        return false;
      }
      reload();
      return true;
    },
  };
}

/**
 * Subscribe a store to cross-tab `storage` events on `target` (normally `window`).
 *
 * @param {{ addEventListener: Function, removeEventListener: Function }} target
 * @param {ReturnType<typeof createPersonalStore>} store
 * @param {() => void} onChange
 * @returns {() => void} Unsubscribe function.
 */
export function listenForStorageChanges(target, store, onChange) {
  /** @param {{ key: string|null }} event */
  const handler = (event) => {
    if (store.handleStorageEvent(event)) {
      onChange();
    }
  };
  target.addEventListener("storage", handler);
  return () => target.removeEventListener("storage", handler);
}
