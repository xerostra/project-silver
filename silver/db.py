"""SQLite base shared by the market store and the kill store.

- WAL mode: readers never block the writer or each other, across threads and processes.
- One writer at a time inside a process (a lock); across processes SQLite's own locking,
  with a generous busy timeout, serialises them.
- Every write is one transaction: it all lands or none of it does.
- Schema changes are numbered migrations tracked in PRAGMA user_version, applied once, in order.
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

STATE = "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL)"


class Database:
    MIGRATIONS = ()  # SQL scripts; index + 1 is the schema version each one brings the file to

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.Lock()
        self.migrate()

    @property
    def db(self):
        """This thread's connection (sqlite3 connections must not be shared across threads)."""
        if not hasattr(self._local, "db"):
            db = sqlite3.connect(self.path, timeout=60)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")  # durable across app crashes; WAL keeps the file consistent
            self._local.db = db
        return self._local.db

    @contextmanager
    def write(self):
        """One transaction: commits on success, rolls back on any error."""
        with self.write_lock, self.db:
            yield self.db

    def migrate(self):
        with self.write() as db:
            db.execute("BEGIN IMMEDIATE")  # no other process can migrate at the same time
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > len(self.MIGRATIONS):
                raise RuntimeError(f"{self.path} has schema v{version}, newer than this code "
                                   f"(v{len(self.MIGRATIONS)}): update the code before using it")
            db.execute(STATE)
            for n, script in enumerate(self.MIGRATIONS[version:], version + 1):
                for statement in filter(str.strip, script.split(";")):
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {n}")

    def state(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def mark(self, key, value=None):
        """Record when something last happened (a sweep, a poll, a heartbeat)."""
        with self.write() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, time.time() if value is None else value))
