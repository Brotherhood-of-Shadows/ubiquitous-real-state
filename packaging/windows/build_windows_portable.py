#!/usr/bin/env python3
"""Report the retirement of the SQLite-based Windows portable build."""

from pathlib import Path


def build_portable(*, include_seed_db: bool = True, runtime_from: Path | None = None) -> Path:
    """Reject builds without modifying previously generated artifacts."""
    raise SystemExit(
        "O pacote portátil para Windows foi descontinuado no IMOB 0.2.0: "
        "esta versão usa MySQL 8.4. Use o Docker Compose descrito no README."
    )


if __name__ == "__main__":
    build_portable()
