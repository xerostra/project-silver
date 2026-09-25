"""Kill events from the official gameinfo API: parsing and the append-only raw archive.

The API only serves the newest ~1,050 events (about an hour on Asia), so anything we don't
collect while it's live is gone. Raw events are archived untouched, one folder per server, so
every derived table (the killboard, Meta counters) can be rebuilt from them.
"""
import gzip
import json
import os
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import config

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent / "data" / "killboard"
SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes", "Cape", "Bag", "Mount", "Potion", "Food")


def parse_time(value):
    """'2026-09-24T04:42:34.7286358Z' (7-digit fraction) -> epoch seconds, or None."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return int(datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _item(raw):
    """{Type, Count, Quality} -> [type, count, quality], or None for an empty slot."""
    if not isinstance(raw, dict) or not isinstance(raw.get("Type"), str):
        return None
    return [raw["Type"], int(num(raw.get("Count")) or 1), int(num(raw.get("Quality")))]


def parse_player(raw, inventory=False):
    raw = raw if isinstance(raw, dict) else {}
    gear = raw.get("Equipment") if isinstance(raw.get("Equipment"), dict) else {}
    out = {
        "id": raw.get("Id") or "", "name": raw.get("Name") or "?",
        "guild": raw.get("GuildName") or "", "guild_id": raw.get("GuildId") or "",
        "alliance": raw.get("AllianceName") or raw.get("AllianceTag") or "",
        "ip": round(num(raw.get("AverageItemPower"))),
        "kill_fame": int(num(raw.get("KillFame"))), "death_fame": int(num(raw.get("DeathFame"))),
        "gear": {slot: _item(gear.get(slot)) for slot in SLOTS if _item(gear.get(slot))},
    }
    if inventory:
        out["inventory"] = [i for i in map(_item, raw.get("Inventory") or []) if i]
    return out


def parse_event(raw):
    """Raw kill event -> compact dict, or None if it has no id or time."""
    if not isinstance(raw, dict) or not isinstance(raw.get("EventId"), int):
        return None
    at = parse_time(raw.get("TimeStamp"))
    if at is None:
        return None
    return {
        "id": raw["EventId"], "at": at,
        "fame": int(num(raw.get("TotalVictimKillFame"))),
        "area": raw.get("KillArea") or "", "battle": raw.get("BattleId"),
        "participants": int(num(raw.get("numberOfParticipants"))) or len(raw.get("Participants") or []),
        "killer": parse_player(raw.get("Killer")),
        "victim": parse_player(raw.get("Victim"), inventory=True),
    }


def weapon_usage(events, player_id):
    """Main-hand weapons the player carried in these events, most used first: [(item_id, count)].
    Tier and enchantment are kept apart, since a T8 build is not a T4 one."""
    used = Counter()
    for e in events:
        for side in ("killer", "victim"):
            if e[side]["id"] == player_id and "MainHand" in e[side]["gear"]:
                used[e[side]["gear"]["MainHand"][0]] += 1
    return sorted(used.items(), key=lambda kv: (-kv[1], kv[0]))


class Archive:
    """Raw events, append-only, in <dir>/<YYYY-MM-DD>.<run>.jsonl.gz: one file per UTC day per
    run of the collector. Each append adds a gzip member; a crash can only cut off the tail of
    the current run's file, and nothing is ever appended after that cut, so every other event
    stays readable. Readers de-duplicate by EventId."""

    def __init__(self, directory=None, run=None):
        self.dir = Path(directory) if directory else ARCHIVE_ROOT / config.SERVER
        self.run = run or time.strftime("%H%M%S") + f"-{os.getpid()}"

    def append(self, raws):
        """Add events and fsync them, so an acknowledged append survives a power cut. Events
        without a valid time are skipped: nothing downstream can place them."""
        by_day = {}
        for r in raws:
            at = parse_time(r.get("TimeStamp"))
            if at is not None:
                by_day.setdefault(datetime.fromtimestamp(at, timezone.utc).date().isoformat(), []).append(r)
        self.dir.mkdir(parents=True, exist_ok=True)
        for day, rows in sorted(by_day.items()):
            data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
            with open(self.dir / f"{day}.{self.run}.jsonl.gz", "ab") as out:
                with gzip.GzipFile(fileobj=out, mode="ab") as gz:
                    gz.write(data)
                out.flush()
                os.fsync(out.fileno())

    def stream(self, days):
        """Raw events from the newest `days` days, oldest day first. A cut-off file ends that
        file's read instead of failing the whole stream."""
        files = sorted(self.dir.glob("*.jsonl.gz"))
        wanted = sorted({f.name[:10] for f in files})[-days:] if days else []
        for path in (f for f in files if f.name[:10] in wanted):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    for line in f:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
            except (OSError, EOFError):
                traceback.print_exc()
