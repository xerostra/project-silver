"""Settings for project-silver. Edit these to match how you play."""

# Game servers: market data host, official game API host, and faction-quest file in the dumps.
SERVERS = {
    "asia": {"label": "Asia", "host": "https://east.albion-online-data.com",
             "gameinfo": "https://gameinfo-sgp.albiononline.com/api/gameinfo", "quests": "quests_asia.json"},
    "americas": {"label": "Americas", "host": "https://west.albion-online-data.com",
                 "gameinfo": "https://gameinfo.albiononline.com/api/gameinfo", "quests": "quests.json"},
    "europe": {"label": "Europe", "host": "https://europe.albion-online-data.com",
               "gameinfo": "https://gameinfo-ams.albiononline.com/api/gameinfo", "quests": "quests_europe.json"},
}
SERVER = "asia"
HOST = SERVERS[SERVER]["host"]
GAMEINFO_HOST = SERVERS[SERVER]["gameinfo"]


def select(server):
    """Switch every default to another server. Call before any client or cache is created
    (the CLI does it first thing); modules read these values when they need them."""
    global SERVER, HOST, GAMEINFO_HOST
    SERVER, HOST, GAMEINFO_HOST = server, SERVERS[server]["host"], SERVERS[server]["gameinfo"]

ROYAL_CITIES = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford"]
CITIES = ROYAL_CITIES + ["Caerleon", "Brecilien"]
BLACK_MARKET = "Black Market"
MARKETS = CITIES + [BLACK_MARKET]  # every location with a player market

# Market fees (fractions). Sales tax is charged on every sale; the setup fee is
# charged whenever you *place* an order (buy or sell), not when you fill one.
SALES_TAX_PREMIUM = 0.04
SALES_TAX_NO_PREMIUM = 0.08
SETUP_FEE = 0.025

# Defaults the dashboard starts with (all adjustable in the UI).
PREMIUM = True
CAPITAL = 5_000_000
# Crafting station fee, silver per 100 nutrition (shown on the station; varies by owner).
STATION_FEE = 300
# Share of an item's daily traded volume you can realistically capture yourself.
VOLUME_CAPTURE = 0.10

# Flip scanner: ignore prices older than this, and keep this many best results.
SCAN_MAX_AGE_HOURS = 24
SCAN_KEEP = 20000

QUALITY_NAMES = {1: "Normal", 2: "Good", 3: "Outstanding", 4: "Excellent", 5: "Masterpiece"}
