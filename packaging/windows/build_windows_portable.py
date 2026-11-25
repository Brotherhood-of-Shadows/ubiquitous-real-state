#!/usr/bin/env python3
"""Build a self-contained Windows x64 portable bundle (`ImobPatoBranco-Windows-x64.zip`).

Assembles:
  - `ImobPatoBranco/ImobPatoBranco.exe` (native freestanding Win32 PE64 launcher)
  - `ImobPatoBranco/LEIA-ME.txt` (end-user instructions in Portuguese)
  - `ImobPatoBranco/alembic.ini` + `migrations/`
  - `ImobPatoBranco/data/imob.db` (optional pre-synced Pato Branco catalog, cleaned & VACUUMed)
  - `ImobPatoBranco/runtime/` (official PSF-signed Python 3.12 Windows x64 embeddable + win_amd64 wheels)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.12.10"
PYTHON_MM = "312"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)

RUNTIME_WHEELS: tuple[str, ...] = (
    "alembic>=1.14.0",
    "beautifulsoup4>=4.12.3",
    "colorama>=0.4.6",
    "fastapi>=0.115.0",
    "httptools>=0.6.4",
    "httpx>=0.28.0",
    "lxml>=5.3.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy>=2.0.36",
    "uvicorn>=0.32.0",
    "websockets>=13.0",
)

KERNEL32_DEF = """\
LIBRARY kernel32.dll
EXPORTS
    GetModuleFileNameW
    GetCommandLineW
    GetFileAttributesW
    SetEnvironmentVariableW
    SetConsoleCtrlHandler
    SetConsoleOutputCP
    SetConsoleTitleW
    CreateProcessW
    WaitForSingleObject
    GetExitCodeProcess
    CloseHandle
    ExitProcess
"""

USER32_DEF = """\
LIBRARY user32.dll
EXPORTS
    MessageBoxW
"""

README_TXT = """\
========================================================================
  IMOB PATO BRANCO — Buscador Unificado de Imóveis (Windows)
========================================================================

COMO USAR:
1. Extraia TODA esta pasta `ImobPatoBranco` de dentro do arquivo .zip
   (por exemplo, na Área de Trabalho ou em Documentos).
2. Dê duplo clique em `ImobPatoBranco.exe`.
3. Uma janela preta (servidor local) abrirá e o seu navegador padrão
   abrirá automaticamente o painel em http://127.0.0.1:8000/

DICAS IMPORTANTES:
- Não precisa instalar Python nem nada extra no computador.
- O banco de dados (`data\\imob.db`) já vem carregado com os imóveis de
  Pato Branco - PR! Seus Favoritos, Ocultos e Anotações ficam salvos
  localmente na pasta `data\\` do seu computador.
- Enquanto estiver pesquisando no navegador, mantenha a janela do
  `ImobPatoBranco.exe` aberta (minimizada). Para desligar o programa,
  basta fechar essa janela.
- Para buscar atualizações das imobiliárias cadastradas, clique em "Sincronizar"
  (ou deixe o timer automático de 6h atualizar em segundo plano).
- Compartilhe o arquivo .zip completo. O .exe precisa das outras pastas.

RECURSOS DESTA VERSÃO:
- Mesmo edifício + mesma unidade identificam o mesmo imóvel; nos demais
  casos, a comparação considera características independentes e descrições.
- Grupos prováveis e pares para revisar, com os motivos nos detalhes.
- Galeria de fotos ampliada dentro do painel.
- Tags de permuta/proposta e anúncios sem preço incluídos por padrão.
- Mapas e quadras: pastas oficiais de 1 a 2500 e importação de PDFs locais.

PARA ATUALIZAR UMA INSTALAÇÃO EXISTENTE:
- Feche o programa e faça uma cópia de segurança de data/imob.db.
- Atualize ImobPatoBranco.exe, runtime/, migrations/, alembic.ini e este
  LEIA-ME.txt, preservando a pasta data/ do usuário.
- Não substitua o banco pessoal pelo banco de exemplo deste pacote.
"""


def _download_python_embed(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if archive_path.exists() and archive_path.stat().st_size > 5_000_000:
        print(f"[1/6] Usando cache do Python Embeddable: {archive_path}")
        return archive_path

    print(f"[1/6] Baixando Python {PYTHON_VERSION} Windows x64 Embeddable oficial (python.org)...")
    req = urllib.request.Request(
        PYTHON_EMBED_URL,
        headers={"User-Agent": "ImobPatoBranco-Builder/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp, archive_path.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)
    return archive_path


def _compile_launcher_exe(launcher_c: Path, build_tmp: Path, output_exe: Path) -> None:
    print("[2/6] Compilando executável nativo PE64 `ImobPatoBranco.exe` via Clang/LLD...")
    build_tmp.mkdir(parents=True, exist_ok=True)
    k32_def = build_tmp / "kernel32.def"
    u32_def = build_tmp / "user32.def"
    k32_lib = build_tmp / "kernel32.lib"
    u32_lib = build_tmp / "user32.lib"
    obj_file = build_tmp / "launcher.obj"

    k32_def.write_text(KERNEL32_DEF, encoding="utf-8")
    u32_def.write_text(USER32_DEF, encoding="utf-8")

    subprocess.run(
        ["llvm-dlltool", "-m", "i386:x86-64", "-d", str(k32_def), "-l", str(k32_lib)],
        check=True,
    )
    subprocess.run(
        ["llvm-dlltool", "-m", "i386:x86-64", "-d", str(u32_def), "-l", str(u32_lib)],
        check=True,
    )
    subprocess.run(
        [
            "clang",
            "--target=x86_64-pc-windows-msvc",
            "-Os",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-stack-protector",
            "-mno-stack-arg-probe",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
            str(launcher_c),
            "-o",
            str(obj_file),
        ],
        check=True,
    )
    subprocess.run(
        [
            "lld-link",
            "/SUBSYSTEM:CONSOLE",
            "/ENTRY:entry_point",
            "/NODEFAULTLIB",
            "/STACK:1048576,131072",
            str(obj_file),
            str(k32_lib),
            str(u32_lib),
            f"/OUT:{output_exe}",
        ],
        check=True,
    )


def _assemble_runtime(
    embed_zip: Path, project_root: Path, runtime_dir: Path, *, runtime_from: Path | None = None
) -> None:
    if runtime_from is not None:
        print(f"[3/6] Reutilizando runtime Windows existente: {runtime_from}")
        shutil.copytree(
            runtime_from,
            runtime_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        _install_runtime_dependencies(embed_zip, project_root, runtime_dir)
    _stage_application_package(project_root, runtime_dir)


def _install_runtime_dependencies(embed_zip: Path, project_root: Path, runtime_dir: Path) -> None:
    print("[3/6] Montando runtime Windows x64 e instalando wheels `win_amd64`...")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(embed_zip, "r") as zf:
        zf.extractall(runtime_dir)

    pth_file = runtime_dir / f"python{PYTHON_MM}._pth"
    pth_file.write_text(
        f"python{PYTHON_MM}.zip\r\n.\r\nsite-packages\r\nimport site\r\n",
        encoding="utf-8",
    )

    site_packages = runtime_dir / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--python-platform",
            "x86_64-pc-windows-msvc",
            "--python-version",
            "3.12",
            "--only-binary",
            ":all:",
            *RUNTIME_WHEELS,
        ],
        cwd=str(project_root),
        check=True,
    )


def _stage_application_package(project_root: Path, runtime_dir: Path) -> None:
    # Always replace application code, even when preserving the dependency runtime.
    site_packages = runtime_dir / "site-packages"
    dest_pkg = site_packages / "imob"
    if dest_pkg.exists():
        shutil.rmtree(dest_pkg)
    shutil.copytree(
        project_root / "src" / "imob",
        dest_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # Trim bytecode caches from site-packages to keep zip smaller and deterministic
    for pycache in site_packages.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)


def _stage_seed_database(source_db: Path, target_db: Path) -> None:
    print("[4/6] Preparando banco inicial `data/imob.db` (limpo de dados pessoais + VACUUM)...")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if not source_db.exists():
        raise FileNotFoundError(f"Banco origem não encontrado em {source_db}")

    # Use SQLite online backup API so WAL pages are safely consolidated
    src_conn = sqlite3.connect(str(source_db))
    dst_conn = sqlite3.connect(str(target_db))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()

    try:
        with dst_conn:
            dst_conn.execute("UPDATE properties SET is_favorite = 0, is_hidden = 0, user_notes = NULL")
            dst_conn.execute("INSERT INTO properties_fts(properties_fts) VALUES ('optimize')")
        dst_conn.execute("PRAGMA journal_mode = DELETE")
        dst_conn.execute("VACUUM")
    finally:
        dst_conn.close()


def _create_zip_bundle(bundle_dir: Path, zip_path: Path) -> None:
    print(f"[5/6] Compactando pacote portátil em {zip_path} ...")
    if zip_path.exists():
        zip_path.unlink()
    parent_root = bundle_dir.parent
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(bundle_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(parent_root).as_posix()
                zf.write(file_path, arcname=arcname)


def build_portable(*, include_seed_db: bool = True, runtime_from: Path | None = None) -> Path:
    """Execute the full Windows x64 portable build and return the output `.zip` path."""
    project_root = Path(__file__).resolve().parents[2]
    build_root = project_root / "build" / "windows"
    dist_root = project_root / "dist"
    bundle_dir = dist_root / "ImobPatoBranco"
    zip_path = dist_root / "ImobPatoBranco-Windows-x64.zip"

    if runtime_from is not None:
        runtime_from = runtime_from.resolve()
        if runtime_from.is_relative_to(bundle_dir.resolve()):
            raise ValueError("Copie o runtime anterior para outra pasta antes de reconstruir o pacote")
        for required in (
            "python.exe",
            f"python{PYTHON_MM}.dll",
            f"python{PYTHON_MM}._pth",
            "site-packages",
        ):
            if not (runtime_from / required).exists():
                raise FileNotFoundError(f"Runtime Windows incompleto: {runtime_from / required}")

    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    embed_zip = _download_python_embed(build_root / "cache")
    _compile_launcher_exe(
        project_root / "packaging" / "windows" / "launcher.c",
        build_root / "obj",
        bundle_dir / "ImobPatoBranco.exe",
    )
    _assemble_runtime(embed_zip, project_root, bundle_dir / "runtime", runtime_from=runtime_from)

    # Copy Alembic config & migrations
    shutil.copy2(project_root / "alembic.ini", bundle_dir / "alembic.ini")
    shutil.copytree(
        project_root / "migrations",
        bundle_dir / "migrations",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    if include_seed_db:
        _stage_seed_database(
            project_root / "data" / "imob.db",
            bundle_dir / "data" / "imob.db",
        )
    else:
        (bundle_dir / "data").mkdir(parents=True, exist_ok=True)

    readme_crlf = README_TXT.replace("\r\n", "\n").replace("\n", "\r\n")
    (bundle_dir / "LEIA-ME.txt").write_bytes(readme_crlf.encode("utf-8"))

    _create_zip_bundle(bundle_dir, zip_path)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    exe_kb = (bundle_dir / "ImobPatoBranco.exe").stat().st_size / 1024
    print(f"[6/6] Build concluído! Launcher: {exe_kb:.1f} KB | Pacote final: {zip_path} ({size_mb:.1f} MB)")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera pacote portátil Windows x64 (.zip + .exe)")
    parser.add_argument(
        "--empty-db",
        action="store_true",
        help="Não incluir o banco inicial pré-sincronizado no pacote",
    )
    parser.add_argument(
        "--runtime-from",
        type=Path,
        help="Reutiliza dependências de um runtime Windows anterior compatível (sem baixar wheels)",
    )
    args = parser.parse_args()
    os.umask(0o022)
    build_portable(include_seed_db=not args.empty_db, runtime_from=args.runtime_from)


if __name__ == "__main__":
    main()
