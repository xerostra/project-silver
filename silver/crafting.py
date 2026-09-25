"""Refining and crafting recipes (from ao-bin-dumps items.json) and city production bonuses.

Return rate = B / (1 + B), where B is the sum of production bonuses:
  18% base in every city, +40% city refining specialty, +15% city crafting
  specialty, +59% with focus, plus any daily bonus event.
  e.g. refining in its bonus city: 58% -> 36.7%; with focus: 117% -> 53.9%.
"""
import json
import threading

from .items import DATA_DIR, cached_download, names

RECIPES_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
RECIPES_FILE = DATA_DIR / "items.json"

BASE_BONUS = 0.18
REFINE_SPECIALTY = 0.40
CRAFT_SPECIALTY = 0.15
FOCUS_BONUS = 0.59
NUTRITION_PER_VALUE = 0.1125  # station fee = item value x 0.1125 x (fee per 100 nutrition) / 100

REFINED = {"ore": "Metal Bars", "wood": "Planks", "fiber": "Cloth", "hide": "Leather", "rock": "Stone Blocks"}

# bonus key -> city with the specialty
SPECIALTY_CITY = {
    **{f"refine:{k}": c for k, c in [("ore", "Thetford"), ("wood", "Fort Sterling"), ("fiber", "Lymhurst"),
                                       ("hide", "Martlock"), ("rock", "Bridgewatch")]},
    **{k: "Fort Sterling" for k in ("cloth_armor", "plate_helmet", "hammer", "spear", "holystaff")},
    **{k: "Lymhurst" for k in ("bow", "sword", "arcanestaff", "leather_helmet", "leather_shoes")},
    **{k: "Bridgewatch" for k in ("plate_armor", "crossbow", "dagger", "cursestaff", "cloth_shoes")},
    **{k: "Martlock" for k in ("axe", "quarterstaff", "froststaff", "plate_shoes",
                               "shieldtype", "booktype", "torchtype")},
    **{k: "Thetford" for k in ("mace", "firestaff", "naturestaff", "leather_armor", "cloth_helmet")},
    **{k: "Caerleon" for k in ("food", "gathering_gear", "tools", "knuckles", "shapeshifterstaff")},
    **{k: "Brecilien" for k in ("capes", "bags", "potions")},
}

PRETTY = {"arcanestaff": "Arcane Staff", "cursestaff": "Cursed Staff", "firestaff": "Fire Staff",
          "froststaff": "Frost Staff", "holystaff": "Holy Staff", "naturestaff": "Nature Staff",
          "quarterstaff": "Quarterstaff", "shapeshifterstaff": "Shapeshifter Staff", "knuckles": "War Gloves"}

# Groups where each alternative recipe is its own row, because choosing the input is the question
# (T1_FISHCHOPS has one recipe per fish). Elsewhere the first recipe without faction hearts is used.
ALTERNATIVES = {"Fish"}

SECTIONS = {"weapons": "Weapons", "armors": "Armor", "head": "Head", "shoes": "Shoes",
            "offhands": "Off-hands", "capes": "Accessories", "bags": "Accessories",
            "consumables": "Consumables", "gathering": "Gathering"}

_lock = threading.Lock()
_recipes = None


def _as_list(x):
    return x if isinstance(x, list) else [x] if x else []


def _market_id(uniquename, level):
    level = int(_num(level))
    return f"{uniquename}@{level}" if level else uniquename


def _classify(kind, item):
    """(section, group, bonus key) for an item we want, or None to skip it."""
    cat, sub = item.get("@shopcategory"), item.get("@shopsubcategory1")
    if cat == "crafting" and sub == "refinedresources" and item.get("@craftingcategory") in REFINED:
        c = item["@craftingcategory"]
        return "Refining", REFINED[c], f"refine:{c}"
    if cat == "crafting" and sub == "fish":
        return "Consumables", "Fish", "butcher"
    if cat == "consumables" and sub in ("food", "potions"):
        return "Consumables", sub.capitalize(), sub
    if cat == "gathering" and sub in ("ore", "wood", "fiber", "hide", "rock", "fish"):
        return ("Gathering", "Tools", "tools") if kind == "weapon" else ("Gathering", "Gathering gear", "gathering_gear")
    if cat in ("capes", "bags"):
        return "Accessories", cat.capitalize(), cat
    if cat in SECTIONS and cat != "consumables" and sub and sub != "other":
        return SECTIONS[cat], PRETTY.get(sub) or sub.replace("_", " ").replace("type", "").strip().title(), sub
    return None


def _resources(req):
    return [r for r in _as_list(req.get("craftresource")) if isinstance(r, dict)] if isinstance(req, dict) else []


def _uses_token(req):
    return any("FACTION" in str(r.get("@uniquename", "")) for r in _resources(req))


def _pick(requirements):
    """First recipe that doesn't use faction tokens."""
    return next((req for req in _as_list(requirements) if isinstance(req, dict) and not _uses_token(req)), None)


def token_option(requirements, plain):
    """The faction-heart variant of a refining recipe: one heart replaces raw material.
    Returns {"id": heart, "count": n, "saves_id": raw, "saves_count": n} or None."""
    req = next((r for r in _as_list(requirements) if isinstance(r, dict) and _uses_token(r)), None)
    if not req or not plain:
        return None
    mid = lambda r: _market_id(r["@uniquename"], _num(r.get("@enchantmentlevel")))
    before = {mid(r): int(_num(r["@count"])) for r in _resources(plain) if r.get("@count")}
    heart = next((r for r in _resources(req) if "FACTION" in str(r.get("@uniquename", ""))), None)
    saved = [(i, before[i] - int(_num(r["@count"]))) for r in _resources(req)
             for i in [mid(r)] if i in before and before[i] > int(_num(r["@count"]))]
    if not heart or len(saved) != 1:
        return None
    return {"id": heart["@uniquename"], "count": int(_num(heart["@count"])),
            "saves_id": saved[0][0], "saves_count": saved[0][1]}


def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_items(content):
    root = json.loads(content)["items"]
    return [(kind, it) for kind, v in root.items() if isinstance(v, list) for it in v if isinstance(it, dict)]


def _valid_items(content):
    try:
        return len(_load_items(content)) > 1000
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _upgrade_variants(item):
    """[(resulting enchant level, upgrade requirement)] for enchanting .0 -> .1 -> .2 ...
    Enchanting happens in your inventory: no station, so no return rate, fee or focus."""
    out = []
    enchantments = item.get("enchantments")
    if isinstance(enchantments, dict):
        for e in _as_list(enchantments.get("enchantment")):
            if isinstance(e, dict) and isinstance(e.get("upgraderequirements"), dict):
                level = int(_num(e.get("@enchantmentlevel")))
                if level >= 1:
                    out.append((level, e["upgraderequirements"]))
    return out


def _craft_recipe(item, level, req, reqs, where, values, display, alternative):
    """One station recipe, or None if malformed. An alternative is keyed and named by its
    first input ("Chopped Fish ← Blackwater Eel"), and takes that input's tier and icon."""
    try:
        inputs = [[_market_id(r["@uniquename"], _num(r.get("@enchantmentlevel"))), int(_num(r["@count"])),
                   r.get("@maxreturnamount") != "0"] for r in _as_list(req and req.get("craftresource"))]
    except (KeyError, TypeError, AttributeError):
        return None
    if not inputs or any(n <= 0 for _, n, _ in inputs):
        return None
    pid = _market_id(item["@uniquename"], _num(level))
    key, name, tier, icon = "craft:" + pid, display.get(pid, pid), int(_num(item.get("@tier"))), None
    if alternative:
        source = inputs[0][0]
        key, name, icon = f"{key}<{source}", f"{name} ← {display.get(source, source)}", source
        tier = int(source[1]) if source[:1] == "T" and source[1:2].isdigit() else tier
    return {
        "key": key, "id": pid, "name": name, "tier": tier, "icon": icon,
        "enchant": int(_num(level)), "section": where[0], "group": where[1], "bonus": where[2],
        "bonus_city": SPECIALTY_CITY.get(where[2]), "upgrade": False,
        "amount": max(1, int(_num(req.get("@amountcrafted"), 1))),
        "silver": int(_num(req.get("@silver"))),
        "focus": int(_num(req.get("@craftingfocus"))),
        "value": values.get(pid) or sum(values.get(i, 0) * n for i, n, _ in inputs),
        "inputs": inputs,
        "token": token_option(reqs, req),
    }


def build_recipes(all_items, display):
    """[(kind, raw item)] -> {"craft:<id>"|"enchant:<id>": recipe}. Recipe inputs are
    [market id, count, returned?]. Malformed entries are skipped, not fatal."""
    values = {}
    for _, it in all_items:
        if it.get("@uniquename") and _num(it.get("@itemvalue")) > 0:
            values[_market_id(it["@uniquename"], _num(it.get("@enchantmentlevel")))] = _num(it["@itemvalue"])
    out = {}
    for kind, item in all_items:
        where = _classify(kind, item)
        if not where or not item.get("@uniquename"):
            continue
        variants = [(item.get("@enchantmentlevel"), item.get("craftingrequirements"))]
        enchantments = item.get("enchantments")
        if isinstance(enchantments, dict):
            variants += [(e.get("@enchantmentlevel"), e.get("craftingrequirements"))
                         for e in _as_list(enchantments.get("enchantment")) if isinstance(e, dict)]
        alternatives = where[1] in ALTERNATIVES
        for level, reqs in variants:
            chosen = ([r for r in _as_list(reqs) if isinstance(r, dict) and not _uses_token(r)] if alternatives
                      else [_pick(reqs)])
            for req in chosen:
                recipe = _craft_recipe(item, level, req, reqs, where, values, display, len(chosen) > 1)
                if recipe:
                    out[recipe.pop("key")] = recipe
        for level, req in _upgrade_variants(item):
            resources = [r for r in _as_list(req.get("upgraderesource"))
                         if isinstance(r, dict) and r.get("@uniquename") and _num(r.get("@count")) > 0]
            if not resources:
                continue
            pid = _market_id(item["@uniquename"], level)
            # one of the item at the level below, plus runes/souls/relics (or extracts/fish sauce)
            inputs = [[_market_id(item["@uniquename"], level - 1), 1, False]]
            inputs += [[_market_id(r["@uniquename"], _num(r.get("@enchantmentlevel"))), int(_num(r["@count"])), False]
                       for r in resources]
            out["enchant:" + pid] = {
                "id": pid, "name": display.get(pid, pid), "tier": int(_num(item.get("@tier"))),
                "enchant": level, "section": "Enchanting", "group": where[1], "bonus": "enchant",
                "bonus_city": None, "upgrade": True, "amount": 1, "silver": 0, "focus": 0,
                "value": 0, "inputs": inputs, "token": None,
            }
    return dict(sorted(out.items()))


def recipes():
    global _recipes
    with _lock:
        if _recipes is None:
            path = cached_download(RECIPES_URL, RECIPES_FILE, _valid_items)
            _recipes = build_recipes(_load_items(path.read_bytes()), names())
        return _recipes


_categories = None


def categories():
    """{item id without enchantment: shop category}, e.g. 'T4_MAIN_SWORD': 'weapons'."""
    global _categories
    with _lock:
        if _categories is None:
            path = cached_download(RECIPES_URL, RECIPES_FILE, _valid_items)
            _categories = {it["@uniquename"]: it["@shopcategory"] for _, it in _load_items(path.read_bytes())
                           if it.get("@uniquename") and it.get("@shopcategory")}
        return _categories


def groups():
    """[{section, group, count}] in a stable display order."""
    order = ["Refining", "Enchanting", "Weapons", "Armor", "Head", "Shoes", "Off-hands",
             "Accessories", "Consumables", "Gathering"]
    counts = {}
    for r in recipes().values():
        counts[(r["section"], r["group"])] = counts.get((r["section"], r["group"]), 0) + 1
    rank = lambda section: order.index(section) if section in order else len(order)
    return [{"section": s, "group": g, "count": n}
            for (s, g), n in sorted(counts.items(), key=lambda x: (rank(x[0][0]), x[0]))]


def bonus_settings():
    return {"base": BASE_BONUS, "refine": REFINE_SPECIALTY, "craft": CRAFT_SPECIALTY,
            "focus": FOCUS_BONUS, "nutrition_per_value": NUTRITION_PER_VALUE}
