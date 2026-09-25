"""The eye against a real 1080p inventory screenshot (tests/fixtures/inventory_1080p.png).

Needs the `eye` extra and the item renders in data/icons (the dashboard caches them), so run:
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
BAG = [  # (item, count) by slot, read off the screenshot by hand
    ("T8_2H_TOOL_AXE", 1), ("T6_2H_TOOL_FISHINGROD_AVALON", 1),
    ("T4_FISH_FRESHWATER_ALL_COMMON", 32), ("T5_FISH_FRESHWATER_ALL_COMMON", 34),
    ("T3_FISH_FRESHWATER_AVALON_RARE", 15), ("T3_FISH_FRESHWATER_ALL_COMMON", 22),
    ("T1_SEAWEED", 27), ("T4_WOOD_LEVEL1@1", 6),
    ("T5_WOOD", 12), ("T4_SKILLBOOK_NONTRADABLE", 1),
    ("T3_SKILLBOOK_NONTRADABLE", 2), ("T5_FISH_FRESHWATER_AVALON_RARE", 1),
    ("T3_FISH_FRESHWATER_FOREST_RARE", 3), ("T4_WOOD_LEVEL2@2", 2),
    ("T1_SKILLBOOK_NONTRADABLE", 4), ("T4_RUNE", 1),
    ("T1_SILVERBAG_NONTRADABLE", 2),
]
GEAR = [  # a live capture: enchanted gear (one lit diamond each) and gaps between slots
    ((0, 0), "T8_2H_TOOL_AXE", 1), ((0, 1), "T6_2H_TOOL_FISHINGROD_AVALON", 1),
    ((0, 2), "T4_ARMOR_LEATHER_SET3@1", 1), ((0, 3), "T4_2H_DOUBLEBLADEDSTAFF@1", 1),
    ((1, 0), "T4_SHOES_GATHERER_ORE@1", 1), ((1, 2), "T8_POTION_CLEANSE", 3),
    ((2, 1), "T4_SKILLBOOK_NONTRADABLE", 1), ((2, 2), "T3_SKILLBOOK_NONTRADABLE", 2),
    ((3, 2), "T1_SKILLBOOK_NONTRADABLE", 4),
]


class Amounts(unittest.TestCase):
    @unittest.skipIf(eye is None, "needs the eye extra")
    def test_parse_amount(self):
        cases = {"1,750": 1750, "19m": 19_000_000, "1.24 m": 1_240_000, "12k": 12_000, "7": 7, "Loadouts": None, "": None}
        for text, want in cases.items():
            self.assertEqual(eye.parse_amount(text), want, text)

    @unittest.skipIf(eye is None, "needs the eye extra")
    def test_family_ignores_tier_and_enchant(self):
        self.assertEqual(eye.family("T4_WOOD_LEVEL1@1"), eye.family("T8_WOOD"))
        self.assertNotEqual(eye.family("T4_WOOD"), eye.family("T4_PLANKS"))


@unittest.skipIf(eye is None, "needs the eye extra")
class Screenshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all((icons.DIR / f"{i}.png").exists() for i in [i for i, _ in BAG] + [i for _, i, _ in GEAR]):
            raise unittest.SkipTest("item renders not cached in data/icons (run the dashboard once)")
        cls.img = cv2.imread(str(FIXTURES / "inventory_1080p.png"))
        cls.refs, cls.reader = eye.References(), eye.Reader()
        cls.snap = eye.read_inventory(cls.img, cls.refs, cls.reader)

    def test_finds_the_bag_grid(self):
        grid = eye.find_bag(self.img)
        self.assertEqual((grid.pitch, grid.w, grid.cols), (81, 71, 4))

    def test_grid_follows_resolution_and_ui_scale(self):
        for scale in (.75, 4 / 3):
            grid = eye.find_bag(cv2.resize(self.img, None, fx=scale, fy=scale))
            self.assertAlmostEqual(grid.pitch, 81 * scale, delta=2)

    def test_names_every_item_with_tier_and_enchant(self):
        self.assertEqual([i["id"] for i in self.snap.items], [i for i, _ in BAG])

    def test_reads_every_stack_count(self):
        self.assertEqual([i["count"] for i in self.snap.items], [n for _, n in BAG])

    def test_reads_the_panel_numbers(self):
        self.assertEqual(self.snap.market_value, 1_240_000)
        self.assertEqual(self.snap.balances, [1750, 19_000_000])
        self.assertEqual(self.snap.weight, 7)

    def test_enchanted_gear_and_gaps(self):
        snap = eye.read_inventory(cv2.imread(str(FIXTURES / "inventory_gear_1080p.png")), self.refs, self.reader)
        self.assertEqual([((i["row"], i["col"]), i["id"], i["count"]) for i in snap.items], GEAR)
        self.assertEqual((snap.balances, snap.weight), ([1750, 18_900_000], 3))

    def test_no_bag_no_snapshot(self):
        self.assertIsNone(eye.read_inventory(np.full((1080, 1920, 3), 90, np.uint8), self.refs, None))


if __name__ == "__main__":
    unittest.main()
