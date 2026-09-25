# Project Silver — data platform plan

Goal: the most complete Albion Online dataset we can legitimately assemble, with the Asia server
first. Everything below was verified against the live sources on 2026-09-24.

## Sources that work

| Source | What it gives | Status |
|---|---|---|
| AODP REST (`east/west/europe.albion-online-data.com`) | Best bid/ask per item, city, quality; daily history; gold | **in use** |
| AODP NATS (`nats://public:thenewalbiondata@www.albion-online-data.com:4222`) | Live orders: `Id`, item, location, quality, enchant, price, **`Amount`**, `AuctionType`, `Expires` | verified, see caveat |
| **Data client private ingest** (`albiondata-client -p http://host:port`) | The same order data for **our** server, posted to us as `POST {base}/{topic}` | verified in client source |
| `ao-bin-dumps` | Items, recipes, enchant paths, item values, zones (`cluster/world.json`: tier, biome, PvP type, exits, map position) | **in use** |
| Official `gameinfo` API (Asia: `gameinfo-sgp.albiononline.com`) | Kill events with full equipment **and inventory**, battles, players, guilds, alliances, leaderboards. `/events` pages 51 at a time up to offset 1000 only (~1 h of Asia kills) | **in use**: collector archives every kill |
| `serverstatus.albiononline.com` | Server up/down | verified, unused |

### Caveat: the public NATS feed has no server tag

Sampled at ~10.8 orders/s (~0.94M/day, ~60 MB/day compact). Payloads carry no server field, and
sampled prices match no single REST server, so the public stream is all servers merged and cannot be
attributed to Asia. **The fix is our own client**: run `albiondata-client` with `-p` pointing at our
own endpoint. We then get Asia-only data, at order granularity, for every market we open in game —
while `-i` keeps contributing to the public pool as now.

That single change is what makes this a platform instead of a viewer: `Amount` is the order depth the
REST API does not expose, and it is the number the Flips tab currently has to estimate.

## What only we would have

1. **Real order books.** Depth per item/city/quality over time, not just the best price.
2. **Order lifecycles.** Orders carry a stable `Id` and `Expires`; tracking them across scans infers
   fills, cancellations and time-to-fill — the basis for an honest liquidity score and for knowing
   whether a spread flip would actually have filled.
3. **Consumption.** `gameinfo` kill events list every item worn and carried when a player dies.
   Aggregated, that is the destruction rate that drives demand — a leading indicator for prices that
   no market API carries.
4. **Full production economics.** Already built: 11,577 recipes, return rates, city bonuses, fees.

## Out of scope: live resource node positions

The earlier projects that mapped node positions worked by reading the game's entity packets (radar).
SBI tolerates passive market data collection — their technical lead's line about the data client is
"as long as you just look and analyze we are ok with it" — but has banned radar-style tools that
expose entity positions. We will not build one.

The dumps do not contain node coordinates either: cluster files hold layout templates, and the 2,747
template files contain tiles, mob spawn points and lights, but no harvestables. Nodes are placed by
the server at runtime from a per-zone budget.

Legitimate substitutes: zone tier/biome/PvP type from `cluster/world.json`, the biome rule (Forest→
wood, Highland→stone, Mountain→ore, Steppe→hide, Swamp→fiber — confirmed by summing the dumps'
tiered entries), and "silver per trip by zone" derived from our own market data.

## Architecture

```
ingesters ──┐
 client -p  │   append-only event log        derived tables            API + UI
 REST poll  ├──►  (JSONL/Parquet by day) ──►  orders_current           /api/*
 gameinfo   │     never rewritten             order_history            dashboard
 dumps      │                                 fills, liquidity         alerts
            │                                 consumption, prices
```

- **Append-only first.** Raw payloads land untouched; every table is rebuildable from them. The live
  feed is perishable: what we fail to capture today cannot be backfilled.
- **Storage.** Start SQLite + daily Parquet; both handle this volume. Move to DuckDB for analytics
  and Postgres/ClickHouse only if multi-user serving demands it.
- **Idempotency.** Orders are keyed `(Id, seen_at)`; kill events by `EventId`.

## Roadmap

- **P0 — done.** REST market data, flip scanner, crafting/refining/enchanting, item lookup.
- **P0.5 — done 2026-09-24.** Local market store (`silver/store.py`, SQLite): every item's prices
  swept every 5 min, 4 months of daily history backfilled then topped up every 3 h, fair prices
  (trimmed, volume-weighted median) for every item/quality/city. All tabs read it. Full sweep
  ~11 s (63 requests), history backfill ~60 s, store ~90 MB.
- **Robustness pass — done 2026-09-24.** Separate `collect` process with a lock file and
  automatic takeover by `serve`; kills and Meta counters in SQLite (`kills.db`), idempotent by
  event id; crash-safe per-run archive files with fsync; versioned schema migrations
  (`silver/db.py`); health in the header; `backup` and `rebuild-kills` commands; systemd unit.
- **P1 — capture.** Private ingest endpoint (`POST /{topic}`), event log, order store. Run the
  client with `-p`. Deliverable: real listed quantities in the Flips and Lookup tabs.
- **P2 — order book engine.** Depth, fills, time-to-fill, liquidity score; replace the estimated
  `Units` column with measured depth.
- **P3 — world.** Zone graph from `cluster/world.json`: real route risk ("4 zones, 2 red") and
  travel cost per flip.
- **P4 — demand.** *Started 2026-09-24:* kill collector + raw archive (`silver/killboard.py`),
  killboard, player and guild pages, gold tab; Meta tab (`silver/meta.py`: weapons and builds by
  fight size, items lost per day, 14 days kept as per-day counters). Still to do: guild and
  alliance activity; correlate losses with price moves.
- **P5 — serving.** Unified API, alerts on price/spread triggers, forecasting, multi-server support.

Each phase stands alone and is useful on its own; none requires the next.
