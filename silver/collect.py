"""Background collection: market prices and history into the market store, kills into the kill
store. Exactly one process per server collects, whoever holds data/<server>/collector.lock:
either `python -m silver collect` (for running 24/7 under a supervisor) or, if nothing else is
collecting, `python -m silver serve` itself. The lock is released by the OS when the process
dies, so a crash never leaves it stuck.

Progress is recorded in the databases (state table), so the dashboard can show it whichever
process is collecting.
"""
import os
import threading
import time
import traceback

from . import config, items
from .store import DATA_DIR, history_days, sync_history, sync_prices

PRICE_SWEEP = 5 * 60
HISTORY_SWEEP = 3 * 3600
STALE = 15 * 60  # no sign of life from a collector for this long means it is down


if os.name == "nt":
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # the first byte; raises OSError if held
else:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire_lock(path=None):
    """The collector lock as an open file, or None if another process holds it."""
    path = path or DATA_DIR / config.SERVER / "collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")  # never truncates: on Windows the holder's locked byte must stay put
    try:
        _lock(f)
    except OSError:
        f.close()
        return None
    return f


class Syncer:
    """Every item's prices every PRICE_SWEEP, and history every HISTORY_SWEEP (all of it on the
    first run), then fair prices. A failed round is recorded and retried next round."""

    def __init__(self, store, api):
        self.store, self.api = store, api
        self.step = None

    def round(self):
        ids = items.tradeable_ids()
        sync_prices(self.store, self.api, ids, progress=lambda d, n: self._step(f"prices {d}/{n}"), swept=True)
        last = self.store.state("history_at")
        if last is None or time.time() - last > HISTORY_SWEEP:
            days = history_days(self.store)
            sync_history(self.store, self.api, ids, days, full=True,
                         progress=lambda d, n: self._step(f"history ({days} days) {d}/{n}"))

    def _step(self, text):
        self.step = text

    def run(self, stop):
        while not stop.is_set():
            try:
                self.round()
                self.store.mark("sync_error_at", 0)
            except Exception:
                traceback.print_exc()
                self.store.mark("sync_error_at")
            self.step = None
            stop.wait(PRICE_SWEEP)


def start(syncer, collector, stop):
    """Run both collection loops in daemon threads until `stop` is set; returns the threads."""
    threads = [threading.Thread(target=target, args=(stop,), daemon=True) for target in (syncer.run, collector.run)]
    for t in threads:
        t.start()
    return threads


def standby(stop, begin, every=60, path=None):
    """Take over collection whenever the lock is free: now, or later if the process holding it
    dies. begin(lock) starts collecting; it is called at most once. Runs until `stop`."""
    while not stop.is_set():
        lock = acquire_lock(path)
        if lock:
            begin(lock)
            return
        stop.wait(every)


def status(store, kills, syncer=None, now=None):
    """What the dashboard shows about collection, from the databases, so it works whichever
    process collects. `syncer` is this process's own, if it is the collector."""
    now = time.time() if now is None else now
    polled, prices = kills.state("polled_at"), store.state("prices_at")
    alive = [t for t in (polled, prices) if t]
    return {
        "prices_at": prices, "history_at": store.state("history_at"), "fair_at": store.state("fair_at"),
        "kills_at": polled,
        "sync_failing": bool(store.state("sync_error_at")), "kills_failing": bool(kills.state("poll_error_at")),
        "here": syncer is not None, "step": syncer.step if syncer else None,
        "collector_down": not alive or now - max(alive) > STALE,
    }
