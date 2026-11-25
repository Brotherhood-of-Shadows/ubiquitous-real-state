"""Command-line interface (`imob serve`, `imob sync`, `imob stats`) and portable `.exe` entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from imob.config import AppSettings, resolve_app_root
from imob.db.engine import create_mysql_engine, create_session_factory, run_migrations
from imob.db.repository import SqlAlchemyPropertyRepository
from imob.domain.registry import AGENCIES
from imob.maps import import_quadra_pdfs
from imob.scrapers import PlatformScraperRegistry
from imob.scrapers.http_client import PoliteHttpClient
from imob.services.sync_service import SyncService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imob",
        description="Imob PB — Motor de busca e sincronização de imobiliárias de Pato Branco - PR",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    serve_p = sub.add_parser("serve", help="Inicia a API REST e o Painel Web local (sem poll ao iniciar)")
    serve_p.add_argument("--host", default=None, help="Host de escuta (padrão: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=None, help="Porta HTTP (padrão: 8000)")
    serve_p.add_argument("--no-timer", action="store_true", help="Desativa o timer periódico em background")
    serve_p.add_argument(
        "--open-browser",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Abre o navegador padrão automaticamente assim que o servidor iniciar",
    )

    sync_p = sub.add_parser("sync", help="Executa sincronização incremental por listagens/JSON")
    sync_p.add_argument(
        "--agency",
        action="append",
        default=[],
        help="Sincroniza apenas a(s) imobiliária(s) indicada(s) pelo slug (ex: --agency apolar)",
    )
    sync_p.add_argument("--force", action="store_true", help="Ignora o TTL e força atualização imediata")

    maps_p = sub.add_parser("maps-import", help="Importa PDFs públicos das quadras para consulta local")
    maps_p.add_argument("directory", type=Path, help="Pasta com arquivos 0928.pdf ou Quadra_0928.pdf")

    sub.add_parser("stats", help="Exibe resumo estatístico do banco MySQL")
    sub.add_parser("migrate", help="Aplica as migrações Alembic no banco MySQL")
    return parser


def _browser_host(bind_host: str) -> str:
    return "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host


def _is_imob_already_running(host: str, port: int) -> bool:
    url = f"http://{_browser_host(host)}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=0.8) as resp:  # noqa: S310
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            return isinstance(payload, dict) and payload.get("status") == "ok"
    except Exception:
        return False


def _can_bind_port(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _find_available_port(host: str, preferred_port: int, max_attempts: int = 20) -> int:
    for candidate in range(preferred_port, preferred_port + max_attempts):
        if _can_bind_port(host, candidate):
            return candidate
    return preferred_port


def _open_browser_when_ready(url: str) -> None:
    health_url = f"{url.rstrip('/')}/api/health"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=0.6) as resp:  # noqa: S310
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.25)
    with contextlib.suppress(Exception):
        webbrowser.open(url)


async def _cmd_sync(settings: AppSettings, agencies: tuple[str, ...], force: bool) -> int:
    engine = create_mysql_engine(settings)
    try:
        run_migrations(engine)
        repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
        async with PoliteHttpClient(
            timeout_seconds=settings.http_timeout_seconds,
            per_domain_delay_seconds=settings.per_domain_delay_seconds,
        ) as http_client:
            registry = PlatformScraperRegistry(http_client, target_city=settings.target_city)
            svc = SyncService(repo, registry, settings)
            report = await svc.sync_agencies(agency_slugs=agencies, force=force)

        elapsed = (report.finished_at - report.started_at).total_seconds()
        print(f"\n=== Resumo da Sincronização ({elapsed:.1f}s) ===")
        for r in report.agencies:
            err_suffix = f" | aviso: {r.error[:90]}" if r.error else ""
            print(
                f"  [{r.status.value:11s}] {r.agency_name:30s} | reqs={r.requests_made:3d} | "
                f"PB={r.accepted_city:4d} (bruto={r.scraped_raw:4d}) | "
                f"+{r.inserted:4d} ~{r.updated:4d} ΔR$={r.price_changes:3d} -{r.deactivated:3d} "
                f"({r.duration_seconds:.1f}s){err_suffix}"
            )
        print(
            f"\nTOTAL -> Requisições: {report.total_requests} | "
            f"Imóveis PB vistos: {report.total_accepted} | "
            f"Novos: {report.total_inserted} | Atualizados: {report.total_updated} | "
            f"Mudanças de preço: {report.total_price_changes} | Inativados: {report.total_deactivated}"
        )
        return 0
    finally:
        engine.dispose()


def _cmd_stats(settings: AppSettings) -> int:
    engine = create_mysql_engine(settings)
    try:
        run_migrations(engine)
        repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
        repo.seed_agencies(AGENCIES)
        st = repo.get_stats()
        print("=== Banco de Imóveis — Pato Branco / PR ===")
        print(f"  Banco MySQL            : {settings.database_label}")
        print(f"  Imobiliárias sincroniz.: {st.agencies_synced}/{st.agencies_total}")
        print(
            f"  Imóveis ativos         : {st.active_properties} "
            f"(inativos/histórico: {st.inactive_properties})"
        )
        print(f"  Para Aluguel           : {st.rent_count}")
        print(f"  Para Venda             : {st.sale_count}")
        print(f"  Quedas de preço        : {st.price_drops_count}")
        print(f"  Grupos duplicados      : {st.duplicate_groups_count}")
        print(f"  Último sync global     : {st.last_global_sync_at or 'Nunca'}")
        return 0
    finally:
        engine.dispose()


def _run_serve(
    base_settings: AppSettings,
    *,
    host_arg: str | None,
    port_arg: int | None,
    no_timer: bool,
    open_browser: bool,
) -> int:
    updates: dict[str, object] = {}
    if host_arg:
        updates["host"] = host_arg
    if port_arg:
        updates["port"] = port_arg
    if no_timer:
        updates["auto_sync_enabled"] = False

    effective = base_settings.model_copy(update=updates)
    _ = effective.database_url  # Fail closed before binding when MySQL credentials are missing.

    if port_arg is None:
        if _is_imob_already_running(effective.host, effective.port):
            existing_url = f"http://{_browser_host(effective.host)}:{effective.port}/"
            print(f"Imob Pato Branco já está em execução em {existing_url}")
            if open_browser:
                with contextlib.suppress(Exception):
                    webbrowser.open(existing_url)
            return 0
        free_port = _find_available_port(effective.host, effective.port)
        if free_port != effective.port:
            effective = effective.model_copy(update={"port": free_port})

    ui_url = f"http://{_browser_host(effective.host)}:{effective.port}/"
    print("=" * 68)
    print(f"  IMOB PATO BRANCO — Painel Unificado ({len(AGENCIES)} imobiliárias)")
    print(f"  Painel no navegador : {ui_url}")
    print(f"  Banco MySQL         : {effective.database_label}")
    print("  Mantenha esta janela aberta enquanto utiliza o painel.")
    print("  Para encerrar, feche esta janela ou pressione Ctrl+C.")
    print("=" * 68)

    if open_browser:
        threading.Thread(target=_open_browser_when_ready, args=(ui_url,), daemon=True).start()

    from imob.api.app import create_app

    app = create_app(effective)
    uvicorn.run(app, host=effective.host, port=effective.port)
    return 0


def main(argv: list[str] | None = None) -> None:
    """Execute the `imob` CLI entry point (defaults to `serve --open-browser` on double-click).

    Args:
        argv: Optional command-line argument list.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        raw_argv = ["serve", "--open-browser"]

    try:
        args = _build_parser().parse_args(raw_argv)
        base_settings = AppSettings()

        if args.command == "maps-import":
            imported, skipped = import_quadra_pdfs(base_settings.maps_dir, args.directory)
            print(f"PDFs importados: {len(imported)} | Arquivos sem quadra identificável: {len(skipped)}")
            for filename in skipped:
                print(f"  Não importado: {filename}")
            return

        if args.command == "migrate":
            engine = create_mysql_engine(base_settings)
            try:
                run_migrations(engine)
            finally:
                engine.dispose()
            print(f"Migrações aplicadas com sucesso em {base_settings.database_label}")
            return

        if args.command == "stats":
            sys.exit(_cmd_stats(base_settings))

        if args.command == "sync":
            slugs = tuple(s.strip() for a in args.agency for s in a.split(",") if s.strip())
            sys.exit(asyncio.run(_cmd_sync(base_settings, slugs, bool(args.force))))

        if args.command in ("serve", None):
            code = _run_serve(
                base_settings,
                host_arg=getattr(args, "host", None),
                port_arg=getattr(args, "port", None),
                no_timer=bool(getattr(args, "no_timer", False)),
                open_browser=bool(getattr(args, "open_browser", True)),
            )
            if code != 0:
                sys.exit(code)
    except Exception as exc:
        logging.exception("Falha ao iniciar o Imob Pato Branco: %s", exc)
        with contextlib.suppress(OSError):
            log_file = resolve_app_root() / "startup-error.log"
            log_file.write_text(traceback.format_exc(), encoding="utf-8")
            print(f"\nDetalhes gravados em: {log_file}")
        if os.environ.get("IMOB_PORTABLE") == "1" and sys.stdin and sys.stdin.isatty():
            with contextlib.suppress(EOFError, KeyboardInterrupt):
                input("\nPressione ENTER para fechar...")
        sys.exit(1)


if __name__ == "__main__":
    main()
