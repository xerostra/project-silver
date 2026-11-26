# Project Silver

A free, self-hosted dashboard for making silver in **Albion Online**: market flips, crafting and
refining profit, enchanting, faction hearts, laborers, farming, focus planning, gold, a live
killboard and the PvP meta. It works on every server (Asia, Americas, Europe).

You run it yourself, on your own PC or an always-on box. There's no account, no ads and no hosted
service: it reads the public [Albion Online Data Project](https://www.albion-online-data.com/) and
the official game API, and keeps everything on your own machine.

> **Is it safe for my account?** Silver never touches the game. It doesn't read the game's
> memory, files or network traffic, and it never sends a key press or click. Market data comes
> from public APIs. The optional [screen reader](#the-eye-optional) only looks at pixels, like a
> screen recorder.

## What you get

| Tab | What it answers |
|---|---|
| **Market** | What does every item *really* sell for? Fair price, 7-day trend, volume, cheapest and dearest city, and today's listings that are well under fair. |
| **Flips** | What can I buy in one city and sell in another (or the Black Market) for profit? Scans all ~9,500 tradeable items in ~10 s. |
| **Craft & Refine** | Which of the game's ~12,000 recipes make money, here, with my focus and bonuses? It can plan the whole chain and make each ingredient wherever that beats buying it. |
| **Enchant** | Which rune/soul/relic upgrades pay off right now? |
| **Resources** | Every raw and refined resource, every tier and enchant, every city. |
| **Lookup** | One item, every quality, every market, with a price chart. Bookmarkable. |
| **Hearts** | Faction heart prices, which refines are worth a heart, and the profit of every faction transport route. |
| **Laborers** | What each laborer journal returns, and whether to fill it or sell it. |
| **Farming** | Profit per plot and per animal, with and without focus. |
| **Focus** | The best uses of your daily focus, and a plan to spend it. |
| **Gold** | Gold price over the last month, plus a converter. |
| **Killboard / Players / Meta** | Live kills, player and guild lookups, and which weapons and builds are winning, by fight size. |
| **Session** | Track a play session's profit and goals, with an always-on-top overlay. |

## Get started

### Windows

Open **PowerShell** (Start menu → type "PowerShell"), paste this and press Enter:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xerostra/project-silver/main/install.ps1 | iex"
```

It asks which server you play on, installs Silver for your user (no admin rights needed), adds
it to the Start menu and opens it. The first start downloads game data and about four months of
price history, which takes a minute or two. Silver runs in a small console window. Keep it open
while you use the dashboard, and close it to stop Silver.

To **update**, paste the same line again. Your data stays in `%LOCALAPPDATA%\Silver`.

<details>
<summary>Install by hand instead (or on macOS and Linux)</summary>

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. `uv tool install "project-silver @ https://github.com/xerostra/project-silver/archive/refs/heads/main.zip"`
3. Run `silver` (Asia) or `silver --server americas` / `silver --server europe`. On Windows,
   `silver --server europe shortcut` adds a Start-menu entry. On macOS and Linux, run
   `silver serve` and open <http://localhost:8765>.

To update, run step 2 again with `--force --reinstall --refresh` added after `install`.
</details>

That's all most players need. Silver collects data while it's open. If you'd like it collecting
around the clock (for longer kill history and the PvP meta), see [Always on](#always-on-optional).

## Help the data, help yourself

Every price here was uploaded by a player running the
[Albion Data Client](https://github.com/ao-data/albiondata-client) while they browse the market.
Prices for a quiet item can be hours or days old. Check the age column before you commit silver.
Run the client yourself: every market you open in game gets refreshed for you and for everyone
else, within minutes.

## Know the data's limits

- **Prices are only as fresh as the last player who opened that market.** Open it in game (with
  the data client running) and refresh before buying.
- **The API never reports how many are listed.** Nothing can tell you "3 listed at 12,991", so
  check real stock in game. The `Units` column is an estimate: `min(capital ÷ buy price,
  10% of daily traded volume)`.
- **Volume and fair prices** only exist for items someone opened the price history for. A `?`
  means unknown, not zero.
- **A very high ROI** usually means a stale price or a lowball buy order that won't fill.

## How the numbers work

<details>
<summary><b>Flip strategies</b></summary>

| Type | What you do | Profit per unit |
|---|---|---|
| Instant | Buy the cheapest listing in A, sell straight into a buy order in B (including the Black Market) | `bid_B × (1 − tax) − ask_A` |
| Sell order | Buy in A, carry it, list in B | `min(ask_B, 7-day avg) × (1 − tax − 2.5%) − ask_A` |
| Spread | Buy order, then sell order, in the same city | `ask × (1 − tax − 2.5%) − bid × 1.025` |

A **spread** flip is market making. You place a buy order just above the highest bid, wait for
someone to fill it, then list what you got just under the lowest ask. It needs no travel and no
crafting, but both orders can sit unfilled, and you pay the 2.5% setup fee twice plus sales tax.
It's only worth it where daily volume is high.

Tax is 4% with premium and 8% without. The 2.5% setup fee applies whenever you place an order.
Route risk: *No travel* (same city, or Caerleon ↔ Black Market), *Safe* (royal ↔ royal),
*Red zone* (into or out of Caerleon), *Mists* (Brecilien).

**From** and **To** sort separately, so you can group by origin, by destination or by route. The
search box matches cities as well as items, for example "martlock".
</details>

<details>
<summary><b>Crafting and refining</b></summary>

Return rate = B / (1 + B), where B = 18% base (every city) + 40% refining specialty or 15% crafting
specialty in the right city + 59% with focus + any daily bonus. Refining in the bonus city returns
36.7%, or 53.9% with focus. Recipes come from the game data. Artifacts and other non-returnable
inputs are charged in full. Station fee = item value × 0.1125 × (fee per 100 nutrition) / 100.

| City | Refining | Crafting +15% |
|---|---|---|
| Bridgewatch | Stone | Plate armor, crossbow, dagger, cursed staff, cloth shoes |
| Fort Sterling | Wood | Cloth armor, plate helmet, hammer, spear, holy staff |
| Lymhurst | Fiber | Leather helmet, leather shoes, sword, bow, arcane staff |
| Martlock | Hide | Plate shoes, axe, quarterstaff, frost staff, off-hands |
| Thetford | Ore | Leather armor, cloth helmet, mace, fire staff, nature staff |
| Caerleon | – | Food, gathering gear, tools, war gloves, shapeshifter staves |
| Brecilien | – | Capes, bags, potions |

These bonuses come from community guides as of 2026. If a patch changes them, edit
`silver/crafting.py` (a pull request is welcome).

**Make ingredients when cheaper** plans the whole chain. Every ingredient, at every level (bars
from ore and lower-tier bars, meals from their ingredients…), is made wherever that beats buying
it, with the return rate of its specialty city. The row's tooltip gives the shopping list and
what to refine or craft where.

**Enchanting** happens in your inventory, not at a station, so there's no return rate, fee or
focus: `profit = sale − price(item one level below) − count × price(rune/soul/relic)`. Runes take
an item to .1, souls to .2 and relics to .3. Potions use arcane extracts and food uses fish sauce.
</details>

<details>
<summary><b>Faction hearts and transports</b></summary>

Every T4–T8 refining recipe has a variant where **one heart replaces one raw resource**, at every
tier. A heart is worth exactly the raw it saves: little on T4 ore, a lot on T8.3 fiber. Treeheart
→ planks, Rockheart → blocks, Mountainheart → bars, Beastheart → leather, Vineheart → cloth.
Shadowheart converts 1:1 into any other heart for 5,000 silver.

Faction transports are fully deterministic. You pay your own hearts, carry a pack to a rival's
smuggling agent, swap it, bring theirs back and get the rival's hearts. The reward depends on the
zone distance:

| Pack | Pay | 4–8 zones | 6–9 | 8–10 | 10–12 | To/from Caerleon (5–7) |
|---|---|---|---|---|---|---|
| Light (125 kg, drop 2 on death) | 3 | 4 | 4 | 5 | 5 | 6 |
| Medium (668 kg, drop 5) | 7 | 9 | 10 | 11 | 12 | 11 |
| Heavy (1,623 kg, drop 10) | 15 | 18 | 19 | 21 | 22 | 21 |

Only Bridgewatch offers the 6–9 band. The other royal stores offer 4–8, 8–10 and 10–12. Silver maps every
store and agent, measures the shortest path, and prices each route at current heart values, with
the zone risk along the way.
</details>

<details>
<summary><b>Laborers, farming and focus</b></summary>

**Laborers.** Each journal has a fame target, a base return and a loot table. Happiness comes from
the bed and table (T2 100 … T8 400 each) plus trophies, and
`yield = min(1.5, happiness / 600)`, so a T8 bed and table give 133%. Returns are valued at the
7-day average by default, since you sell them over time. Contracts, houses and laborer level
aren't modelled.

**Farming.** Crops return 3–6 (doubled with premium) every 22 hours. Seed return rises by tier,
and watering adds the opposite amount. At NPC seed prices, watering is worth exactly 4 silver per
focus at every tier. Animals need `grow time ÷ seconds per nutrition` nutrition and gain offspring
chance per focused feeding.

**Focus.** A use is ranked by the silver focus *adds* (profit with focus minus profit without),
per point of focus after your reduction. Crafts are capped at 10% of daily traded volume.
</details>

<details>
<summary><b>Fair price</b></summary>

Fair price is what an item actually trades at, taken from the daily sale history rather than one
listing. It's the volume-weighted median of the daily average price over the last 14 days (28 if
it traded on fewer than 3 days), per city and pooled across cities. The Black Market is kept
separate. Days priced more than 4× away from the typical day are dropped first, so troll trades
can't set it. Items that sold on only a day or two can still be off, so the days traded are
always shown. Prices marked `~` are typical prices filling a gap where there's no live listing.
</details>

## The eye (optional)

On Windows (or WSL), Silver can read your inventory off the screen and value it. It's an extra
download (OCR), so it isn't installed by default. Add it in PowerShell:

```powershell
uv tool install --force "project-silver[eye] @ https://github.com/xerostra/project-silver/archive/refs/heads/main.zip"
silver eye                # read your bag once; --every 5 keeps reading
```

The installer leaves the eye out, so run this line again after updating.

Open your bag in game, windowed or borderless. Exclusive fullscreen can capture as black. It reads
items, tiers, enchants, stack counts, your silver and gold, the zone and the game's own value
estimate, using only screen pixels and OCR on your CPU. Nothing reads the game or sends input.

With the eye installed, the **Session** tab records your silver and inventory every 5 seconds
while a session runs, so profit = silver gained + fair value of the items gained. Without it, type
your silver at the start and end. **Pop out overlay** opens a small always-on-top window with the
clock, profit, rate and goals. It needs the dashboard opened at `http://localhost:8765`.

## Always on (optional)

For most players, running Silver while you play is plenty. Kills older than about an hour and
prices nobody recorded can't be fetched later, though. If you have an always-on machine (a home
server, a NAS, a Raspberry Pi or a small VPS) and want unbroken history, run it there.

**With Docker:**

```bash
git clone https://github.com/xerostra/project-silver
cd project-silver
# set SILVER_SERVER in compose.yaml to asia, americas or europe
docker compose up -d                   # then open http://localhost:8765 on that machine
git pull && docker compose up -d --build   # update later
```

Data lives in the `silver-data` volume and survives updates.

**Without Docker** (Linux): clone the repo and run `uv sync` in it, then install
`deploy/silver-collect.service` (instructions inside). It runs the collector under systemd and
restarts it if it ever stops. `silver serve` is the
dashboard. Either one collects when the other isn't, and they never double-count.

- **Load:** every item's prices every 5 minutes, sale history every 3 hours, and kills every
  30 seconds. That's about 15 MB of kills a day and a mostly idle CPU core.
- **One game server per instance.** For two, run a second one with another `SILVER_SERVER`,
  port and volume (or data folder).
- **Back up.** The kill archive and history older than ~4 months exist nowhere else.
  `silver backup <folder>` (in Docker: `docker compose exec silver silver backup /data/backups`)
  makes a consistent copy while it runs.

### Exposing it

The dashboard has **no login**. Anyone who can reach it can see it and start or stop sessions.
By default it only listens on the machine itself. To reach it from other devices, open it on
your home network only (`"8765:8765"` in `compose.yaml`, or `silver serve --host 0.0.0.0`). If
you want it on the internet, put it behind a reverse proxy with authentication, or a private
network such as Tailscale or WireGuard. Don't expose port 8765 directly.

Please be gentle with the free APIs. Each instance already stays under the Data Project's rate
limits, but you don't need more than one instance per server.

## For developers

```bash
uv sync                                # install pinned dependencies into .venv
uv run silver serve                    # dashboard at http://localhost:8765 (also collects)
uv run silver collect                  # collection only
uv run silver scan --strategy instant --max-age 1 --min-volume 50 --capital 2000000
uv run silver rebuild-kills            # recompute the killboard and Meta from the raw kill archive
uv run python -m unittest discover tests   # tests (add --extra eye for the eye's)
```

From a source checkout, data lives in the repo's `data/`. Installed, it's in your user data folder
(`silver/paths.py`). `SILVER_DATA` overrides both, and `SILVER_SERVER` sets the default server.
On WSL, if the Windows browser can't reach `localhost`, use `serve --host 0.0.0.0`. The UI toolkit
reference is at `/kit`. Settings (taxes, defaults) are in `silver/config.py`.

```
 collector (one per server)                 data/<server>/                     dashboard
 ─ market sync: prices 5 min, history 3 h ─► market.db  (SQLite, WAL)  ◄─ reads ─ serve
 ─ kill feed: every 30 s ─┬───────────────► kills.db   (SQLite, WAL)  ◄─ reads ─ serve
                          └─ raw first ───► killboard/<day>.<run>.jsonl.gz  (append-only)
```

- **One collector per server**, whichever process holds `data/<server>/collector.lock`. A crash
  never leaves the lock stuck, and every kill is keyed by its event id, so nothing double-counts.
- **Nothing is lost on a crash.** Each write is one transaction. Kills are archived (and fsynced)
  before the database, and failed API calls retry with backoff.
- **Schema versions.** Each database upgrades itself on start. Code older than the file refuses to
  open it rather than misread it.
- **API manners.** gzip on every request (~25× smaller), one shared limiter under 180 requests/min
  and 300 per 5 min, and item lists batched under 4,096-character URLs.
- **Icons** are cached locally from Albion's render service, which is slow and has no long-term cache.

`docs/platform.md` has the data sources, what was verified, and ideas for where this could go.
Issues and pull requests are welcome.

## Credits and disclaimer

- Market data: [Albion Online Data Project](https://www.albion-online-data.com/), contributed by
  players running its data client.
- Game data (items, recipes, zones, quests): [ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps).
- Kills, players and guilds: the official Albion Online gameinfo API. Item icons: Albion's render service.

Project Silver is a fan project. It isn't affiliated with or endorsed by Sandbox Interactive.
Albion Online and its item art are trademarks and property of Sandbox Interactive GmbH.

Released under the [MIT License](LICENSE).
