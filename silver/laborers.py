"""Island laborers: what each journal returns, and how happiness scales it.

Everything comes from the game data (ao-bin-dumps):
- items.json journal items: fame to fill (`maxfame`), base return (`baselootamount`), the NPC price
  of an empty journal, and a weighted loot table (e.g. T5 ore 94.5%, .1 5%, .2 0.5%, .3 0.1%).
  Mercenaries return silver instead of items.
- gamedata.json LabourerSettings: happiness comes in bars of 100 points, up to 9 bars, and yield
  tops out at 1.5x. So 100% yield = 900 / 1.5 = 600 points, and
      yield = min(1.5, happiness points / 600)
- items.json furniture: beds and tables give happiness (T2 100 ... T8 400) to the laborers they
  serve; trophies add a little more.

A laborer consumes one full journal per job and returns base x yield items, drawn from the loot table.
"""
import json
import re
import threading

from .crafting import _as_list, _load_items, _market_id, _num, RECIPES_FILE, RECIPES_URL, _valid_items
from .items import DATA_DIR, cached_download, names

GAMEDATA_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/gamedata.json"
GAMEDATA_FILE = DATA_DIR / "gamedata.json"

_lock = threading.Lock()
_cache = None


def _valid_gamedata(content):
    try:
        return "LabourerSettings" in json.loads(content)["AO-GameData"]
    except (ValueError, KeyError, TypeError):
        return False


def settings():
    """{max_yield, points_per_bar, max_bars, full_yield_points} from gamedata.json."""
    path = cached_download(GAMEDATA_URL, GAMEDATA_FILE, _valid_gamedata)
    raw = json.loads(path.read_bytes())["AO-GameData"]["LabourerSettings"]
    max_yield = _num(raw.get("@maxyield"), 1.5)
    per_bar, bars = _num(raw.get("@happinessperbarsegment"), 100), _num(raw.get("@maxbarsegments"), 9)
    return {"max_yield": max_yield, "points_per_bar": per_bar, "max_bars": bars,
            "full_yield_points": per_bar * bars / max_yield}


def yield_multiplier(points, cfg):
    """Happiness points -> share of the base return a laborer brings back."""
    return max(0.0, min(cfg["max_yield"], points / cfg["full_yield_points"]))


def laborer_name(journal_name):
    """"Expert Prospector's Journal (Partially Full)" -> "Prospector";
    "Adept's Generalist Trophy Journal" -> "Generalist"."""
    m = re.match(r"^\S+?(?:'s)?\s+(.+?)(?:'s)?\s+(?:Trophy\s+)?Journal\b", journal_name or "")
    return m.group(1) if m else journal_name


def build_jobs(all_items, display):
    """One entry per journal (type x tier). Malformed journals are skipped."""
    jobs = []
    for kind, item in all_items:
        uid = item.get("@uniquename", "")
        m = re.fullmatch(r"T([2-8])_JOURNAL_(\w+)", uid)
        if kind != "journalitem" or not m:
            continue
        loot, silver = [], 0.0
        entries = [e for e in _as_list((item.get("lootlist") or {}).get("loot")) if isinstance(e, dict)]
        total = sum(_num(e.get("@weight")) for e in entries)
        if not entries or total <= 0 or _num(item.get("@baselootamount")) <= 0:
            continue
        for e in entries:
            share = _num(e.get("@weight")) / total
            if e.get("@silveramount"):
                silver += share * _num(e["@silveramount"])
            elif e.get("@itemname"):
                loot.append([_market_id(e["@itemname"], e.get("@itemenchantmentlevel")),
                             _num(e.get("@itemamount"), 1), round(share, 6)])
        name = display.get(uid, uid)
        jobs.append({
            "id": uid, "kind": m.group(2), "tier": int(m.group(1)),
            "laborer": laborer_name(name), "trophy": m.group(2).startswith("TROPHY"),
            "full": uid + "_FULL", "empty": uid + "_EMPTY",
            "fame": int(_num(item.get("@maxfame"))), "base": _num(item.get("@baselootamount")),
            "empty_silver": int(_num((item.get("craftingrequirements") or {}).get("@silver"))),
            "silver": round(silver, 2), "loot": loot,
        })
    return sorted(jobs, key=lambda j: (j["trophy"], j["laborer"], j["tier"]))


def build_furniture(all_items):
    """Happiness sources: beds and tables by tier (and how many laborers each serves), trophies."""
    beds, tables, trophies = {}, {}, {}
    for _, it in all_items:
        happy = _num(it.get("@labourerhappiness"))
        tier = int(_num(it.get("@tier")))
        if happy <= 0 or not tier:
            continue
        kind = it.get("@labourerfurnituretype")
        served = int(_num(it.get("@labourersperfurnitureitem"), 1))
        if kind == "bed":
            beds[tier] = {"happiness": happy, "serves": served}
        elif kind == "table":
            tables[tier] = {"happiness": happy, "serves": served}
        elif kind == "trophy":
            affects = it.get("@labourersaffected", "all")
            trophies[affects] = max(trophies.get(affects, 0), happy)
    return {"beds": dict(sorted(beds.items())), "tables": dict(sorted(tables.items())), "trophy_max": trophies}


def catalog():
    """Cached {jobs, furniture, settings} built from the game data."""
    global _cache
    with _lock:
        if _cache is None:
            path = cached_download(RECIPES_URL, RECIPES_FILE, _valid_items)
            all_items = _load_items(path.read_bytes())
            _cache = {"jobs": build_jobs(all_items, names()), "furniture": build_furniture(all_items),
                      "settings": settings()}
        return _cache


def item_ids():
    """Full and empty journals plus everything a laborer can return, for one price fetch."""
    ids = set()
    for job in catalog()["jobs"]:
        ids.update({job["full"], job["empty"]})
        ids.update(i for i, _, _ in job["loot"])
    return sorted(ids)
