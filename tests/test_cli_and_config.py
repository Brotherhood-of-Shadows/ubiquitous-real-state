"""Unit tests for portable application root discovery and Windows/CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from imob.cli import _build_parser, _find_available_port
from imob.config import AppSettings, resolve_app_root


def test_resolve_app_root_honors_imob_app_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify `IMOB_APP_ROOT` overrides source-tree root discovery for portable `.exe` builds."""
    monkeypatch.setenv("IMOB_APP_ROOT", str(tmp_path))

    actual_root = resolve_app_root()
    settings = AppSettings()

    assert actual_root == tmp_path.resolve()
    assert settings.db_path == tmp_path.resolve() / "data" / "imob.db"
    assert settings.sqlalchemy_url == f"sqlite:///{(tmp_path.resolve() / 'data' / 'imob.db').as_posix()}"


def test_cli_parser_supports_serve_browser_flag_and_port_fallback() -> None:
    """Verify `serve --open-browser` parses cleanly and `_find_available_port` returns a bindable port."""
    args = _build_parser().parse_args(["serve", "--open-browser", "--no-timer"])
    port = _find_available_port("127.0.0.1", 18950)

    assert (args.command, args.open_browser, args.no_timer) == ("serve", True, True)
    assert 18950 <= port < 18970
