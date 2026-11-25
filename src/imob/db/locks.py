"""Cross-process MySQL named locks (`GET_LOCK`/`RELEASE_LOCK`) held on a dedicated connection."""

from __future__ import annotations

import hashlib
import logging
import threading

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

MIGRATION_LOCK_NAME = "imob_migrate"
STARTUP_LOCK_TIMEOUT_SECONDS = 60


def _lock_name(purpose: str, database: str) -> str:
    digest = hashlib.sha256(f"{purpose}\0{database}".encode()).hexdigest()
    return f"imob:{digest[:59]}"


class MySqlNamedLock:
    """A non-blocking MySQL named lock scoped to the engine's database.

    MySQL lock names are server-wide, so include the normalized database name and purpose in the hash.
    The lock belongs to the connection that took it, so that connection is kept out of the pool
    until `release()`. If the connection dies, MySQL frees the lock.
    """

    def __init__(self, engine: Engine, base_name: str) -> None:
        """Prepare (but do not take) the lock.

        Args:
            engine: MySQL engine; a dedicated connection is opened on acquisition.
            base_name: Lock purpose, for example `imob_sync`.
        """
        self._purpose = base_name
        self.name = _lock_name(base_name, engine.url.database or "")
        self._engine = engine
        self._conn: Connection | None = None
        self._guard = threading.Lock()

    def try_acquire(self, timeout_seconds: int = 0) -> bool:
        """Take the lock, waiting at most `timeout_seconds` (no wait by default).

        Returns:
            `True` when this object now holds the lock, `False` when another session holds it.

        Raises:
            SQLAlchemyError: When the database cannot be reached.
        """
        with self._guard:
            if self._conn is not None:
                raise RuntimeError(f"Lock '{self.name}' is already held by this object")
            conn = self._engine.connect()
            try:
                database = conn.scalar(
                    text("SELECT IF(@@lower_case_table_names = 0, DATABASE(), LOWER(DATABASE()))")
                )
                self.name = _lock_name(self._purpose, str(database or ""))
                acquired = (
                    conn.scalar(
                        text("SELECT GET_LOCK(:name, :timeout)"),
                        {"name": self.name, "timeout": timeout_seconds},
                    )
                    == 1
                )
                # Named locks are session-scoped; end the implicit transaction so no snapshot stays open.
                conn.commit()
            except BaseException:
                # A successful GET_LOCK must never leak into the pool if commit fails.
                conn.invalidate()
                conn.close()
                raise
            if not acquired:
                conn.close()
                return False
            self._conn = conn
            return True

    def release(self) -> None:
        """Release the lock and return its connection; safe to call more than once."""
        with self._guard:
            conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": self.name})
            conn.commit()
        except SQLAlchemyError:
            logger.warning("Could not release lock '%s'; closing its connection frees it", self.name)
            conn.invalidate()
        finally:
            conn.close()
