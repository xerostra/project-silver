"""Play sessions: what you held at each moment, what you gained, and goals to aim at.

A session is a stretch of play with a start and an end. While one runs, snapshots record your
silver, gold and every item you hold (bag and worn), from the eye (silver/eye.py) or typed in.
Profit is the change in net worth: silver gained plus the market value of the items gained,
so selling loot for silver, or spending silver on gear, doesn't count twice or at all.

Stored in data/<server>/sessions.db, next to the market data.
"""
import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import config
from .db import Database
from .store import DATA_DIR

GOAL_KINDS = {  # kind -> what it measures
    "profit": "silver plus the value of items gained",
    "silver": "silver gained",
    "loot": "value of items gained",
    "per_hour": "profit per hour",
    "hours": "hours played",
}
PERIODS = ("session", "today", "week")


class Sessions(Database):
    MIGRATIONS = (
        """
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL);
        CREATE TABLE snapshots (
            session INTEGER NOT NULL REFERENCES sessions(id), at REAL NOT NULL,
            silver INTEGER, gold INTEGER, held TEXT NOT NULL, zone TEXT, source TEXT NOT NULL);
        CREATE INDEX snapshots_by_session ON snapshots (session, at);
        CREATE TABLE goals (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target REAL NOT NULL,
                            period TEXT NOT NULL, created REAL NOT NULL);
        """,
    )

    def __init__(self, path=None):
        super().__init__(path or DATA_DIR / config.SERVER / "sessions.db")

    # sessions ------------------------------------------------------------------------------
    def start(self, now=None):
        """A new session (ending any still open); returns its id."""
        now = time.time() if now is None else now
        with self.write() as db:
            db.execute("UPDATE sessions SET ended = ? WHERE ended IS NULL", (now,))
            return db.execute("INSERT INTO sessions (started) VALUES (?)", (now,)).lastrowid

    def stop(self, now=None):
        with self.write() as db:
            db.execute("UPDATE sessions SET ended = ? WHERE ended IS NULL", (time.time() if now is None else now,))

    def active(self):
        """The open session as {id, started, ended}, or None."""
        row = self.db.execute("SELECT id, started, ended FROM sessions WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return dict(zip(("id", "started", "ended"), row)) if row else None

    def recent(self, since=0, limit=50):
        """Sessions started since `since`, newest first."""
        rows = self.db.execute("SELECT id, started, ended FROM sessions WHERE started >= ? ORDER BY started DESC LIMIT ?",
                               (since, limit))
        return [dict(zip(("id", "started", "ended"), r)) for r in rows]

    # snapshots -----------------------------------------------------------------------------
    def record(self, session, silver, gold, held, zone=None, source="eye", now=None):
        """Store what was seen, unless it is exactly what the last snapshot already says."""
        last = self.last(session)
        if last and (last["silver"], last["gold"], last["held"], last["zone"]) == (silver, gold, held, zone):
            return False
        with self.write() as db:
            db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (session, time.time() if now is None else now, silver, gold,
                        json.dumps(held, sort_keys=True), zone, source))
        return True

    def snapshots(self, session):
        rows = self.db.execute("SELECT at, silver, gold, held, zone, source FROM snapshots WHERE session = ? ORDER BY at",
                               (session,))
        return [{"at": at, "silver": s, "gold": g, "held": json.loads(h), "zone": z, "source": src}
                for at, s, g, h, z, src in rows]

    def last(self, session):
        snaps = self.db.execute("SELECT at, silver, gold, held, zone FROM snapshots WHERE session = ? ORDER BY at DESC LIMIT 1",
                                (session,)).fetchone()
        if not snaps:
            return None
        at, s, g, h, z = snaps
        return {"at": at, "silver": s, "gold": g, "held": json.loads(h), "zone": z}

    # goals ---------------------------------------------------------------------------------
    def add_goal(self, kind, target, period, now=None):
        if kind not in GOAL_KINDS or period not in PERIODS or not target > 0:
            raise ValueError(f"bad goal: {kind!r} {target!r} per {period!r}")
        with self.write() as db:
            return db.execute("INSERT INTO goals (kind, target, period, created) VALUES (?, ?, ?, ?)",
                              (kind, float(target), period, time.time() if now is None else now)).lastrowid

    def remove_goal(self, goal_id):
        with self.write() as db:
            db.execute("DELETE FROM goals WHERE id = ?", (goal_id,))

    def goals(self):
        rows = self.db.execute("SELECT id, kind, target, period FROM goals ORDER BY id")
        return [dict(zip(("id", "kind", "target", "period"), r)) for r in rows]


def held_from(snapshot):
    """{item id: count} over the bag and worn gear of an eye Snapshot."""
    held = {}
    for i in snapshot.items + snapshot.equipment:
        held[i["id"]] = held.get(i["id"], 0) + i["count"]
    return held


def summarize(session, snaps, price, now=None):
    """What a session came to. price(item_id) -> silver each, or None if unknown."""
    now = time.time() if now is None else now
    hours = ((session["ended"] or now) - session["started"]) / 3600
    with_silver = [s for s in snaps if s["silver"] is not None]
    silver = with_silver[-1]["silver"] - with_silver[0]["silver"] if len(with_silver) > 1 else 0
    first, last = (snaps[0]["held"], snaps[-1]["held"]) if snaps else ({}, {})
    loot = []
    for item_id in sorted(set(first) | set(last)):
        change = last.get(item_id, 0) - first.get(item_id, 0)
        if change:
            each = price(item_id)
            loot.append({"id": item_id, "change": change, "each": each, "value": each * change if each else None})
    loot.sort(key=lambda x: -abs(x["value"] or 0))
    loot_value = sum(x["value"] or 0 for x in loot)
    profit = silver + loot_value
    zones = []
    for s in snaps:
        if s["zone"] and s["zone"] not in zones:
            zones.append(s["zone"])
    return {
        "id": session["id"], "started": session["started"], "ended": session["ended"], "hours": hours,
        "silver": silver, "loot": loot, "loot_value": loot_value, "profit": profit,
        "per_hour": profit / hours if hours >= 1 / 60 else None,
        "snapshots": len(snaps), "zones": zones,
        "balance": {k: next((s[k] for s in reversed(snaps) if s[k] is not None), None) for k in ("silver", "gold")},
    }


def period_start(period, now, tz_minutes):
    """When 'today' or this 'week' (from Monday) began, in the player's local time.
    tz_minutes is the browser's getTimezoneOffset(): minutes to add to local time to get UTC."""
    local = datetime.fromtimestamp(now, timezone(timedelta(minutes=-tz_minutes)))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (day - timedelta(days=day.weekday()) if period == "week" else day).timestamp()


def goal_progress(goal, summaries, now, tz_minutes=0):
    """How far a goal has got: {value, fraction}. summaries are newest first; for 'session'
    goals only the newest (the one running, or the last) counts."""
    if goal["period"] == "session":
        chosen = summaries[:1]
    else:
        since = period_start(goal["period"], now, tz_minutes)
        chosen = [s for s in summaries if s["started"] >= since]
    kind = goal["kind"]
    if kind == "per_hour":
        hours = sum(s["hours"] for s in chosen)
        value = sum(s["profit"] for s in chosen) / hours if hours >= 1 / 60 else 0
    else:
        value = sum(s[{"hours": "hours", "loot": "loot_value"}.get(kind, kind)] for s in chosen)
    return {**goal, "value": value, "fraction": max(0.0, min(1.0, value / goal["target"]))}


class EyeWatch:
    """Reads the screen every few seconds while a session runs, and records what it sees.
    The eye's references (every item render) load once, the first time it starts."""

    def __init__(self, sessions, every=5.0):
        self.sessions, self.every = sessions, every
        self.lock = threading.Lock()
        self.stop_event = None
        self.status = {"state": "off"}
        self._eye = None  # (module, References, Reader), built on first use

    def start(self, session):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
            self.stop_event = stop = threading.Event()
        threading.Thread(target=self._run, args=(session, stop), daemon=True, name="eye").start()

    def stop(self):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
                self.stop_event = None
        self.status = {"state": "off"}

    def _load(self):
        if self._eye is None:
            from . import eye  # needs the eye extra
            self.status = {"state": "starting"}
            self._eye = (eye, eye.References(), eye.Reader())
        return self._eye

    def _run(self, session, stop):
        try:
            eye, refs, reader = self._load()
        except ImportError:
            self.status = {"state": "unavailable", "error": "the eye isn't installed: uv sync --extra eye"}
            return
        screen = eye.Screen()
        try:
            while not stop.is_set():
                try:
                    snap = eye.read_inventory(screen.capture(), refs, reader)
                    if snap is None:
                        self.status = {**self.status, "state": "waiting", "error": None}
                    else:
                        self.sessions.record(session, snap.silver, snap.gold, held_from(snap), snap.zone)
                        self.status = {"state": "watching", "seen_at": time.time(), "zone": snap.zone, "error": None}
                except Exception as exc:  # keep watching: one bad frame shouldn't end the session's eye
                    traceback.print_exc()
                    self.status = {**self.status, "state": "error", "error": str(exc)}
                stop.wait(self.every)
        finally:
            screen.close()
