"""Faction hearts: prices, refining uses and the Shadowheart conversion.

A heart is a faction-warfare token that trades on the market. Two things give it value:

1. **Refining.** Every T4-T8 refining recipe has a variant where one heart replaces one raw
   resource: T8 bars are 5 ore + 1 T7 bar, or 4 ore + 1 Mountainheart + 1 T7 bar. The heart
   count is 1 at every tier, so a heart is worth exactly the raw it saves - which makes it
   worth most on T8 lines and least on T4.
2. **Faction transports.** Missions pay out rival hearts for carrying trade packs; see
   transports.py for the deterministic per-path rewards.

Shadowheart converts 1:1 into any other heart for 5,000 silver, which caps every other heart at
the Shadowheart price plus that fee.
"""
from .crafting import recipes
from .items import names

CONVERSION_SILVER = 5000

SHADOWHEART = "T1_FACTION_CAERLEON_TOKEN_1"
# heart -> the faction/biome it comes from, for display and for caravan packs
FACTIONS = {
    SHADOWHEART: "Caerleon",
    "T1_FACTION_FOREST_TOKEN_1": "Lymhurst",
    "T1_FACTION_HIGHLAND_TOKEN_1": "Martlock",
    "T1_FACTION_MOUNTAIN_TOKEN_1": "Fort Sterling",
    "T1_FACTION_STEPPE_TOKEN_1": "Bridgewatch",
    "T1_FACTION_SWAMP_TOKEN_1": "Thetford",
}


def uses():
    """Every refining recipe that can spend a heart, newest data each call.
    [{heart, refined, refined_name, tier, line, raw, raw_name, saves}]"""
    display = names()
    out = []
    for recipe in recipes().values():
        token = recipe.get("token")
        if not token:
            continue
        out.append({
            "heart": token["id"], "heart_name": display.get(token["id"], token["id"]),
            "hearts_needed": token["count"],
            "refined": recipe["id"], "refined_name": recipe["name"],
            "tier": recipe["tier"], "enchant": recipe["enchant"], "line": recipe["group"],
            "raw": token["saves_id"], "raw_name": display.get(token["saves_id"], token["saves_id"]),
            "saves": token["saves_count"],
        })
    out.sort(key=lambda u: (u["line"], u["tier"], u["enchant"]))
    return out


def catalog():
    """The six hearts, with the refining line each one serves."""
    display, lines = names(), {}
    for use in uses():
        lines.setdefault(use["heart"], set()).add(use["line"])
    return [{"id": heart, "name": display.get(heart, heart), "faction": faction,
             "lines": sorted(lines.get(heart, []))}
            for heart, faction in FACTIONS.items()]


def item_ids():
    """Hearts plus every raw resource a heart can replace, for one price fetch."""
    return sorted({h["id"] for h in catalog()} | {u["raw"] for u in uses()})
