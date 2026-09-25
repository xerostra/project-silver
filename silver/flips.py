"""Find flip opportunities in a snapshot of market prices.

Strategies (fees from config; the dashboard recomputes profit when you toggle premium):
  instant - buy the cheapest sell order in A, sell straight into the best buy order in B.
            Covers city -> city, city -> Black Market and Caerleon -> Black Market.
            A buy order accepts equal-or-better quality, so a better-quality item can fill it.
            profit = buy_B * (1 - tax) - sell_A
  order   - buy the cheapest sell order in A, list it as a sell order in B at B's price.
            Slower (you wait for a buyer), usually bigger margin.
            profit = sell_B * (1 - tax - setup) - sell_A
  spread  - in one city, place a buy order just above the best bid, then relist just
            under the best ask. No travel, needs patience and volume.
            profit = sell * (1 - tax - setup) - buy * (1 + setup)
"""
import time
from collections import defaultdict

from . import config
from .items import label, names, tier, tradeable_ids
from .store import sync_history, sync_prices

STRATEGIES = ("instant", "order", "spread")


def sales_tax(premium):
    return config.SALES_TAX_PREMIUM if premium else config.SALES_TAX_NO_PREMIUM


def profit(strategy, buy, sell, premium):
    tax, setup = sales_tax(premium), config.SETUP_FEE
    if strategy == "instant":
        return sell * (1 - tax) - buy
    if strategy == "order":
        return sell * (1 - tax - setup) - buy
    return sell * (1 - tax - setup) - buy * (1 + setup)


def est_units(f, capital=config.CAPITAL, capture=config.VOLUME_CAPTURE):
    """Whole units a day you could both afford and expect to move. The API never says how
    many units are listed at a price, so this is capped by traded volume, not real stock.
    None when the item has no volume history."""
    if not f.get("vol"):
        return None
    return int(min(capital / f["buy"], f["vol"] * capture))


def est_daily(f, capital=config.CAPITAL, capture=config.VOLUME_CAPTURE):
    """Rough silver/day: profit per unit x est_units."""
    units = est_units(f, capital, capture)
    return None if units is None else f["profit"] * units


def route_risk(src, dst):
    """Rough travel danger of carrying goods from src to dst."""
    if src == dst or {src, dst} == {"Caerleon", config.BLACK_MARKET}:
        return "none"      # same city, no hauling
    if "Brecilien" in (src, dst):
        return "mists"     # through the Mists
    if {src, dst} & {"Caerleon", config.BLACK_MARKET}:
        return "red"       # hauling into or out of Caerleon crosses red zones
    return "safe"          # royal city <-> royal city


def rank_key(f):
    """Best first; every field breaks ties so the order never depends on input order."""
    return (-f["profit"], -f["roi"], f["item_id"], f["quality"], f["strategy"], f["from"], f["to"], f["src_quality"])


def find(rows, premium=True, max_age_hours=config.SCAN_MAX_AGE_HOURS, now=None):
    """All profitable flips in price rows (as returned by AlbionAPI.prices), sorted by rank_key."""
    now = time.time() if now is None else now
    oldest = now - max_age_hours * 3600

    # item -> quality -> city -> price row, dropping stale sides
    book = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        sell = r["sell"] if r["sell"] and r["sell_at"] and r["sell_at"] >= oldest else None
        buy = r["buy"] if r["buy"] and r["buy_at"] and r["buy_at"] >= oldest else None
        if sell or buy:
            book[r["item_id"]][r["quality"]][r["city"]] = {**r, "sell": sell, "buy": buy}

    found = []

    def add(strategy, item_id, quality, src_q, src, dst, buy, buy_at, sell, sell_at):
        p = profit(strategy, buy, sell, premium)
        if p <= 0:
            return
        found.append({
            "strategy": strategy, "item_id": item_id, "quality": quality, "src_quality": src_q,
            "from": src, "to": dst, "buy": buy, "sell": sell,
            "buy_at": buy_at, "sell_at": sell_at,
            "profit": round(p), "roi": round(p / buy, 4), "risk": route_risk(src, dst),
        })

    for item_id, quals in book.items():
        # cheapest[q][city] = (price, quality, row): cheapest sell order at quality >= q
        cheapest, running = {}, {}
        for q in sorted(quals, reverse=True):
            for city, row in quals[q].items():
                if row["sell"] and (city not in running or row["sell"] < running[city][0]):
                    running[city] = (row["sell"], q, row)
            cheapest[q] = dict(running)

        for q, cities in quals.items():
            for dst, drow in cities.items():
                if drow["buy"]:
                    for src, (price, src_q, srow) in cheapest[q].items():
                        if src != dst and src != config.BLACK_MARKET:
                            add("instant", item_id, q, src_q, src, dst, price, srow["sell_at"], drow["buy"], drow["buy_at"])
                if dst == config.BLACK_MARKET:
                    continue  # players can't list sell orders or buy from the Black Market
                if drow["sell"]:
                    for src, srow in cities.items():
                        if src not in (dst, config.BLACK_MARKET) and srow["sell"]:
                            add("order", item_id, q, q, src, dst, srow["sell"], srow["sell_at"], drow["sell"], drow["sell_at"])
                if drow["sell"] and drow["buy"]:
                    add("spread", item_id, q, q, dst, dst, drow["buy"], drow["buy_at"], drow["sell"], drow["sell_at"])
    return sorted(found, key=rank_key)


def scan(store, api, progress=print, premium=True):
    """Full market scan: sweep every tradeable item's prices into the store, find flips, and
    attach 7-day volume from the stored history."""
    ids = sorted(tradeable_ids(), key=lambda i: (tier(i), i))
    started = time.time()
    sync_prices(store, api, ids, progress=lambda d, n: progress(f"prices {d}/{n}"), swept=True)
    rows = store.prices(ids)
    found = find(rows, premium=premium, now=started)

    warnings = []
    vol_ids = sorted({f["item_id"] for f in found})
    if vol_ids and store.state("history_at") is None:  # first run: history not synced yet
        try:
            sync_history(store, api, vol_ids, 7, progress=lambda d, n: progress(f"volume {d}/{n}"))
        except Exception as exc:  # instant flips don't need history; the rest get dropped below
            warnings.append(f"Volume lookup failed ({exc}); only instant flips are shown.")
    vols = store.volumes(vol_ids)
    found = [f for f in found if apply_history(f, vols.get((f["item_id"], f["to"], f["quality"])), premium)]
    found.sort(key=rank_key)
    found = found[:config.SCAN_KEEP]

    all_names = names()
    for f in found:
        f["name"] = all_names.get(f["item_id"], f["item_id"])
        f["label"] = label(f["item_id"])
    return {
        "scanned_at": int(time.time()),
        "duration": round(time.time() - started, 1),
        "items_scanned": len(ids),
        "price_rows": len(rows),
        "warnings": warnings,
        "results": found,
    }


def apply_history(f, hist, premium):
    """Attach 7-day volume/price where the goods get sold. For flips that end in a
    sell order, the current ask can be a troll listing nobody pays, so expect to sell
    at no more than the 7-day average traded price; without history there is no way
    to tell, so those are dropped. Returns False if the flip should be dropped."""
    f["vol"] = hist["vol"] if hist else None
    f["avg"] = hist["avg"] if hist else None
    f["listed"] = f["sell"]
    if f["strategy"] != "instant" and not hist:
        return False
    if f["strategy"] != "instant" and hist["avg"] < f["sell"]:
        f["sell"] = hist["avg"]
        p = profit(f["strategy"], f["buy"], f["sell"], premium)
        f["profit"], f["roi"] = round(p), round(p / f["buy"], 4)
    return f["profit"] > 0
