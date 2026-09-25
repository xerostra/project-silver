# Project Silver

Albion Online market and PvP tools, **Asia server** by default (`--server americas|europe` for the others), built on the
[Albion Online Data Project](https://www.albion-online-data.com/api/) API (`east.albion-online-data.com`).

## Install (Windows)

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), once, in PowerShell:
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Install Silver: `uv tool install "project-silver @ git+https://github.com/<owner>/project-silver"`
3. Run `silver`: it starts collecting and opens the dashboard in its own window (Edge or Chrome
   app mode). `silver shortcut` adds it to the Start menu. Keep the console window open while
   you use it; closing it stops Silver.

The first start downloads game data and about four months of price history (a minute or two).
Your data lives in `%LOCALAPPDATA%\Silver`; everything is local to your PC. Update with
`uv tool upgrade project-silver`. For the screen-reading eye, install `project-silver[eye]` instead.

## Run from source

```bash
uv run silver                          # the dashboard in its own window, started if needed
uv run silver serve                    # just the server, at http://localhost:8765 (also collects, see below)
                                       # UI toolkit reference (silver/web/silver.css) at /kit
uv run silver collect                  # collection only, for running 24/7
uv run silver --server europe serve    # another server (americas, europe; default asia)
uv run silver scan                     # full market scan, best flips in the terminal
uv run silver scan --strategy instant --max-age 1 --min-volume 50 --capital 2000000
uv run silver backup /mnt/c/Users/you/silver-backup   # copy everything collected (safe while running)
uv run silver rebuild-kills            # recompute the killboard and Meta from the raw kill archive
```

Needs Python 3.10+ and [uv](https://docs.astral.sh/uv/): `uv sync` installs the pinned dependencies
(`pyproject.toml`, `uv.lock`) into `.venv`, and `uv run` uses them. Add a dependency with `uv add <name>`.
From a source checkout, data lives in the repo's `data/`; installed, in the user's data folder
(`silver/paths.py`); `SILVER_DATA` overrides both.
Tests: `uv run python -m unittest discover tests` (add `--extra eye` for the eye's).
If your Windows browser can't reach WSL on localhost, run `serve --host 0.0.0.0`.

## Dashboard

All tabs read from a local market store (`data/<server>/market.db`), which the dashboard keeps
current in the background: every item's prices every 5 minutes, and daily sale history every
3 hours (the first run backfills about 4 months, which takes a minute). Pages load in a fraction
of a second, work through API outages, and survive restarts. **Refresh** on a tab still fetches
its items live. The header shows when prices and history last synced.

- **Market:** the fair price of every traded item (see below): 7-day trend, units and silver traded
  per day, cheapest and dearest city, the Black Market, and today's best ask and bid against fair,
  so items listed well under what they normally sell for stand out. Filter by name, category,
  tier, volume and days traded; click a row for its history.

- **Resources:** ore, bars, wood, planks, stone, blocks, hide, leather, fiber, cloth, fish,
  crops and herbs. Every tier and enchantment, in every city. Each cell shows the price to buy now
  (cheapest listing) and to sell now (best buy order), the data age and 7-day volume. The last
  column shows the best instant and sell-order route.
- **Flips:** scans all ~9,500 tradeable items in about 10 s. Filter by strategy, route risk,
  volume and profit, and sort by any column. **From** and **To** are separate sortable columns:
  sorting by one breaks ties with the other, so you can group by origin, by destination, or by
  the route. The search box matches city names as well as items, e.g. "martlock".
- **Craft & Refine:** profit for all ~12,000 recipes in the game. Two kinds, picked in the
  recipe-group dropdown:
  - *Crafting and refining* (~7,400): refining, weapons, armor, off-hands, capes, bags, food,
    potions, gathering gear and tools, made from raw materials. The *Fish* group has one row per
    fish for chopping (which fish gives the most Chopped Fish for its price) plus fish sauce. Pick the city, focus, daily bonus
    and station fee.
  - *Enchanting* (~4,500): upgrading an item one level, e.g. 4.0 → 4.1 → 4.2, by buying the level
    below plus the runes/souls/relics (or arcane extracts / fish sauce) it consumes.
  For both, choose where and how you buy materials and sell the product. **Make ingredients when
  cheaper** plans the whole chain instead of buying every ingredient: each one, at every level
  (bars from ore and lower-tier bars, planks from wood, meals from their ingredients…), is made
  wherever that beats buying it, with the return rate of its specialty city. The row shows the
  saving, and its tooltip the shopping list and what to refine or craft where. Each row shows the return
  rate, cost per unit, profit, ROI, volume and silver per focus. Hover a row to see its ingredients.
- **Enchant:** every enchanting upgrade as a trade, across all items at once: one level
  (.0 → .1) or several (.0 → .3). The item and each rune/soul/relic are bought wherever they're
  cheapest right now, and the result sold wherever pays best, instantly (buy orders, the Black
  Market included) or listed at the lower of today's ask and the city's fair price. Each row also
  shows the typical profit at fair prices, and how many sell a day (cities and Black Market).
- **Lookup:** search any of the ~9,500 tradeable items by name, id or tier label ("adept bag",
  "broadsword 6.1", `T5_METALBAR`) and see every quality against every market at once, with data
  age, 7-day volume and fair price, plus a chart of its daily sale price (all city markets or one). Chips switch tier and enchantment. The URL carries the item
  (`#lookup=T6_MAIN_SWORD`), so a lookup can be bookmarked.
- **Hearts:** the six faction hearts (Shadowheart, Treeheart, Rockheart, Mountainheart,
  Beastheart, Vineheart). Prices in every city; which refines are worth spending a heart on; the
  Shadowheart conversion; and the profit of every faction transport path.
- **Laborers:** every laborer journal (11 laborer types, T2–T8, plus trophy journals). Set the
  bed, table and trophies to get happiness and yield; see what each full journal returns, its
  value, profit per journal, silver per 1,000 fame, and whether to use a filled journal or sell it.
- **Farming:** every crop, herb and animal. Profit per plot per day for plants (with or without
  watering) and per animal raised (feed, offspring, focus feedings).
- **Focus:** ranks every use of focus — refining, crafting, watering, breeding — by the silver it
  adds, and plans your day: which crafts, how many, until the focus runs out. It works from
  the stored prices; *Refresh market data* re-fetches its ~8,000 items live (about 10 s).
- **Gold:** the gold price in silver, hourly for the last ~30 days (1 day / 1 week / 1 month chart),
  with 24-hour and 7-day change, 7-day range and averages, and a gold ↔ silver converter.
- **Killboard:** live Asia kills from the official gameinfo API: killer and victim, guild, item power,
  gear, what the victim carried, fame and party size. Filter by name, guild or alliance and minimum fame.
- **Players:** search any player or guild. Players show kill/death fame, lifetime PvE fame by area,
  gathering, crafting, fishing and farming fame, the weapons they used in recent fights, and their
  recent kills and deaths. Guilds show totals, top killers and every member. Click any name to open it.
- **Meta:** from the collected kills, which weapons and full builds are used and how they fare,
  by fight size (solo, 2–5, 6–19, ZvZ 20+) over today, 7 or 14 days, and every item lost on death
  with its daily rate. Each kill is split between the players who made it, so kill share is fair
  between solo and group play.
- The Premium, Capital and Max price age settings recalculate everything instantly and are remembered.

## Strategies

| Type | What you do | Profit per unit |
|---|---|---|
| Instant | Buy cheapest listing in A, sell straight into a buy order in B (includes → Black Market) | `bid_B × (1 − tax) − ask_A` |
| Sell order | Buy in A, carry, list in B | `min(ask_B, 7-day avg) × (1 − tax − 2.5%) − ask_A` |
| Spread | Buy order then sell order, same city | `ask × (1 − tax − 2.5%) − bid × 1.025` |

A **spread** flip is market making: you place a buy order just above the highest existing bid, wait
for someone to sell into it, then list what you bought just under the lowest ask. No travel and no
crafting, but both orders can sit unfilled, and you pay the 2.5% setup fee twice plus sales tax.
Only worth it where daily volume is high; a huge ROI usually means a lowball bid nobody will fill.

Tax is 4% with premium and 8% without. The 2.5% setup fee applies whenever you place an order.
Route risk: *No travel* (same city, or Caerleon ↔ Black Market), *Safe* (royal ↔ royal),
*Red zone* (into/out of Caerleon), *Mists* (Brecilien).

## Crafting maths

Return rate = B / (1 + B), where B = 18% base (every city) + 40% refining specialty or 15% crafting
specialty in the right city + 59% with focus + any daily bonus. Refining in the bonus city returns
36.7%, or 53.9% with focus. Recipes come from the game data (ao-bin-dumps `items.json`). Artifacts and
other inputs the game marks as non-returnable are charged in full. Station fee =
item value × 0.1125 × (fee per 100 nutrition) / 100.

| City | Refining | Crafting +15% |
|---|---|---|
| Bridgewatch | Stone | Plate armor, crossbow, dagger, cursed staff, cloth shoes |
| Fort Sterling | Wood | Cloth armor, plate helmet, hammer, spear, holy staff |
| Lymhurst | Fiber | Leather helmet, leather shoes, sword, bow, arcane staff |
| Martlock | Hide | Plate shoes, axe, quarterstaff, frost staff, off-hands |
| Thetford | Ore | Leather armor, cloth helmet, mace, fire staff, nature staff |
| Caerleon | – | Food, gathering gear, tools, war gloves, shapeshifter staves |
| Brecilien | – | Capes, bags, potions |

These values come from community guides as of 2026. If a patch changes them, edit `silver/crafting.py`.

**Enchanting** is done from your inventory, not at a station, so there is no return rate, no station
fee and no focus cost: `profit = sale − price(item one level below) − count × price(rune/soul/relic)`.
Runes take an item to .1, souls to .2, relics to .3 (potions use arcane extracts, food uses fish
sauce). Raw and refined resources have no upgrade recipe in the game data, so resource enchanting
isn't covered.

## Hearts and royal transports

Every T4–T8 refining recipe has a variant where **one heart replaces one raw resource** (T8 bars:
5 ore + 1 T7 bar, or 4 ore + 1 Mountainheart + 1 T7 bar). It is one heart at every tier, so a heart
is worth exactly the raw it saves — little on T4 ore, a lot on T8.3 fiber. Hearts map to lines by
biome: Treeheart → planks, Rockheart → stone blocks, Mountainheart → bars, Beastheart → leather,
Vineheart → cloth. Shadowheart refines nothing but converts 1:1 into any other heart for 5,000 silver.

**Faction transports** are fully deterministic (`quests_asia.json`, `questgivertypes.json`,
`cluster/world.json`). Your faction's store offers missions: pay your own hearts, carry a pack to
a rival's smuggling agent, swap it, bring theirs back, and receive the rival's hearts. Which
missions reach an agent depends on its zone distance from the store:

| Pack | Pay | 4–8 zones | 6–9 | 8–10 | 10–12 | To/from Caerleon (5–7) |
|---|---|---|---|---|---|---|
| Light (125 kg, drop 2 on death) | 3 | 4 | 4 | 5 | 5 | 6 |
| Medium (668 kg, drop 5) | 7 | 9 | 10 | 11 | 12 | 11 |
| Heavy (1,623 kg, drop 10) | 15 | 18 | 19 | 21 | 22 | 21 |

Stores differ: only Bridgewatch offers the 6–9 band, the other royal stores 4–8, 8–10 and 10–12.
The tool maps every store and agent (16 agents), measures the shortest path over the zone graph,
and prices each path at current heart values, with the zone risk along the way.

## Laborers

All from the game data. Each journal has a fame target, a base return and a weighted loot table
(T5 prospector: 32 ore, 94.5% plain / 5% .1 / 0.5% .2 / 0.05% .3). Mercenaries return silver.
Happiness comes from the bed and table (T2 100 … T8 400 each) plus trophies, and
`yield = min(1.5, happiness / 600)`: `gamedata.json` gives 9 bars of 100 points and a 1.5× cap,
so 100% yield is 900 / 1.5 = 600 points. A T8 bed and table give 800 points, 133% yield.

Returns are valued at their 7-day average price by default, since they arrive daily and get sold
over time; instant and sell-order valuations are one click away. Not modelled: laborer contracts,
houses and furniture (one-time costs), and laborer level.

## Farming and focus

From the game data: crops return 3–6 (premium doubles it) every 22 hours, with seed return rising
by tier (T2 33% … T8 93%) and watering (1,000 focus) adding the opposite amount (+133% … +13%).
At NPC seed prices watering is worth exactly **4 silver per focus at every tier** — the game is
balanced that way. Animals need `grow time ÷ seconds per nutrition` nutrition (every crop is 48)
and gain offspring chance per focused feeding; exotic mounts start at 0% and need focus to breed.

The planner ranks a use by the silver focus *adds*: profit with focus minus profit without it (or
all of it, if the craft only pays with focus), divided by the focus cost after your reduction.
Crafts are capped at 10% of daily traded volume; breeding counts per animal per day. Prices must
be within the max price age; on Asia, 6 hours prices few recipes and 24 hours about 700.

## Fair price

What an item actually trades at, from the daily sale history rather than one listing: the
volume-weighted median of the daily average price over the last 14 days (28 if it traded on
fewer than 3 days), per city and pooled over the city markets (the Black Market is kept
separate). Days priced more than 4× away from the typical day are dropped first, so a few troll
trades can't set it. An item that sold on only a day or two can still be wrong, so the days
traded are always shown and the Market tab hides such items by default. The trend compares the
last 7 days' median with the 7 before.

## Know the data's limits

- Prices only update when someone running the data client opens that market. Check the age
  column, and open the market in-game to refresh it (your client uploads it) before you commit.
- **The API never reports order quantity.** The prices endpoint returns only prices and
  timestamps, so nothing can tell you "3 listed at 12,991" — check real stock in-game.
  The `Units` column is a volume-based estimate instead: `min(capital ÷ buy price,
  10% of daily traded volume)`, whole units, and `Est./day = profit × Units`.
- Volume and fair prices come from the history endpoint, which only covers items someone opened
  the price history for. A `?` means unknown, not zero.
- Very high ROI usually means a stale price or a lowball buy order that won't fill.

## How it runs

```
 collector (one per server)                 data/<server>/                     dashboard
 ─ market sync: prices 5 min, history 3 h ─► market.db  (SQLite, WAL)  ◄─ reads ─ serve
 ─ kill feed: every 30 s ─┬───────────────► kills.db   (SQLite, WAL)  ◄─ reads ─ serve
                          └─ raw first ───► killboard/<day>.<run>.jsonl.gz  (append-only)
```

- **One collector per server**, whoever holds `data/<server>/collector.lock`. `serve` collects
  itself when nothing else is, and otherwise just serves, taking over within a minute if the
  collector stops. Running two copies never double-counts: the lock stops the second collector,
  and every kill is keyed by its event id anyway. The OS releases the lock when a process dies,
  so a crash never leaves it stuck.
- **Nothing is lost on a crash.** Every database write is one transaction. A new kill is written
  to the raw archive (and fsynced) before the database, so a crash in between only means it is
  fetched again. Each run writes its own archive file, so a write cut off by a crash never has
  anything appended after it. Failed API calls are retried with backoff; a failed round is
  recorded and retried on the next one, and the loops never die.
- **Restarts are instant**: the killboard and Meta counters live in `kills.db`, not in memory.
  `rebuild-kills` recomputes them from the raw archive, after changing how kills are counted
  or to recover a lost database.
- **Schema versions**: each database records its version and upgrades itself on start; code
  older than the file refuses to open it rather than misread it.
- **Health** shows in the header: when prices, history and kills last came in, and a warning if a
  sync is failing or no collector has been seen for 15 minutes.

### Running 24/7

The feeds are perishable: kills older than an hour and prices nobody recorded can't be fetched
later. For unbroken history, run the collector somewhere always on (a small Linux VPS or a
Raspberry Pi is plenty: ~15 MB of kills a day, one CPU core mostly idle) with
`deploy/silver-collect.service`, which restarts it if it ever stops. On WSL it runs only while
WSL does, which usually means while a terminal is open. Back up `data/` regularly: the
kill archive and history older than the API's ~4-month window exist nowhere else.

## The eye (inventory from the screen)

`uv sync --extra eye` once, then `uv run --extra eye silver eye` reads the inventory and worn
gear off your screen and values them (`--every 5` keeps reading; pass a PNG path to read a screenshot
instead). It also reads your silver and gold, the zone, and the game's own value estimates.
Open your bag in game, windowed or borderless: exclusive fullscreen can capture as black.

It only ever looks at pixels, like a screen recorder: nothing reads the game's memory, files or
network traffic, and nothing sends a key or a click. How it reads (`silver/eye.py`):

- **Bag grid:** the largest set of same-size slot frames on one regular grid, so resolution, UI
  scale and panel position need no setup. The skill bar is a grid too, but ten wide; a bag is four.
- **Item:** correlation against every official render in `data/icons` (named by item id, so the
  best match is the id). Renders are the tiles the game draws, so no model is needed.
- **Tier:** the badge colour (learned from the renders), because enchanted resources use one tile
  at every tier. **Enchant:** the lit diamonds under the art (one green = .1, two blue = .2).
- **Numbers:** stack counts, the balances beside the coins, the load and the game's own
  "Est. Market Value", by OCR (RapidOCR on the CPU). A full bag reads in about 1.5 s.

Tested against two real 1080p captures in `tests/fixtures/`: every item, tier, enchant and count
(`uv run --extra eye python -m unittest tests.test_eye`).

## Sessions, goals and the overlay

The **Session** tab tracks a stretch of play. Press **Start session**: while it runs, the eye (if
installed: start the dashboard with `uv run --extra eye silver serve`) reads your screen
every 5 seconds and, whenever your inventory is open, records your silver, gold, zone and every
item in your bag and worn gear. Without the eye, type your silver at the start and end instead.

- **Profit** = silver gained + the fair-price value of items gained (or lost). Selling loot for
  silver moves value from one to the other, so it isn't counted twice; buying gear isn't a loss.
- **Goals:** profit, silver, loot value, profit per hour or hours played, per session, today or this
  week (your local time), each with a progress bar.
- **Pop out overlay** opens a small always-on-top window (Chrome/Edge Picture-in-Picture) with the
  clock, profit, rate and your top goals, for over the game (windowed or borderless). Open the
  dashboard at `http://localhost:8765` for it: Picture-in-Picture needs localhost, not a LAN
  address, and falls back to an ordinary popup there.

Sessions live in `data/<server>/sessions.db` (`silver/sessions.py`).

## Item icons

Icons come from Albion's render service, which is slow (1 to 60 s for an image it hasn't
rendered lately) and has no long-term cache. The dashboard keeps its own copy in `data/icons/`
(about 8 KB each): once an icon is there it loads instantly and the browser keeps it for a year.
An icon not cached yet is shown straight from the render service while a copy is fetched in the
background, and each page queues the icons it lists, in the order it shows them, so they are
usually cached before you scroll to them. Icons on screen jump the queue. One image per item
(quality only changes the frame; it is in the tooltip).

## API usage

Every request sends `Accept-Encoding: gzip`, which makes responses about 25× smaller. One shared limiter
stays under 180 requests/min and 300 per 5 min. Item lists are batched to keep URLs under 4096 chars.
Item names come from [ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps), cached in `data/` and refreshed weekly.
Settings (server, taxes, defaults) are in `silver/config.py`.
