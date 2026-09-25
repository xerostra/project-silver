"""Kill store (data/<server>/kills.db) and the collector that fills it from the live feed.

Each new kill is written in one transaction: the event (for the killboard) and its Meta counters
land together or not at all, and the event id makes it idempotent, so a kill seen twice (a
restart, overlapping pages, a second collector) is only ever counted once. The raw event goes to
the append-only archive first: a crash before the database write just means it is fetched and
archived again, and duplicates there are harmless, while a lost raw event would be gone for good.
"""
import json
import time
import traceback

from . import config, meta
from .api import GameInfo
from .db import Database
from .killboard import Archive, parse_event
from .store import DATA_DIR

KEEP_EVENT_DAYS = 2  # the killboard shows recent kills; older ones live on in the archive

SCHEMA = """
CREATE TABLE IF NOT EXISTS kills (id INTEGER PRIMARY KEY, at INTEGER, event TEXT);
CREATE INDEX IF NOT EXISTS kills_at ON kills (at);
CREATE TABLE IF NOT EXISTS counts (
    day TEXT, kind TEXT, size TEXT, key TEXT, side TEXT, amount REAL,
    PRIMARY KEY (day, kind, size, key, side)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, first INTEGER, last INTEGER)
"""


class KillStore(Database):
    MIGRATIONS = (SCHEMA,)

    def __init__(self, path=None):
        super().__init__(path or DATA_DIR / config.SERVER / "kills.db")

    def known(self, ids):
        ids = list(ids)
        if not ids:
            return set()
        rows = self.db.execute("SELECT id FROM kills WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))
        return {r[0] for r in rows}

    def add(self, raws):
        """Store raw events not seen before, with their Meta counters. Returns how many were new."""
        new = 0
        with self.write() as db:
            for raw in raws:
                event, counted = parse_event(raw), meta.counter_rows(raw)
                if not event or not counted:
                    continue
                if not db.execute("INSERT OR IGNORE INTO kills VALUES (?, ?, ?)",
                                  (event["id"], event["at"], json.dumps(event, separators=(",", ":")))).rowcount:
                    continue  # already stored: already counted
                day, at, rows = counted
                db.executemany("INSERT INTO counts VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE "
                               "SET amount = amount + excluded.amount", [(day, *r) for r in rows])
                db.execute("INSERT INTO days VALUES (?, ?, ?) ON CONFLICT DO UPDATE "
                           "SET first = min(first, excluded.first), last = max(last, excluded.last)", (day, at, at))
                new += 1
        return new

    def prune(self, now=None):
        """Drop killboard events older than KEEP_EVENT_DAYS and counters older than meta.KEEP_DAYS."""
        now = time.time() if now is None else now
        oldest_day = meta.day_of(now - (meta.KEEP_DAYS - 1) * 86400)
        with self.write() as db:
            db.execute("DELETE FROM kills WHERE at < ?", (now - KEEP_EVENT_DAYS * 86400,))
            db.execute("DELETE FROM counts WHERE day < ?", (oldest_day,))
            db.execute("DELETE FROM days WHERE day < ?", (oldest_day,))

    def recent(self, limit=500):
        rows = self.db.execute("SELECT event FROM kills ORDER BY at DESC, id DESC LIMIT ?", (limit,))
        return [json.loads(r[0]) for r in rows]

    def summary(self, days=7, size=None, top=60, lost_filter=None):
        """The Meta view over the newest `days` days with kills (see meta.summarize)."""
        chosen = [r[0] for r in self.db.execute("SELECT day FROM days ORDER BY day DESC LIMIT ?", (days,))][::-1]
        if not chosen:
            return meta.summarize([], [], 0, size, top, lost_filter)
        first, last = self.db.execute("SELECT min(first), max(last) FROM days WHERE day >= ?", (chosen[0],)).fetchone()
        rows = self.db.execute("SELECT kind, size, key, side, sum(amount) FROM counts WHERE day >= ? "
                               "GROUP BY kind, size, key, side", (chosen[0],))
        return meta.summarize(rows, chosen, round((last - first) / 3600, 1), size, top, lost_filter)

    def rebuild(self, archive, days=meta.KEEP_DAYS, chunk=2000):
        """Recompute the killboard and every counter from the raw archive (after a change to how
        kills are counted, or to recover a lost database). Returns how many kills were stored."""
        with self.write() as db:
            for table in ("kills", "counts", "days"):
                db.execute(f"DELETE FROM {table}")
        total, batch = 0, []
        for raw in archive.stream(days):
            batch.append(raw)
            if len(batch) >= chunk:
                total, batch = total + self.add(batch), []
        total += self.add(batch)
        self.prune()
        return total


class Collector:
    """Polls the gameinfo kill feed into the archive and the kill store."""

    def __init__(self, kills, client=None, archive=None):
        self.kills = kills
        self.client = client or GameInfo()
        self.archive = archive or Archive()

    def poll(self):
        """Page back from the newest event until a page holds nothing new or the API's depth
        limit is reached. Pages shift while we read them as new kills arrive; that only repeats
        events (skipped by id), it never skips one. Returns how many kills were new."""
        fresh = {}
        offset = 0
        while offset <= self.client.MAX_OFFSET:
            page = [r for r in self.client.events(offset) if isinstance(r, dict) and isinstance(r.get("EventId"), int)]
            known = self.kills.known(r["EventId"] for r in page)
            new = {r["EventId"]: r for r in page if r["EventId"] not in known and r["EventId"] not in fresh}
            fresh.update(new)
            if not new or len(page) < self.client.PAGE:
                break
            offset += self.client.PAGE
        events = [fresh[i] for i in sorted(fresh)]
        if events:
            self.archive.append(events)  # raw first: see the module docstring
        added = self.kills.add(events)
        self.kills.mark("polled_at")
        return added

    def run(self, stop, interval=30):
        """Poll until `stop` is set. An error is recorded and retried next time, never fatal."""
        last_prune = 0
        while not stop.is_set():
            try:
                self.poll()
                self.kills.mark("poll_error_at", 0)
                if time.time() - last_prune > 3600:
                    self.kills.prune()
                    last_prune = time.time()
            except Exception:
                traceback.print_exc()
                self.kills.mark("poll_error_at")
            stop.wait(interval)
