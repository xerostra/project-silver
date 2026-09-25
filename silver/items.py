"""Item IDs and display names (from the ao-bin-dumps project) plus the resource catalog."""
import re
import threading
import time
from pathlib import Path

import requests

ITEMS_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.txt"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ITEMS_FILE = DATA_DIR / "items.txt"
REFRESH_DAYS = 7

# IDs that look like tiered items but can't be traded on the market. "TOKEN" is deliberately
# not here: faction hearts, dungeon maps and crystallized artifacts all trade.
UNTRADEABLE = re.compile(r"QUESTITEM|NONTRADABLE|TUTORIAL|_TRASH|UNIQUE|_EMPTY$")

_lock = threading.Lock()
_names = None


_download_lock = threading.Lock()


def cached_download(url, path, validate):
    """Return path, re-downloading when missing or older than REFRESH_DAYS.
    A download only replaces the cached copy if validate(bytes) accepts it; if the
    download fails or is invalid, the old copy is kept (or an error raised if none)."""
    with _download_lock:
        if path.exists() and time.time() - path.stat().st_mtime < REFRESH_DAYS * 86400:
            return path
        try:
            resp = requests.get(url, headers={"Accept-Encoding": "gzip"}, timeout=120)
            resp.raise_for_status()
            if not validate(resp.content):
                raise ValueError(f"downloaded {url} failed validation")
            DATA_DIR.mkdir(exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(resp.content)
            tmp.replace(path)  # atomic: readers never see a half-written file
        except (requests.RequestException, ValueError):
            if not path.exists():
                raise
            path.touch()  # keep using the old copy; retry after another REFRESH_DAYS
        return path


def parse_names(text):
    """items.txt lines look like '   1: T4_BAG    : Adept's Bag'."""
    parsed = {}
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1].strip():
            parsed[parts[1].strip()] = parts[2].strip()
    return parsed


def _valid_names(content):
    return len(parse_names(content.decode("utf-8", "replace"))) > 1000


def names():
    """{item_id: display name} from ao-bin-dumps' items.txt."""
    global _names
    with _lock:
        if _names is None:
            path = cached_download(ITEMS_URL, ITEMS_FILE, _valid_names)
            _names = parse_names(path.read_text(encoding="utf-8", errors="replace"))
    return _names


def tier(item_id):
    m = re.match(r"T(\d)_", item_id)
    return int(m.group(1)) if m else 0


def enchant(item_id):
    m = re.search(r"@(\d)$", item_id)
    return int(m.group(1)) if m else 0


def label(item_id):
    """Albion shorthand: T4 flat = '4.0', T6 enchant 2 = '6.2'."""
    return f"{tier(item_id)}.{enchant(item_id)}"


def base_of(item_id):
    """Item id -> base name without its tier and enchantment, or None.
    Gear enchants as 'T4_BAG@1', resources as 'T4_ORE_LEVEL1@1'; both give 'BAG' / 'ORE'."""
    m = re.match(r"T[1-8]_(.+)$", item_id)
    if not m:
        return None
    rest = m.group(1)
    e = re.search(r"@([1-4])$", rest)
    if e:
        rest = rest[: e.start()]
        level = f"_LEVEL{e.group(1)}"
        if rest.endswith(level):
            rest = rest[: -len(level)]
    return rest


def variants(item_id):
    """Every tier/enchantment of the same item that exists: [{tier, enchant, id}].
    Both enchantment spellings are tried, since a plain id doesn't say which one it uses."""
    rest = base_of(item_id)
    if not rest:
        return []
    known, found = names(), []
    for t in range(1, 9):
        for e in range(5):
            spellings = [f"T{t}_{rest}"] if e == 0 else [f"T{t}_{rest}_LEVEL{e}@{e}", f"T{t}_{rest}@{e}"]
            match = next((c for c in spellings if c in known), None)
            if match:
                found.append({"tier": t, "enchant": e, "id": match})
    return found


def search(query, limit=40):
    """Tradeable items matching every word of the query, in name or id; best matches first.
    Searching '4.2 bag' or 'adept bag' both work."""
    terms = query.lower().split()
    if not terms:
        return []
    known, hits = names(), []
    for item_id in tradeable_ids():
        name = known[item_id].lower()
        haystack = f"{name} {item_id.lower()} {label(item_id)}"
        if not all(t in haystack for t in terms):
            continue
        # exact name first, then earliest match in the name, then lowest tier, then id
        hits.append(((name != query.lower(), name.find(terms[0]) % 1000, tier(item_id), enchant(item_id), item_id),
                     item_id))
    hits.sort()
    return [{"id": i, "name": known[i], "tier": tier(i), "enchant": enchant(i), "label": label(i)}
            for _, i in hits[:limit]]


def tradeable_ids():
    return [i for i, n in names().items() if n and re.match(r"T[1-8]_", i) and not UNTRADEABLE.search(i)]


# (dashboard tab, item key) for the gatherable and refined resource families.
FAMILIES = [
    ("Ore", "ORE"), ("Metal Bars", "METALBAR"),
    ("Wood", "WOOD"), ("Planks", "PLANKS"),
    ("Stone", "ROCK"), ("Stone Blocks", "STONEBLOCK"),
    ("Hide", "HIDE"), ("Leather", "LEATHER"),
    ("Fiber", "FIBER"), ("Cloth", "CLOTH"),
]
CROPS = ["T1_CARROT", "T2_BEAN", "T3_WHEAT", "T4_TURNIP", "T5_CABBAGE", "T6_POTATO", "T7_CORN", "T8_PUMPKIN"]
HERBS = ["T2_AGARIC", "T3_COMFREY", "T4_BURDOCK", "T5_TEASEL", "T6_FOXGLOVE", "T7_MULLEIN", "T8_YARROW"]


def resource_groups():
    """[(tab name, [item ids])], each list ordered by tier then enchantment.
    Candidates are generated for every tier/enchant and kept only if the game has them."""
    known = names()
    groups = []
    for title, key in FAMILIES:
        ids = []
        for t in range(1, 9):
            for e in range(5):
                item_id = f"T{t}_{key}" if e == 0 else f"T{t}_{key}_LEVEL{e}@{e}"
                if item_id in known:
                    ids.append(item_id)
        groups.append((title, ids))
    fish = sorted((i for i in known if re.match(r"T\d_FISH_", i)), key=lambda i: (tier(i), "RARE" in i, i))
    groups.append(("Fish", fish + [i for i in ("T1_FISHCHOPS", "T1_SEAWEED") if i in known]))
    groups.append(("Crops", [i for i in CROPS if i in known]))
    groups.append(("Herbs", [i for i in HERBS if i in known]))
    return groups
