"""Faction transports (royal caravans), priced per path from the game data.

How a transport works (quests_<server>.json, questgivertypes.json, cluster/world.json):
- Your faction's store in its home city (FACTIONSTORE_<faction>) offers missions. Accepting one
  costs `cost` of your own faction's hearts, and gives you a trade pack.
- You carry it to a rival faction's smuggling agent (TRADING_OUTPOST_SMUGGLER_<faction>), swap
  it for their pack, and carry that back. The reward is `reward` of the rival's hearts.
- Each mission only uses agents whose zone distance from the store is within its band, and the
  reward grows with the band: e.g. a heavy pack costs 15 hearts and pays 18 (4-8 zones)
  up to 22 (10-12 zones); to Caerleon it pays 21 Shadowhearts.

Everything is deterministic, so for each (store, agent) path we can list which missions reach
it and exactly how many hearts go in and come out. Silver values are left to the caller.
"""
import json
import re
import threading
from collections import deque

from . import config
from .crafting import RECIPES_FILE, RECIPES_URL, _load_items, _valid_items
from .items import DATA_DIR, cached_download

BASE = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/"


def _files():
    """Dump files to fetch: (remote path, local path). The quest file is per server region."""
    quests = config.SERVERS[config.SERVER]["quests"]
    return {
        "world": ("cluster/world.json", DATA_DIR / "world.json"),
        "quests": (quests, DATA_DIR / quests),
        "givers": ("questgivertypes.json", DATA_DIR / "questgivertypes.json"),
    }

SIZES = ("LIGHT", "MEDIUM", "HEAVY")

_lock = threading.Lock()
_cache = None


def _as_list(x):
    return x if isinstance(x, list) else [x] if x else []


def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def heart_id(faction):
    """"Swamp" -> "T1_FACTION_SWAMP_TOKEN_1"."""
    return f"T1_FACTION_{faction.upper()}_TOKEN_1"


def parse_world(world):
    """Zones, the exit graph, faction stores and smuggling agents from cluster/world.json."""
    zones, graph, stores, agents = {}, {}, {}, []
    for c in _as_list(world["world"]["clusters"]["cluster"]):
        zid = c.get("@id")
        if not zid or zid.startswith(("Debug", "DEBUG")):
            continue
        zones[zid] = {"name": c.get("@displayname", zid), "type": c.get("@type", "")}
        graph[zid] = sorted({e["@targetid"].rsplit("@", 1)[1]
                             for e in _as_list((c.get("exits") or {}).get("exit"))
                             if isinstance(e, dict) and "@" in str(e.get("@targetid", ""))})
        for q in _as_list((c.get("questgivers") or {}).get("questgiver")):
            kind = str(q.get("@type", ""))
            if m := re.fullmatch(r"FACTIONSTORE_([A-Z]+)", kind):
                stores[m.group(1).capitalize()] = zid
            elif m := re.fullmatch(r"TRADING_OUTPOST_SMUGGLER_([A-Z]+)", kind):
                agents.append({"faction": m.group(1).capitalize(), "zone": zid})
    # exits are one-way records; travel works both ways
    for zid, targets in list(graph.items()):
        for t in targets:
            if t in graph and zid not in graph[t]:
                graph[t] = sorted(graph[t] + [zid])
    return zones, graph, stores, sorted(agents, key=lambda a: (a["faction"], a["zone"]))


def parse_missions(quests):
    """Non-debug trade pack missions: {name: {size, targets, min, max, cost, reward}}."""
    out = {}
    for q in _as_list(quests["quests"].get("playerfactionquest")):
        name = q.get("@uniquename", "")
        size = next((s for s in SIZES if name.startswith(f"MISSIONS_{s}_TRADE_PACK")), None)
        if not size or "DEBUG" in name:
            continue
        v = q.get("variables") or {}
        faction_var = _as_list(v.get("dynamicplayerfaction"))[0] if v.get("dynamicplayerfaction") else {}
        giver = _as_list(v.get("dynamicquestgiver"))[0] if v.get("dynamicquestgiver") else {}
        dist = _as_list(giver.get("distancefromquestgiver"))[0] if giver.get("distancefromquestgiver") else {}
        cost = _as_list((q.get("costs") or {}).get("item"))
        reward = _as_list((q.get("rewards") or {}).get("item"))
        if not cost or not reward or not dist:
            continue
        out[name] = {
            "size": size.capitalize(),
            "targets": sorted(f.get("@faction") for f in _as_list(faction_var.get("filter")) if f.get("@faction")),
            "min": int(_num(dist.get("@mindistance"))), "max": int(_num(dist.get("@maxdistance"))),
            "cost": int(_num(cost[0].get("@amount"))), "reward": int(_num(reward[0].get("@count"))),
            "hours": _num(q.get("@questtimeout")) / 3600,
        }
    return out


def parse_offers(givers):
    """{faction: set of mission names its store offers}."""
    offers = {}
    for g in _as_list(givers["questgivertypes"].get("factionquestgivertype")):
        m = re.fullmatch(r"FACTIONSTORE_([A-Z]+)", g.get("@uniquename", ""))
        if not m:
            continue
        names = {q.get("@uniquename") for grp in _as_list(g.get("questgroup"))
                 for q in _as_list(grp.get("quest")) if isinstance(q, dict)}
        offers[m.group(1).capitalize()] = names
    return offers


def shortest_path(graph, start, goal):
    """Zones from start to goal by fewest exits (BFS; neighbours in sorted order, so the
    same path every time). None if unreachable."""
    prev, queue = {start: None}, deque([start])
    while queue:
        zone = queue.popleft()
        if zone == goal:
            path = []
            while zone is not None:
                path.append(zone)
                zone = prev[zone]
            return path[::-1]
        for nxt in graph.get(zone, []):
            if nxt not in prev:
                prev[nxt] = zone
                queue.append(nxt)
    return None


def zone_risk(zone_type):
    """Coarse PvP danger of a zone, from its type."""
    t = zone_type.upper()
    if "BLACK" in t:
        return "black"
    if "RED" in t:
        return "red"
    if "YELLOW" in t:
        return "yellow"
    return "safe"


def routes(zones, graph, stores, agents, missions, offers):
    """One row per (store, rival agent) path, with every mission that reaches it."""
    out = []
    for home, store_zone in sorted(stores.items()):
        for agent in agents:
            if agent["faction"] == home:
                continue
            path = shortest_path(graph, store_zone, agent["zone"])
            if not path:
                continue
            hops = len(path) - 1
            options = [
                {"mission": name, **{k: m[k] for k in ("size", "cost", "reward", "min", "max", "hours")}}
                for name, m in sorted(missions.items())
                if name in offers.get(home, ()) and agent["faction"] in m["targets"] and m["min"] <= hops <= m["max"]
            ]
            if not options:
                continue
            risks = [zone_risk(zones[z]["type"]) for z in path[1:]]
            out.append({
                "from": home, "from_zone": zones[store_zone]["name"], "pay": heart_id(home),
                "to": agent["faction"], "get": heart_id(agent["faction"]),
                "agent_zone": zones[agent["zone"]]["name"], "agent_risk": zone_risk(zones[agent["zone"]]["type"]),
                "hops": hops, "path": [zones[z]["name"] for z in path],
                "risk": {r: risks.count(r) for r in ("safe", "yellow", "red", "black") if r in risks},
                "missions": options,
            })
    return out


def parse_packs(all_items):
    """{size: {weight, drop}} from the trade pack items: how heavy each pack is, and how many
    hearts it breaks into if you're killed carrying it."""
    packs = {}
    for _, it in all_items:
        m = re.fullmatch(r"QUESTITEM_CARAVAN_TRADEPACK_[A-Z]+_(LIGHT|MEDIUM|HEAVY)", it.get("@uniquename", ""))
        if not m:
            continue
        drop = (it.get("replaceondeath") or {}).get("replacementitem") or {}
        packs.setdefault(m.group(1).capitalize(), {"weight": _num(it.get("@weight")), "drop": int(_num(drop.get("@count")))})
    return packs


def catalog():
    """Cached transport routes built from the game data."""
    global _cache
    with _lock:
        if _cache is None:
            raw = {key: json.loads(cached_download(BASE + remote, local, lambda c: len(c) > 1000).read_bytes())
                   for key, (remote, local) in _files().items()}
            zones, graph, stores, agents = parse_world(raw["world"])
            missions = parse_missions(raw["quests"])
            offers = parse_offers(raw["givers"])
            packs = parse_packs(_load_items(cached_download(RECIPES_URL, RECIPES_FILE, _valid_items).read_bytes()))
            _cache = {"routes": routes(zones, graph, stores, agents, missions, offers), "packs": packs,
                      "missions": missions, "stores": {f: zones[z]["name"] for f, z in stores.items()}}
        return _cache
