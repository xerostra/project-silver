"""What people fight with and what they lose, aggregated per day from kill events.

Each kill is one kill split evenly between the participants, and one death for the victim, so
kills and deaths balance and a kill share above 50% really means winning more than losing (a
five-player gank would otherwise count five kills against one death). Builds are
compared by item line, not tier or enchantment: "Heavy Crossbow + Cowl + Robe..." whatever the
tier. Items lost are counted exactly (tier and enchantment kept), since that is what gets rebought.
Counters are kept per day in the kill store (killstore.py); this module only computes them.
"""
import re
from collections import Counter
from datetime import datetime, timezone

from .killboard import num, parse_player, parse_time

# fight size by participants on the killing side
SIZES = (("solo", 1), ("small", 5), ("group", 19), ("zvz", float("inf")))
BUILD_SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes", "Cape")
KEEP_DAYS = 14


def fight_size(participants):
    return next(name for name, most in SIZES if participants <= most)


def base_id(item_id):
    """'T5_2H_CROSSBOWLARGE@1' -> '2H_CROSSBOWLARGE'."""
    return re.sub(r"^T\d_", "", item_id.split("@")[0])


def build_of(gear):
    """Item lines in BUILD_SLOTS order ('' for an empty slot), or None without a weapon."""
    if "MainHand" not in gear:
        return None
    return tuple(base_id(gear[s][0]) if s in gear else "" for s in BUILD_SLOTS)


def lost_items(victim):
    """Units the victim lost: everything worn and carried. {market id: units}."""
    out = Counter()
    for item_id, count, _ in (*victim["gear"].values(), *victim["inventory"]):
        out[item_id] += count
    return out


def day_of(at):
    return datetime.fromtimestamp(at, timezone.utc).date().isoformat()


def counter_rows(raw):
    """One raw kill event -> (day, at, [(kind, size, key, side, amount)]), or None if it has no
    time or victim. kind is kill / weapon / build / lost; side is kills / deaths / seen for
    weapons and builds. A build key is its item lines joined with '|'."""
    at = parse_time(raw.get("TimeStamp")) if isinstance(raw, dict) else None
    if at is None or not isinstance(raw.get("Victim"), dict):
        return None
    victim = parse_player(raw["Victim"], inventory=True)
    winners = {p["id"]: p for p in map(parse_player, raw.get("Participants") or []) if p["id"]}
    if not winners:  # older events may list no participants: credit the killer
        killer = parse_player(raw.get("Killer"))
        winners = {killer["id"]: killer}
    size = fight_size(int(num(raw.get("numberOfParticipants"))) or len(winners))
    rows = [("kill", size, "", "", 1)]
    share = 1 / len(winners)
    for side, players, credit in (("kills", winners.values(), share), ("deaths", [victim], 1)):
        for p in players:
            build = build_of(p["gear"])
            if build:
                for kind, key in (("weapon", build[0]), ("build", "|".join(build))):
                    rows += [(kind, size, key, side, credit), (kind, size, key, "seen", 1)]
    rows += [("lost", size, item_id, "", units) for item_id, units in lost_items(victim).items()]
    return day_of(at), at, rows


def summarize(rows, days, hours, size=None, top=60, lost_filter=None):
    """Counter rows (kind, size, key, side, amount) summed over some days -> the Meta view.
    size limits it to one fight size; lost_filter(item_id) drops items from the losses."""
    kills, tables, lost = 0, {"weapon": {}, "build": {}}, Counter()
    for kind, s, key, side, amount in rows:
        if size is not None and s != size:
            continue
        if kind == "kill":
            kills += amount
        elif kind == "lost":
            if lost_filter is None or lost_filter(key):
                lost[key] += amount
        elif kind in tables:
            tables[kind].setdefault(key, {"key": key, "seen": 0, "kills": 0, "deaths": 0})[side] += amount

    def ranked(kind):
        out = sorted(tables[kind].values(), key=lambda r: (-r["seen"], r["key"]))[:top]
        return [{"seen": int(r["seen"]), "kills": round(r["kills"], 2), "deaths": int(r["deaths"]), "key": r["key"]} for r in out]

    return {
        "days": days, "hours": hours, "kills": int(kills),
        "weapons": [{"weapon": r.pop("key"), **r} for r in ranked("weapon")],
        "builds": [{"build": r.pop("key").split("|"), **r} for r in ranked("build")],
        "lost": [{"id": i, "units": int(n)} for i, n in sorted(lost.items(), key=lambda kv: (-kv[1], kv[0]))[:300]],
    }


def line_names(bases, known):
    """Item line -> (icon id, name without the tier word): '2H_CROSSBOWLARGE' ->
    ('T4_2H_CROSSBOWLARGE', 'Heavy Crossbow'). Named from the lowest regular tier with the usual
    "Adept's X" form, since some T8 items have unique names. Unknown lines keep their id."""
    out = {}
    for b in bases:
        tiers = [f"T{t}_{b}" for t in (4, 5, 6, 7, 8, 3, 2, 1) if f"T{t}_{b}" in known]
        full = next((i for i in tiers if re.match(r"\S+'s ", known[i])), tiers[0] if tiers else None)
        out[b] = (full, re.sub(r"^\S+'s ", "", known[full])) if full else (None, b)
    return out

