"""Island farming and animal breeding, from the game data (ao-bin-dumps items.json + loot.json).

Plants (crops and herbs), one 22-hour cycle per seed:
    harvest   = average of the loot list (crops: 3-6, so 4.5), doubled with premium
    seeds     = seed chance (+ focus bonus if watered for 1,000 focus); above 1 means a
                chance of a second seed. T2 33% +133%, T5 80% +40%, T8 93% +13%.

Animals, one baby raised to grown:
    offspring = offspring chance + focus bonus per feeding x feedings with focus
                (T6 ox: 81% + 4 x 5.93%). Exotic mounts have 0% base: focus is the only way.
    feed      = grow time / seconds per nutrition, bought as the cheapest accepted food
                (every crop is 48 nutrition, meat 52).

Every function takes prices from the caller, so the economics are pure and testable:
`sell(item)` is what an output nets you, `buy(item)` what an input costs. Seeds and babies
you get back are valued at `buy`, since they replace ones you'd otherwise pay for.
"""
import json
import re
import threading

from .crafting import RECIPES_FILE, RECIPES_URL, _as_list, _load_items, _num, _valid_items
from .items import DATA_DIR, cached_download, names

LOOT_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/loot.json"
LOOT_FILE = DATA_DIR / "loot.json"
PREMIUM_HARVEST = 2  # premium doubles plant harvests (3-6 -> 6-12)

_lock = threading.Lock()
_cache = None


def _valid_loot(content):
    try:
        return len(content) > 100_000 and "_LOOT" in content.decode("utf-8", "replace")
    except AttributeError:
        return False


def _loot_lists(content):
    """{loot list name: [(item id, chance, average amount)]} for the lists farming uses."""
    out = {}

    def walk(node):
        if isinstance(node, dict):
            name = node.get("@name")
            if isinstance(name, str) and name.endswith("_LOOT") and "Item" in node:
                entries = []
                for e in _as_list(node["Item"]):
                    if not isinstance(e, dict) or not e.get("@type"):
                        continue
                    lo, _, hi = str(e.get("@amount", "1")).partition("-")
                    entries.append((e["@type"], _num(e.get("@chance"), 1), (_num(lo, 1) + _num(hi or lo, 1)) / 2))
                out[name] = entries
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(content))
    return out


def build(all_items, loot, display):
    """(plants, animals, foods) from raw game data. Malformed entries are skipped."""
    plants, animals, foods = [], [], {}
    for _, it in all_items:
        uid = it.get("@uniquename", "")
        if it.get("@nutrition") and it.get("@foodcategory"):
            foods.setdefault(it["@foodcategory"], []).append([uid, _num(it["@nutrition"])])
        if not re.fullmatch(r"T\d_FARM_\w+", uid):
            continue
        focus_cost = _num(it.get("@activefarmfocuscost"))
        bonus = _num(it.get("@activefarmbonus"))
        npc = _num((it.get("craftingrequirements") or {}).get("@silver")) or None
        tier = int(_num(it.get("@tier")))
        harvest = it.get("harvest")
        if it.get("@kind") == "plant" and isinstance(harvest, dict):
            entries = loot.get(harvest.get("@lootlist"), [])
            if not entries:
                continue
            crop = entries[0]  # the main crop; the rest (worms) are extras
            plants.append({
                "id": uid, "name": display.get(uid, uid), "tier": tier,
                "kind": "Herb" if it.get("@shopsubcategory1") == "herbgarden" else "Crop",
                "crop": crop[0], "crop_name": display.get(crop[0], crop[0]),
                "harvest": crop[1] * crop[2],
                "extras": [[i, c * a] for i, c, a in entries[1:]],
                "seed_chance": _num((harvest.get("seed") or {}).get("@chance")),
                "focus_bonus": bonus, "focus_cost": focus_cost, "npc_price": npc,
                "hours": _num(harvest.get("@growtime")) / 3600,
            })
        grown = it.get("grownitem")
        if it.get("@kind") == "animal" and isinstance(grown, dict) and grown.get("@uniquename"):
            food = ((it.get("consumption") or {}).get("food")) or {}
            per = _num(food.get("@secondspernutrition"))
            seconds = _num(grown.get("@growtime"))
            animals.append({
                "id": uid, "name": display.get(uid, uid), "tier": tier,
                "grown": grown["@uniquename"], "grown_name": display.get(grown["@uniquename"], grown["@uniquename"]),
                "offspring_chance": _num((grown.get("offspring") or {}).get("@chance")),
                "focus_bonus": bonus, "focus_cost": focus_cost,
                "feedings": int(_num(it.get("@activefarmmaxcycles"), 1)),
                "npc_price": npc, "days": seconds / 86400,
                "food_category": (food.get("acceptedfood") or {}).get("@foodcategory"),
                "nutrition": seconds / per if per else 0,
            })
    plants.sort(key=lambda p: (p["kind"], p["tier"]))
    animals.sort(key=lambda a: (a["tier"], a["name"]))
    for lst in foods.values():
        lst.sort()
    return plants, animals, foods


def catalog():
    global _cache
    with _lock:
        if _cache is None:
            all_items = _load_items(cached_download(RECIPES_URL, RECIPES_FILE, _valid_items).read_bytes())
            loot = _loot_lists(cached_download(LOOT_URL, LOOT_FILE, _valid_loot).read_bytes())
            plants, animals, foods = build(all_items, loot, names())
            _cache = {"plants": plants, "animals": animals, "foods": foods}
        return _cache


def item_ids():
    cat = catalog()
    ids = set()
    for p in cat["plants"]:
        ids.update([p["id"], p["crop"]] + [i for i, _ in p["extras"]])
    for a in cat["animals"]:
        ids.update([a["id"], a["grown"]])
    for category in ("plants", "meat"):
        ids.update(i for i, _ in cat["foods"].get(category, []))
    return sorted(ids)


# ---------- economics: prices come in as plain dicts, so all of this is pure ----------

def unit_value(price, npc_price):
    """What one seed/baby is worth to you: its market price, but never more than the NPC sells it for."""
    candidates = [p for p in (price, npc_price) if p]
    return min(candidates) if candidates else None


def plant_result(plant, sell, buy, premium, water):
    """Per seed planted, one cycle. Returns None if the crop or seed can't be valued."""
    crop, seed = sell(plant["crop"]), unit_value(buy(plant["id"]), plant["npc_price"])
    if not crop or not seed:
        return None
    harvest = plant["harvest"] * (PREMIUM_HARVEST if premium else 1)
    extras = sum(n * (sell(i) or 0) for i, n in plant["extras"])
    seeds_back = plant["seed_chance"] + (plant["focus_bonus"] if water else 0)
    revenue = harvest * crop + extras + seeds_back * seed
    focus = plant["focus_cost"] if water else 0
    return {"harvest": harvest, "seeds_back": seeds_back, "revenue": revenue, "cost": seed,
            "profit": revenue - seed, "focus": focus, "hours": plant["hours"],
            "focus_value": plant["focus_bonus"] * seed / plant["focus_cost"] if plant["focus_cost"] else 0}


def cheapest_feed(foods, category, price):
    """Cheapest silver per nutrition among foods of a category: (item id, silver per nutrition)."""
    best = None
    for item_id, nutrition in foods.get(category, []):
        p = price(item_id)
        if p and nutrition and (best is None or p / nutrition < best[1]):
            best = (item_id, p / nutrition)
    return best


def animal_result(animal, sell, buy, foods, feed_with_focus):
    """Per baby raised to grown. `feed_with_focus` = how many feedings use focus (0..feedings)."""
    grown, baby = sell(animal["grown"]), unit_value(buy(animal["id"]), animal["npc_price"])
    feed = cheapest_feed(foods, animal["food_category"], buy)
    if not grown or not baby or not feed:
        return None
    focused = max(0, min(int(feed_with_focus), animal["feedings"]))
    offspring = animal["offspring_chance"] + animal["focus_bonus"] * focused
    feed_cost = animal["nutrition"] * feed[1]
    revenue = grown + offspring * baby
    cost = baby + feed_cost
    return {"offspring": offspring, "revenue": revenue, "cost": cost, "feed": feed[0], "feed_cost": feed_cost,
            "profit": revenue - cost, "focus": focused * animal["focus_cost"], "days": animal["days"],
            "focus_value": animal["focus_bonus"] * baby / animal["focus_cost"] if animal["focus_cost"] else 0}
