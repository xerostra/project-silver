"""Player and guild lookups from the gameinfo API, shaped for the dashboard.
Every field is optional in the source, so each one falls back to an empty value."""
from .killboard import num, parse_event, parse_player, weapon_usage

PVE_AREAS = ("Royal", "Outlands", "Avalon", "Hellgate", "CorruptedDungeon", "Mists")
RESOURCES = ("Fiber", "Hide", "Ore", "Rock", "Wood")


def _dict(value):
    return value if isinstance(value, dict) else {}


def search_results(raw):
    raw = _dict(raw)
    players = [{"id": p.get("Id"), "name": p.get("Name") or "?", "guild": p.get("GuildName") or "",
                "alliance": p.get("AllianceName") or "", "kill_fame": int(num(p.get("KillFame"))),
                "death_fame": int(num(p.get("DeathFame")))}
               for p in raw.get("players") or [] if isinstance(p, dict) and p.get("Id")]
    guilds = [{"id": g.get("Id"), "name": g.get("Name") or "?", "alliance": g.get("AllianceName") or "",
               "death_fame": int(num(g.get("DeathFame")))}
              for g in raw.get("guilds") or [] if isinstance(g, dict) and g.get("Id")]
    return {"players": players, "guilds": guilds}


def lifetime(raw):
    """LifetimeStatistics -> flat totals: PvE fame by area, gathered units by resource, other fame."""
    stats = _dict(raw)
    pve, gathering = _dict(stats.get("PvE")), _dict(stats.get("Gathering"))
    return {
        "pve": int(num(pve.get("Total"))),
        "pve_areas": {a: int(num(pve.get(a))) for a in PVE_AREAS},
        "gathering": int(num(_dict(gathering.get("All")).get("Total"))),
        "gathering_by": {r: int(num(_dict(gathering.get(r)).get("Total"))) for r in RESOURCES},
        "crafting": int(num(_dict(stats.get("Crafting")).get("Total"))),
        "fishing": int(num(stats.get("FishingFame"))),
        "farming": int(num(stats.get("FarmingFame"))),
    }


def player_profile(raw, kills, deaths):
    """Profile + recent kills and deaths, newest first, and the weapons seen in them."""
    player = parse_player(raw)
    player.pop("gear")  # the profile endpoint always reports it empty
    parse = lambda rows: sorted(filter(None, map(parse_event, rows or [])), key=lambda e: -e["at"])
    kills, deaths = parse(kills), parse(deaths)
    return {"player": {**player, "lifetime": lifetime(_dict(raw).get("LifetimeStatistics"))},
            "kills": kills, "deaths": deaths, "weapons": weapon_usage(kills + deaths, player["id"])}


def guild_profile(data, members):
    """Guild totals, top killers and every member with their fame, best killers first."""
    data = _dict(data)
    guild, overall = _dict(data.get("guild")), _dict(data.get("overall"))
    rows = []
    for m in members or []:
        if not isinstance(m, dict):
            continue
        p = parse_player(m)
        life = lifetime(m.get("LifetimeStatistics"))
        rows.append({k: p[k] for k in ("id", "name", "kill_fame", "death_fame")} |
                    {k: life[k] for k in ("pve", "gathering", "crafting")})
    rows.sort(key=lambda r: (-r["kill_fame"], r["name"]))
    return {
        "id": guild.get("Id") or "", "name": guild.get("Name") or "?",
        "alliance": guild.get("AllianceTag") or guild.get("AllianceName") or "",
        "founder": guild.get("FounderName") or "", "founded": guild.get("Founded") or "",
        "kill_fame": int(num(guild.get("killFame"))), "death_fame": int(num(guild.get("DeathFame"))),
        "kills": int(num(overall.get("kills"))), "deaths": int(num(overall.get("deaths"))),
        "top": [{"id": p.get("Id"), "name": p.get("Name") or "?", "kill_fame": int(num(p.get("KillFame"))),
                 "kills": int(num(p.get("totalKills")))}
                for p in data.get("topPlayers") or [] if isinstance(p, dict)],
        "members": rows,
    }
