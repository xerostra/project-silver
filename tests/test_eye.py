"""The eye against real 1080p captures in tests/fixtures (crops of the inventory panel only).

Needs the `eye` extra and the item renders in data/icons (`silver eye` fetches them), so run:
uv run --extra eye python -m unittest tests.test_eye"""
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
    from silver import eye, icons
except ImportError:  # the eye extra isn't installed
    eye = None

FIXTURES = Path(__file__).parent / "fixtures"
# Every slot read off the captures by hand: ((row, col) or gear slot, item, count).
BAG = [
    ((0, 0), "T8_2H_TOOL_AXE", 1), ((0, 1), "T6_2H_TOOL_FISHINGROD_AVALON", 1),
    ((0, 2), "T4_FISH_FRESHWATER_ALL_COMMON", 32), ((0, 3), "T5_FISH_FRESHWATER_ALL_COMMON", 34),
    ((1, 0), "T3_FISH_FRESHWATER_AVALON_RARE", 15), ((1, 1), "T3_FISH_FRESHWATER_ALL_COMMON", 22),
    ((1, 2), "T1_SEAWEED", 27), ((1, 3), "T4_WOOD_LEVEL1@1", 6),
    ((2, 0), "T5_WOOD", 12), ((2, 1), "T4_SKILLBOOK_NONTRADABLE", 1),
    ((2, 2), "T3_SKILLBOOK_NONTRADABLE", 2), ((2, 3), "T5_FISH_FRESHWATER_AVALON_RARE", 1),
    ((3, 0), "T3_FISH_FRESHWATER_FOREST_RARE", 3), ((3, 1), "T4_WOOD_LEVEL2@2", 2),
    ((3, 2), "T1_SKILLBOOK_NONTRADABLE", 4), ((3, 3), "T4_RUNE", 1),
    ((4, 0), "T1_SILVERBAG_NONTRADABLE", 2),
]
GEAR_IN_BAG = [  # enchanted gear (one lit diamond each) and gaps between slots
    ((0, 0), "T8_2H_TOOL_AXE", 1), ((0, 1), "T6_2H_TOOL_FISHINGROD_AVALON", 1),
    ((0, 2), "T4_ARMOR_LEATHER_SET3@1", 1), ((0, 3), "T4_2H_DOUBLEBLADEDSTAFF@1", 1),
    ((1, 0), "T4_SHOES_GATHERER_ORE@1", 1), ((1, 2), "T8_POTION_CLEANSE", 3),
    ((2, 1), "T4_SKILLBOOK_NONTRADABLE", 1), ((2, 2), "T3_SKILLBOOK_NONTRADABLE", 2),
    ((3, 2), "T1_SKILLBOOK_NONTRADABLE", 4),
]
WORN = [  # the paper doll above a one-row bag; the off-hand is a two-hander's ghost, so empty
    ("bag", "T4_BAG@1", 1), ("head", "T4_HEAD_PLATE_SET3@1", 1), ("cape", "T6_BACKPACK_GATHERER_FISH", 1),
    ("weapon", "T4_2H_DOUBLEBLADEDSTAFF@1", 1), ("armor", "T4_ARMOR_LEATHER_SET3@1", 1),
    ("potion", "T8_POTION_CLEANSE", 3), ("shoes", "T4_SHOES_GATHERER_ORE@1", 1), ("food", "T7_MEAL_PIE@1", 2),
]
WORN_BAG = [((0, 0), "T8_2H_TOOL_AXE", 1), ((0, 1), "T6_2H_TOOL_FISHINGROD_AVALON", 1),
            ((0, 2), "T3_FISHINGBAIT", 20), ((0, 3), "T6_JOURNAL_FISHING_EMPTY", 3)]


def slots(snap):
    return [((i["row"], i["col"]), i["id"], i["count"]) for i in snap.items]


@unittest.skipIf(eye is None, "needs the eye extra")
class Amounts(unittest.TestCase):
    def test_parse_amount(self):
        cases = {"1,750": 1750, "19m": 19_000_000, "1.24 m": 1_240_000, "12k": 12_000, "7": 7, "Loadouts": None, "": None}
        for text, want in cases.items():
            self.assertEqual(eye.parse_amount(text), want, text)

    def test_family_ignores_tier_and_enchant(self):
        self.assertEqual(eye.family("T4_WOOD_LEVEL1@1"), eye.family("T8_WOOD"))
        self.assertNotEqual(eye.family("T4_WOOD"), eye.family("T4_PLANKS"))


@unittest.skipIf(eye is None, "needs the eye extra")
class Screenshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        needed = {i for _, i, _ in BAG + GEAR_IN_BAG + WORN + WORN_BAG}
        if not all((icons.DIR / f"{i}.png").exists() for i in needed):
            raise unittest.SkipTest("item renders not cached in data/icons (run `silver eye` once)")
        cls.refs, cls.reader = eye.References(), eye.Reader()
        cls.read = {name: eye.read_inventory(cv2.imread(str(FIXTURES / f"{name}.png")), cls.refs, cls.reader)
                    for name in ("inventory_1080p", "inventory_gear_1080p", "inventory_worn_1080p")}

    def test_finds_the_bag_grid(self):
        grid = eye.find_bag(cv2.imread(str(FIXTURES / "inventory_1080p.png")))
        self.assertEqual((grid.pitch, grid.w, grid.cols), (81, 71, 4))

    def test_grid_follows_resolution_and_ui_scale(self):
        img = cv2.imread(str(FIXTURES / "inventory_1080p.png"))
        for scale in (.75, 4 / 3):
            self.assertAlmostEqual(eye.find_bag(cv2.resize(img, None, fx=scale, fy=scale)).pitch, 81 * scale, delta=2)

    def test_names_every_item_with_tier_enchant_and_count(self):
        self.assertEqual(slots(self.read["inventory_1080p"]), BAG)

    def test_enchanted_gear_and_gaps_in_the_bag(self):
        self.assertEqual(slots(self.read["inventory_gear_1080p"]), GEAR_IN_BAG)

    def test_worn_gear(self):
        snap = self.read["inventory_worn_1080p"]
        self.assertEqual([(i["slot"], i["id"], i["count"]) for i in snap.equipment], WORN)
        self.assertEqual(slots(snap), WORN_BAG)  # the value tooltip over the bag adds nothing

    def test_reads_the_panel_numbers(self):
        snap = self.read["inventory_1080p"]
        self.assertEqual((snap.silver, snap.gold, snap.market_value, snap.weight), (19_000_000, 1750, 1_240_000, 7))
        worn = self.read["inventory_worn_1080p"]  # its value tooltip is open: exact values
        self.assertEqual((worn.silver, worn.market_value, worn.gear_value), (18_400_000, 1_269_717, 562_135))

    def test_zone_from_the_location_bar(self):
        screen = np.full((1080, 1920, 3), 60, np.uint8)
        bar = cv2.imread(str(FIXTURES / "location_bar_1080p.png"))
        screen[1020:1020 + bar.shape[0], 1525:1525 + bar.shape[1]] = bar
        self.assertEqual(eye.read_zone(screen, self.reader), "Lymhurst Portal")
        self.assertIsNone(eye.read_zone(bar, self.reader))  # not a whole screen

    def test_no_bag_no_snapshot(self):
        self.assertIsNone(eye.read_inventory(np.full((1080, 1920, 3), 90, np.uint8), self.refs, self.reader))


if __name__ == "__main__":
    unittest.main()
