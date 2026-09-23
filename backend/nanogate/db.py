"""SQLite storage with ordered SQL migrations. One connection per thread, WAL mode."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def migrate(self) -> list[str]:
        c = self.conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at REAL NOT NULL)")
        done = {r["name"] for r in c.execute("SELECT name FROM schema_migrations")}
        applied = []
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            c.executescript(f.read_text())
            c.execute("INSERT INTO schema_migrations(name, applied_at) VALUES (?,?)", (f.name, time.time()))
            applied.append(f.name)
        return applied

    @contextmanager
    def tx(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Serializable write transaction (BEGIN IMMEDIATE takes the SQLite write lock up front)."""
        c = self.conn()
        with self.write_lock:
            c.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self.write_lock:
            self.conn().execute(sql, args)

    def healthy(self) -> tuple[bool, str | None]:
        try:
            self.conn().execute("SELECT 1").fetchone()
            return True, None
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
