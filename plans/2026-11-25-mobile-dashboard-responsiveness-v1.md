# Mobile dashboard responsiveness

## Objective

Make the IMOB dashboard (`src/imob/web/`) usable on phones (320–430 px wide) and tablets, and keep the desktop as it is. Two things are fixed first: the filter sidebar stays on screen and covers the listings, and fields and cards overlap or overflow. Loading must stay as fast as it is now. That means no new dependencies, fonts, images or network requests, and no extra JS module. The site must keep working under the strict CSP (`style-src 'self'`, so no inline `style=`) and under a stripped path prefix (relative `static/` and `api/` paths). There is no spec file; the request comes from the user through the parent agent. The workflow is planner → implementer → reviewer, with no deploy.

### Diagnosed root causes (verified in source)
1. **Filter panel covers the listings.** `.sidebar` is `position: sticky; top: 74px; max-height: calc(100vh - 92px); overflow-y: auto` (`src/imob/web/styles.css:205-215`). At ≤980 px the grid collapses to one column (`styles.css:198-202`), but the sticky rule is not reset. The sidebar stays pinned to the viewport above the results. It is a positioned element, so it paints over the listings, while cards with the hover `transform` (`styles.css:403-406`) paint over it. That gives the "permanently visible filter" and the overlap.
2. **The sticky topbar grows tall on narrow screens.** `.topbar` is sticky (`styles.css:44-57`) and wraps its brand, 7 stat pills and 2 buttons (`index.html:10-35`) into 3–5 rows on phones. It then takes up a large part of the viewport. Even at 981–1300 px it wraps to two rows (about 110 px), so the sidebar's hard-coded `top: 74px` slides under it on tablets and small laptops.
3. **Fixed minimum widths overflow narrow viewports.** `.cards-grid` uses `minmax(310px, 1fr)` (`styles.css:386-390`). With 2×20 px layout padding, 320 px phones have only 280 px, so cards overflow sideways. `.sort-box select { min-width: 215px }` (`styles.css:381-384`) plus its label overflows the toolbar. `.gallery-strip` uses `minmax(180px, …)` (`styles.css:662-667`).
4. **Rows without wrapping push content out.** `.card-agency-line` (`styles.css:482-488`), `.duplicate-peer-row` (`styles.css:749-754`) and a long `.price-main` ("Aluguel R$ … | Venda R$ …", `app.js:1001-1009`) cannot wrap or shrink.
5. **Mobile ergonomics.** Inputs are 13 px (`styles.css:276-287`), so iOS Safari zooms on focus. Touch targets for chips and `.btn-icon` are about 24 px (`styles.css:307-316,578-585`). Modals are `94vw × 90vh` (`styles.css:608-625`), which is cramped with 20 px padding.

## Implementation Plan

- [ ] 1. **Markup for a mobile filter disclosure** in `src/imob/web/index.html`.
  - Put a disclosure button first inside `.layout`, before the `<aside>` at `index.html:41-42`. Use `type="button"`, `id="btn-toggle-filters"`, classes `btn btn-outline filters-toggle`, `aria-expanded="false"` and `aria-controls="filter-panel"`. Its text is "Filtros", followed by `<span id="active-filter-count" class="filter-count hidden"></span>`.
  - Give the `<aside class="sidebar">` the attribute `id="filter-panel"` and the extra class `is-collapsed`. Starting collapsed in the markup avoids a flash of the open panel before the module loads.
  - Add a last child inside `#filter-form`, after the agencies field group (`index.html:200-210`): `<div class="filters-mobile-actions">` with `<button type="button" class="btn btn-primary" id="btn-show-results">Ver resultados</button>`.
  - Add no inline `style=`, no new `<link>`/`<script>` tags and no absolute `/` paths. This keeps `tests/test_maps.py:134-140` passing.

- [ ] 2. **Fix the layout with CSS only** in `src/imob/web/styles.css`.
  - **Base rules (all widths):**
    - `.filters-toggle, .filters-mobile-actions { display: none; }`.
    - `.filter-count` is a small pill.
    - Make `.topbar` (`:44-57`) `position: static`. Remove `position`/`z-index` or keep `z-index` harmless.
    - Change the desktop `.sidebar` (`:205-215`) to `top: 12px; max-height: calc(100vh - 24px)`. This removes the fragile 74 px coupling (root cause 2).
    - `.cards-grid` → `repeat(auto-fill, minmax(min(100%, 310px), 1fr))`.
    - `.gallery-strip` → `minmax(min(100%, 160px), 1fr)`.
    - `.card-agency-line` gets `gap: 8px; min-width: 0`. Its children get `overflow-wrap: anywhere`.
    - `.price-main` gets `overflow-wrap: anywhere`.
    - `.duplicate-peer-row` gets `flex-wrap: wrap; gap: 6px`.
    - `.card-footer` gets `flex-wrap: wrap`.
    - Wrap the `.prop-card:hover` transform (`:403-406`) in `@media (hover: hover)` so touch devices don't get a stuck hover state or a new stacking layer.
  - **Extend `@media (max-width: 980px)` (`:198-202`):**
    - `.filters-toggle { display: flex; width: 100%; min-height: 44px; justify-content: center; }`.
    - `.sidebar { position: static; top: auto; max-height: none; overflow: visible; }`.
    - `.sidebar.is-collapsed { display: none; }`. This rule exists only inside the media query, so desktop always shows the panel without JS.
    - `.filters-mobile-actions { display: block; position: sticky; bottom: 0; padding: 10px 0 0; background: var(--surface); }` with its button at `width: 100%`.
    - `input[type=search], input[type=number], select, textarea { font-size: 16px; }` to prevent iOS zoom.
    - Chips, `.btn-icon`, `.purpose-tab`, `.btn-sm` and `.check-row` get `min-height: 40px`.
    - `.checklist-box { max-height: 220px; }`.
  - **Add a new `@media (max-width: 640px)` block:**
    - `.layout { padding: 12px 10px 32px; gap: 12px; }`.
    - `.topbar { padding: 10px 12px; }`.
    - `.topbar .subtitle { display: none; }`.
    - `.topbar-actions { width: 100%; }` with its buttons at `flex: 1 1 0`.
    - `.stat-pill { font-size: 11px; padding: 3px 8px; }`.
    - `.results-toolbar { padding: 10px 12px; }`.
    - `.sort-box { width: 100%; }`, and `.sort-box select { min-width: 0; flex: 1 1 auto; width: 100%; }`.
    - `.card-media { height: 180px; }`.
    - `.modal { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); }` and the same `max-height` on `.modal-box`. Keep the `90vh` declarations before them as a fallback.
    - `.modal-header, .modal-body { padding-inline: 14px; }`.
    - `.two-cols` stays two columns, because the inputs fit at 320 px once the font is 16 px and the padding is smaller. If the 320 px screenshot shows the select labels clipping, switch to `.two-cols { grid-template-columns: 1fr; }` only below 360 px.
    - `.maps-form input { max-width: none; flex: 1 1 120px; }`.
  - Use no `@import`, `url(http…)` or web fonts. The change is in the existing file only.

- [ ] 3. **Minimal JS for the disclosure and the active-filter count** in `src/imob/web/app.js`.
  - Add an exported pure function `countActiveFilters(params)` near `safeHttpUrl` (`app.js:76-105`), with JSDoc. It counts the keys of the `URLSearchParams` from `buildQueryParams` (`app.js:507-572`). It skips `page`, `page_size` and `sort`, and skips `only_with_price` when its value is `"false"`.
  - Add a `setFiltersExpanded(expanded)` method on `DashboardController`, with JSDoc. It toggles `is-collapsed` on `#filter-panel` and sets `aria-expanded` on `#btn-toggle-filters` to `"true"`/`"false"`.
  - In `bindEvents` (`app.js:354-463`):
    - Clicking `#btn-toggle-filters` toggles the panel based on its current `aria-expanded`.
    - Clicking `#btn-show-results` calls `setFiltersExpanded(false)`, then `mustGetById("btn-toggle-filters").focus()`, so focus never stays on a hidden element. Focusing the toggle also scrolls it into view directly above the results.
  - In `refreshProperties` (`app.js:806-838`), right after `const params = this.buildQueryParams()`, update `#active-filter-count`:
    - Set its `textContent` to `String(n)`.
    - Toggle the `hidden` class when `n === 0`.
    - Set `aria-label` on the toggle to "Filtros, N ativos".
    - This also covers `resetAllFilters` (`app.js:486-500`) and the URL-hydrated state, because both go through `refreshProperties`.
  - Hiding uses `display: none`, so form values, facet `Set`s, URL sync and debounce are unaffected. Add no `matchMedia` or resize listeners and no `style` writes, to satisfy `tests/test_maps.py:192-195`.

- [ ] 4. **Regression tests** in a new file, `tests/test_web_responsive.py`. Follow the module docstring style of `tests/test_maps.py`, with `_WEB_DIR` defined the same way as `tests/test_maps.py:29`. Each test gets a docstring.
  - **HTML:**
    - `#btn-toggle-filters` has `aria-controls="filter-panel"` and `aria-expanded="false"`.
    - `<aside` contains `id="filter-panel"` and `is-collapsed`.
    - `#btn-show-results` exists.
    - Exactly one `<link rel="stylesheet"` and one `<script`.
    - No `http://`/`https://` in `src=`/`href=` attributes.
  - **CSS:**
    - The `.sidebar.is-collapsed` rule appears only after `@media (max-width: 980px)`. Check this with a simple brace-depth or substring check.
    - `minmax(min(100%, 310px)` is present.
    - No `@import` and no `url(` referencing `http`.
    - Inside the 980 px block, `.sidebar` contains `position: static`.
  - **JS:** use the same `node --input-type=module` subprocess approach as `tests/test_maps.py:202-310`. Import `countActiveFilters` from `./src/imob/web/app.js` and assert:
    - 0 for `page=1&page_size=30&sort=menor_preco&only_with_price=false`.
    - 3 for that plus `purpose=aluguel&types=casa&min_bedrooms=2`.
    - 1 for `only_with_price=true` alone.

- [ ] 5. **Browser verification** with a mock harness that lives outside the repo, so no files are added there. No dev server is needed. The app needs MySQL (`src/imob/config.py:160`, `src/imob/cli.py:189`), so cards are rendered instead from a throwaway, zero-dependency Node script in `/tmp/imob-mobile-harness/server.mjs`.
  - The script serves `src/imob/web/` at `/imob/` (`index.html`) and `/imob/static/*`. It answers these with fixture JSON:
    - `GET /imob/api/stats`, `GET /imob/api/facets`, `GET /imob/api/agencies`, `GET /imob/api/sync/status` and `GET /imob/api/maps`.
    - `POST /imob/api/properties/search`: about 30 items with long titles and agency names, both prices, many amenities, and some items with no photo.
    - `GET /imob/api/properties/{id}`.
    - The shapes follow `src/imob/web/api.js` typedefs and the stub at `tests/test_maps.py:231-265`.
  - It sends the production CSP string (`src/imob/api/app.py:32`) on every response, so CSP violations show up.
  - Capture screenshots with `firefox --headless --screenshot /tmp/imob-mobile-harness/w{W}.png --window-size={W},{H} http://127.0.0.1:{port}/imob/`. Firefox is at `/usr/bin/firefox`; no Chromium or Playwright is installed. Use these sizes:
    - 320×640, 360×740, 390×844, 430×932 (phones);
    - 768×1024 (tablet);
    - 1024×768 and 1440×900 (desktop).
  - For the open-panel state, add a harness-only route `/imob/?open` that serves `index.html` with `is-collapsed` stripped. The harness is a test fixture; the app source stays unchanged.
  - The user or reviewer then does an interactive pass in desktop Firefox Responsive Design Mode (Ctrl+Shift+M) at 360 px and 390 px. Optionally, after an approved deploy, they also check on a real phone. This is the only way to check keyboard, focus and touch behavior here; there is no headless driver.

- [ ] 6. **Records.**
  - Add a short "Uso no celular" note to `README.md` (near the dashboard section, about `README.md:87`) saying the filters open from the "Filtros" button on narrow screens.
  - Leave `pyproject.toml:3` at `version = "0.2.0"` unless the user asks for a bump. The change is UI-only and backward compatible.
  - There is no summary file convention in this repo. `plans/` holds plans only. Summarize the change in the implementer's final report and in the commit message.

## Verification Criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests` → clean.
- `uv run python -m mypy src tests` → no errors.
- `uv run python -m pytest -v` → all pass. This includes:
  - the new `tests/test_web_responsive.py`;
  - the unchanged `tests/test_maps.py::test_public_hardening_headers_docs_disabled_and_pdf_serving` (CSP, `no-cache`, relative `static/` paths, no `style="`);
  - `tests/test_maps.py::test_web_assets_avoid_inline_styles_and_work_under_stripped_proxy_prefix` (no `style:`/`setAttribute("style"`, no `/api/` absolute paths, prefix resolution).
- `git diff --stat` touches only these files:
  - `src/imob/web/index.html`, `styles.css`, `app.js`;
  - `tests/test_web_responsive.py`;
  - `README.md`.
  - `api.js`, `personal_state.js`, `pyproject.toml`/`uv.lock` and any `package.json` are unchanged, so no new dependencies and no new requests.
- Harness screenshots at 320, 360, 390 and 430 px:
  - no horizontal scrollbar or clipped card; cards are one column;
  - the filter panel is hidden by default and only the "Filtros" button shows above the toolbar;
  - the toolbar's sort select fits;
  - there is no element overlapping the listings anywhere on the page;
  - the topbar scrolls away instead of covering content.
- `?open` screenshots at 360 and 390 px:
  - the panel flows above the results, not sticky and not overlaying them;
  - two-column inputs are readable;
  - "Ver resultados" is pinned at the bottom of the panel.
- Screenshots at 768 px: single column, same collapsed behavior. At 1024 and 1440 px:
  - the toggle and "Ver resultados" are absent;
  - the sidebar shows even with `is-collapsed` in the markup;
  - the 320 px + results two-column layout matches the pre-change screenshot, apart from the topbar no longer being sticky.
- No CSP violation messages in the harness. Check in the Firefox console during the interactive pass, or confirm the page renders styled and populated in the headless screenshots.
- Interactive pass (Responsive Design Mode, 390 px):
  - Tab reaches "Filtros". Enter/Space toggles it, and `aria-expanded` changes (check in the Inspector).
  - Typed values, selected chips, neighborhoods and checkboxes survive collapse and expand.
  - The count badge updates; "Limpar tudo" clears it to hidden.
  - "Ver resultados" collapses the panel and returns focus to "Filtros".
  - Focusing an input does not zoom in iOS emulation, because the font is 16 px.
  - The detail, agencies and maps modals fit the viewport, scroll inside, and close with Esc and ×.
  - The photo viewer still navigates with the arrow keys.
- Reload with a filtered URL (e.g. `?purpose=aluguel&min_bedrooms=2`) at 390 px: the panel is collapsed, the badge shows 2, and the results are filtered.
- Loading cost: the network panel shows the same 4 static requests as before (`index.html`, `styles.css`, `app.js`, `api.js`, plus `personal_state.js` as before). `styles.css` grows by less than about 3 KB.

## Potential Risks and Mitigations

1. Changing `.topbar` from sticky to static also changes the desktop: the stats no longer stay pinned while scrolling.
   Mitigation: this is the smallest robust fix for the 74 px overlap at 981–1300 px. If the user wants the pinned header back on wide screens, re-add `position: sticky` under `@media (min-width: 1440px)` after checking in a screenshot that it fits on one row there. Call this out in the implementer's report.
2. If the module fails to load, the panel stays hidden on phones because `is-collapsed` is in the markup.
   Mitigation: the dashboard is useless without JS anyway, since every listing is fetched by `app.js`. Desktop is unaffected because the hiding rule is mobile-only.
3. Enlarging touch targets inside the 980 px query may make the tablet sidebar longer.
   Mitigation: the panel is collapsed by default there, and the targets apply only at ≤980 px.
4. `100dvh` is not supported by older mobile browsers.
   Mitigation: keep the existing `90vh` declarations before it as a fallback.
5. The headless Firefox `--screenshot` cannot click, so it cannot exercise the disclosure.
   Mitigation: use the harness `?open` variant for the visual check, plus the unit test of `countActiveFilters` and a mandatory interactive Responsive Design Mode pass by the reviewer.
6. Harness fixtures may drift from the real API shapes and hide layout cases.
   Mitigation: base the fixtures on the `api.js` JSDoc typedefs. Include worst-case strings: long titles and agency names, dual price, 10 amenities, missing photo. Optionally re-check against the local `docker compose up -d --build --wait` stack at `http://127.0.0.1:8090/`, which is read-only and touches no deploy.

## Alternative Approaches

1. **Off-canvas drawer or `<dialog>` for the filters on mobile.** This is a more app-like UX and could reuse native `showModal()` focus trapping. It was not chosen because it needs more JS (open/close, focus restore, backdrop), and a desktop/mobile split of the same form would need matchMedia or a DOM move. That raises the risk of breaking form state, and it is not the small CSS-first change the user asked for.
2. **Wrap the filter form in `<details>/<summary>`.** This needs no JS for toggling. It was not chosen because `<details>` also collapses on desktop unless JS forces `open` there. That reintroduces a matchMedia listener, and `<summary>` styling and `aria` behavior vary across mobile browsers. The button and `aria-expanded` disclosure gets the same result with about 15 lines of JS, and the desktop stays pure CSS.
