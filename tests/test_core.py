"""Unit tests for the pure logic. Run: python3 -m unittest discover tests"""
import datetime
import random
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

from silver import api as api_mod, collect, enchanting, production, icons as icons_mod, crafting, farming, flips, focus, gold, hearts, intel, items, killboard, killstore, laborers, meta, store, transports
from silver import db as db_mod
from silver.api import URL_LIMIT, AlbionAPI, parse_price_row, parse_ts

NOW = 1_800_000_000


def row(item, city, sell=None, buy=None, quality=1, age=0):
    return {"item_id": item, "city": city, "quality": quality,
            "sell": sell, "sell_at": NOW - age if sell else None,
            "buy": buy, "buy_at": NOW - age if buy else None}


def only(found, **match):
    hits = [f for f in found if all(f[k] == v for k, v in match.items())]
    assert len(hits) == 1, hits
    return hits[0]


class FindFlips(unittest.TestCase):
    def test_instant_to_black_market(self):
        found = flips.find([row("X", "Caerleon", sell=100), row("X", "Black Market", buy=150)], now=NOW)
        f = only(found, strategy="instant")
        self.assertEqual((f["from"], f["to"], f["risk"]), ("Caerleon", "Black Market", "none"))
        self.assertEqual(f["profit"], round(150 * 0.96 - 100))

    def test_black_market_is_never_a_source_or_order_target(self):
        found = flips.find([row("X", "Black Market", sell=10, buy=500), row("X", "Martlock", sell=400, buy=20)], now=NOW)
        self.assertTrue(all(f["from"] != "Black Market" for f in found))
        self.assertTrue(all(not (f["to"] == "Black Market" and f["strategy"] != "instant") for f in found))

    def test_stale_prices_are_ignored(self):
        rows = [row("X", "Martlock", sell=100, age=48 * 3600), row("X", "Thetford", buy=200)]
        self.assertEqual(flips.find(rows, now=NOW, max_age_hours=24), [])

    def test_better_quality_can_fill_lower_quality_buy_order(self):
        rows = [row("X", "Lymhurst", sell=100, quality=3), row("X", "Black Market", buy=200, quality=2)]
        f = only(flips.find(rows, now=NOW), strategy="instant")
        self.assertEqual((f["quality"], f["src_quality"]), (2, 3))

    def test_unprofitable_after_fees_is_dropped(self):
        # 102 * (1 - 0.04) = 97.9 < 100
        self.assertEqual(flips.find([row("X", "Martlock", sell=100), row("X", "Thetford", buy=102)], now=NOW), [])

    def test_premium_changes_tax(self):
        self.assertAlmostEqual(flips.profit("instant", 100, 200, premium=False), 200 * 0.92 - 100)
        self.assertAlmostEqual(flips.profit("spread", 100, 200, premium=True), 200 * (1 - 0.065) - 100 * 1.025)


class ApplyHistory(unittest.TestCase):
    def flip(self, strategy, sell):
        return {"strategy": strategy, "buy": 100, "sell": sell, "profit": sell - 100, "roi": 0}

    def test_sell_order_capped_at_7_day_average(self):
        f = self.flip("order", 8_888_888)
        self.assertTrue(flips.apply_history(f, {"vol": 10, "avg": 150, "days": 7}, premium=True))
        self.assertEqual((f["sell"], f["listed"]), (150, 8_888_888))
        self.assertEqual(f["profit"], round(150 * 0.935 - 100))

    def test_sell_order_without_history_is_dropped(self):
        self.assertFalse(flips.apply_history(self.flip("order", 500), None, premium=True))

    def test_instant_keeps_real_buy_order_price(self):
        f = self.flip("instant", 500)
        self.assertTrue(flips.apply_history(f, None, premium=True))
        self.assertEqual(f["sell"], 500)


class RouteRisk(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(flips.route_risk("Martlock", "Thetford"), "safe")
        self.assertEqual(flips.route_risk("Martlock", "Black Market"), "red")
        self.assertEqual(flips.route_risk("Black Market", "Caerleon"), "none")
        self.assertEqual(flips.route_risk("Brecilien", "Caerleon"), "mists")


class Api(unittest.TestCase):
    def test_batches_respect_url_limit(self):
        api = AlbionAPI("https://example.com")
        ids = [f"T8_2H_SOMETHING_LONG_NAME_{i}@3" for i in range(1000)]
        params = {"locations": "Martlock,Thetford"}
        batches = list(api._batches(ids, "prices", params))
        self.assertEqual(sum(batches, []), ids)
        for b in batches:
            url = f"https://example.com/api/v2/stats/prices/{','.join(b)}.json?locations=Martlock,Thetford"
            self.assertLessEqual(len(url.replace("@", "%40")), URL_LIMIT)

    def test_parse_ts(self):
        self.assertIsNone(parse_ts("0001-01-01T00:00:00"))
        self.assertEqual(parse_ts("1970-01-01T00:01:00"), 60)


class Crafting(unittest.TestCase):
    def test_market_id(self):
        self.assertEqual(crafting._market_id("T5_METALBAR_LEVEL1", "1"), "T5_METALBAR_LEVEL1@1")
        self.assertEqual(crafting._market_id("T5_METALBAR", "0"), "T5_METALBAR")
        self.assertEqual(crafting._market_id("T5_METALBAR", None), "T5_METALBAR")

    def test_pick_skips_faction_token_recipe(self):
        plain = {"craftresource": [{"@uniquename": "T5_ORE", "@count": "3"}]}
        token = {"craftresource": [{"@uniquename": "T1_FACTION_MOUNTAIN_TOKEN_1", "@count": "1"}]}
        self.assertIs(crafting._pick([token, plain]), plain)

    def test_classify(self):
        bar = {"@shopcategory": "crafting", "@shopsubcategory1": "refinedresources", "@craftingcategory": "ore"}
        self.assertEqual(crafting._classify("simpleitem", bar), ("Refining", "Metal Bars", "refine:ore"))
        staff = {"@shopcategory": "weapons", "@shopsubcategory1": "cursestaff"}
        self.assertEqual(crafting._classify("weapon", staff), ("Weapons", "Cursed Staff", "cursestaff"))
        self.assertEqual(crafting.SPECIALTY_CITY["cursestaff"], "Bridgewatch")
        self.assertIsNone(crafting._classify("furnitureitem", {"@shopcategory": "furniture"}))

    def test_each_fish_is_its_own_chopping_recipe(self):
        fish = lambda name, made: {"@amountcrafted": made, "craftresource": {"@uniquename": name, "@count": "1"}}
        chops = {"@uniquename": "T1_FISHCHOPS", "@tier": "1", "@shopcategory": "crafting", "@shopsubcategory1": "fish",
                 "craftingrequirements": [fish("T3_FISH_A", "3"), fish("T7_FISH_B", "30")]}
        sauce = {"@uniquename": "T1_FISHSAUCE_LEVEL1", "@tier": "1", "@shopcategory": "crafting",
                 "@shopsubcategory1": "fish", "craftingrequirements": fish("T1_FISHCHOPS", "1")}
        out = crafting.build_recipes([("simpleitem", chops), ("simpleitem", sauce)], {"T7_FISH_B": "Eel"})
        self.assertEqual(sorted(out), ["craft:T1_FISHCHOPS<T3_FISH_A", "craft:T1_FISHCHOPS<T7_FISH_B",
                                       "craft:T1_FISHSAUCE_LEVEL1"])  # one recipe: keyed as usual
        eel = out["craft:T1_FISHCHOPS<T7_FISH_B"]
        self.assertEqual((eel["id"], eel["tier"], eel["amount"], eel["icon"]), ("T1_FISHCHOPS", 7, 30, "T7_FISH_B"))
        self.assertEqual(eel["name"], "T1_FISHCHOPS ← Eel")


class Enchanting(unittest.TestCase):
    SWORD = {"@uniquename": "T6_MAIN_SWORD", "@tier": "6", "@shopcategory": "weapons", "@shopsubcategory1": "sword",
             "craftingrequirements": {"craftresource": [{"@uniquename": "T6_METALBAR", "@count": "16"}]},
             "enchantments": {"enchantment": [
                 {"@enchantmentlevel": "1", "upgraderequirements": {"upgraderesource": {"@uniquename": "T6_RUNE", "@count": "288"}},
                  "craftingrequirements": {"craftresource": [{"@uniquename": "T6_METALBAR_LEVEL1", "@enchantmentlevel": "1", "@count": "16"}]}},
                 {"@enchantmentlevel": "2", "upgraderequirements": {"upgraderesource": {"@uniquename": "T6_SOUL", "@count": "288"}}}]}}

    def test_upgrade_consumes_previous_level_and_runes(self):
        out = crafting.build_recipes([("weapon", self.SWORD)], {})
        one = out["enchant:T6_MAIN_SWORD@1"]
        self.assertEqual(one["inputs"], [["T6_MAIN_SWORD", 1, False], ["T6_RUNE", 288, False]])
        self.assertEqual(out["enchant:T6_MAIN_SWORD@2"]["inputs"][0][0], "T6_MAIN_SWORD@1")  # chains
        self.assertEqual((one["section"], one["group"], one["upgrade"]), ("Enchanting", "Sword", True))
        self.assertEqual((one["focus"], one["silver"], one["value"], one["amount"]), (0, 0, 0, 1))
        self.assertIsNone(one["bonus_city"])  # no city bonus applies to enchanting

    def test_nothing_is_returned_when_enchanting(self):
        out = crafting.build_recipes([("weapon", self.SWORD)], {})
        self.assertTrue(all(not returned for _, _, returned in out["enchant:T6_MAIN_SWORD@1"]["inputs"]))

    def test_craft_and_enchant_recipes_coexist(self):
        out = crafting.build_recipes([("weapon", self.SWORD)], {})
        self.assertEqual(out["craft:T6_MAIN_SWORD@1"]["inputs"], [["T6_METALBAR_LEVEL1@1", 16, True]])
        self.assertEqual(out["craft:T6_MAIN_SWORD@1"]["section"], "Weapons")


class EstimatedUnits(unittest.TestCase):
    def flip(self, buy, vol):
        return {"buy": buy, "profit": 10, "vol": vol}

    def test_capped_by_capital_and_volume(self):
        self.assertEqual(flips.est_units(self.flip(100, 10_000), capital=5_000, capture=0.1), 50)   # capital
        self.assertEqual(flips.est_units(self.flip(100, 300), capital=5_000_000, capture=0.1), 30)  # volume
        self.assertEqual(flips.est_daily(self.flip(100, 300), capital=5_000_000, capture=0.1), 300)

    def test_whole_units_only(self):
        self.assertEqual(flips.est_units(self.flip(100, 148), capital=5_000_000, capture=0.1), 14)

    def test_unknown_without_history(self):
        self.assertIsNone(flips.est_units({"buy": 100, "profit": 10, "vol": None}))
        self.assertIsNone(flips.est_daily({"buy": 100, "profit": 10, "vol": None}))


class ItemLookup(unittest.TestCase):
    NAMES = {
        "T4_BAG": "Adept's Bag", "T4_BAG@1": "Adept's Bag", "T5_BAG": "Expert's Bag",
        "T4_ORE": "Iron Ore", "T4_ORE_LEVEL1@1": "Uncommon Iron Ore", "T5_ORE": "Titanium Ore",
        "T6_MAIN_SWORD": "Master's Broadsword", "T4_JOURNAL_FISHING_EMPTY": "Empty journal",
    }

    def setUp(self):
        self._saved = items._names
        items._names = dict(self.NAMES)

    def tearDown(self):
        items._names = self._saved

    def test_base_of_handles_both_enchant_styles(self):
        self.assertEqual(items.base_of("T6_MAIN_SWORD@2"), "MAIN_SWORD")
        self.assertEqual(items.base_of("T4_ORE_LEVEL3@3"), "ORE")
        self.assertEqual(items.base_of("T4_ORE"), "ORE")
        self.assertIsNone(items.base_of("UNIQUE_HIDEOUT"))

    def test_variants_lists_existing_tiers_and_enchants(self):
        self.assertEqual(items.variants("T4_BAG"),
                         [{"tier": 4, "enchant": 0, "id": "T4_BAG"},
                          {"tier": 4, "enchant": 1, "id": "T4_BAG@1"},
                          {"tier": 5, "enchant": 0, "id": "T5_BAG"}])

    def test_variants_of_a_resource_find_the_level_spelling(self):
        # starting from the plain id, which doesn't reveal how its enchants are spelled
        self.assertIn({"tier": 4, "enchant": 1, "id": "T4_ORE_LEVEL1@1"}, items.variants("T4_ORE"))
        self.assertIn({"tier": 4, "enchant": 1, "id": "T4_ORE_LEVEL1@1"}, items.variants("T4_ORE_LEVEL1@1"))

    def test_search_matches_every_word_and_ranks_exact_names_first(self):
        self.assertEqual([r["id"] for r in items.search("bag")], ["T4_BAG", "T4_BAG@1", "T5_BAG"])
        self.assertEqual([r["id"] for r in items.search("expert bag")], ["T5_BAG"])
        self.assertEqual([r["id"] for r in items.search("4.1 bag")], ["T4_BAG@1"])  # by tier label
        self.assertEqual([r["id"] for r in items.search("T5_ORE")], ["T5_ORE"])
        self.assertEqual(items.search("   "), [])

    def test_search_skips_untradeable(self):
        self.assertEqual(items.search("journal"), [])


class Hearts(unittest.TestCase):
    PLAIN = {"craftresource": [{"@uniquename": "T8_FIBER_LEVEL3", "@enchantmentlevel": "3", "@count": "5"},
                               {"@uniquename": "T7_CLOTH_LEVEL3", "@enchantmentlevel": "3", "@count": "1"}]}
    WITH_HEART = {"craftresource": [{"@uniquename": "T8_FIBER_LEVEL3", "@enchantmentlevel": "3", "@count": "4"},
                                    {"@uniquename": "T1_FACTION_SWAMP_TOKEN_1", "@count": "1"},
                                    {"@uniquename": "T7_CLOTH_LEVEL3", "@enchantmentlevel": "3", "@count": "1"}]}

    def test_heart_replaces_one_raw_with_market_ids(self):
        self.assertEqual(crafting.token_option([self.PLAIN, self.WITH_HEART], self.PLAIN),
                         {"id": "T1_FACTION_SWAMP_TOKEN_1", "count": 1, "saves_id": "T8_FIBER_LEVEL3@3", "saves_count": 1})

    def test_plain_recipe_is_still_the_default(self):
        self.assertIs(crafting._pick([self.WITH_HEART, self.PLAIN]), self.PLAIN)

    def test_no_heart_variant(self):
        self.assertIsNone(crafting.token_option([self.PLAIN], self.PLAIN))
        self.assertIsNone(crafting.token_option([self.WITH_HEART], None))

    def test_hearts_are_tradeable_but_quest_tokens_are_not(self):
        saved = items._names
        items._names = {"T1_FACTION_SWAMP_TOKEN_1": "Vineheart", "T4_ARTEFACT_TOKEN_FAVOR_1": "Crystallized Spirit",
                        "QUESTITEM_EXP_TOKEN_D1": "quest", "UNIQUE_TOKEN_X": "unique"}
        try:
            self.assertEqual(sorted(items.tradeable_ids()), ["T1_FACTION_SWAMP_TOKEN_1", "T4_ARTEFACT_TOKEN_FAVOR_1"])
        finally:
            items._names = saved

    def test_catalog_links_each_heart_to_its_refining_line(self):
        fake = {"craft:T8_CLOTH_LEVEL3@3": {
            "id": "T8_CLOTH_LEVEL3@3", "name": "Exceptional Baroque Cloth", "tier": 8, "enchant": 3, "group": "Cloth",
            "token": {"id": "T1_FACTION_SWAMP_TOKEN_1", "count": 1, "saves_id": "T8_FIBER_LEVEL3@3", "saves_count": 1}},
            "craft:T6_MAIN_SWORD": {"id": "T6_MAIN_SWORD", "name": "Broadsword", "tier": 6, "enchant": 0,
                                    "group": "Sword", "token": None}}
        with mock.patch.object(hearts, "recipes", return_value=fake), \
                mock.patch.object(hearts, "names", return_value={"T1_FACTION_SWAMP_TOKEN_1": "Vineheart"}):
            (use,) = hearts.uses()
            self.assertEqual((use["heart_name"], use["raw"], use["saves"], use["line"]),
                             ("Vineheart", "T8_FIBER_LEVEL3@3", 1, "Cloth"))
            vine = next(h for h in hearts.catalog() if h["id"] == "T1_FACTION_SWAMP_TOKEN_1")
            self.assertEqual(vine["lines"], ["Cloth"])
            self.assertIn("T8_FIBER_LEVEL3@3", hearts.item_ids())
            self.assertEqual(len(hearts.catalog()), 6)  # every heart listed, Shadowheart included


class Laborers(unittest.TestCase):
    CFG = {"max_yield": 1.5, "points_per_bar": 100, "max_bars": 9, "full_yield_points": 600}
    ORE = {"@uniquename": "T5_JOURNAL_ORE", "@maxfame": "3600", "@baselootamount": "32",
           "craftingrequirements": {"@silver": "4000"}, "lootlist": {"loot": [
               {"@itemname": "T5_ORE", "@itemamount": "1", "@weight": "1889"},
               {"@itemname": "T5_ORE_LEVEL1", "@itemenchantmentlevel": "1", "@itemamount": "1", "@weight": "100"},
               {"@itemname": "T5_ORE_LEVEL2", "@itemenchantmentlevel": "2", "@itemamount": "1", "@weight": "11"}]}}
    MERC = {"@uniquename": "T5_JOURNAL_MERCENARY", "@maxfame": "4368", "@baselootamount": "1",
            "craftingrequirements": {"@silver": "4000"},
            "lootlist": {"loot": {"@silveramount": "1746", "@weight": "1"}}}

    def test_yield_follows_happiness_and_caps(self):
        self.assertEqual(laborers.yield_multiplier(600, self.CFG), 1.0)
        self.assertAlmostEqual(laborers.yield_multiplier(800, self.CFG), 4 / 3)  # T8 bed + T8 table
        self.assertEqual(laborers.yield_multiplier(2000, self.CFG), 1.5)
        self.assertEqual(laborers.yield_multiplier(-10, self.CFG), 0.0)

    def test_laborer_names(self):
        self.assertEqual(laborers.laborer_name("Expert Prospector's Journal (Partially Full)"), "Prospector")
        self.assertEqual(laborers.laborer_name("Adept's Generalist Trophy Journal (Partially Full)"), "Generalist")
        self.assertEqual(laborers.laborer_name("Elder Fisherman's Trophy Journal"), "Fisherman")

    def test_jobs_parse_loot_shares_and_market_ids(self):
        (ore,) = laborers.build_jobs([("journalitem", self.ORE)], {"T5_JOURNAL_ORE": "Expert Prospector's Journal"})
        self.assertEqual((ore["laborer"], ore["base"], ore["fame"], ore["empty_silver"]), ("Prospector", 32, 3600, 4000))
        self.assertEqual([i for i, _, _ in ore["loot"]], ["T5_ORE", "T5_ORE_LEVEL1@1", "T5_ORE_LEVEL2@2"])
        self.assertAlmostEqual(sum(share for _, _, share in ore["loot"]), 1.0)
        self.assertEqual((ore["full"], ore["empty"]), ("T5_JOURNAL_ORE_FULL", "T5_JOURNAL_ORE_EMPTY"))

    def test_mercenary_returns_silver(self):
        (merc,) = laborers.build_jobs([("journalitem", self.MERC)], {})
        self.assertEqual((merc["silver"], merc["loot"]), (1746.0, []))

    def test_malformed_journals_are_skipped(self):
        broken = {"@uniquename": "T5_JOURNAL_WOOD", "@baselootamount": "0", "lootlist": {"loot": []}}
        self.assertEqual(laborers.build_jobs([("journalitem", broken), ("simpleitem", self.ORE)], {}), [])

    def test_furniture_happiness(self):
        bed = {"@uniquename": "T8_FURNITUREITEM_BED", "@tier": "8", "@labourerfurnituretype": "bed",
               "@labourerhappiness": "400", "@labourersperfurnitureitem": "1"}
        table = {"@uniquename": "T8_FURNITUREITEM_TABLE", "@tier": "8", "@labourerfurnituretype": "table",
                 "@labourerhappiness": "400", "@labourersperfurnitureitem": "6"}
        f = laborers.build_furniture([("furnitureitem", bed), ("furnitureitem", table)])
        self.assertEqual(f["beds"][8], {"happiness": 400, "serves": 1})
        self.assertEqual(f["tables"][8]["serves"], 6)


class Farming(unittest.TestCase):
    CABBAGE = {"id": "T5_FARM_CABBAGE_SEED", "crop": "T5_CABBAGE", "harvest": 4.5, "extras": [["T1_WORM", 0.1]],
               "seed_chance": 0.8, "focus_bonus": 0.4, "focus_cost": 1000, "npc_price": 10000, "hours": 22}
    OX = {"id": "T6_FARM_OX_BABY", "grown": "T6_FARM_OX_GROWN", "offspring_chance": 0.8104, "focus_bonus": 0.0593,
          "focus_cost": 1000, "feedings": 4, "npc_price": 675000, "days": 7.83, "food_category": "plants",
          "nutrition": 12961}
    FOODS = {"plants": [["T1_CARROT", 48], ["T5_CABBAGE", 48]]}

    def test_loot_list_averages_ranges(self):
        raw = b'{"x": [{"@name": "T5_CABBAGE_LOOT", "Item": [{"@type": "T5_CABBAGE", "@chance": "1.0", "@amount": "3-6"},' \
              b'{"@type": "T1_WORM", "@chance": "0.1", "@amount": "1"}]}]}'
        self.assertEqual(farming._loot_lists(raw)["T5_CABBAGE_LOOT"], [("T5_CABBAGE", 1.0, 4.5), ("T1_WORM", 0.1, 1.0)])

    def test_seed_value_never_exceeds_npc_price(self):
        self.assertEqual(farming.unit_value(15000, 10000), 10000)
        self.assertEqual(farming.unit_value(None, 10000), 10000)
        self.assertEqual(farming.unit_value(8000, None), 8000)
        self.assertIsNone(farming.unit_value(None, None))

    def test_plant_premium_doubles_harvest_and_watering_adds_seeds(self):
        prices = {"T5_CABBAGE": 300, "T1_WORM": 100, "T5_FARM_CABBAGE_SEED": 9000}
        get = prices.get
        dry = farming.plant_result(self.CABBAGE, get, get, premium=False, water=False)
        wet = farming.plant_result(self.CABBAGE, get, get, premium=True, water=True)
        self.assertEqual((dry["harvest"], wet["harvest"]), (4.5, 9.0))
        self.assertAlmostEqual(wet["seeds_back"], 1.2)
        self.assertAlmostEqual(dry["profit"], 4.5 * 300 + 0.1 * 100 + 0.8 * 9000 - 9000)
        self.assertAlmostEqual(wet["focus_value"], 0.4 * 9000 / 1000)

    def test_animal_focus_feedings_and_feed_cost(self):
        prices = {"T6_FARM_OX_GROWN": 900000, "T6_FARM_OX_BABY": 500000, "T1_CARROT": 24, "T5_CABBAGE": 300}
        get = prices.get
        r = farming.animal_result(self.OX, get, get, self.FOODS, feed_with_focus=4)
        self.assertEqual(r["feed"], "T1_CARROT")                      # cheapest per nutrition
        self.assertAlmostEqual(r["feed_cost"], 12961 * 24 / 48)
        self.assertAlmostEqual(r["offspring"], 0.8104 + 4 * 0.0593)
        self.assertEqual(r["focus"], 4000)
        capped = farming.animal_result(self.OX, get, get, self.FOODS, feed_with_focus=99)
        self.assertEqual(capped["focus"], 4000)                       # can't exceed its feedings


class FocusPlanner(unittest.TestCase):
    BAR = {"id": "T5_METALBAR", "name": "Bar", "tier": 5, "enchant": 0, "section": "Refining", "group": "Metal Bars",
           "bonus": "refine:ore", "bonus_city": "Thetford", "upgrade": False, "amount": 1, "silver": 0,
           "focus": 94, "value": 32, "inputs": [["T5_ORE", 3, True], ["T4_METALBAR", 1, True]]}

    def market(self, bar_avg, vol=1000):
        fresh = NOW - 60
        prices = {"T5_ORE": {"Martlock": {"sell": 400, "sell_at": fresh}},
                  "T4_METALBAR": {"Martlock": {"sell": 500, "sell_at": fresh}}}
        volumes = {"T5_METALBAR": {"Thetford": {"avg": bar_avg, "vol": vol, "days": 7}}}
        return focus.Market(prices, volumes, premium=True, max_age_hours=6, now=NOW)

    def test_return_rates_match_the_game(self):
        self.assertAlmostEqual(focus.return_rate(self.BAR, focus=False), 0.58 / 1.58)   # 36.7%
        self.assertAlmostEqual(focus.return_rate(self.BAR, focus=True), 1.17 / 2.17)    # 53.9%
        plain = {**self.BAR, "bonus_city": None}
        self.assertAlmostEqual(focus.return_rate(plain, focus=False), 0.18 / 1.18)      # 15.3%

    def profits(self, bar_avg):
        sale = bar_avg * (1 - 0.04 - 0.025)
        fee = 32 * 0.1125 * 300 / 100
        mats = lambda rate: (3 * 400 + 500) * (1 - rate)
        return (sale - mats(0.58 / 1.58) - fee, sale - mats(1.17 / 2.17) - fee)

    def test_gain_is_what_focus_adds_when_profitable_anyway(self):
        without, with_focus = self.profits(2000)
        self.assertGreater(without, 0)
        use = focus.craft_use(self.BAR, self.market(2000), reduction=0.5)
        self.assertAlmostEqual(use["gain"], with_focus - without)
        self.assertAlmostEqual(use["focus"], 47)                        # 50% reduction
        self.assertAlmostEqual(use["per_focus"], (with_focus - without) / 47)

    def test_gain_is_the_whole_profit_when_only_focus_makes_it_pay(self):
        without, with_focus = self.profits(1000)
        self.assertLess(without, 0)
        self.assertGreater(with_focus, 0)
        self.assertAlmostEqual(focus.craft_use(self.BAR, self.market(1000))["gain"], with_focus)

    def test_unpriced_or_losing_recipes_are_skipped(self):
        self.assertIsNone(focus.craft_use(self.BAR, self.market(100)))               # loses even with focus
        empty = focus.Market({}, {}, now=NOW)
        self.assertIsNone(focus.craft_use(self.BAR, empty))                          # no prices

    def test_typical_prices_fill_in_only_where_nothing_is_live(self):
        fair = {"T5_ORE": {"Martlock": {"price": 999, "vol": 5}},
                "T4_METALBAR": {"Lymhurst": {"price": 450, "vol": 9}, "Thetford": {"price": 480, "vol": 2}},
                "T5_METALBAR": {"Thetford": {"price": 2000, "vol": 40}}}
        live = self.market(2000)
        live.fair = fair
        self.assertEqual((live.ask("T5_ORE"), live.estimated), (400, set()))       # a fresh ask wins
        blind = focus.Market({}, {}, premium=True, now=NOW, fair=fair)
        self.assertEqual(blind.ask("T4_METALBAR"), 450)                              # cheapest typical city
        self.assertAlmostEqual(blind.sale("T5_METALBAR"), 2000 * (1 - 0.04 - 0.025))  # as a sell order
        self.assertEqual(blind.daily_volume("T4_METALBAR"), 9)
        self.assertEqual(blind.estimated, {"T4_METALBAR", "T5_METALBAR"})
        use = focus.craft_use(self.BAR, blind)
        self.assertTrue(use["est"])
        self.assertFalse(focus.craft_use(self.BAR, self.market(2000))["est"])
        self.assertIsNone(focus.Market({}, {}, now=NOW).ask("T5_ORE"))              # nothing at all: still None

    def test_plan_is_greedy_within_caps(self):
        uses = [{"kind": "Refine", "id": "A", "focus": 100, "gain": 500, "per_focus": 5, "daily_cap": 3},
                {"kind": "Water", "id": "B", "focus": 1000, "gain": 3000, "per_focus": 3, "daily_cap": None},
                {"kind": "Craft", "id": "C", "focus": 50, "gain": 100, "per_focus": 2, "daily_cap": None}]
        p = focus.plan(uses, daily_focus=2500, farm_slots=1)
        self.assertEqual([(s["id"], s["times"]) for s in p["steps"]], [("A", 3), ("B", 1)])
        self.assertEqual((p["focus_used"], p["silver"]), (1300, 3 * 500 + 3000))   # C has no known volume
        guessed = [{**uses[0], "est": True}] + uses[1:]
        self.assertEqual([s["id"] for s in focus.plan(guessed, 2500, farm_slots=1)["steps"]], ["B"])  # estimates stay out


class Determinism(unittest.TestCase):
    def test_find_ignores_input_order(self):
        rng = random.Random(1)
        cities = ["Martlock", "Thetford", "Lymhurst", "Caerleon", "Black Market"]
        rows = [row(f"I{i}", c, sell=rng.randint(50, 150), buy=rng.randint(50, 150), quality=q)
                for i in range(20) for c in cities for q in (1, 2)]
        expected = flips.find(rows, now=NOW)
        for _ in range(5):
            rng.shuffle(rows)
            self.assertEqual(flips.find(rows, now=NOW), expected)

    def test_equal_profit_ties_are_ordered(self):
        rows = [row("B", "Martlock", sell=100), row("B", "Thetford", buy=200),
                row("A", "Martlock", sell=100), row("A", "Thetford", buy=200)]
        self.assertEqual([f["item_id"] for f in flips.find(rows, now=NOW) if f["strategy"] == "instant"], ["A", "B"])


class ParsingIsTolerant(unittest.TestCase):
    def test_bad_price_rows_are_skipped(self):
        good = {"item_id": "X", "city": "Martlock", "quality": 1, "sell_price_min": 10,
                "sell_price_min_date": "2026-01-01T00:00:00", "buy_price_max": 0,
                "buy_price_max_date": "0001-01-01T00:00:00"}
        self.assertEqual(parse_price_row(good)["sell"], 10)
        self.assertIsNone(parse_price_row({"city": "Martlock"}))                       # missing id
        self.assertIsNone(parse_price_row({**good, "sell_price_min_date": "garbage"}))  # no valid timestamp
        self.assertIsNone(parse_price_row({**good, "sell_price_min": "10"}))            # wrong type
        self.assertIsNone(parse_price_row({**good, "sell_price_min": -5}))


    def test_build_recipes_skips_malformed(self):
        sword = {"@uniquename": "T4_MAIN_SWORD", "@tier": "4", "@shopcategory": "weapons",
                 "@shopsubcategory1": "sword", "craftingrequirements": {
                     "@amountcrafted": "0", "craftresource": [{"@uniquename": "T4_METALBAR", "@count": "16"}]}}
        broken = {**sword, "@uniquename": "T5_MAIN_SWORD", "craftingrequirements": {"craftresource": [{"@count": "3"}]}}
        empty = {**sword, "@uniquename": "T6_MAIN_SWORD", "craftingrequirements": {"@craftingfocus": "0"}}
        out = crafting.build_recipes([("weapon", sword), ("weapon", broken), ("weapon", empty)], {})
        self.assertEqual(list(out), ["craft:T4_MAIN_SWORD"])
        self.assertEqual(out["craft:T4_MAIN_SWORD"]["amount"], 1)  # never divide by zero


class Downloads(unittest.TestCase):
    def test_invalid_download_keeps_old_copy(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "items.txt"
            path.write_text("old")
            resp = mock.Mock(content=b"<html>error</html>", raise_for_status=lambda: None)
            with mock.patch.object(items, "REFRESH_DAYS", 0), mock.patch.object(items.requests, "get", return_value=resp):
                items.cached_download("http://x", path, validate=lambda c: c.startswith(b"ok"))
            self.assertEqual(path.read_text(), "old")

    def test_temp_files_do_not_collide(self):
        with tempfile.TemporaryDirectory() as d:
            written = []
            resp = mock.Mock(content=b"ok", raise_for_status=lambda: None)
            real_write = Path.write_bytes
            with mock.patch.object(items.requests, "get", return_value=resp), \
                    mock.patch.object(Path, "write_bytes", lambda p, b: (written.append(p.name), real_write(p, b))):
                items.cached_download("http://x", Path(d) / "items.txt", validate=lambda c: True)
                items.cached_download("http://x", Path(d) / "items.json", validate=lambda c: True)
            self.assertEqual(len(set(written)), 2)


class Retries(unittest.TestCase):
    def test_retries_invalid_json_then_succeeds(self):
        bad = mock.Mock(status_code=200, headers={}, json=mock.Mock(side_effect=ValueError))
        good = mock.Mock(status_code=200, headers={}, json=mock.Mock(return_value=[{"ok": 1}]))
        client = AlbionAPI("https://example.com")
        client._local.session = mock.Mock(get=mock.Mock(side_effect=[bad, requests.ConnectionError(), good]))
        with mock.patch.object(api_mod.time, "sleep"):
            self.assertEqual(client._get("/p", {}), [{"ok": 1}])

    def test_client_error_is_not_retried(self):
        client = AlbionAPI("https://example.com")
        client._local.session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=404, headers={})))
        with self.assertRaises(api_mod.APIError) as caught:
            client._get("/p", {})
        self.assertEqual(client._local.session.get.call_count, 1)
        self.assertEqual(caught.exception.status, 404)

    def test_wrong_body_type_is_retried(self):
        as_list = mock.Mock(status_code=200, headers={}, json=mock.Mock(return_value=[]))
        as_dict = mock.Mock(status_code=200, headers={}, json=mock.Mock(return_value={"ok": 1}))
        client = api_mod.GameInfo("https://example.com")
        client._local.session = mock.Mock(get=mock.Mock(side_effect=[as_list, as_dict]))
        with mock.patch.object(api_mod.time, "sleep"):
            self.assertEqual(client._get("/p", {}, expect=dict), {"ok": 1})



class Transports(unittest.TestCase):
    """A line of zones: Thetford(0) - 1 - 2 - ... - 9, rival agents at zones 5 and 9."""

    @staticmethod
    def world():
        clusters = []
        for i in range(10):
            c = {"@id": str(i), "@displayname": f"Zone {i}", "@type": "OPENPVP_RED" if i == 3 else "SAFEAREA",
                 "exits": {"exit": [{"@targetid": f"x@{i + 1}"}]} if i < 9 else {}}
            if i == 0:
                c["questgivers"] = {"questgiver": {"@type": "FACTIONSTORE_SWAMP"}}
            if i in (5, 9):
                c["questgivers"] = {"questgiver": {"@type": "TRADING_OUTPOST_SMUGGLER_FOREST"}}
            clusters.append(c)
        clusters.append({"@id": "Debug", "@displayname": "Debug"})
        return {"world": {"clusters": {"cluster": clusters}}}

    @staticmethod
    def quest(name, lo, hi, cost, reward, targets=("Forest",)):
        return {"@uniquename": name, "@questtimeout": "86400",
                "variables": {"dynamicplayerfaction": {"filter": [{"@faction": t} for t in targets]},
                              "dynamicquestgiver": {"distancefromquestgiver": {"@mindistance": str(lo), "@maxdistance": str(hi)}}},
                "costs": {"item": {"@amount": str(cost)}}, "rewards": {"item": {"@count": str(reward)}}}

    def setUp(self):
        self.zones, self.graph, self.stores, self.agents = transports.parse_world(self.world())
        quests = {"quests": {"playerfactionquest": [
            self.quest("MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DISTANCE_A", 4, 8, 15, 18),
            self.quest("MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DISTANCE_C", 8, 10, 15, 21),
            self.quest("MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DEBUG", 0, 6, 15, 23)]}}
        self.missions = transports.parse_missions(quests)
        self.offers = {"Swamp": set(self.missions)}

    def test_world_parsing(self):
        self.assertNotIn("Debug", self.zones)
        self.assertEqual(self.stores, {"Swamp": "0"})
        self.assertEqual([a["zone"] for a in self.agents], ["5", "9"])
        self.assertIn("0", self.graph["1"])  # exits work both ways

    def test_missions_skip_debug_and_parse_bands(self):
        self.assertEqual(sorted(self.missions), ["MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DISTANCE_A",
                                                 "MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DISTANCE_C"])
        a = self.missions["MISSIONS_HEAVY_TRADE_PACK_TO_OUTPOST_DISTANCE_A"]
        self.assertEqual((a["size"], a["min"], a["max"], a["cost"], a["reward"], a["targets"]),
                         ("Heavy", 4, 8, 15, 18, ["Forest"]))

    def test_routes_pick_missions_by_distance(self):
        rows = transports.routes(self.zones, self.graph, self.stores, self.agents, self.missions, self.offers)
        by_zone = {r["agent_zone"]: r for r in rows}
        near, far = by_zone["Zone 5"], by_zone["Zone 9"]
        self.assertEqual((near["hops"], [m["reward"] for m in near["missions"]]), (5, [18]))    # band A only
        self.assertEqual((far["hops"], [m["reward"] for m in far["missions"]]), (9, [21]))      # band C only
        self.assertEqual((near["pay"], near["get"]), ("T1_FACTION_SWAMP_TOKEN_1", "T1_FACTION_FOREST_TOKEN_1"))
        self.assertEqual(near["risk"], {"safe": 4, "red": 1})

    def test_store_only_gets_missions_it_offers(self):
        rows = transports.routes(self.zones, self.graph, self.stores, self.agents, self.missions, {"Swamp": set()})
        self.assertEqual(rows, [])

    def test_no_route_to_own_faction(self):
        agents = [{"faction": "Swamp", "zone": "5"}]
        self.assertEqual(transports.routes(self.zones, self.graph, self.stores, agents, self.missions, self.offers), [])

    def test_shortest_path_is_deterministic(self):
        graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
        self.assertEqual(transports.shortest_path(graph, "a", "d"), ["a", "b", "d"])
        self.assertIsNone(transports.shortest_path(graph, "d", "a"))

    def test_zone_risk(self):
        self.assertEqual([transports.zone_risk(t) for t in ("OPENPVP_BLACK_2", "OPENPVP_RED", "OPENPVP_YELLOW", "SAFEAREA")],
                         ["black", "red", "yellow", "safe"])

    def test_packs(self):
        pack = {"@uniquename": "QUESTITEM_CARAVAN_TRADEPACK_SWAMP_HEAVY", "@weight": "1623",
                "replaceondeath": {"replacementitem": {"@uniquename": "T1_FACTION_SWAMP_TOKEN_1", "@count": "10"}}}
        self.assertEqual(transports.parse_packs([("simpleitem", pack)]), {"Heavy": {"weight": 1623, "drop": 10}})


class ServerSmoke(unittest.TestCase):
    """Every endpoint's payload builds against an empty market, so a missing name or a broken
    refactor fails here rather than in the browser."""

    def setUp(self):
        from silver import server
        self.server = server
        self.api = mock.patch.object(server, "api", mock.Mock(iter_batches=mock.Mock(return_value=iter(()))))
        self.api.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = mock.patch.object(server, "store", store.Store(Path(self.tmp.name) / "m.db"))
        self.store.start()
        self.kills = mock.patch.object(server, "kills", killstore.KillStore(Path(self.tmp.name) / "k.db"))
        self.kills.start()
        self.icons = mock.patch.object(server, "icons", mock.Mock())  # no real downloads from tests
        self.icons.start()
        server.snapshots.refreshed.clear()  # no state from other tests (and fails if the global is gone)

    def tearDown(self):
        self.icons.stop()
        self.kills.stop()
        self.store.stop()
        self.api.stop()
        self.tmp.cleanup()

    def test_payloads_build(self):
        s = self.server
        self.assertIn("groups", s.resources_payload(False))
        self.assertIn("hearts", s.hearts_payload(False))
        self.assertIn("jobs", s.laborers_payload(False))
        self.assertIn("plants", s.farming_payload({}, False))
        self.assertEqual(s.focus_payload({})["ready"], False)
        group = crafting.groups()[0]
        self.assertIn("recipes", s.craft_payload(group["section"], group["group"], False))
        self.assertIsNone(s.craft_payload("Nope", "Nope", False))
        self.assertIsNone(s.item_payload("NOPE", False))
        self.assertIn("variants", s.item_payload("T4_BAG", False))
        self.assertIn("events", s.killboard_payload())
        self.assertIn("weapons", s.meta_payload({"size": ["zvz"], "days": ["99"]}))
        self.assertEqual(s.market_payload({})["rows"], [])
        self.assertEqual(s.enchant_payload({"mode": ["nonsense"]})["rows"], [])
        group = next(g for g in crafting.groups() if g["group"] == "Metal Bars")
        planned = s.craft_payload(group["section"], group["group"], False, {"plan": ["1"], "city": ["Nowhere"]})
        self.assertEqual(len(planned["plans"]), len(planned["recipes"]))  # an empty market: nothing to plan with

    def test_session_endpoints(self):
        from silver import sessions
        s = self.server
        with mock.patch.object(s, "play", sessions.Sessions(Path(self.tmp.name) / "p.db")), \
                mock.patch.object(s, "watch", mock.Mock(status={"state": "off"})):
            self.assertIsNone(s.session_payload({})["current"])
            self.assertEqual(s.session_action("/api/session/silver", {"silver": 5})[0], 400)  # nothing running
            self.assertEqual(s.session_action("/api/session/start", {}), (200, {"ok": True}))
            s.watch.start.assert_called_once()
            self.assertEqual(s.session_action("/api/session/silver", {"silver": 1_000})[0], 200)
            self.assertEqual(s.session_action("/api/session/silver", {"silver": 4_000})[0], 200)
            self.assertEqual(s.session_action("/api/goals", {"kind": "silver", "target": 6_000, "period": "session"})[0], 200)
            self.assertEqual(s.session_action("/api/goals", {"kind": "nope", "target": 1, "period": "session"})[0], 400)
            got = s.session_payload({"tz": ["-420"]})
            self.assertEqual((got["current"]["silver"], got["goals"][0]["fraction"]), (3_000, .5))
            s.session_action("/api/session/stop", {})
            self.assertIsNone(s.session_payload({})["active"])
            self.assertEqual(s.session_action("/api/nope", {})[0], 404)

    def test_gold_serves_last_good_value_when_refresh_fails(self):
        s = self.server
        cache = s.Cached(s.build_gold, ttl=0)
        s.api.gold = mock.Mock(return_value=[{"price": 13000, "timestamp": "2026-09-01T00:00:00"}])
        self.assertEqual(cache.get()["summary"]["price"], 13000)
        s.api.gold = mock.Mock(side_effect=api_mod.APIError("down"))
        with mock.patch("traceback.print_exc"):
            got = cache.get()
        self.assertEqual(got["summary"]["price"], 13000)
        self.assertIn("down", got["warning"])

    def test_query_numbers_are_bounded(self):
        self.assertEqual(self.server.number({"x": ["abc"]}, "x", 5, 0, 10), 5)
        self.assertEqual(self.server.number({"x": ["99"]}, "x", 5, 0, 10), 10)
        self.assertEqual(self.server.number({"x": ["nan"]}, "x", 5, 0, 10), 5)
        self.assertEqual(self.server.number({}, "x", 5, 0, 10), 5)


class Toolkit(unittest.TestCase):
    """web/silver.css: every token used is defined, and every theme keeps text readable."""
    WEB = Path(__file__).resolve().parent.parent / "silver" / "web"
    TEXT_ROLES = ("--text", "--muted", "--accent", "--good", "--info", "--warn", "--bad")  # 4.5:1, WCAG AA

    @classmethod
    def setUpClass(cls):
        cls.css = (cls.WEB / "silver.css").read_text()

    def theme(self, selector):
        start = self.css.index(selector + " {")
        body = self.css[start:self.css.index("}", start)]
        return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", body))

    @staticmethod
    def contrast(a, b):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def test_pages_exist_and_only_use_defined_tokens(self):
        from silver import server
        for name, _ in server.PAGES.values():
            self.assertTrue((self.WEB / name).is_file(), name)
        defined = set(re.findall(r"(--[\w-]+)\s*:", self.css))
        for name in ("silver.css", "index.html", "kit.html"):
            used = set(re.findall(r"var\((--[\w-]+)[,)]", (self.WEB / name).read_text()))  # skips JS-built names
            self.assertEqual(used - defined, set(), name)

    def test_both_light_theme_copies_match(self):
        # Light is written twice: once for the system preference, once for data-theme="light".
        self.assertEqual(self.theme(':root:not([data-theme="dark"])'), self.theme(':root[data-theme="light"]'))

    def test_text_roles_are_readable_on_every_surface(self):
        dark = self.theme(":root")
        for name, theme in (("dark", dark), ("light", {**dark, **self.theme(':root[data-theme="light"]')})):
            for bg in ("--bg", "--panel", "--panel-2"):
                for fg in self.TEXT_ROLES:
                    self.assertGreaterEqual(self.contrast(theme[fg], theme[bg]), 4.5, f"{name} {fg} on {bg}")
                self.assertGreaterEqual(self.contrast(theme["--faint"], theme[bg]), 3, f"{name} --faint on {bg}")
            self.assertGreaterEqual(self.contrast(theme["--on-accent"], theme["--accent"]), 4.5, f"{name} primary button")


class Gold(unittest.TestCase):
    def test_points_are_sorted_deduplicated_and_validated(self):
        raw = [{"price": 13000, "timestamp": "2026-09-02T00:00:00"}, {"price": 12000, "timestamp": "2026-09-01T00:00:00"},
               {"price": 12500, "timestamp": "2026-09-01T00:00:00"}, {"price": 0, "timestamp": "2026-09-03T00:00:00"},
               {"price": True, "timestamp": "2026-09-04T00:00:00"}, {"timestamp": "bad"}, "junk"]
        points = gold.parse_points(raw)
        self.assertEqual([p for _, p in points], [12500, 13000])  # later duplicate wins, junk dropped

    def test_summary_measures_from_the_newest_point(self):
        day = gold.DAY
        points = [(0, 10000), (6 * day, 10500), (7 * day, 11000), (8 * day, 12100)]
        s = gold.summary(points)
        self.assertEqual(s["price"], 12100)
        self.assertAlmostEqual(s["change_24h"], 0.1)            # vs 11000 one day earlier
        self.assertAlmostEqual(s["change_7d"], 12100 / 10000 - 1)  # last point at or before 7 days back
        self.assertEqual((s["low_7d"], s["high_7d"]), (10500, 12100))  # the day-0 point is outside the week
        self.assertIsNone(gold.summary([]))
        self.assertIsNone(gold.summary([(0, 5)])["change_24h"])


def kill(event_id, at="2026-09-24T04:42:34.7286358Z", killer="K", victim="V", **extra):
    player = lambda name: {"Id": name, "Name": name, "GuildName": "", "AverageItemPower": 1000.4,
                           "Equipment": {"MainHand": {"Type": f"T4_MAIN_{name}", "Count": 1, "Quality": 2}, "Head": None},
                           "Inventory": [None, {"Type": "T4_BAG", "Count": 1, "Quality": 1}]}
    return {"EventId": event_id, "TimeStamp": at, "TotalVictimKillFame": 100, "Killer": player(killer),
            "Victim": player(victim), **extra}


class FakeFeed:
    """Newest-first feed; new_after_first_page events arrive between page requests."""
    PAGE, MAX_OFFSET = 3, 6

    def __init__(self, ids):
        self.ids = sorted(ids, reverse=True)
        self.calls = []

    def events(self, offset):
        self.calls.append(offset)
        return [kill(i) for i in self.ids[offset:offset + self.PAGE]]


class Killboard(unittest.TestCase):
    def test_parse_event_keeps_gear_and_carried_items(self):
        e = killboard.parse_event(kill(7))
        self.assertEqual(e["at"], 1790224954)  # 7-digit fraction and Z suffix are handled
        self.assertEqual(e["killer"]["gear"], {"MainHand": ["T4_MAIN_K", 1, 2]})
        self.assertEqual(e["killer"]["ip"], 1000)
        self.assertEqual(e["victim"]["inventory"], [["T4_BAG", 1, 1]])
        self.assertNotIn("inventory", e["killer"])

    def test_parse_event_tolerates_missing_fields(self):
        self.assertIsNone(killboard.parse_event({"EventId": 1}))  # no time
        self.assertIsNone(killboard.parse_event({"TimeStamp": "2026-09-24T00:00:00Z"}))  # no id
        e = killboard.parse_event({"EventId": 1, "TimeStamp": "2026-09-24T00:00:00Z", "Killer": None})
        self.assertEqual((e["killer"]["name"], e["killer"]["gear"], e["fame"]), ("?", {}, 0))

    def test_weapon_usage_counts_both_sides(self):
        events = [killboard.parse_event(kill(1, killer="A")), killboard.parse_event(kill(2, victim="A")),
                  killboard.parse_event(kill(3, killer="B"))]
        self.assertEqual(killboard.weapon_usage(events, "A"), [("T4_MAIN_A", 2)])

    def test_a_cut_off_file_keeps_everything_before_the_cut(self):
        with tempfile.TemporaryDirectory() as d:
            archive = killboard.Archive(d)
            archive.append([kill(1)])
            archive.append([kill(2)])
            path = next(Path(d).glob("*.jsonl.gz"))
            path.write_bytes(path.read_bytes()[:-40])  # the second append cut off mid-write, as in a crash
            with mock.patch.object(killboard.traceback, "print_exc"):
                self.assertEqual([r["EventId"] for r in archive.stream(10)], [1])

    def test_each_run_writes_its_own_file_per_day(self):
        with tempfile.TemporaryDirectory() as d:
            killboard.Archive(d, run="a").append([kill(1, at="2026-09-23T23:59:59Z"), kill(2, at="2026-09-24T00:00:01Z")])
            killboard.Archive(d, run="b").append([kill(3, at="2026-09-24T02:00:00Z"), kill(4, at="bad")])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["2026-09-23.a.jsonl.gz", "2026-09-24.a.jsonl.gz", "2026-09-24.b.jsonl.gz"])
            self.assertEqual([r["EventId"] for r in killboard.Archive(d).stream(1)], [2, 3])  # newest day only


class Collecting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.kills = killstore.KillStore(self.dir / "kills.db")

    def tearDown(self):
        self.tmp.cleanup()

    def collector(self, feed, run="r"):
        return killstore.Collector(self.kills, feed, killboard.Archive(self.dir / "raw", run=run))

    def test_poll_pages_until_nothing_new_and_archives_once(self):
        feed = FakeFeed(range(1, 6))
        c = self.collector(feed)
        self.assertEqual(c.poll(), 5)
        self.assertEqual(feed.calls, [0, 3])  # second page is short: the feed ends there
        feed.ids, feed.calls = sorted(range(1, 8), reverse=True), []
        self.assertEqual(c.poll(), 2)
        self.assertEqual(feed.calls, [0, 3])  # stops at the first page with nothing new
        self.assertEqual([e["id"] for e in self.kills.recent()], [7, 6, 5, 4, 3, 2, 1])
        self.assertEqual(len(list(killboard.Archive(self.dir / "raw").stream(100))), 7)
        self.assertIsNotNone(self.kills.state("polled_at"))

    def test_poll_stops_at_the_depth_limit(self):
        feed = FakeFeed(range(1, 100))
        self.assertEqual(self.collector(feed).poll(), 9)  # offsets 0, 3, 6 only
        self.assertEqual(feed.calls, [0, 3, 6])

    def test_a_restart_or_second_collector_never_counts_a_kill_twice(self):
        self.collector(FakeFeed(range(1, 4)), run="first").poll()
        self.assertEqual(self.collector(FakeFeed(range(1, 5)), run="second").poll(), 1)
        self.assertEqual(self.kills.add([kill(2)]), 0)
        self.assertEqual(self.kills.summary()["kills"], 4)

    def test_rebuild_from_the_archive_matches_live_collection(self):
        self.collector(FakeFeed(range(1, 4))).poll()
        live = self.kills.summary()
        kill_day = datetime.datetime(2026, 9, 24, 12, tzinfo=datetime.timezone.utc).timestamp()
        with mock.patch("time.time", return_value=kill_day):  # rebuild prunes by today's date
            self.assertEqual(self.kills.rebuild(killboard.Archive(self.dir / "raw")), 3)
        self.assertEqual(self.kills.summary(), live)

    def test_poll_errors_are_recorded_not_raised(self):
        broken = mock.Mock(PAGE=3, MAX_OFFSET=6, events=mock.Mock(side_effect=api_mod.APIError("down")))
        stop = mock.Mock(is_set=mock.Mock(side_effect=[False, True]), wait=mock.Mock())
        with mock.patch.object(killstore.traceback, "print_exc"):
            self.collector(broken).run(stop)
        self.assertTrue(self.kills.state("poll_error_at"))

    def test_only_one_process_holds_the_collector_lock(self):
        first = collect.acquire_lock(self.dir / "c.lock")
        self.assertIsNotNone(first)
        self.assertIsNone(collect.acquire_lock(self.dir / "c.lock"))
        first.close()  # released, as when the process exits
        again = collect.acquire_lock(self.dir / "c.lock")
        self.assertIsNotNone(again)
        again.close()

    def test_standby_takes_over_when_the_collector_dies(self):
        import threading
        path = self.dir / "c.lock"
        other = collect.acquire_lock(path)
        got, stop = [], threading.Event()
        waiting = threading.Thread(target=collect.standby, args=(stop, got.append, 0.01, path))
        waiting.start()
        time.sleep(0.05)
        self.assertEqual(got, [])   # someone else is collecting
        other.close()               # ...and dies
        waiting.join(timeout=2)
        self.assertEqual(len(got), 1)
        got[0].close()
        stop.set()

    def test_status_flags_a_dead_collector(self):
        market = store.Store(self.dir / "m.db")
        self.assertTrue(collect.status(market, self.kills)["collector_down"])
        self.kills.mark("polled_at", 1000)
        self.assertFalse(collect.status(market, self.kills, now=1000 + 60)["collector_down"])
        self.assertTrue(collect.status(market, self.kills, now=1000 + collect.STALE + 1)["collector_down"])


class Intel(unittest.TestCase):
    def test_search_skips_rows_without_ids(self):
        out = intel.search_results({"players": [{"Id": "a", "Name": "A", "KillFame": None}, {"Name": "no id"}],
                                    "guilds": "junk"})
        self.assertEqual(out, {"players": [{"id": "a", "name": "A", "guild": "", "alliance": "",
                                            "kill_fame": 0, "death_fame": 0}], "guilds": []})

    def test_guild_members_sorted_by_kill_fame(self):
        members = [{"Id": "a", "Name": "A", "KillFame": 5}, {"Id": "b", "Name": "B", "KillFame": 50,
                   "LifetimeStatistics": {"PvE": {"Total": 7}}}, "junk"]
        g = intel.guild_profile({"guild": {"Name": "G"}, "overall": {"kills": 3}}, members)
        self.assertEqual([m["name"] for m in g["members"]], ["B", "A"])
        self.assertEqual((g["name"], g["kills"], g["members"][0]["pve"]), ("G", 3, 7))

    def test_player_profile_orders_events_newest_first(self):
        kills = [kill(1, at="2026-09-01T00:00:00Z", killer="me"), kill(2, at="2026-09-02T00:00:00Z", killer="me")]
        prof = intel.player_profile({"Id": "me", "Name": "me"}, kills, None)
        self.assertEqual([e["id"] for e in prof["kills"]], [2, 1])
        self.assertEqual(prof["weapons"], [("T4_MAIN_me", 2)])
        self.assertNotIn("gear", prof["player"])



class ServerChoice(unittest.TestCase):
    def tearDown(self):
        from silver import config
        config.select("asia")

    def test_select_switches_every_default(self):
        from silver import config
        config.select("europe")
        self.assertEqual(AlbionAPI().host, "https://europe.albion-online-data.com")
        self.assertEqual(api_mod.GameInfo().host, "https://gameinfo-ams.albiononline.com/api/gameinfo")
        self.assertEqual(killboard.Archive().dir.name, "europe")
        self.assertEqual(transports._files()["quests"][0], "quests_europe.json")



class Meta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kills = killstore.KillStore(Path(self.tmp.name) / "kills.db")
        self.ids = iter(range(1, 1000))

    def tearDown(self):
        self.tmp.cleanup()

    def fight(self, at="2026-09-24T01:00:00Z", helpers=("K",), victim="V", size=None):
        raw = kill(next(self.ids), at=at, killer=helpers[0], victim=victim)
        raw["Participants"] = [kill(0, killer=h)["Killer"] for h in helpers]
        raw["numberOfParticipants"] = size or len(helpers)
        return raw

    def test_lines_and_builds_ignore_tier_and_enchantment(self):
        self.assertEqual(meta.base_id("T5_2H_CROSSBOWLARGE@1"), "2H_CROSSBOWLARGE")
        gear = {"MainHand": ["T8_MAIN_SWORD@3", 1, 1], "Shoes": ["T4_SHOES_PLATE_SET1", 1, 1]}
        self.assertEqual(meta.build_of(gear), ("MAIN_SWORD", "", "", "", "SHOES_PLATE_SET1", ""))
        self.assertIsNone(meta.build_of({"Head": ["T4_HEAD_CLOTH_SET1", 1, 1]}))
        self.assertEqual([meta.fight_size(n) for n in (1, 5, 6, 19, 20)], ["solo", "small", "group", "group", "zvz"])
        self.assertIsNone(meta.counter_rows({"Victim": {}}))  # no time

    def test_a_kill_is_split_between_participants(self):
        self.kills.add([self.fight(helpers=("A", "B"))])
        s = self.kills.summary()
        self.assertEqual(s["kills"], 1)
        self.assertEqual({w["weapon"]: (w["seen"], w["kills"], w["deaths"]) for w in s["weapons"]},
                         {"MAIN_A": (1, 0.5, 0), "MAIN_B": (1, 0.5, 0), "MAIN_V": (1, 0, 1)})
        self.assertEqual(sum(w["kills"] for w in s["weapons"]), sum(w["deaths"] for w in s["weapons"]))
        self.assertEqual(s["builds"][0]["build"], ["MAIN_A", "", "", "", "", ""])
        self.assertEqual(s["lost"], [{"id": "T4_BAG", "units": 1}, {"id": "T4_MAIN_V", "units": 1}])

    def test_fight_size_filter_and_lost_filter(self):
        self.kills.add([self.fight(helpers=("A",)), self.fight(helpers=("B",), size=25)])
        self.assertEqual([w["weapon"] for w in self.kills.summary(size="zvz")["weapons"]], ["MAIN_B", "MAIN_V"])
        self.assertEqual(self.kills.summary(size="solo")["kills"], 1)
        self.assertEqual(self.kills.summary(lost_filter=lambda i: i != "T4_BAG")["lost"], [{"id": "T4_MAIN_V", "units": 2}])

    def test_days_window_hours_and_pruning(self):
        self.kills.add([self.fight(at=f"2026-09-{day}T01:00:00Z") for day in ("21", "22", "23")])
        s = self.kills.summary(days=2)
        self.assertEqual((s["days"], s["kills"], s["hours"]), (["2026-09-22", "2026-09-23"], 2, 24.0))
        self.kills.prune(now=parse_ts("2026-09-23T12:00:00") + (meta.KEEP_DAYS - 2) * 86400)
        self.assertEqual(self.kills.summary(days=7)["days"], ["2026-09-22", "2026-09-23"])
        self.assertEqual(self.kills.recent(), [])  # killboard events older than 2 days are gone

    def test_line_names_drop_the_tier_word(self):
        known = {"T4_2H_AXE": "Adept's Greataxe", "T8_2H_AXE": "The Hand of Khor", "T8_ONLY": "Unique"}
        self.assertEqual(meta.line_names(["2H_AXE", "ONLY", "X"], known),
                         {"2H_AXE": ("T4_2H_AXE", "Greataxe"), "ONLY": ("T8_ONLY", "Unique"), "X": (None, "X")})


class Migrations(unittest.TestCase):
    def test_versions_apply_once_in_order_and_newer_files_are_refused(self):
        class V1(db_mod.Database):
            MIGRATIONS = ("CREATE TABLE a (x)",)

        class V2(db_mod.Database):
            MIGRATIONS = ("CREATE TABLE a (x)", "ALTER TABLE a ADD COLUMN y; CREATE TABLE b (z)")

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.db"
            V1(path).db.execute("INSERT INTO a VALUES (1)").connection.commit()
            v2 = V2(path)
            self.assertEqual(v2.db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(v2.db.execute("SELECT x, y FROM a").fetchall(), [(1, None)])  # data kept
            V2(path)  # already current: nothing re-runs
            with self.assertRaises(RuntimeError):
                V1(path)


class Store(unittest.TestCase):
    DAY = store.DAY
    TODAY = 1_790_000_000 - 1_790_000_000 % 86400

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.Store(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_price_side_keeps_its_newest_observation(self):
        old = row("X", "Martlock", sell=100, buy=50, age=100)
        new = row("X", "Martlock", sell=120, age=0)
        for order in ([old, new], [new, old]):
            self.db.db.execute("DELETE FROM prices")
            for r in order:
                self.db.put_prices([r])
            (got,) = self.db.prices(["X"])
            self.assertEqual((got["sell"], got["buy"]), (120, 50))

    def test_reads_filter_by_ids_qualities_and_cities(self):
        self.db.put_prices([row("A", "Martlock", sell=1), row("A", "Thetford", sell=2, quality=2), row("B", "Martlock", sell=3)])
        self.assertEqual([r["item_id"] for r in self.db.prices(["A"])], ["A", "A"])
        self.assertEqual([r["city"] for r in self.db.prices(qualities=[2])], ["Thetford"])
        self.assertEqual([r["item_id"] for r in self.db.prices(cities=["Martlock"])], ["A", "B"])
        self.assertEqual(self.db.prices([]), [])

    def test_bad_history_is_skipped_and_volumes_average_over_traded_days(self):
        series = [{"item_id": "X", "location": "Martlock", "quality": 1,
                   "data": [{"item_count": 10, "avg_price": 100, "timestamp": "2026-09-20T00:00:00"},
                            {"item_count": 30, "avg_price": 200, "timestamp": "2026-09-21T00:00:00"},
                            {"item_count": 5, "avg_price": 100}]},                 # no timestamp
                  {"item_id": "Y", "location": "Martlock"},                        # no quality
                  {"item_id": "Z", "location": "Martlock", "quality": 1, "data": [{"item_count": "x"}]}]
        self.db.put_history(store.parse_history(series))
        now = parse_ts("2026-09-22T12:00:00")
        self.assertEqual(self.db.volumes(days=7, now=now), {("X", "Martlock", 1): {"vol": 20.0, "avg": 175, "days": 2}})
        self.assertEqual(self.db.volumes(days=1, now=now), {})

    def test_weighted_median_follows_volume_not_outliers(self):
        self.assertEqual(store.weighted_median([(100, 50), (101, 40), (99999, 1)]), 100)
        self.assertEqual(store.weighted_median([(100, 1), (200, 10)]), 200)
        self.assertIsNone(store.weighted_median([]))

    def test_fair_price_windows_and_trend(self):
        d = lambda n: self.TODAY - n * self.DAY
        points = [(d(i), 10, 110) for i in range(1, 7)] + [(d(i), 10, 100) for i in range(8, 14)]
        f = store.fair_price(points, self.TODAY)
        self.assertEqual((f["days"], f["vol"], f["last_day"]), (12, 10.0, d(1)))
        self.assertAlmostEqual(f["trend"], 0.1)
        sparse = [(d(20), 4, 500), (d(25), 6, 520)]  # under 3 days in 14: widen to 28
        self.assertEqual(store.fair_price(sparse, self.TODAY)["price"], 520)
        self.assertIsNone(store.fair_price([(d(40), 1, 1)], self.TODAY))

    def test_troll_days_are_trimmed_before_the_median(self):
        d = lambda n: self.TODAY - n * self.DAY
        points = [(d(i), 5, 1000 + i) for i in range(1, 6)] + [(d(6), 500, 99_999_999), (d(7), 400, 1)]
        self.assertEqual(store.fair_price(points, self.TODAY)["price"], 1003)  # the two troll days carry most volume
        self.assertEqual(store.trim_outliers(points[:2]), points[:2])            # too few points to judge

    def test_pooled_fair_price_leaves_out_the_black_market(self):
        rows = [("X", "Martlock", 1, self.TODAY - self.DAY, 10, 100),
                ("X", "Black Market", 1, self.TODAY - self.DAY, 1000, 900)]
        fair = {(i, c): p for i, q, c, p, *_ in store.compute_fair(rows, self.TODAY)}
        self.assertEqual(fair, {("X", store.ALL): 100, ("X", "Martlock"): 100, ("X", "Black Market"): 900})

    def test_rebuild_fair_and_read_it_back(self):
        self.db.put_history([("X", "Martlock", 1, self.TODAY - self.DAY, 10, 100)])
        self.assertEqual(self.db.rebuild_fair(now=self.TODAY + 60), 2)
        self.assertEqual(self.db.fair(["X"])["X"][1][store.ALL]["price"], 100)
        self.assertEqual(self.db.history("X", now=self.TODAY), {"Martlock": [(self.TODAY - self.DAY, 10, 100)]})

    def test_history_sync_reaches_back_far_enough(self):
        self.assertEqual(store.history_days(self.db), store.BACKFILL_DAYS)
        self.db.mark("history_at", 1_000_000)
        self.assertEqual(store.history_days(self.db, now=1_000_000 + 3600), 3)
        self.assertEqual(store.history_days(self.db, now=1_000_000 + 10 * self.DAY), 13)

    def test_sync_stores_each_batch_and_marks_a_sweep(self):
        raw = {"item_id": "X", "city": "Martlock", "quality": 1, "sell_price_min": 7,
               "sell_price_min_date": "2026-09-01T00:00:00", "buy_price_max": 0, "buy_price_max_date": "0001-01-01T00:00:00"}
        fake = mock.Mock(iter_batches=mock.Mock(return_value=iter([[raw], [{"junk": 1}]])))
        store.sync_prices(self.db, fake, ["X"], swept=True)
        self.assertEqual(self.db.prices()[0]["sell"], 7)
        self.assertIsNotNone(self.db.state("prices_at"))

    def test_snapshot_serves_stored_prices_when_refresh_fails(self):
        from silver import server
        self.db.put_prices([row("X", "Martlock", sell=5)])
        self.db.mark("prices_at")
        self.db.mark("history_at")
        broken = mock.Mock(iter_batches=mock.Mock(side_effect=api_mod.APIError("down")))
        with mock.patch.object(server, "store", self.db), mock.patch.object(server, "api", broken), \
                mock.patch("traceback.print_exc"):
            snap = server.Snapshots().get("k", ["X"], ["X"], ["Martlock"], refresh=True)
        self.assertEqual(snap["prices"]["X"]["Martlock"]["sell"], 5)
        self.assertIn("down", snap["warning"])
        with mock.patch.object(server, "store", self.db), mock.patch.object(server, "api", broken):
            with self.assertRaises(api_mod.APIError):  # nothing stored for this item: nothing to fall back on
                server.Snapshots().get("k", ["NEW"], ["NEW"], ["Martlock"], refresh=True)



class Icons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fetched = []

    def tearDown(self):
        self.tmp.cleanup()

    def cache(self, answers):
        def fetch(item_id):
            self.fetched.append(item_id)
            answer = answers[item_id]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return icons_mod.Icons(self.tmp.name, fetch=fetch)

    def test_downloads_once_marks_missing_and_retries_errors(self):
        c = self.cache({"T4_BAG": icons_mod.PNG + b"x", "T4_NOPE": None, "T4_DOWN": OSError("offline")})
        with mock.patch.object(icons_mod.traceback, "print_exc"):
            c.want(["T4_BAG", "T4_BAG", "T4_NOPE", "T4_DOWN", "../etc/passwd", ""])
            c.join()
        self.assertEqual(sorted(self.fetched), ["T4_BAG", "T4_DOWN", "T4_NOPE"])  # once each, bad ids never
        self.assertEqual(c.path("T4_BAG").read_bytes(), icons_mod.PNG + b"x")
        self.assertTrue(c.missing("T4_NOPE"))
        self.assertIsNone(c.path("T4_DOWN"))
        self.assertFalse(c.missing("T4_DOWN"))  # a network error is not "missing": asked again later
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp")), [])

    def test_cached_and_missing_icons_are_not_fetched_again(self):
        c = self.cache({})
        (Path(self.tmp.name) / "T4_BAG.png").write_bytes(icons_mod.PNG)
        (Path(self.tmp.name) / "T4_NOPE.missing").touch()
        c.want(["T4_BAG", "T4_NOPE"])
        c.join()
        self.assertEqual(self.fetched, [])

    def test_icons_a_browser_waits_for_jump_the_queue(self):
        gate = threading.Event()
        c = icons_mod.Icons(self.tmp.name, fetch=lambda i: (gate.wait(), self.fetched.append(i), icons_mod.PNG)[2], workers=1)
        c.want(["T4_A"])                               # the one worker takes this and waits at the gate
        time.sleep(0.05)
        c.want(["T4_B", "T4_C"])                       # queued for later
        c.want(["T4_C"], icons_mod.URGENT)             # ...until a browser asks for C
        gate.set()
        c.join()
        self.assertEqual(self.fetched, ["T4_A", "T4_C", "T4_B"])

    def test_server_serves_cached_redirects_unknown_and_refuses_bad_ids(self):
        from silver import server
        c = self.cache({"T5_BAG": icons_mod.PNG})
        (Path(self.tmp.name) / "T4_BAG.png").write_bytes(icons_mod.PNG)
        (Path(self.tmp.name) / "T4_NOPE.missing").touch()
        sent = []
        handler = mock.Mock(_send=lambda status, body, ctype="application/json", cache="no-store", headers=(): sent.append((status, cache, dict(headers))))
        with mock.patch.object(server, "icons", c):
            for item_id in ("T4_BAG", "T4_NOPE", "T5_BAG", "..%2Fx"):
                server.Handler._send_icon(handler, item_id)
            c.join()
        self.assertEqual(sent[0][:2], (200, "public, max-age=31536000, immutable"))
        self.assertEqual(sent[1][0], 404)
        self.assertEqual((sent[2][0], sent[2][2]["Location"]), (302, icons_mod.render_url("T5_BAG")))
        self.assertEqual(sent[3][0], 404)
        self.assertIsNotNone(c.path("T5_BAG"))  # fetched in the background for next time



class Enchant(unittest.TestCase):
    NOW = 1_800_000_000
    STEPS = [{"id": f"T6_X@{n}", "enchant": n, "tier": 6, "name": "X", "upgrade": True,
              "inputs": [[f"T6_X@{n - 1}" if n > 1 else "T6_X", 1, False], [mat, 10, False]]}
             for n, mat in ((1, "RUNE"), (2, "SOUL"), (3, "RELIC"))]

    def quote(self, sell=None, buy=None, age=0):
        return {"sell": sell, "sell_at": self.NOW - age if sell else None, "buy": buy, "buy_at": self.NOW - age if buy else None}

    @staticmethod
    def fair(price, days=10, vol=5):
        return {"price": price, "days": days, "vol": vol}

    def test_jumps_cover_every_level_range_and_add_up_materials(self):
        jumps = {(j["from"], j["id"]): j["materials"] for j in enchanting.jumps(self.STEPS)}
        self.assertEqual(len(jumps), 6)  # .0-.1 .0-.2 .0-.3 .1-.2 .1-.3 .2-.3
        self.assertEqual(jumps[("T6_X", "T6_X@3")], [("RELIC", 10), ("RUNE", 10), ("SOUL", 10)])
        self.assertEqual(jumps[("T6_X@2", "T6_X@3")], [("RELIC", 10)])

    def test_buys_each_part_where_cheapest_and_sells_where_it_pays_best(self):
        prices = {"T6_X": {"Martlock": self.quote(sell=1000), "Thetford": self.quote(sell=900)},
                  "RUNE": {"Lymhurst": self.quote(sell=5), "Martlock": self.quote(sell=4, age=99 * 3600)},  # stale
                  "T6_X@1": {"Black Market": self.quote(buy=2000), "Lymhurst": self.quote(sell=3000)}}
        fair = {"T6_X@1": {"Lymhurst": self.fair(2500), store.ALL: self.fair(2500)},
                "T6_X": {store.ALL: self.fair(950)}, "RUNE": {store.ALL: self.fair(5)}}
        step = [j for j in enchanting.jumps(self.STEPS) if j["levels"] == (0, 1)]
        (row,) = enchanting.evaluate(step, prices, fair, oldest=self.NOW - 6 * 3600)
        self.assertEqual([(b["city"], b["price"]) for b in row["buy"]], [("Thetford", 900), ("Lymhurst", 5)])
        self.assertEqual(row["sell"], {"price": 2500, "city": "Lymhurst", "how": "order"})  # ask 3000 capped at fair
        self.assertEqual(row["now_profit"], round(2500 * (1 - 0.04 - 0.025) - 950))
        instant = enchanting.evaluate(step, prices, fair, oldest=self.NOW - 6 * 3600, mode="instant")[0]
        self.assertEqual((instant["sell"]["city"], instant["now_profit"]), ("Black Market", round(2000 * 0.96 - 950)))
        self.assertEqual(row["typical_profit"], round(2500 * (1 - 0.04 - 0.025) - 1000))

    def test_thin_fair_prices_are_not_listed_against_and_the_black_market_counts_as_typical(self):
        fair = {"T6_X@1": {"Lymhurst": self.fair(9999, days=2), "Black Market": self.fair(3000)},
                "T6_X": {store.ALL: self.fair(1000)}, "RUNE": {store.ALL: self.fair(10)}}
        step = [j for j in enchanting.jumps(self.STEPS) if j["levels"] == (0, 1)]
        (row,) = enchanting.evaluate(step, {}, fair, oldest=self.NOW)
        self.assertNotIn("now_profit", row)  # no fresh prices at all
        self.assertEqual((row["typical_sell"], row["typical_profit"]), ("Black Market", round(3000 * 0.96 - 1100)))
        self.assertIsNone(enchanting.best_sale({}, fair["T6_X@1"], self.NOW, True, "best"))  # 2 days: too thin



class MakeOrBuy(unittest.TestCase):
    """Bars: T5 bar = 2 T5 ore + 1 T4 bar, T4 bar = 2 T4 ore (both returnable), refined in Thetford."""

    @staticmethod
    def recipe(product, inputs, amount=1, city="Thetford", value=0):
        return {"id": product, "inputs": inputs, "amount": amount, "silver": 0, "focus": 100, "value": value,
                "bonus": "refine:ore", "bonus_city": city}

    def setUp(self):
        self.t4 = self.recipe("T4_BAR", [["T4_ORE", 2, True]])
        self.t5 = self.recipe("T5_BAR", [["T5_ORE", 2, True], ["T4_BAR", 1, True]])
        self.ways = {"T4_BAR": [self.t4], "T5_BAR": [self.t5]}
        self.prices = {"T4_ORE": 10, "T5_ORE": 20, "T4_BAR": 100}

    def planner(self, **kw):
        return production.Planner(self.ways, lambda i: (self.prices[i], "Martlock") if i in self.prices else None,
                                  "Martlock", station_fee=0, **kw)

    def test_return_rate_matches_the_documented_values(self):
        self.assertAlmostEqual(production.return_rate(self.t4, "Thetford"), 0.58 / 1.58)
        self.assertAlmostEqual(production.return_rate(self.t4, "Martlock"), 0.18 / 1.18)
        self.assertAlmostEqual(production.return_rate(self.t4, "Thetford", focus=True), 1.17 / 2.17)

    def test_makes_an_ingredient_when_cheaper_than_buying_it(self):
        keep = 1 - 0.58 / 1.58  # share of returnable inputs used up, refining in Thetford
        plan = self.planner().make(self.t5, city="Thetford")
        t4_made = 2 * keep * 10
        self.assertAlmostEqual(plan["unit"], 2 * keep * 20 + keep * t4_made)  # a made T4 bar beats 100
        s = production.summarize(plan)
        self.assertEqual([(m["id"], m["units"]) for m in s["makes"]], [("T4_BAR", round(keep, 3))])
        self.assertEqual({b["id"]: b["units"] for b in s["buys"]}, {"T5_ORE": round(2 * keep, 3), "T4_ORE": round(2 * keep * keep, 3)})

    def test_buys_an_ingredient_when_cheaper_and_handles_missing_prices(self):
        self.prices["T4_BAR"] = 1
        plan = self.planner().make(self.t5, city="Thetford")
        self.assertEqual(production.summarize(plan)["makes"], [])
        del self.prices["T4_ORE"], self.prices["T4_BAR"]
        self.assertIsNone(self.planner().make(self.t5, city="Thetford"))  # no way to get a T4 bar

    def test_batches_fees_and_unreturned_inputs(self):
        batch = self.recipe("T1_CHOPS", [["FISH", 1, False]], amount=10, value=100)
        planner = production.Planner({}, lambda i: (50, "Martlock"), "Martlock", station_fee=200)
        fee = 100 * production.NUTRITION_PER_VALUE * 200 / 100
        self.assertAlmostEqual(planner.make(batch)["unit"], (50 + fee) / 10)  # nothing returned, fee shared by 10

    def test_intermediate_steps_move_to_their_specialty_city_unless_told_not_to(self):
        keep_here = 1 - 0.18 / 1.18
        away = production.summarize(self.planner().make(self.t5, city="Martlock"))
        self.assertEqual(away["makes"][0]["city"], "Thetford")
        home = production.summarize(self.planner(best_city=False).make(self.t5, city="Martlock"))
        self.assertEqual(home["makes"][0], {"id": "T4_BAR", "units": round(keep_here, 3), "city": "Martlock",
                                            "rate": round(0.18 / 1.18, 4)})

    def test_a_recipe_that_needs_its_own_product_is_not_used(self):
        self.ways["T4_ORE"] = [self.recipe("T4_ORE", [["T4_BAR", 1, False]])]  # a loop: ore from bars
        plan = self.planner().make(self.t5, city="Thetford")
        self.assertIsNotNone(plan)  # terminates, and the ore is simply bought


if __name__ == "__main__":
    unittest.main()


class DataFolder(unittest.TestCase):
    def test_source_checkout_env_and_installed_locations(self):
        from silver import paths
        with tempfile.TemporaryDirectory() as tmp:
            repo, site = Path(tmp) / "repo", Path(tmp) / "site-packages"
            (repo / "silver").mkdir(parents=True)
            (repo / "pyproject.toml").touch()
            (repo / ".git").mkdir()
            (site / "silver").mkdir(parents=True)
            self.assertEqual(paths.data_dir({}, "linux", repo / "silver"), repo / "data")
            self.assertEqual(paths.data_dir({"SILVER_DATA": "/x"}, "linux", repo / "silver"), Path("/x"))
            self.assertEqual(paths.data_dir({"LOCALAPPDATA": "C:/Users/a/AppData/Local"}, "win32", site / "silver"),
                             Path("C:/Users/a/AppData/Local/Silver"))
            self.assertEqual(paths.data_dir({"XDG_DATA_HOME": "/d"}, "linux", site / "silver"), Path("/d/silver"))
