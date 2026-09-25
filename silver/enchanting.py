"""Enchanting as a trade: buy an item, upgrade it in your inventory, sell the result.

Enchanting needs no station, so every part can be bought wherever it is cheapest and the result
sold wherever pays best: instantly into a buy order (the Black Market included), or listed as a
sell order. Besides one-level steps (.0 -> .1) this covers jumps over several levels (.0 -> .3),
which can pay when a single step doesn't.

Two views of each upgrade:
- now: today's fresh listings and buy orders, i.e. what you could do right now;
- typical: fair prices on both sides (see store.py), selling either listed in the city markets
  or to the Black Market, i.e. whether it usually pays. A good "now" with a bad "typical"
  usually means a stale or lucky price.
"""
from collections import defaultdict

from . import config
from .flips import sales_tax
from .store import ALL


def jumps(recipes):
    """Upgrade recipes -> every jump from level a to b (a < b) with all steps known:
    {"id": item@b, "from": item@a, "levels": (a, b), "materials": [(id, count)], "tier", "name"}."""
    by_line = defaultdict(dict)
    for r in recipes:
        if r.get("upgrade") and len(r["inputs"]) >= 2:
            by_line[r["id"].split("@")[0]][r["enchant"]] = r
    out = []
    for line, steps in sorted(by_line.items()):
        for a in range(0, 3):
            materials = defaultdict(int)
            for b in range(a + 1, 4):
                step = steps.get(b)
                if not step:
                    break
                for mat, count, _ in step["inputs"][1:]:
                    materials[mat] += count
                out.append({"id": step["id"], "from": steps[a + 1]["inputs"][0][0], "levels": (a, b),
                            "materials": sorted(materials.items()), "tier": step["tier"], "name": step["name"]})
    return out


def best_ask(quotes, oldest):
    """Cheapest fresh sell order in a city market: (price, city, at) or None."""
    asks = [(q["sell"], c, q["sell_at"]) for c, q in quotes.items()
            if c != config.BLACK_MARKET and q["sell"] and q["sell_at"] >= oldest]
    return min(asks) if asks else None


def best_sale(quotes, fair_by_city, oldest, premium, mode):
    """Best net silver for one unit: (net, gross, city, how) or None. how is "instant" (into a
    buy order, the Black Market included) or "order" (listed at the lower of the current ask and
    the city's fair price, or at the fair price where nothing is listed)."""
    tax = sales_tax(premium)
    options = []
    if mode in ("instant", "best"):
        options += [(q["buy"] * (1 - tax), q["buy"], c, "instant") for c, q in quotes.items()
                    if q["buy"] and q["buy_at"] >= oldest]
    if mode in ("order", "best"):
        for c, fair in fair_by_city.items():
            if c in (ALL, config.BLACK_MARKET) or fair["days"] < 3:
                continue
            q = quotes.get(c) or {}
            price = min(q["sell"], fair["price"]) if q.get("sell") and q["sell_at"] >= oldest else fair["price"]
            options.append((price * (1 - tax - config.SETUP_FEE), price, c, "order"))
    return max(options, default=None)


def typical_exits(fair_by_city, tax, mode):
    """[(net, where)] for selling at fair prices: listed in the city markets, or instantly to the
    Black Market (which only ever buys, so its fair price is what it pays)."""
    out = []
    pooled, bm = fair_by_city.get(ALL), fair_by_city.get(config.BLACK_MARKET)
    if pooled and mode in ("order", "best"):
        out.append((pooled["price"] * (1 - tax - config.SETUP_FEE), "city markets"))
    if bm and mode in ("instant", "best"):
        out.append((bm["price"] * (1 - tax), config.BLACK_MARKET))
    return out


def evaluate(jump_list, prices, fair, oldest, premium=True, mode="best"):
    """Price every jump. prices: {id: {city: quote}}; fair: {id: {city: fair}} (Normal quality).
    Rows have now_* fields when every part and a buyer are fresh, and typical_* when every part
    has a fair price."""
    tax = sales_tax(premium)
    rows = []
    for j in jump_list:
        parts = [(j["from"], 1)] + j["materials"]
        target_fair = fair.get(j["id"], {})
        row = {**j, "levels": list(j["levels"]), "vol": target_fair.get(ALL, {}).get("vol"),
               "bm_vol": target_fair.get(config.BLACK_MARKET, {}).get("vol")}

        asks = [best_ask(prices.get(i, {}), oldest) for i, _ in parts]
        sale = best_sale(prices.get(j["id"], {}), fair.get(j["id"], {}), oldest, premium, mode)
        if all(asks) and sale:
            cost = sum(a[0] * n for a, (_, n) in zip(asks, parts))
            row.update(now_cost=round(cost), now_profit=round(sale[0] - cost), now_roi=round((sale[0] - cost) / cost, 4),
                       buy=[{"id": i, "count": n, "price": a[0], "city": a[1], "at": a[2]} for (i, n), a in zip(parts, asks)],
                       sell={"price": sale[1], "city": sale[2], "how": sale[3]})

        fairs = [fair.get(i, {}).get(ALL, {}).get("price") for i, _ in parts]
        exits = typical_exits(fair.get(j["id"], {}), tax, mode)
        if all(fairs) and exits:
            cost = sum(p * n for p, (_, n) in zip(fairs, parts))
            net, where = max(exits)
            row.update(typical_cost=round(cost), typical_profit=round(net - cost),
                       typical_roi=round((net - cost) / cost, 4), typical_sell=where)

        if "now_profit" in row or "typical_profit" in row:
            rows.append(row)
    return rows
