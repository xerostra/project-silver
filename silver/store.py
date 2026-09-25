"""Local market store: latest prices and daily history for every item, in SQLite.

Every tab reads from here, so pages are instant, survive restarts and keep working when the API
is down. A background sync keeps it current (sync_prices / sync_history); a Refresh button
fetches just its own items live and writes them here too.

Fair price: what an item really trades at, from history rather than one listing. It is the
volume-weighted median of the daily average prices over the last 14 days (28 if fewer than 3
days traded), per city and across the city markets. Days priced over 4x away from the plain
median day are dropped first, so a few big troll trades can't anchor it; the weights then make a
500-unit day count more than a 2-unit one. An item that trades on only a day or two can still be
wrong, which is why `days` is always reported with the price.
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .api import location_param, market_params, parse_price_row, parse_ts, positive_int
from .db import Database

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DAY = 86400
BACKFILL_DAYS = 120     # history the API keeps is about this deep
FAIR_DAYS, FAIR_WIDE_DAYS, FAIR_MIN_POINTS = 14, 28, 3
OUTLIER = 4             # drop daily prices this many times above or below the median day
ALL = ""                # the fair-price "city" pooling every city market (not the Black Market)

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    item TEXT, city TEXT, quality INTEGER, sell INTEGER, sell_at INTEGER, buy INTEGER, buy_at INTEGER,
    PRIMARY KEY (item, city, quality)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS history (
    item TEXT, city TEXT, quality INTEGER, day INTEGER, count INTEGER, avg INTEGER,
    PRIMARY KEY (item, city, quality, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fair (
    item TEXT, quality INTEGER, city TEXT, price INTEGER, vol REAL, days INTEGER, trend REAL, last_day INTEGER,
    PRIMARY KEY (item, quality, city)) WITHOUT ROWID
"""

# Each side of a price keeps whichever observation is newest.
UPSERT_PRICE = """
INSERT INTO prices VALUES (:item_id, :city, :quality, :sell, :sell_at, :buy, :buy_at)
ON CONFLICT (item, city, quality) DO UPDATE SET
  sell    = CASE WHEN excluded.sell_at >= coalesce(sell_at, 0) THEN excluded.sell    ELSE sell    END,
  sell_at = CASE WHEN excluded.sell_at >= coalesce(sell_at, 0) THEN excluded.sell_at ELSE sell_at END,
  buy     = CASE WHEN excluded.buy_at  >= coalesce(buy_at, 0)  THEN excluded.buy     ELSE buy     END,
  buy_at  = CASE WHEN excluded.buy_at  >= coalesce(buy_at, 0)  THEN excluded.buy_at  ELSE buy_at  END
"""


def parse_history(series):
    """Raw history series -> [(item, city, quality, day, count, avg)]; bad points are skipped."""
    out = []
    for s in series:
        try:
            key = (str(s["item_id"]), str(s["location"]), int(s["quality"]))
            points = s.get("data") or []
        except (KeyError, TypeError, ValueError):
            continue
        for p in points:
            if not isinstance(p, dict):
                continue
            count, avg, at = positive_int(p.get("item_count")), positive_int(p.get("avg_price")), parse_ts(p.get("timestamp"))
            if count and avg and at:
                out.append((*key, at - at % DAY, count, avg))
    return out


def weighted_median(points):
    """[(price, weight)] -> the price where half the weight lies on either side."""
    points = sorted(points)
    half, run = sum(w for _, w in points) / 2, 0
    for price, weight in points:
        run += weight
        if run >= half:
            return price
    return None


def trim_outliers(points):
    """Drop (day, count, avg) points priced over OUTLIER x away from the unweighted median point.
    Unweighted, so one huge-volume troll day can't make itself the reference."""
    if len(points) < 3:
        return points
    prices = sorted(a for _, _, a in points)
    mid = prices[len(prices) // 2]
    return [p for p in points if mid / OUTLIER <= p[2] <= mid * OUTLIER]


def fair_price(points, today):
    """[(day, count, avg)] -> {price, vol, days, trend, last_day} or None.
    trend = last 7 days' median against the 7 before, as a fraction (None if either is empty)."""
    points = trim_outliers([p for p in points if p[0] > today - FAIR_WIDE_DAYS * DAY])
    recent = [p for p in points if p[0] > today - FAIR_DAYS * DAY]
    if len({d for d, _, _ in recent}) < FAIR_MIN_POINTS:
        recent = points
    if not recent:
        return None
    days = {d for d, _, _ in recent}
    week = weighted_median([(a, c) for d, c, a in points if d > today - 7 * DAY])
    before = weighted_median([(a, c) for d, c, a in points if today - 14 * DAY < d <= today - 7 * DAY])
    return {"price": weighted_median([(a, c) for _, c, a in recent]),
            "vol": round(sum(c for _, c, _ in recent) / len(days), 1),
            "days": len(days), "trend": round(week / before - 1, 4) if week and before else None,
            "last_day": max(days)}


def compute_fair(rows, today):
    """History rows (item, city, quality, day, count, avg) -> fair rows (item, quality, city, ...),
    per city and pooled over the city markets (ALL)."""
    series = defaultdict(list)
    for item, city, quality, day, count, avg in rows:
        series[(item, quality, city)].append((day, count, avg))
        if city != config.BLACK_MARKET:
            series[(item, quality, ALL)].append((day, count, avg))
    out = []
    for (item, quality, city), points in sorted(series.items()):
        f = fair_price(points, today)
        if f:
            out.append((item, quality, city, f["price"], f["vol"], f["days"], f["trend"], f["last_day"]))
    return out


def today_start(now=None):
    now = time.time() if now is None else now
    return int(now - now % DAY)


class Store(Database):
    """Latest prices, daily history and fair prices for one server (data/<server>/market.db)."""
    MIGRATIONS = (SCHEMA,)

    def __init__(self, path=None):
        super().__init__(path or DATA_DIR / config.SERVER / "market.db")

    # ---- writes ----
    def put_prices(self, rows):
        """Price rows shaped like AlbionAPI.prices."""
        with self.write() as db:
            db.executemany(UPSERT_PRICE, rows)

    def put_history(self, points):
        with self.write() as db:
            db.executemany("INSERT OR REPLACE INTO history VALUES (?, ?, ?, ?, ?, ?)", points)

    def rebuild_fair(self, now=None):
        """Recompute every fair price from the last FAIR_WIDE_DAYS of history."""
        today = today_start(now)
        rows = self.db.execute("SELECT item, city, quality, day, count, avg FROM history WHERE day > ?",
                               (today - FAIR_WIDE_DAYS * DAY,)).fetchall()
        fair = compute_fair(rows, today)
        with self.write() as db:
            db.execute("DELETE FROM fair")
            db.executemany("INSERT INTO fair VALUES (?, ?, ?, ?, ?, ?, ?, ?)", fair)
        self.mark("fair_at")
        return len(fair)

    # ---- reads ----
    @staticmethod
    def _where(ids, qualities, cities):
        """SQL filter for optional id/quality/city lists. Ids go in as one JSON array: there
        can be thousands, more than SQLite allows as separate parameters."""
        clauses, args = [], []
        if ids is not None:
            clauses.append("item IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(list(ids)))
        if qualities:
            clauses.append(f"quality IN ({','.join('?' * len(qualities))})")
            args += list(qualities)
        if cities:
            clauses.append(f"city IN ({','.join('?' * len(cities))})")
            args += list(cities)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def prices(self, ids=None, qualities=None, cities=None):
        """Rows shaped like AlbionAPI.prices, sorted by (item, city, quality)."""
        where, args = self._where(ids, qualities, cities)
        cur = self.db.execute(f"SELECT item, city, quality, sell, sell_at, buy, buy_at FROM prices{where} "
                              "ORDER BY item, city, quality", args)
        return [{"item_id": i, "city": c, "quality": q, "sell": s, "sell_at": sa, "buy": b, "buy_at": ba}
                for i, c, q, s, sa, b, ba in cur]

    def volumes(self, ids=None, days=7, qualities=None, cities=None, now=None):
        """{(item, city, quality): {vol, avg, days}} over the last `days` days, like
        api.summarize_history: averages over the days that have data."""
        where, args = self._where(ids, qualities, cities)
        where += (" AND " if where else " WHERE ") + "day > ?"
        args.append(today_start(now) - days * DAY)
        cur = self.db.execute(f"SELECT item, city, quality, sum(count), sum(count * avg), count(*) FROM history{where} "
                              "GROUP BY item, city, quality", args)
        return {(i, c, q): {"vol": round(n / d, 1), "avg": round(total / n), "days": d} for i, c, q, n, total, d in cur}

    def fair(self, ids=None, qualities=(1,)):
        """{item: {quality: {city or ALL: {price, vol, days, trend, last_day}}}}."""
        where, args = self._where(ids, qualities, None)
        out = {}
        for item, q, city, price, vol, days, trend, last in self.db.execute(
                f"SELECT item, quality, city, price, vol, days, trend, last_day FROM fair{where}", args):
            out.setdefault(item, {}).setdefault(q, {})[city] = {
                "price": price, "vol": vol, "days": days, "trend": trend, "last_day": last}
        return out

    def history(self, item, days=BACKFILL_DAYS, quality=1, now=None):
        """One item's daily points: {city: [(day, count, avg)]}, oldest first."""
        cur = self.db.execute("SELECT city, day, count, avg FROM history WHERE item = ? AND quality = ? AND day > ? "
                              "ORDER BY city, day", (item, quality, today_start(now) - days * DAY))
        out = {}
        for city, day, count, avg in cur:
            out.setdefault(city, []).append((day, count, avg))
        return out


# ---- sync from the API ----
def sync_prices(store, api, ids, progress=None, swept=False):
    """Fetch current prices for ids (every market, every quality) into the store."""
    for rows in api.iter_batches(ids, "prices", market_params(config.MARKETS, None), progress=progress):
        store.put_prices([r for r in map(parse_price_row, rows) if r])
    if swept:
        store.mark("prices_at")


def history_days(store, now=None):
    """How far back a history sync must reach: everything on the first run, else back to a
    couple of days before the last sync (the newest days keep filling in)."""
    last = store.state("history_at")
    if last is None:
        return BACKFILL_DAYS
    now = time.time() if now is None else now
    return max(3, min(BACKFILL_DAYS, int((now - last) / DAY) + 3))


def sync_history(store, api, ids, days, progress=None, full=False):
    """Fetch `days` of daily history for ids into the store, a batch at a time.
    full=True marks a sync of every item and rebuilds the fair prices."""
    today = datetime.now(timezone.utc).date()
    params = {"time-scale": 24, "date": (today - timedelta(days=days)).isoformat(),
              "end_date": today.isoformat(), "locations": ",".join(location_param(m) for m in config.MARKETS)}
    for series in api.iter_batches(ids, "history", params, progress=progress):
        store.put_history(parse_history(series))
    if full:
        store.mark("history_at")
        store.rebuild_fair()
