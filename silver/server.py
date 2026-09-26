"""Local dashboard server: serves web/index.html and JSON built from the API.

GET  /api/config                 defaults and fee constants for the page
GET  /api/resources[?refresh=1]  resource prices for every city (+ 7-day volume)
GET  /api/hearts[?refresh=1]     faction hearts: prices, refining uses, transport routes
GET  /api/laborers[?refresh=1]   journals, laborer returns, happiness data, prices
GET  /api/search?q=X             tradeable items matching a name or id
GET  /api/item?id=X[&refresh=1]  one item: every quality in every market, and its other tiers
GET  /api/craft/groups           recipe groups (Refining/Metal Bars, Weapons/Sword, ...) + bonus values
GET  /api/craft?section=X&group=Y[&refresh=1]  recipes in a group with current prices
GET  /api/flips                  latest flip scan + progress of a running one
GET  /api/focus?premium&reduction&station_fee&daily&farm_slots&pasture_slots&max_age
                                 focus uses ranked by silver per focus, and a plan for the day
POST /api/focus/scan             refresh the planner's market data in the background
GET  /api/farming?premium&water&feed_focus&max_age[&refresh=1]  crops, herbs and animals, costed
POST /api/flips/scan             start a full market scan in the background
GET  /api/enchanting?max_age&premium&mode   every enchanting upgrade priced now and typically
GET  /api/market?max_age=H        every item's fair price, city spread and today's best ask/bid
GET  /api/status                 background market sync: last sweeps, current step, last error
GET  /api/gold                   hourly gold price (about 30 days) and its summary
GET  /api/killboard              newest kill events collected from the live feed
GET  /api/intel/search?q=X       players and guilds matching a name
GET  /api/player?id=X            a player's profile, recent kills and deaths
GET  /api/guild?id=X             a guild's totals and members
GET  /api/meta?days=N&size=S     weapons, builds and items lost in collected kills (S: solo/small/group/zvz)
GET  /api/session?tz=M            the running session, recent ones, goals and the eye's state (M: the
                                 browser's getTimezoneOffset(), for "today" and "this week")
POST /api/session/start|stop     start (the eye watches while it runs) or end a session
POST /api/session/silver         {"silver": N}: record your silver by hand (without the eye)
POST /api/goals                  {"kind", "target", "period"}: add a goal; /api/goals/delete {"id"}
"""
import gzip
import json
import signal
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import icons as icons_mod
from . import collect, config, crafting, enchanting, production, farming, flips, focus, gold, hearts, intel, items, laborers, meta, sessions, transports
from .api import AlbionAPI, APIError, GameInfo
from .killstore import Collector, KillStore
from .store import ALL, Store, sync_history, sync_prices

WEB_DIR = Path(__file__).resolve().parent / "web"
HTML, CSS = "text/html; charset=utf-8", "text/css; charset=utf-8"
PAGES = {  # URL path -> (file in WEB_DIR, content type)
    "/": ("index.html", HTML), "/index.html": ("index.html", HTML),
    "/kit": ("kit.html", HTML), "/silver.css": ("silver.css", CSS),
    # the app icon: tab and window (SVG), Windows (ICO), installed-app/taskbar (manifest + PNGs)
    "/app.svg": ("app.svg", "image/svg+xml"), "/app.ico": ("app.ico", "image/x-icon"),
    "/favicon.ico": ("app.ico", "image/x-icon"),
    "/app-192.png": ("app-192.png", "image/png"), "/app-512.png": ("app-512.png", "image/png"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
PRICE_TTL = 60          # seconds before prices are re-fetched
VOLUME_TTL = 30 * 60    # daily history changes slowly
GOLD_TTL = 5 * 60       # gold is hourly

api = AlbionAPI()
gameinfo = GameInfo()


class Snapshots:
    """Prices and 7-day volumes for a named set of items, read from the local store.
    refresh=True (or a store that has never been swept) fetches the items live first; if that
    fails, whatever the store holds is served with a warning."""

    def __init__(self):
        self.lock = threading.Lock()
        self.locks = {}      # one lock per key, so a slow refresh only blocks its own key
        self.refreshed = {}  # key -> when its items were last fetched live
        self.history_fetched = set()

    def get(self, key, ids, volume_ids, locations, refresh=False, qualities=(1,), shape=None):
        with self.lock:
            key_lock = self.locks.setdefault(key, threading.Lock())
        qualities = list(qualities) or None
        warning = None
        with key_lock:
            if refresh or store.state("prices_at") is None:
                try:
                    sync_prices(store, api, ids)
                    self.refreshed[key] = time.time()
                except Exception as exc:
                    if not store.prices(ids, qualities, locations):
                        raise
                    traceback.print_exc()
                    warning = f"Refresh failed, showing stored prices: {exc}"
            if store.state("history_at") is None and key not in self.history_fetched:
                try:  # the first full history sync is still running: fetch this set's week now
                    sync_history(store, api, volume_ids, 7)
                    self.history_fetched.add(key)
                except Exception:  # volume is extra info; keep serving prices
                    traceback.print_exc()
        volumes = {}
        for (item_id, city, quality), v in sorted(store.volumes(volume_ids, 7, qualities, locations).items()):
            volumes.setdefault(quality if shape else item_id, {})[city] = v
        return {"prices": (shape or by_item_city)(store.prices(ids, qualities, locations)),
                "prices_at": max(store.state("prices_at") or 0, self.refreshed.get(key, 0)),
                "volumes": volumes, "warning": warning}


def by_item_city(rows):
    """Price rows -> {item_id: {city: {sell, sell_at, buy, buy_at}}}."""
    out = {}
    for r in rows:
        out.setdefault(r["item_id"], {})[r["city"]] = {k: r[k] for k in ("sell", "sell_at", "buy", "buy_at")}
    return out


def by_quality_city(rows):
    """Price rows for one item -> {quality: {city: {sell, sell_at, buy, buy_at}}}."""
    out = {}
    for r in rows:
        out.setdefault(r["quality"], {})[r["city"]] = {k: r[k] for k in ("sell", "sell_at", "buy", "buy_at")}
    return out


def item_payload(item_id, refresh):
    """Every quality of one item in every market, plus its other tiers/enchantments."""
    known = items.names()
    if item_id not in known:
        return None
    snap = snapshots.get("item:" + item_id, [item_id], [item_id], config.MARKETS, refresh,
                         qualities=(), shape=by_quality_city)
    return {"id": item_id, "name": known[item_id], "label": items.label(item_id),
            "variants": items.variants(item_id), "fetched_at": int(snap["prices_at"]),
            "warning": snap["warning"], "prices": snap["prices"], "volumes": snap["volumes"],
            "fair": store.fair([item_id], qualities=None).get(item_id, {}),
            "history": store.history(item_id)}


_jumps = None


def enchant_payload(query):
    """Every enchanting upgrade (one level or several) priced now and typically, best first."""
    global _jumps
    if _jumps is None:
        _jumps = enchanting.jumps(crafting.recipes().values())
    ids = sorted({i for j in _jumps for i in (j["id"], j["from"], *(m for m, _ in j["materials"]))})
    mode = query.get("mode", ["best"])[0]
    rows = enchanting.evaluate(
        _jumps, by_item_city(store.prices(ids, [1], config.MARKETS)),
        {i: f.get(1, {}) for i, f in store.fair(ids).items()},
        oldest=time.time() - number(query, "max_age", 6, 1, 72) * 3600,
        premium=flag(query, "premium", config.PREMIUM), mode=mode if mode in ("instant", "order", "best") else "best")
    rows.sort(key=lambda r: (-(r.get("now_profit") if r.get("now_profit") is not None else -1e18),
                             -(r.get("typical_profit") or -1e18), r["id"], r["levels"]))
    known = items.names()
    icons.want(r["id"] for r in rows[:300])
    return {"rows": rows, "fetched_at": int(store.state("prices_at") or 0),
            "names": {i: known.get(i, i) for r in rows for i, _ in r["materials"]}}


def market_payload(query):
    """Every item with a fair price (Normal quality): what it trades at across the city markets,
    its cheapest and dearest city, the Black Market, and today's best fresh ask and bid."""
    oldest = time.time() - number(query, "max_age", 6, 1, 72) * 3600
    fair = store.fair(qualities=(1,))
    prices = by_item_city(store.prices(list(fair), [1], config.MARKETS))
    known, cats = items.names(), crafting.categories()
    rows = []
    for item_id, by_quality in sorted(fair.items()):
        f = by_quality.get(1, {})
        pooled = f.get(ALL)
        if not pooled or item_id not in known:
            continue
        cities = sorted((v["price"], c) for c, v in f.items() if c not in (ALL, config.BLACK_MARKET))
        here = prices.get(item_id, {})
        asks = [(p["sell"], c, p["sell_at"]) for c, p in here.items()
                if c != config.BLACK_MARKET and p["sell"] and p["sell_at"] >= oldest]
        bids = [(p["buy"], c, p["buy_at"]) for c, p in here.items() if p["buy"] and p["buy_at"] >= oldest]
        rows.append({
            "id": item_id, "name": known[item_id], "cat": cats.get(item_id.split("@")[0], ""),
            "tier": items.tier(item_id), "enchant": items.enchant(item_id),
            "fair": pooled["price"], "vol": pooled["vol"], "days": pooled["days"], "trend": pooled["trend"],
            "low": cities[0][::-1] if cities else None, "high": cities[-1][::-1] if cities else None,
            "bm": f.get(config.BLACK_MARKET, {}).get("price"),
            "ask": min(asks) if asks else None, "bid": max(bids) if bids else None,
        })
    icons.want(r["id"] for r in sorted(rows, key=lambda r: -r["fair"] * r["vol"]))  # the page's default order
    return {"fair_at": store.state("fair_at"), "rows": rows}


def hearts_payload(refresh):
    """Faction hearts: prices, what they save in refining, and caravan pack costs."""
    ids = hearts.item_ids()
    snap = snapshots.get("hearts", ids, ids, config.CITIES, refresh)
    return {"fetched_at": int(snap["prices_at"]), "warning": snap["warning"],
            "hearts": hearts.catalog(), "uses": hearts.uses(),
            "transports": transports.catalog()["routes"], "packs": transports.catalog()["packs"],
            "conversion_silver": hearts.CONVERSION_SILVER,
            "shadowheart": hearts.SHADOWHEART,
            "prices": snap["prices"], "volumes": snap["volumes"]}


def laborers_payload(refresh):
    """Journals, what each laborer returns, happiness data, and prices for all of it."""
    ids = laborers.item_ids()
    snap = snapshots.get("laborers", ids, ids, config.CITIES, refresh)
    names = items.names()
    cat = laborers.catalog()
    loot_ids = sorted({i for job in cat["jobs"] for i, _, _ in job["loot"]})
    return {"fetched_at": int(snap["prices_at"]), "warning": snap["warning"], **cat,
            "names": {i: names.get(i, i) for i in loot_ids},
            "prices": snap["prices"], "volumes": snap["volumes"], "fair": fair_by_item(ids)}


def number(query, name, default, lo, hi):
    """A bounded number from the query string; anything missing or malformed gives the default."""
    try:
        value = float(query.get(name, [default])[0])
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, value)) if value == value else default  # NaN -> default


def flag(query, name, default):
    return query.get(name, ["1" if default else "0"])[0] == "1"


def fair_by_item(ids):
    """{item: {city or "" (all cities pooled): {price, vol}}} at Normal quality: what each item
    typically trades at. Pages and the planners fall back to it, marked ~, where nothing is live."""
    return {i: {c: {"price": v["price"], "vol": v["vol"]} for c, v in q.get(1, {}).items()}
            for i, q in store.fair(sorted(ids)).items()}


def focus_payload(query):
    """Rank every focus use and plan the day, from the stored market data."""
    state = focus_job.state()
    refreshed = state.pop("result")
    if store.state("prices_at") is None and refreshed is None:
        return {**state, "ready": False}
    ids = focus.item_ids()
    volumes = {}
    for (item_id, city, _), v in sorted(store.volumes(ids, 7, [1], config.MARKETS).items()):
        volumes.setdefault(item_id, {})[city] = v
    premium = flag(query, "premium", config.PREMIUM)
    market = focus.Market(by_item_city(store.prices(ids, [1], config.MARKETS)), volumes, premium=premium,
                          max_age_hours=number(query, "max_age", 6, 1, 72), fair=fair_by_item(ids))
    uses = focus.rank(market, reduction=number(query, "reduction", 0, 0, 95) / 100,
                      station_fee=number(query, "station_fee", config.STATION_FEE, 0, 10000), premium=premium)
    day = focus.plan(uses, daily_focus=number(query, "daily", 10000, 0, 1_000_000),
                     farm_slots=int(number(query, "farm_slots", 0, 0, 1000)),
                     pasture_slots=int(number(query, "pasture_slots", 0, 0, 1000)))
    return {**state, "ready": True, "fetched_at": int(max(store.state("prices_at") or 0, refreshed or 0)),
            "count": len(uses), "uses": uses[:300], "plan": day}


def farming_payload(query, refresh):
    """Every crop, herb and animal, costed with the page's settings."""
    ids = farming.item_ids()
    snap = snapshots.get("farming", ids, ids, config.CITIES, refresh)
    premium = flag(query, "premium", config.PREMIUM)
    water = flag(query, "water", True)
    market = focus.Market(snap["prices"], snap["volumes"], premium=premium,
                          max_age_hours=number(query, "max_age", 6, 1, 72), fair=fair_by_item(ids))
    cat = farming.catalog()
    plants = []
    for p in cat["plants"]:
        r = farming.plant_result(p, market.sale, market.ask, premium, water)
        plants.append({**p, "result": r and {**r, "est": market.any_estimated(*farming.plant_items(p))}})
    animals = []
    for a in cat["animals"]:
        focused = a["feedings"] if flag(query, "feed_focus", True) else 0
        r = farming.animal_result(a, market.sale, market.ask, cat["foods"], focused)
        animals.append({**a, "result": r and {**r, "est": market.any_estimated(*farming.animal_items(a, r))}})
    return {"fetched_at": int(snap["prices_at"]), "warning": snap["warning"],
            "plants": plants, "animals": animals}


def resources_payload(refresh):
    groups = items.resource_groups()
    ids = [i for _, g in groups for i in g]
    snap = snapshots.get("resources", ids, ids, config.CITIES, refresh)
    names = items.names()
    return {
        "fetched_at": int(snap["prices_at"]),
        "warning": snap["warning"],
        "groups": [{"name": title, "items": [
            {"id": i, "name": names.get(i, i), "tier": items.tier(i), "enchant": items.enchant(i),
             "label": items.label(i)} for i in g]} for title, g in groups],
        "prices": snap["prices"],
        "volumes": snap["volumes"],
    }


_ways = None


def station_ways():
    """{item id: [station recipes that make it]} (enchanting upgrades are not stations)."""
    global _ways
    if _ways is None:
        _ways = {}
        for r in crafting.recipes().values():
            if not r["upgrade"]:
                _ways.setdefault(r["id"], []).append(r)
    return _ways


def ingredient_tree(recipes, ways):
    """Every item that could go into these recipes, however deep: their inputs, the inputs of
    any input that can be made, and so on."""
    seen, todo = set(), [i for r in recipes for i, _, _ in r["inputs"]]
    while todo:
        item_id = todo.pop()
        if item_id not in seen:
            seen.add(item_id)
            todo += [i for r in ways.get(item_id, ()) for i, _, _ in r["inputs"]]
    return seen


def buyer(prices, cities, mode, oldest):
    """buy(item id) -> (unit price, city) the Craft tab's way: cheapest fresh sell order, or a
    buy order plus its setup fee, in the given cities."""
    def buy(item_id):
        best = None
        for c in cities:
            q = prices.get(item_id, {}).get(c)
            if not q:
                continue
            if mode == "ask":
                price = q["sell"] if q["sell"] and q["sell_at"] >= oldest else None
            else:
                price = q["buy"] * (1 + config.SETUP_FEE) if q["buy"] and q["buy_at"] >= oldest else None
            if price and (not best or price < best[0]):
                best = (price, c)
        return best
    return buy


def craft_payload(section, group, refresh, query=None):
    """Recipes in one section+group plus prices of everything they use; None if unknown.
    Section matters: "Sword" exists under both Weapons (craft) and Enchanting (upgrade).
    With plan=1, also a make-or-buy plan per recipe (production.py) for the page's settings."""
    query = query or {}
    chosen = [r for r in crafting.recipes().values() if r["section"] == section and r["group"] == group]
    if not chosen:
        return None
    products = sorted({r["id"] for r in chosen})
    inputs = sorted({i for r in chosen for i, _, _ in r["inputs"]})
    planning = flag(query, "plan", False) and not chosen[0]["upgrade"]
    tree = sorted(ingredient_tree(chosen, station_ways())) if planning else inputs
    snap = snapshots.get(f"craft:{section}:{group}:{planning}", sorted(set(products) | set(tree)),
                         products, config.MARKETS, refresh)
    names = items.names()
    fair = fair_by_item(set(products) | set(tree))  # the page falls back to these, marked ~
    out = {"fetched_at": int(snap["prices_at"]), "warning": snap["warning"], "recipes": chosen, "prices": snap["prices"],
           "volumes": snap["volumes"], "fair": fair, "names": {i: names.get(i, i) for i in tree}}
    if planning:
        city = query.get("city", [""])[0]
        city = city if city in config.CITIES else config.CITIES[0]
        cities = [city] if query.get("mat_src", ["cheapest"])[0] == "craft" else config.CITIES
        buy = buyer(snap["prices"], cities, query.get("mat_mode", ["ask"])[0],
                    time.time() - number(query, "max_age", 6, 1, 72) * 3600)
        planner = production.Planner(station_ways(), buy, city, focus=flag(query, "focus", False),
                                     daily=number(query, "daily", 0, 0, 100) / 100,
                                     station_fee=number(query, "fee", config.STATION_FEE, 0, 10000))
        out["plans"] = [production.summarize(m) if (m := planner.make(r, city=city)) else None for r in chosen]
    return out


class Cached:
    """One value from build(), rebuilt after ttl seconds. If a rebuild fails, the last good
    value keeps being served with a warning."""

    def __init__(self, build, ttl):
        self.build, self.ttl = build, ttl
        self.lock = threading.Lock()
        self.value, self.at, self.warning = None, 0, None

    def get(self):
        with self.lock:
            if self.value is None or time.time() - self.at > self.ttl:
                try:
                    self.value, self.at, self.warning = self.build(), time.time(), None
                except Exception as exc:
                    if self.value is None:
                        raise
                    traceback.print_exc()
                    self.warning = f"Refresh failed, showing older data: {exc}"
            return {**self.value, "warning": self.warning}


def build_gold():
    points = gold.parse_points(api.gold())
    return {"summary": gold.summary(points), "points": points}


def names_for(events):
    """Display names for every item worn or carried in these events."""
    known = items.names()
    ids = {i[0] for e in events for side in ("killer", "victim")
           for i in (*e[side]["gear"].values(), *e[side].get("inventory", ()))}
    return {i: known[i] for i in sorted(ids) if i in known}


def killboard_payload():
    events = kills.recent(500)
    names = names_for(events)
    icons.want(names)  # every item worn or carried on the page
    return {"events": events, "names": names, "polled_at": kills.state("polled_at"),
            "failing": bool(kills.state("poll_error_at"))}


def meta_payload(query):
    """Meta builds and losses over the newest days of collected kills."""
    size = query.get("size", [""])[0]
    tradeable = set(items.tradeable_ids())
    summary = kills.summary(days=int(number(query, "days", 7, 1, meta.KEEP_DAYS)),
                                 size=size if size in dict(meta.SIZES) else None,
                                 lost_filter=tradeable.__contains__)
    lines = {w["weapon"] for w in summary["weapons"]} | {b for r in summary["builds"] for b in r["build"] if b}
    known = items.names()
    fair = store.fair([r["id"] for r in summary["lost"]])
    for r in summary["lost"]:  # valued at the Normal-quality fair price: losses mix qualities
        price = fair.get(r["id"], {}).get(1, {}).get(ALL, {}).get("price")
        r["value"] = price * r["units"] if price else None
    line_icons = meta.line_names(sorted(lines), known)
    icons.want([icon for icon, _ in line_icons.values()] + [r["id"] for r in summary["lost"]])
    return {**summary,
            "lines": {b: {"icon": icon, "name": name} for b, (icon, name) in line_icons.items()},
            "names": {r["id"]: known.get(r["id"], r["id"]) for r in summary["lost"]}}


def player_payload(player_id):
    profile = intel.player_profile(gameinfo.player(player_id), gameinfo.player_events(player_id, "kills"),
                                   gameinfo.player_events(player_id, "deaths"))
    names = names_for(profile["kills"] + profile["deaths"])
    known = items.names()
    names.update({i: known[i] for i, _ in profile["weapons"] if i in known})
    return {**profile, "names": names}


def guild_payload(guild_id):
    return intel.guild_profile(gameinfo.guild(guild_id), gameinfo.guild_members(guild_id))


class Job:
    """Runs `work(progress)` in a background thread, one at a time; the page polls state()."""

    def __init__(self, work):
        self.work = work
        self.lock = threading.Lock()
        self.result, self.running, self.progress, self.error = None, False, "", None

    def start(self):
        with self.lock:
            if self.running:
                return False
            self.running, self.error, self.progress = True, None, "starting"
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        try:
            result = self.work(self._set_progress)
            with self.lock:
                self.result = result
        except Exception as exc:  # surface to the page instead of dying silently
            traceback.print_exc()
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.running, self.progress = False, ""

    def _set_progress(self, text):
        with self.lock:
            self.progress = text

    def state(self):
        with self.lock:
            return {"running": self.running, "progress": self.progress,
                    "error": self.error, "result": self.result}


def refresh_focus_prices(progress):
    """Fetch live prices for everything the focus planner can price; returns when."""
    sync_prices(store, api, focus.item_ids(), progress=lambda d, n: progress(f"prices {d}/{n}"))
    return time.time()


store = None   # market store, opened by serve() (tests use temporary ones)
kills = None   # kill store, likewise
syncer = None  # set when this process is the collector
play = None    # session store, opened by serve()
watch = None   # the eye, watching while a session runs
snapshots = Snapshots()
scanner = Job(lambda progress: flips.scan(store, api, progress=progress))
focus_job = Job(refresh_focus_prices)
gold_cache = Cached(build_gold, GOLD_TTL)
icons = icons_mod.Icons()


def session_payload(query):
    """The running session (or the last), sessions since the week began, goals, and the eye."""
    tz = number(query, "tz", 0, -14 * 60, 14 * 60)
    now = time.time()
    since = min(sessions.period_start("week", now, tz), now - 14 * 86400)
    runs = play.recent(since=since) or play.recent(limit=1)
    snaps = {r["id"]: play.snapshots(r["id"]) for r in runs}
    ids = {i for ss in snaps.values() for s in ss for i in s["held"]}
    fair = store.fair(list(ids)) if ids else {}
    price = lambda i: fair.get(i, {}).get(1, {}).get(ALL, {}).get("price")
    summaries = [sessions.summarize(r, snaps[r["id"]], price, now) for r in runs]
    names = items.names()
    for s in summaries[:1]:
        for x in s["loot"]:
            x["name"] = names.get(x["id"], x["id"])
    return {
        "active": play.active(), "now": now,
        "current": summaries[0] if summaries else None,
        "history": [{k: v for k, v in s.items() if k != "loot"} for s in summaries],
        "goals": [sessions.goal_progress(g, summaries, now, tz) for g in play.goals()],
        "kinds": sessions.GOAL_KINDS, "periods": sessions.PERIODS,
        "eye": watch.status if watch else {"state": "off"},
    }


def session_action(path, body):
    """POST /api/session/* and /api/goals*: returns (status, payload)."""
    if path == "/api/session/start":
        watch.start(play.start())
    elif path == "/api/session/stop":
        play.stop()
        watch.stop()
    elif path == "/api/session/silver":
        run = play.active()
        silver = body.get("silver")
        if not run or not isinstance(silver, (int, float)) or silver < 0:
            return 400, {"error": "needs a running session and a silver amount"}
        last = play.last(run["id"]) or {"gold": None, "held": {}, "zone": None}
        play.record(run["id"], int(silver), last["gold"], last["held"], last["zone"], source="typed")
    elif path == "/api/goals":
        try:
            play.add_goal(body.get("kind"), body.get("target"), body.get("period"))
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
    elif path == "/api/goals/delete":
        play.remove_goal(body.get("id"))
    else:
        return 404, {"error": "not found"}
    return 200, {"ok": True}


def client_config():
    return {
        "premium": config.PREMIUM, "capital": config.CAPITAL,
        "volume_capture": config.VOLUME_CAPTURE,
        "tax_premium": config.SALES_TAX_PREMIUM, "tax_no_premium": config.SALES_TAX_NO_PREMIUM,
        "setup_fee": config.SETUP_FEE, "max_age_hours": config.SCAN_MAX_AGE_HOURS,
        "host": config.HOST, "server": config.SERVERS[config.SERVER]["label"], "quality_names": config.QUALITY_NAMES, "station_fee": config.STATION_FEE,
        "cities": config.CITIES,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    def _send(self, status, body, content_type="application/json", cache="no-store", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        compressible = not content_type.startswith("image/")  # PNGs are already compressed
        if compressible and len(data) > 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
            data = gzip.compress(data, compresslevel=5)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_icon(self, item_id):
        """From disk if we have it (cached by the browser for a year); otherwise off to the
        render service for now, with a copy fetched in the background for next time."""
        if not icons_mod.ITEM_ID.fullmatch(item_id):
            return self._send(404, {"error": "bad item id"})
        path = icons.path(item_id)
        if path:
            return self._send(200, path.read_bytes(), "image/png", cache="public, max-age=31536000, immutable")
        if icons.missing(item_id):
            return self._send(404, {"error": "no icon"}, cache="public, max-age=86400")
        icons.want([item_id], icons_mod.URGENT)
        self._send(302, b"", "text/plain", headers=[("Location", icons_mod.render_url(item_id))])

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        refresh = query.get("refresh") == ["1"]
        try:
            if url.path in PAGES:
                name, content_type = PAGES[url.path]
                self._send(200, (WEB_DIR / name).read_bytes(), content_type)
            elif url.path.startswith("/icon/") and url.path.endswith(".png"):
                self._send_icon(unquote(url.path[len("/icon/"):-len(".png")]))
            elif url.path == "/api/session":
                self._send(200, session_payload(query))
            elif url.path == "/api/config":
                self._send(200, client_config())
            elif url.path == "/api/resources":
                self._send(200, resources_payload(refresh))
            elif url.path == "/api/laborers":
                self._send(200, laborers_payload(refresh))
            elif url.path == "/api/hearts":
                self._send(200, hearts_payload(refresh))
            elif url.path == "/api/search":
                self._send(200, {"results": items.search(query.get("q", [""])[0])})
            elif url.path == "/api/item":
                item_id = query.get("id", [""])[0]
                payload = item_payload(item_id, refresh)
                if payload is None:
                    self._send(404, {"error": f"unknown item {item_id!r}"})
                else:
                    self._send(200, payload)
            elif url.path == "/api/craft/groups":
                self._send(200, {"groups": crafting.groups(), "bonus": crafting.bonus_settings()})
            elif url.path == "/api/craft":
                section, group = query.get("section", [""])[0], query.get("group", [""])[0]
                payload = craft_payload(section, group, refresh, query)
                if payload is None:
                    self._send(400, {"error": f"unknown recipe group {section!r}/{group!r}"})
                else:
                    self._send(200, payload)
            elif url.path == "/api/flips":
                self._send(200, scanner.state())
            elif url.path == "/api/focus":
                self._send(200, focus_payload(query))
            elif url.path == "/api/farming":
                self._send(200, farming_payload(query, refresh))
            elif url.path == "/api/enchanting":
                self._send(200, enchant_payload(query))
            elif url.path == "/api/market":
                self._send(200, market_payload(query))
            elif url.path == "/api/status":
                self._send(200, collect.status(store, kills, syncer))
            elif url.path == "/api/gold":
                self._send(200, gold_cache.get())
            elif url.path == "/api/killboard":
                self._send(200, killboard_payload())
            elif url.path == "/api/meta":
                self._send(200, meta_payload(query))
            elif url.path == "/api/intel/search":
                q = query.get("q", [""])[0].strip()
                self._send(200, intel.search_results(gameinfo.search(q)) if len(q) >= 3
                           else {"players": [], "guilds": []})
            elif url.path in ("/api/player", "/api/guild"):
                target = query.get("id", [""])[0]
                if not target:
                    self._send(400, {"error": "missing id"})
                else:
                    build = player_payload if url.path == "/api/player" else guild_payload
                    self._send(200, build(target))
            else:
                self._send(404, {"error": "not found"})
        except APIError as exc:
            if exc.status == 404:
                self._send(404, {"error": "not found in the game's records"})
            else:
                traceback.print_exc()
                self._send(502, {"error": str(exc)})
        except ConnectionError:
            pass  # browser went away mid-response
        except Exception as exc:
            traceback.print_exc()
            self._send(502, {"error": str(exc)})

    def do_POST(self):
        # drain any body so it can't be misread as the next request on this keep-alive connection
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= 1_000_000:
            self.close_connection = True
            return self._send(400, {"error": "bad Content-Length"})
        raw = self.rfile.read(length)
        path = urlparse(self.path).path
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._send(400, {"error": "body must be JSON"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "body must be a JSON object"})
        if path.startswith(("/api/session/", "/api/goals")):
            self._send(*session_action(path, body))
        elif path == "/api/flips/scan":
            self._send(202, {"started": scanner.start()})
        elif path == "/api/focus/scan":
            self._send(202, {"started": focus_job.start()})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet


def serve(host="127.0.0.1", port=8765, on_ready=None):
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise SystemExit(f"Can't listen on {host}:{port} ({exc.strerror}). Is it already running? Try --port.")
    global store, kills, play, watch
    store, kills = Store(), KillStore()
    play = sessions.Sessions()
    watch = sessions.EyeWatch(play)
    if run := play.active():
        watch.start(run["id"])  # a session left running when the server last stopped
    print(f"Project Silver dashboard: http://localhost:{port}  "
          f"({config.SERVERS[config.SERVER]['label']}: {config.HOST})", flush=True)
    stop = threading.Event()

    def begin(lock):
        global syncer
        held.append(lock)  # keep the lock file open for as long as this process lives
        syncer = collect.Syncer(store, api)
        collect.start(syncer, Collector(kills, gameinfo), stop)
        print("Collecting market data and kills in this process.", flush=True)

    held = []
    threading.Thread(target=collect.standby, args=(stop, begin), daemon=True).start()
    print("Ctrl+C to stop. If another process is collecting, this one only serves, and takes over if it stops.",
          flush=True)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    if on_ready:
        threading.Thread(target=on_ready, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
