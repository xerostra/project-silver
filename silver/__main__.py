"""python -m silver serve          -> dashboard at http://localhost:8765 (collects too, unless
                                    another process already is)
python -m silver collect        -> collection only, for running 24/7 under a supervisor
python -m silver scan           -> full market scan, best flips printed to the terminal
python -m silver rebuild-kills  -> recompute the killboard and Meta counters from the raw archive
python -m silver backup DIR     -> consistent copy of everything collected, safe while running"""
import argparse
import shutil
import signal
import threading
import time

from . import collect, config, flips
from .api import AlbionAPI, APIError
from .killboard import Archive
from .killstore import Collector, KillStore
from .store import Store


def fmt_age(ts):
    if not ts:
        return "-"
    mins = (time.time() - ts) / 60
    return f"{mins:.0f}m" if mins < 90 else f"{mins / 60:.1f}h"


def cmd_scan(args):
    result = flips.scan(Store(), AlbionAPI(), progress=lambda s: print(f"\r{s:<30}", end="", flush=True),
                        premium=not args.no_premium)
    print(f"\rScanned {result['items_scanned']} items in {result['duration']}s\n")
    oldest = time.time() - args.max_age * 3600
    rows = [
        f for f in result["results"]
        if (not args.strategy or f["strategy"] == args.strategy)
        and f["buy_at"] >= oldest and f["sell_at"] >= oldest
        and f["buy"] <= args.capital
        and (f["vol"] or 0) >= args.min_volume
    ]
    # results arrive in flips.rank_key order; stable sorts keep that as the tie-breaker
    if args.sort == "daily":
        rows.sort(key=lambda f: -(flips.est_daily(f, args.capital) or 0))
    elif args.sort == "roi":
        rows.sort(key=lambda f: -f["roi"])
    for w in result["warnings"]:
        print(f"warning: {w}")
    header = (f"{'item':<38} {'q':>1} {'strategy':<8} {'route':<30} {'buy':>10} {'sell':>10} "
              f"{'profit':>9} {'roi':>6} {'vol/d':>7} {'units':>6} {'ages':>11}")
    print(header)
    print("-" * len(header))
    for f in rows[:args.top]:
        name = f"{f['label']} {f['name']}"[:38]
        route = f"{f['from']} > {f['to']}"[:30]
        vol = f"{f['vol']:.0f}" if f["vol"] is not None else "-"
        units = flips.est_units(f, args.capital)
        print(f"{name:<38} {f['quality']:>1} {f['strategy']:<8} {route:<30} {f['buy']:>10,} {f['sell']:>10,} "
              f"{f['profit']:>9,} {f['roi']:>6.0%} {vol:>7} {units if units is not None else '-':>6} "
              f"{fmt_age(f['buy_at']):>5}/{fmt_age(f['sell_at']):<5}")


def cmd_collect(args):
    lock = collect.acquire_lock()
    if not lock:
        raise SystemExit("Another process is already collecting for this server.")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    store, kills = Store(), KillStore()
    threads = collect.start(collect.Syncer(store, AlbionAPI()), Collector(kills), stop)
    print(f"Collecting {config.SERVERS[config.SERVER]['label']} market data and kills. Ctrl+C or SIGTERM to stop.",
          flush=True)
    while not stop.wait(600):
        st = collect.status(store, kills)
        print(time.strftime("%H:%M"), "prices", fmt_age(st["prices_at"]), "· kills", fmt_age(st["kills_at"]),
              "· FAILING" if st["sync_failing"] or st["kills_failing"] else "", flush=True)
    for t in threads:  # let an in-flight write finish
        t.join(timeout=30)


def cmd_rebuild_kills(args):
    total = KillStore().rebuild(Archive())
    print(f"Rebuilt the killboard and Meta counters from {total:,} archived kills.")


def cmd_backup(args):
    """SQLite's VACUUM INTO makes a consistent copy even while the collector writes; archive
    files are append-only, so any not yet in the backup are copied as they are."""
    from pathlib import Path
    dest = Path(args.dest) / config.SERVER
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    for db in (Store(), KillStore()):
        target = dest / f"{db.path.stem}-{stamp}.db"
        db.db.execute("VACUUM INTO ?", (str(target),))
        print(f"{db.path.name} -> {target}")
    archive, copied = Archive(), 0
    (dest / "killboard").mkdir(exist_ok=True)
    for f in sorted(archive.dir.glob("*.jsonl.gz")):
        out = dest / "killboard" / f.name
        if not out.exists() or out.stat().st_size != f.stat().st_size:
            shutil.copy2(f, out)
            copied += 1
    print(f"kill archive: {copied} new or grown files -> {dest / 'killboard'}")


def main():
    parser = argparse.ArgumentParser(prog="silver", description="Albion Online market and PvP tools")
    parser.add_argument("--server", choices=sorted(config.SERVERS), default=config.SERVER,
                        help="game server (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the local dashboard")
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 if Windows can't reach WSL on localhost")
    p.add_argument("--port", type=int, default=8765)

    sub.add_parser("collect", help="collect market data and kills only (no dashboard), e.g. under systemd")
    sub.add_parser("rebuild-kills", help="recompute the killboard and Meta counters from the raw kill archive")
    p = sub.add_parser("backup", help="copy the databases and kill archive somewhere safe (fine while running)")
    p.add_argument("dest", help="backup folder, e.g. /mnt/c/Users/you/silver-backup")

    p = sub.add_parser("scan", help="scan the whole market and print the best flips")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--strategy", choices=flips.STRATEGIES)
    p.add_argument("--max-age", type=float, default=2, help="ignore prices older than this many hours")
    p.add_argument("--capital", type=int, default=config.CAPITAL, help="skip items costing more than this")
    p.add_argument("--min-volume", type=float, default=0, help="minimum units traded per day at the destination")
    p.add_argument("--sort", choices=["daily", "profit", "roi"], default="daily",
                   help="daily = estimated silver/day (needs volume data), profit = per unit")
    p.add_argument("--no-premium", action="store_true", help="use the 8%% non-premium sales tax")

    args = parser.parse_args()
    config.select(args.server)
    try:
        if args.cmd == "serve":
            from .server import serve
            serve(args.host, args.port)
        elif args.cmd == "collect":
            cmd_collect(args)
        elif args.cmd == "rebuild-kills":
            cmd_rebuild_kills(args)
        elif args.cmd == "backup":
            cmd_backup(args)
        else:
            cmd_scan(args)
    except APIError as exc:
        raise SystemExit(f"\nerror: {exc}")


if __name__ == "__main__":
    main()
