# IMOB public Docker port (MySQL, browser-local state, scheduled sync)

## Objective

Make IMOB (`/home/htxzz77/Projects/imob`) deployable as an isolated, anonymous, read-only public site on the same server as GoGon, and work like GoGon. There is no task spec file. The requirements come from the user request relayed by the parent agent:

- Docker Compose stack modelled on GoGon (`/home/htxzz77/Projects/GoGon/compose.yaml`, `Dockerfile`, `docs/deployment.md`): a non-root, read-only app container, a private `mysql:8.4` service of its own on an internal network, named volumes, published on loopback only. IMOB has its own compose project, volumes, networks, `.env` and directory. It shares no data with GoGon.
- Anonymous public access like cookbook: no login, and no server-side per-user data. The app sends its own security headers. **The parent's decision is option C.** Cookbook's existing Caddy owns 80/443 on that server, so IMOB binds a dedicated loopback port only. Public HTTPS exposure through the existing Caddy is a separate step that needs approval. IMOB does not modify cookbook or bind 80/443.
- Remove every public write and scrape trigger: the sync UI and "force sync" buttons, `POST /api/sync`, `POST /api/properties/{id}/enrich`, `PATCH /api/properties/{id}/state`, and `PUT /api/maps/quadras/{n}/pdf`, which is the anonymous PDF upload.
- Sync runs automatically at 06:00 and 18:00 `America/Sao_Paulo`. Pato Branco-PR uses BRT (UTC-3) with no DST since 2019, and nothing in the repo contradicts this.
- Favorites, hidden ("descartados") and notes move into a bounded `localStorage` store in the browser. It handles quota and errors safely.
- MySQL replaces SQLite, including the FTS5 search, the `printf` SQL function and the SQLite PRAGMAs. Existing SQLite data is not migrated; the MySQL database starts empty.

**Local deliverables** (this plan) are code, tests, Dockerfile, compose files and docs, verified on the workstation. **Real deployment** happens on the shared server: creating `.env`, `docker compose up`, the first sync, and any Caddy or DNS change. Each of those needs explicit user approval and is listed only as a prerequisite (Task 13). Nothing in GoGon or cookbook is changed.

Stages run one at a time, with a review after each: **Stage A** browser-local state and public API restrictions (Tasks 1–4). **Stage B** MySQL port (Tasks 5–9). **Stage C** scheduled sync and Docker (Tasks 10–12). **Stage D** integrated checks and deployment runbook (Tasks 13–14).

## Implementation Plan

### Stage A: browser-local personal state, public API restrictions (still on SQLite)

- [ ] 1. **Remove the public mutation endpoints.**
  - `src/imob/api/routes.py:271-306`: delete the `enrich_property_detail` and `update_user_state` routes.
  - `routes.py:324-367`: delete `trigger_sync`. Keep `GET /api/sync/status` at `routes.py:309`, which is read-only; Stage C reshapes its payload.
  - `src/imob/api/maps.py:49-60`: delete `put_quadra_pdf`. The CLI `imob maps-import` (`src/imob/cli.py:247-252`) stays as the only, operator-only way to add PDFs.
  - Remove the now-unused imports and `src/imob/api/schemas.py` `TriggerSyncRequest`/`TriggerSyncAccepted`, unless the CLI still uses them.
  - Remove `app.state.bg_tasks` handling (`src/imob/api/app.py:60-61,80-83`).
  - `SyncService.enrich_property` (`src/imob/services/sync_service.py:305-329`) loses its only caller: delete it together with `apply_detail_patch` callers in the API. Keep `apply_detail_patch` in the repository only if a test or CLI still uses it; otherwise delete it, along with its port in `src/imob/ports.py`.
  - Acceptance: `POST /api/sync`, `POST /api/properties/1/enrich`, `PATCH /api/properties/1/state` and `PUT /api/maps/quadras/1/pdf` each return 404 or 405, and no GET handler writes to the DB or makes outbound HTTP.
- [ ] 2. **Remove server-side personal state from the read models and queries.**
  - `src/imob/domain/models.py`: drop `is_favorite`, `is_hidden` and `user_notes` from `PropertySummary` (`:289-291`); drop `UserPropertyStatePatch` (`:363-370`); drop `favorites_count`/`hidden_count` from `CatalogStats` (`:406-407`).
  - `PropertyFilterParams` (`:311-348`): replace `only_favorites`/`include_hidden` with bounded `include_ids: tuple[int, ...]` (max 200; when non-empty, only these IDs are returned) and `exclude_ids: tuple[int, ...]` (max 500). Cap `q` with `max_length=120`.
  - `src/imob/db/repository.py`:
    - `_build_filter_conditions` (`:1097-1104`): use `PropertyRow.id.in_(include_ids)` and `not_in(exclude_ids)`.
    - Remove `update_user_state` (`:827-860`), the `is_hidden` filters in `get_facets` (`:876,926`), the fav/hidden counts in `get_stats` (`:1000-1011,1040-1041`), and the `_to_property_summary` fields (`:1313-1315`).
    - Stop writing the three columns at `:331-333`. The columns stay in the SQLite schema until Stage B's fresh baseline drops them.
  - Delete the matching protocol in `src/imob/ports.py:191`, `CatalogService.update_user_state` (`src/imob/services/catalog_service.py:68`), and the CLI stats line `src/imob/cli.py:176`.
- [ ] 3. **Add a read-only search body for ID lists.** Add `POST /api/properties/search` in `routes.py`. Its pydantic body carries the same filters as `GET /api/properties` plus `include_ids`/`exclude_ids`, with list and string length limits and `extra="forbid"`. It only reads, and avoids URL-length limits. `GET /api/properties` stays for API users, without the ID lists. Share the conversion to `PropertyFilterParams` between the two handlers.
- [ ] 4. **Browser-only state and UI.**
  - Create `src/imob/web/personal_state.js` as a pure ES module with no DOM access.
    - It stores one `localStorage` key, `imob.personal.v1`, holding `{v:1, favorites, hidden, notes}`. Each entry keyed by property ID stores `agency_slug` + `external_id` (a stale-ID guard) and a timestamp.
    - Limits: favorites ≤ 200, hidden ≤ 500, notes ≤ 200 entries of ≤ 2000 chars each, and the serialized JSON ≤ 256 KiB.
    - On load, a JSON parse failure, a wrong version or an invalid entry drops only the bad part. Storage that is missing or throws `SecurityError` falls back to in-memory state.
    - On save, catch `QuotaExceededError` and other errors and return a typed result. Never evict silently; refuse new entries at the limit with a reason.
    - A `storage` event listener re-reads the state for multi-tab use. Provide `clearAll()`.
  - `src/imob/web/app.js`:
    - Replace `toggleUserFlag` (`:1154-1168`) and the notes save (`:1486-1522`) with calls to the store, and show a non-blocking error for a quota or limit failure.
    - Merge the local flags into cards (`:818,1075-1091`) and drop stale entries whose slug/external_id don't match.
    - `refreshProperties` (`:727`) calls the new POST search with `include_ids` when "Somente favoritos" is checked and `exclude_ids` unless `f-hidden` is checked.
    - `stat-favs` (`:566`) comes from the store.
    - Remove the enrich button (`:1303-1323`) and the maps upload input and handler (`:1733-1750`).
  - `src/imob/web/api.js`: remove `enrichPropertyDetail` (`:628`), `patchPropertyState` (`:644`), `triggerSync` (`:677`) and `uploadQuadraPdf` (`:739`) with their typedefs. Remove the `is_favorite`/`is_hidden`/`user_notes` and `favorites_count`/`hidden_count` decoding (`:110-112,348-350,535-536`). Add `searchProperties(body, signal)`.
  - Also delete the sync UI. In `src/imob/web/index.html`, remove `#btn-sync-smart` (`:34-36`), `#sync-banner` (`:41-44`) and `#btn-force-sync-all` (`:258`). In `app.js`, remove the per-agency "Sync" buttons (`:1578-1602`), the empty-state sync button (`:773-787`), `startSync`, `checkInitialSyncStatus`, `startSyncPolling` and `stopSyncPolling` (`:226,399-405,1616-1700,1765-1773`).
  - The agencies modal shows read-only last-sync data and the schedule text.
  - Add a "Seus favoritos e anotações ficam apenas neste navegador" note and a "Limpar dados deste navegador" button.

### Stage B: MySQL persistence port

- [ ] 5. **Dependencies and settings.**
  - `pyproject.toml:7-17`: add `pymysql[rsa]`, which includes `cryptography`, needed for MySQL 8.4 `caching_sha2_password` without TLS. Add `tzdata` so `zoneinfo` works in slim images and on Windows. Bump `version` to `0.2.0` and update `uv.lock`.
  - `src/imob/config.py:43-76`: replace `db_path` and `sqlalchemy_url` with:
    - `mysql_host` (default `127.0.0.1`), `mysql_port` (3306), `mysql_user`, `mysql_password: SecretStr`, `mysql_database` (`imob`);
    - a `database_url` property built with `sqlalchemy.engine.URL.create("mysql+pymysql", …, query={"charset": "utf8mb4"})`, so special characters are escaped and the password never appears in logs or `repr`;
    - `maps_dir: Path`, defaulting to `resolve_app_root()/"data"/"maps"`.
  - Missing credentials make startup fail closed with a clear error that names the variable, never its value.
- [ ] 6. **Engine and migrations.**
  - Rewrite `src/imob/db/engine.py`:
    - Delete `FTS5_STATEMENTS`, `create_sqlite_engine` and `ensure_fts5_objects`.
    - Add `create_mysql_engine(settings)` with `pool_pre_ping=True`, `pool_recycle=1800`, `isolation_level="READ COMMITTED"`, and `connect_args={"init_command": "SET time_zone='+00:00'"}`.
    - `run_migrations(engine)` passes the connection through `AlembicConfig.attributes["connection"]`, so no URL or password is written into the Alembic config.
  - Update `migrations/env.py:27-41` to use `config.attributes.get("connection")` when present and drop `render_as_batch`. Set `alembic.ini:5` to an empty placeholder with no SQLite URL.
  - Replace `migrations/versions/0001_initial_schema.py`, `0002_negotiation_identity.py` and `0003_similarity_pairs.py` with one `0001_mysql_baseline.py`. No data migration is required. It creates the same tables minus the three personal columns and `ix_properties_favorite`, and `ix_properties_active_purpose_type` loses `is_hidden`. Details:
    - InnoDB, `utf8mb4` / `utf8mb4_0900_ai_ci` table options.
    - `sa.Double()` for every price, area and coordinate column. Plain `Float` becomes 4-byte `FLOAT` on MySQL and corrupts prices like 1 250 000.50.
    - `DATETIME(fsp=6)` for the timestamps.
    - `MEDIUMTEXT` for `description` and `image_urls_json`.
    - A `FULLTEXT` index `ft_properties_search` on `(title, neighborhood, street, building_name, ref_code, description)`.
    - On `property_matches`, keep the FK `ON DELETE CASCADE` and **omit `ck_match_order`**. MySQL rejects a CHECK on a column used in a FK referential action (error 3823). Enforce `left_id < right_id` in code (Task 7).
- [ ] 7. **ORM and repository dialect fixes.**
  - `src/imob/db/orm.py:92-127,166-171`: `Float` → `Double`. Use `DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")`, MEDIUMTEXT variants, and the `mysql_charset`/`mysql_collate` table args. Remove the personal columns and `ix_properties_favorite` (`:60,138-140`), drop `ck_match_order` (`:204`), and declare the FULLTEXT index (`Index(..., mysql_prefix="FULLTEXT")`) so the metadata matches the migration.
  - `src/imob/db/repository.py`:
    - `:86-91`: rewrite `_build_fts_match_expression` to produce boolean-mode terms `+tok*`. Tokens are ≥ 2 chars, at most 8, and stripped of the operators `+-<>()~*"@`.
    - `:1198-1200`: replace the FTS5 subquery with `text("MATCH(title, neighborhood, street, building_name, ref_code, description) AGAINST (:q IN BOOLEAN MODE)")`. The column list must equal the index.
    - `:620`: replace `func.printf("unique-%d", id)` with `func.concat("unique-", PropertyRow.id)`.
    - Update the class docstring at `:103`.
  - `_rebuild_similarity_groups` (`:1344-1365`): assert that `pair.left_id < pair.right_id`, or normalize the order, before inserting.
  - Keep `SyncService._write_lock` (`src/imob/services/sync_service.py:64-66`) and update its comment. It serializes the global `property_matches` rebuild, which would deadlock under concurrent InnoDB writers.
  - Add `src/imob/db/locks.py` with a context manager over `GET_LOCK('imob_sync', 0)`/`RELEASE_LOCK` on a dedicated connection. `SyncService.sync_agencies` takes it through a small port method, so a CLI `imob sync` and the scheduled run can never overlap across processes. If the lock is busy, it raises the existing "already in progress" `DomainValidationError`.
- [ ] 8. **CLI and app wiring.**
  - `src/imob/cli.py:119-125,157-161,254-260`: use `create_mysql_engine` and `run_migrations(engine)`, and remove the FTS calls. Change the "SQLite" strings (`:67-68,166,217`) to report host/database without credentials.
  - `maps-import` uses `settings.maps_dir`. Change `src/imob/maps.py:86-114` and `src/imob/api/maps.py:31,38` from `db_path.parent` plus `"maps"` to `maps_dir`.
  - `src/imob/api/app.py:53-58`: create the engine, run the migrations, seed the agencies. Add a DB `SELECT 1` to `/api/health` (`routes.py:71-78`) that returns 503 when MySQL is unreachable, for the compose healthcheck.
  - Update the FastAPI `version` at `app.py:92` to `0.2.0`.
- [ ] 9. **Tests on MySQL.**
  - Add `compose.test.yaml` with project `imob-test`: `mysql:8.4` on `127.0.0.1:33306`, a tmpfs datadir, and the same server flags as production (Task 11).
  - Add `tests/conftest.py`. Its fixture reads `IMOB_TEST_MYSQL_URL` (root on the throwaway server only), creates a unique `imob_test_<hex>` database per test, runs the migrations, yields the settings and engine, then drops the database. Without the URL it skips with a clear reason, and when `IMOB_REQUIRE_MYSQL=1` is set it fails instead.
  - Port the files that use `create_sqlite_engine`/`db_path`: `tests/test_repository_and_api.py` (`:17-19,80-90,205,263-292,339-345`), `test_identity_persistence.py`, `test_catalog_coverage.py`, `test_cli_and_config.py`, `test_agil.py` and `test_maps.py`. The maps tests move to `maps_dir` and assert that PUT is gone.
  - New tests:
    - FULLTEXT prefix and accent-insensitive search (`agua` finds `Água`, and the 2-letter token `la` finds `La Salle`).
    - Price precision round trip (`1250000.55`).
    - UTC datetime round trip.
    - Dedup ranking with `concat`.
    - `include_ids`/`exclude_ids` bounds return 422 when exceeded.
    - The named lock rejects a second concurrent sync.

### Stage C: automated sync and Docker deployment

- [ ] 10. **Fixed-time scheduler.**
  - In `src/imob/config.py`, replace `auto_sync_enabled`/`timer_check_minutes` semantics with `sync_schedule: tuple[time, ...] = (06:00, 18:00)` (env `IMOB_SYNC_TIMES="06:00,18:00"`) and `sync_timezone: str = "America/Sao_Paulo"`, validated with `zoneinfo.ZoneInfo`. Keep `auto_sync_enabled`, which `serve --no-timer` uses, and `sync_interval_hours` for CLI TTL runs.
  - `src/imob/services/sync_service.py:92-120`: rewrite `_periodic_loop`.
    - Compute the next slot with a pure `next_run_at(now_utc, times, tz)` function and sleep until then, recomputing after each wake so clock jumps are safe.
    - Then run `sync_agencies(force=True)`, logging and swallowing errors.
    - There is no run on boot and no catch-up for a missed slot, which keeps "no requests on startup" (README `:22`).
  - `SyncRuntimeStatus` (`:30-42`) exposes `next_scheduled_run_at`, the schedule and timezone, `is_running` and progress. It drops `sync_interval_hours`/`next_timer_check_at`.
  - Update the agencies modal text in `app.js:1542-1543`.
  - Unit-test `next_run_at` for 05:59 → 06:00, 06:00:01 → 18:00, 18:30 → next day 06:00, using fixed `datetime`s and no sleeping.
- [ ] 11. **Container and compose (local deliverables only).**
  - Add `Dockerfile`:
    - A pinned `python:3.12-slim-bookworm` base (pinned tag, digest comment as in GoGon `Dockerfile:5-7`).
    - A build stage with a pinned uv image running `uv sync --frozen --no-dev --no-editable`.
    - A runtime stage that copies `.venv`, `alembic.ini` and `migrations/`, with `IMOB_APP_ROOT=/app` and `IMOB_MAPS_DIR=/var/lib/imob/maps`.
    - A non-root UID 10001, `EXPOSE 8000`, a Python `urllib` HEALTHCHECK on `/api/health`, and `CMD ["imob","serve","--host","0.0.0.0","--port","8000"]`. An explicit port skips port probing at `cli.py:201-211`.
    - Verify that `web/*.js|css|html` end up inside the wheel.
  - Add `.dockerignore`, modelled on GoGon's: exclude `.venv`, `dist/`, `build/`, `data/`, caches, `.env*` and `tests/`.
  - Add `compose.yaml` with `name: imob`:
    - **Service `imob`**:
      - `env_file: .env`, with `IMOB_MYSQL_HOST: mysql`;
      - `ports: "127.0.0.1:${IMOB_PORT:-8090}:8000"`, never `0.0.0.0`, and different from GoGon's 8787;
      - networks `default` (outbound scraping) and `db`;
      - `read_only`, tmpfs `/tmp`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit`, `init: true`, `restart: unless-stopped`;
      - the `imob-maps` volume mounted `:ro`;
      - local logging at 10m×3.
    - **Service `mysql`**:
      - `mysql:8.4` with `MYSQL_PASSWORD: ${IMOB_MYSQL_PASSWORD:?…}` and `MYSQL_RANDOM_ROOT_PASSWORD`;
      - `command: --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci --innodb-ft-min-token-size=2 --innodb-ft-enable-stopword=OFF --mysqlx=OFF`. The default stopwords include `de`/`la`, and the default minimum token length is 3;
      - volume `imob-mysql`, the `db` network only (`internal: true`), no ports, and the GoGon-style `mysqladmin ping` healthcheck.
    - **Service `imob-ops`** (`profiles: [ops]`): the same image, the `imob-maps` volume mounted read-write, and `./maps-import` bind-mounted read-only, for `imob maps-import /import` and a manual `imob sync`.
    - Compose project naming gives volumes `imob_imob-mysql` and `imob_imob-maps`, which are isolated from `gogon_*`.
  - Add `.env.example` with no secrets: `IMOB_PORT`, `IMOB_MYSQL_USER=imob`, `IMOB_MYSQL_PASSWORD=` (empty), `IMOB_MYSQL_DATABASE=imob`, `IMOB_SYNC_TIMES`, `IMOB_SYNC_TIMEZONE`. Add `.env` and `maps-import/` to `.gitignore`.
- [ ] 12. **Public-access hardening in the app itself.** Proxy headers come later, so they can't be relied on.
  - Add a small middleware in `src/imob/api/app.py` that sets `Content-Security-Policy`: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'`. Listing images come from agency hosts, and the PDF iframe is same-origin. Also set `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Cache-Control: no-cache` on `/` and `/static`.
  - Disable `/docs` and `/redoc` (`docs_url=None`, `redoc_url=None`), because Swagger UI loads a CDN script that the CSP would block. Remove the "API Docs" link at `index.html:37`. `/openapi.json` stays.
  - Check that `app.js` never sets `style` attributes through `setAttribute` or HTML strings, which `style-src 'self'` would block. If it does, switch to CSS classes.
  - Make the UI paths relative (`index.html:7,285` `static/…`, and the `requestJson` base in `api.js:428`). The same build then works at a host root or behind a path prefix, whichever edge is approved later.

### Stage D: integrated checks, docs, deployment gate

- [ ] 13. **Docs and the deployment runbook.**
  - Add `docs/deployment.md`. It covers:
    - host prerequisites;
    - `.env` creation with `openssl rand -hex 24`, `umask 077`, `chmod 600`, never printed;
    - a separate checkout directory, not inside GoGon's;
    - checking that the port is free (`ss -ltn | grep :8090`) and that `docker compose ls` shows no `imob` project;
    - `docker compose build && docker compose up -d && docker compose ps`;
    - the first catalog load, `docker compose run --rm imob-ops imob sync --force`. The DB starts empty and the scheduler does not run on boot;
    - map import;
    - logs, update and reset (typed-confirmation volume removal, as in cookbook README §7);
    - "no backups".
  - A clearly separated **"Public exposure: requires approval"** section describes the Caddy reverse proxy to `127.0.0.1:${IMOB_PORT}`. It lists the hostname or path decision and warns that IMOB must not inherit cookbook's `img-src 'self' data:` CSP. It states that nothing there is applied by this work.
  - Update `README.md`: the stack line `:3`; the sync section `:21-25,94-105`; the endpoint table `:125-138`, removing the four mutation routes and adding `POST /api/properties/search`; the env var table `:148-157`; the Windows portable section `:113-121` (see Task 14); the maps section `:255-276`, removing the PUT; the personal-state description `:30`; and the stale `cd` path at `:76`.
  - Add a short "Mudanças 0.2.0" summary in the README: the MySQL port, the new schedule, browser-only personal state and the removed endpoints.
- [ ] 14. **Windows portable build.** It bundles SQLite (`packaging/windows/build_windows_portable.py:17,246-268,323-327`) and cannot work without a local MySQL. Unless the user decides otherwise (see open decisions), make `build_portable` exit early with a clear message that the portable build is retired as of 0.2.0, and remove its README section. Leave the existing `dist/` and `build/` artifacts untouched: they are build outputs, and deleting them needs approval.

## Verification Criteria

- `uv lock --check` (or `uv sync --frozen`) succeeds after the dependency change.
- `uv run ruff check src tests` and `uv run ruff format --check src tests`: no findings.
- `uv run mypy src tests`: no errors (strict).
- `docker compose -f compose.test.yaml up -d --wait`, then `IMOB_TEST_MYSQL_URL=mysql+pymysql://root:…@127.0.0.1:33306 IMOB_REQUIRE_MYSQL=1 uv run pytest -v`: all tests pass and none are skipped for a missing MySQL. Afterwards, `docker compose -f compose.test.yaml down -v`.
- `uv run pytest -v` without MySQL: the unit tests pass, and the DB tests are reported as skipped with the reason.
- `grep -rnE "sqlite|fts5|printf|PRAGMA" src migrations alembic.ini`: no matches.
- `grep -rnE "triggerSync|enrichPropertyDetail|patchPropertyState|uploadQuadraPdf|btn-sync|btn-force-sync" src/imob/web`: no matches.
- `node --check src/imob/web/app.js src/imob/web/api.js src/imob/web/personal_state.js`: OK. Optionally, `node --test tests/web/` for the store, covering quota rejection, corrupt JSON and limits.
- Local stack: `cp .env.example .env`, set a throwaway password, run `docker compose config` (valid; `mysql` shows no `ports`; `imob` publishes only on `127.0.0.1`), then `docker compose up -d --build` and `docker compose ps` (both services healthy).
  - `curl -fsS http://127.0.0.1:8090/api/health` returns `{"status":"ok",…}`.
  - `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8090/api/sync` returns 404 or 405. The same holds for the enrich POST, the state PATCH and the maps PUT.
  - `curl -sI http://127.0.0.1:8090/` shows the CSP header.
  - `docker compose exec imob sh -c 'touch /app/x'` fails, because the root filesystem is read-only.
  - `docker compose exec imob id -u` prints `10001`.
- Local manual browser check:
  - Favorite, hide and note a property. Reload: the state persists. A second browser profile does not see it.
  - Fill storage with a DevTools snippet: the UI shows the quota message and does not crash.
  - The "Somente favoritos" and "Incluir descartados" filters work.
- `docker compose run --rm imob-ops imob sync --agency apolar --force` completes, and `GET /api/sync/status` shows the next run at the next 06:00 or 18:00 BRT.
- `docker compose down` (no `-v`). Confirm that `docker volume ls` shows only `imob_*` volumes created by this work, and that no `gogon_*` or `cookbook_*` resource was touched.

## Potential Risks and Mitigations

1. MySQL FULLTEXT behaves differently from FTS5: minimum token size, stopwords, and accent and prefix handling.
   Mitigation: set the server flags `innodb_ft_min_token_size=2` and `innodb_ft_enable_stopword=OFF` in both compose files, use the `utf8mb4_0900_ai_ci` collation, and add search tests for accents, 2-letter tokens and prefixes. The flags only apply to indexes built after the server starts with them, which holds because the database is fresh.
2. Silent loss of precision or time zone: `Float` becomes 4-byte FLOAT on MySQL, and DATETIME drops tzinfo and microseconds.
   Mitigation: use `Double` and `DATETIME(fsp=6)`, set the session `time_zone='+00:00'`, keep `_ensure_utc` on reads, and add round-trip tests.
3. Error 3823 on the `property_matches` CHECK plus the FK cascade, which would make the migration fail at deploy time.
   Mitigation: omit the CHECK in the baseline, enforce the order in code, and the MySQL-backed migration test catches it locally.
4. Deadlocks or overlapping runs: the global `property_matches` rebuild, plus a CLI sync and the scheduler running at once.
   Mitigation: keep the in-process `_write_lock`, add the MySQL `GET_LOCK` across processes, run one uvicorn worker, and use READ COMMITTED.
5. Anonymous abuse of the read endpoints (heavy dedup and FULLTEXT queries).
   Mitigation: caps on `page_size` (existing), on `q` length and on ID-list sizes, plus `mem_limit`/`pids_limit`. Rate limiting belongs at the edge proxy and is part of the approval-gated exposure step.
6. Port or name collisions with GoGon or cookbook on the shared host.
   Mitigation: a distinct compose project, volumes, networks and directory; default port 8090 on loopback only; the runbook preflight checks `ss` and `docker compose ls`; no 80/443 binding.
7. An empty site after the first deploy until 06:00 or 18:00.
   Mitigation: a documented one-off `imob-ops imob sync --force` as a deployment step that needs approval.
8. Stale browser state after a DB reset (IDs reused).
   Mitigation: entries store `agency_slug`/`external_id` and are dropped on mismatch.
9. `caching_sha2_password` auth failing with PyMySQL.
   Mitigation: use the `pymysql[rsa]` extra, which pulls in `cryptography`. The compose stack test exercises a real 8.4 server.
10. If exposed under cookbook's Caddy site, cookbook's CSP would block external listing images.
    Mitigation: IMOB sets its own CSP. The runbook calls for a separate site block or hostname and flags it as an approval item.

## Alternative Approaches

1. **Keep personal state server-side under anonymous browser tokens**, with a cookie and a per-visitor table. It would allow server-side favorites filtering without sending ID lists, but it adds a shared mutation surface, storage growth and abuse vectors to a public anonymous site. Not chosen: the user asked for browser-only state.
2. **A separate scheduler, either a cron sidecar container or host cron running `imob sync` at 06:00 and 18:00**, rather than an in-process loop. It isolates scraping from serving, but it needs a second long-running service or a host-level change, and GoGon runs its schedulers in-process. Not chosen. The `GET_LOCK` design still allows switching later without code changes.
