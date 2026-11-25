// Unit tests for the browser-local personal store and search body builder.
// Run with: node --test tests/web/
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildSearchBody } from "../../src/imob/web/api.js";
import {
  LIMITS,
  STORAGE_KEY,
  createPersonalStore,
  listenForStorageChanges,
  parseState,
  resolveStorage,
} from "../../src/imob/web/personal_state.js";

class MemoryStorage {
  constructor(initial = {}) {
    this.data = new Map(Object.entries(initial));
    this.failNextWith = null;
  }
  getItem(key) {
    return this.data.has(key) ? this.data.get(key) : null;
  }
  setItem(key, value) {
    if (this.failNextWith) {
      const err = this.failNextWith;
      this.failNextWith = null;
      throw err;
    }
    this.data.set(key, String(value));
  }
  removeItem(key) {
    this.data.delete(key);
  }
}

const quotaError = () => Object.assign(new Error("full"), { name: "QuotaExceededError", code: 22 });
const listing = (n, slug = "arcari") => ({ agency_slug: slug, external_id: String(n), id: n });

test("favorites, hidden and notes persist under one key and survive a reload", () => {
  const storage = new MemoryStorage();
  const store = createPersonalStore({ storage, now: () => 1000 });
  assert.equal(store.mode, "persistent");
  assert.deepEqual(store.setFavorite(listing(1), true), { ok: true });
  assert.deepEqual(store.setHidden(listing(2), true), { ok: true });
  assert.deepEqual(store.setNote(listing(1), "  Visitar sábado  "), { ok: true });
  assert.deepEqual([...storage.data.keys()], [STORAGE_KEY]);

  const again = createPersonalStore({ storage });
  assert.ok(again.isFavorite(listing(1)));
  assert.ok(again.isHidden(listing(2)));
  assert.equal(again.getNote(listing(1)), "Visitar sábado");
  assert.deepEqual(again.favoriteKeys(), [{ agency_slug: "arcari", external_id: "1" }]);
  assert.deepEqual(again.hiddenKeys(), [{ agency_slug: "arcari", external_id: "2" }]);
  const saved = JSON.parse(storage.getItem(STORAGE_KEY));
  assert.equal(saved.v, 1);
  assert.deepEqual(saved.notes["arcari/1"], {
    agency_slug: "arcari",
    external_id: "1",
    t: 1000,
    text: "Visitar sábado",
  });

  assert.deepEqual(again.setNote(listing(1), "   "), { ok: true });
  assert.equal(again.getNote(listing(1)), "");
  assert.equal(again.counts().notes, 0);
});

test("identity is the stable agency + external ID, never the transient database ID", () => {
  const store = createPersonalStore({ storage: new MemoryStorage() });
  store.setFavorite({ agency_slug: "arcari", external_id: "7", id: 1 }, true);
  // After a database rebuild, ID 1 belongs to another listing: it must not look favorited.
  assert.equal(store.isFavorite({ agency_slug: "ederson", external_id: "99", id: 1 }), false);
  assert.equal(store.isFavorite({ agency_slug: "ederson", external_id: "7", id: 1 }), false);
  assert.equal(store.isFavorite({ agency_slug: "arcari", external_id: "7", id: 42 }), true);
  assert.equal(store.setFavorite({ agency_slug: "Bad Slug", external_id: "1" }, true).reason, "invalid");
});

test("quota failure is reported and keeps the last saved state in storage and memory", () => {
  const storage = new MemoryStorage();
  const store = createPersonalStore({ storage });
  store.setFavorite(listing(1), true);
  const before = storage.getItem(STORAGE_KEY);

  storage.failNextWith = quotaError();
  const result = store.setFavorite(listing(2), true);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "quota");
  assert.match(result.message, /espaço/);
  assert.equal(storage.getItem(STORAGE_KEY), before);
  assert.equal(store.isFavorite(listing(2)), false);
  assert.equal(store.isFavorite(listing(1)), true);

  storage.failNextWith = new Error("boom");
  assert.equal(store.setNote(listing(1), "x").reason, "storage");
  assert.equal(store.getNote(listing(1)), "");
});

test("limits refuse new entries with a reason and never evict existing ones", () => {
  const storage = new MemoryStorage();
  const store = createPersonalStore({ storage });
  for (let i = 0; i < LIMITS.favorites; i += 1) {
    assert.equal(store.setFavorite(listing(i), true).ok, true);
  }
  const refused = store.setFavorite(listing("extra"), true);
  assert.equal(refused.reason, "limit");
  assert.equal(store.counts().favorites, LIMITS.favorites);
  assert.ok(store.isFavorite(listing(0)));
  assert.equal(store.setFavorite(listing(0), true).ok, true, "re-adding an existing entry is fine");

  for (let i = 0; i < LIMITS.hidden; i += 1) {
    assert.equal(store.setHidden(listing(i, "solar"), true).ok, true);
  }
  assert.equal(store.setHidden(listing("extra", "solar"), true).reason, "limit");

  assert.equal(store.setNote(listing(1), "x".repeat(LIMITS.noteChars + 1)).reason, "too_long");
  assert.equal(store.setNote(listing(1), "x".repeat(LIMITS.noteChars)).ok, true);
});

test("the serialized state is capped in bytes", () => {
  const store = createPersonalStore({ storage: new MemoryStorage() });
  let result = { ok: true };
  let i = 0;
  // 200 notes of 2000 multi-byte chars would exceed 256 KiB.
  while (result.ok && i < LIMITS.notes) {
    result = store.setNote(listing(i), "ç".repeat(LIMITS.noteChars));
    i += 1;
  }
  assert.equal(result.ok, false);
  assert.equal(result.reason, "limit");
  assert.match(result.message, /256 KB/);
  assert.equal(store.counts().notes, i - 1);
});

test("corrupt JSON, wrong version and invalid entries drop only the bad part", () => {
  assert.deepEqual(parseState("{not json").state, { favorites: {}, hidden: {}, notes: {} });
  assert.match(parseState("{not json").issues[0], /corrompidos/);
  assert.match(parseState(JSON.stringify({ v: 2, favorites: {} })).issues[0], /versão/);
  assert.equal(parseState("[]").issues.length, 1);

  const good = { agency_slug: "arcari", external_id: "1", t: 1 };
  const raw = JSON.stringify({
    v: 1,
    favorites: {
      "arcari/1": good,
      "arcari/2": { agency_slug: "arcari", external_id: "3", t: 1 }, // key mismatch
      "arcari/4": { agency_slug: "arcari", external_id: "4" }, // no timestamp
    },
    hidden: "nope",
    notes: {
      "arcari/1": { ...good, text: "ok" },
      "arcari/5": { agency_slug: "arcari", external_id: "5", t: 1, text: "x".repeat(LIMITS.noteChars + 1) },
    },
  });
  const { state, issues } = parseState(raw);
  assert.deepEqual(Object.keys(state.favorites), ["arcari/1"]);
  assert.deepEqual(state.hidden, {});
  assert.deepEqual(Object.keys(state.notes), ["arcari/1"]);
  assert.equal(issues.length, 3);

  const storage = new MemoryStorage({ [STORAGE_KEY]: raw });
  const store = createPersonalStore({ storage });
  assert.ok(store.isFavorite(listing(1)));
  assert.equal(store.loadIssues.length, 3);
});

test("missing storage or SecurityError falls back to in-memory state", () => {
  const securityError = () => {
    throw Object.assign(new Error("denied"), { name: "SecurityError" });
  };
  assert.equal(resolveStorage(securityError), null);
  assert.equal(resolveStorage(() => undefined), null);
  assert.equal(
    resolveStorage(() => ({ getItem: securityError, setItem() {}, removeItem() {} })),
    null
  );

  const store = createPersonalStore({ storage: resolveStorage(securityError) });
  assert.equal(store.mode, "memory");
  assert.equal(store.setFavorite(listing(1), true).ok, true);
  assert.ok(store.isFavorite(listing(1)));
  assert.equal(store.clearAll().ok, true);
  assert.equal(store.counts().favorites, 0);
});

test("clearAll removes only this app's key", () => {
  const storage = new MemoryStorage({ other: "keep" });
  const store = createPersonalStore({ storage });
  store.setFavorite(listing(1), true);
  assert.equal(store.clearAll().ok, true);
  assert.equal(storage.getItem(STORAGE_KEY), null);
  assert.equal(storage.getItem("other"), "keep");
  assert.equal(store.counts().favorites, 0);
});

test("storage events from other tabs reload state, and writes merge with other tabs", () => {
  const storage = new MemoryStorage();
  const tabA = createPersonalStore({ storage });
  const tabB = createPersonalStore({ storage });
  tabA.setFavorite(listing(1), true);

  const listeners = new Map();
  const target = {
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type) => listeners.delete(type),
  };
  let changes = 0;
  const unsubscribe = listenForStorageChanges(target, tabB, () => {
    changes += 1;
  });
  listeners.get("storage")({ key: "unrelated" });
  assert.equal(changes, 0);
  listeners.get("storage")({ key: STORAGE_KEY });
  assert.equal(changes, 1);
  assert.ok(tabB.isFavorite(listing(1)));

  // Tab B writes without having seen a later tab A change: nothing is lost.
  tabA.setHidden(listing(2), true);
  tabB.setFavorite(listing(3), true);
  const merged = createPersonalStore({ storage });
  assert.ok(merged.isFavorite(listing(1)));
  assert.ok(merged.isFavorite(listing(3)));
  assert.ok(merged.isHidden(listing(2)));

  unsubscribe();
  assert.equal(listeners.has("storage"), false);
});

test("search body keeps known filters, adds only listing keys and drops UI-only flags", () => {
  const params = new URLSearchParams({
    q: " solar ",
    purpose: "aluguel",
    types: "apartamento,casa",
    neighborhoods: "centro",
    min_price: "1000",
    max_area: "",
    min_bedrooms: "2",
    only_with_price: "false",
    deduplicate: "true",
    only_favorites: "true",
    include_hidden: "true",
    page: "2",
    page_size: "30",
    sort: "menor_preco",
  });
  const fav = { agency_slug: "arcari", external_id: "1", t: 5, text: "secret" };
  const body = buildSearchBody(params, { includeKeys: [fav], excludeKeys: [] });
  assert.deepEqual(body, {
    q: "solar",
    purpose: "aluguel",
    sort: "menor_preco",
    types: ["apartamento", "casa"],
    neighborhoods: ["centro"],
    min_price: 1000,
    min_bedrooms: 2,
    page: 2,
    page_size: 30,
    only_with_price: false,
    deduplicate: true,
    include_keys: [{ agency_slug: "arcari", external_id: "1" }],
  });
  assert.equal(JSON.stringify(body).includes("secret"), false);

  const emptyFavs = buildSearchBody(new URLSearchParams(), { includeKeys: [] });
  assert.deepEqual(emptyFavs, { include_keys: [] });
  const excluded = buildSearchBody(new URLSearchParams(), { includeKeys: null, excludeKeys: [fav] });
  assert.deepEqual(excluded, { exclude_keys: [{ agency_slug: "arcari", external_id: "1" }] });
});

test("future-version data is preserved unchanged and writes are refused until cleared", () => {
  const future = JSON.stringify({ v: 2, favorites: { anything: { new: "shape" } } });
  const storage = new MemoryStorage({ [STORAGE_KEY]: future });
  const store = createPersonalStore({ storage });
  assert.equal(store.unsupportedVersion, true);
  assert.equal(store.mode, "persistent");
  assert.match(store.loadIssues[0], /outra versão/);

  for (const result of [
    store.setFavorite(listing(1), true),
    store.setHidden(listing(1), true),
    store.setNote(listing(1), "nota"),
    store.setFavorite(listing(1), false),
  ]) {
    assert.equal(result.ok, false);
    assert.equal(result.reason, "unsupported_version");
    assert.match(result.message, /Limpar dados/);
  }
  assert.equal(storage.getItem(STORAGE_KEY), future);
  assert.equal(store.counts().favorites, 0);

  // A version object is never stringified into user-facing text.
  const hostile = { toString: () => "INJECTED" };
  const parsed = parseState(JSON.stringify({ v: { toString: hostile } }));
  assert.equal(parsed.issues.join(" ").includes("INJECTED"), false);
  assert.equal(parsed.issues.join(" ").includes("[object"), false);

  // Compatible data written by another tab unblocks the store.
  storage.data.set(STORAGE_KEY, JSON.stringify({ v: 1, favorites: {}, hidden: {}, notes: {} }));
  assert.equal(store.setFavorite(listing(2), true).ok, true);
  assert.equal(store.unsupportedVersion, false);

  // Explicit clear also unblocks.
  storage.data.set(STORAGE_KEY, future);
  assert.equal(store.setFavorite(listing(3), true).reason, "unsupported_version");
  assert.equal(store.clearAll().ok, true);
  assert.equal(store.unsupportedVersion, false);
  assert.equal(store.setFavorite(listing(3), true).ok, true);
});

test("a read failure during a write keeps the snapshot and does not overwrite a newer tab", () => {
  const storage = new MemoryStorage();
  const tabA = createPersonalStore({ storage });
  const tabB = createPersonalStore({ storage });
  assert.equal(tabA.setFavorite(listing(1), true).ok, true);
  assert.equal(tabB.setHidden(listing(2), true).ok, true); // tab A has not seen this yet
  const newer = storage.getItem(STORAGE_KEY);

  const realGetItem = storage.getItem.bind(storage);
  storage.getItem = () => {
    throw new Error("read failed");
  };
  const failed = tabA.setFavorite(listing(3), true);
  assert.equal(failed.ok, false);
  assert.equal(failed.reason, "storage");
  assert.equal(realGetItem(STORAGE_KEY), newer, "stale snapshot must not be saved");
  assert.ok(tabA.isFavorite(listing(1)), "last snapshot is kept in memory");
  assert.equal(tabA.isFavorite(listing(3)), false);
  assert.equal(tabA.mode, "persistent");

  storage.getItem = realGetItem;
  assert.equal(tabA.setFavorite(listing(3), true).ok, true);
  const merged = createPersonalStore({ storage });
  assert.ok(merged.isFavorite(listing(1)));
  assert.ok(merged.isFavorite(listing(3)));
  assert.ok(merged.isHidden(listing(2)), "newer tab write survives the retry");
});
