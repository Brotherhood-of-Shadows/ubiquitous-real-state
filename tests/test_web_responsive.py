"""Mobile dashboard responsiveness, filter disclosure markup, and active-filter counting."""

import json
import re
import shutil
import subprocess
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "imob" / "web"


def test_mobile_filter_disclosure_markup_and_single_static_bundle() -> None:
    """Verify disclosure button, collapsed filter panel, and zero extra asset or remote requests."""
    html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")

    toggle_match = re.search(r"<button\b[^>]*\bid=\"btn-toggle-filters\"[^>]*>", html)
    assert toggle_match is not None
    toggle_tag = toggle_match.group(0)
    assert 'aria-controls="filter-panel"' in toggle_tag
    assert 'aria-expanded="false"' in toggle_tag

    aside_match = re.search(r"<aside\b[^>]*>", html)
    assert aside_match is not None
    aside_tag = aside_match.group(0)
    assert 'id="filter-panel"' in aside_tag
    assert "is-collapsed" in aside_tag

    assert 'id="btn-show-results"' in html
    assert 'id="active-filter-count"' in html
    assert html.count('<link rel="stylesheet"') == 1
    assert html.count("<script") == 1
    assert not re.search(r"""\b(?:src|href)\s*=\s*["']https?://""", html, flags=re.IGNORECASE)


def test_responsive_css_keeps_collapsed_rule_mobile_only_and_avoids_external_assets() -> None:
    """Verify `.sidebar.is-collapsed` and static positioning stay inside the <=980px media query."""
    css = (_WEB_DIR / "styles.css").read_text(encoding="utf-8")

    media_idx = css.find("@media (max-width: 980px)")
    collapsed_idx = css.find(".sidebar.is-collapsed")
    assert media_idx != -1
    assert collapsed_idx > media_idx
    assert css.count(".sidebar.is-collapsed") == 1

    # Extract the @media (max-width: 980px) block by tracking brace depth.
    brace_start = css.find("{", media_idx)
    assert brace_start != -1
    depth = 0
    brace_end = -1
    for idx in range(brace_start, len(css)):
        if css[idx] == "{":
            depth += 1
        elif css[idx] == "}":
            depth -= 1
            if depth == 0:
                brace_end = idx
                break
    assert brace_end != -1
    media_980_block = css[brace_start : brace_end + 1]
    assert ".sidebar.is-collapsed" in media_980_block
    assert re.search(r"\.sidebar\s*\{[^}]*position:\s*static", media_980_block) is not None

    assert "minmax(min(100%, 310px)" in css
    assert "@import" not in css.lower()
    assert not re.search(r"url\(\s*['\"]?https?://", css, flags=re.IGNORECASE)


def test_count_active_filters_ignores_pagination_sort_and_default_price_toggle() -> None:
    """Ensure `countActiveFilters` counts only user-selected filter keys in URLSearchParams."""
    node = shutil.which("node")
    assert node is not None, "node is required to verify frontend ES modules"
    script = """
import { countActiveFilters } from "./src/imob/web/app.js";

const baseline = new URLSearchParams("page=1&page_size=30&sort=menor_preco&only_with_price=false");
const withThree = new URLSearchParams(
  "page=1&page_size=30&sort=menor_preco&only_with_price=false&purpose=aluguel&types=casa&min_bedrooms=2"
);
const onlyPriceTrue = new URLSearchParams("only_with_price=true");

console.log(JSON.stringify({
  baseline: countActiveFilters(baseline),
  withThree: countActiveFilters(withThree),
  onlyPriceTrue: countActiveFilters(onlyPriceTrue),
}));
"""
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=_WEB_DIR.parent.parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    out = json.loads(proc.stdout)
    assert out == {
        "baseline": 0,
        "withThree": 3,
        "onlyPriceTrue": 1,
    }
