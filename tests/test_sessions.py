import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from silver import sessions

HOUR = 3600
PRICES = {"T4_WOOD": 100, "T5_FISH": 1000, "T4_BAG@1": 5000}


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sessions.Sessions(Path(self.tmp.name) / "s.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_session_open_at_a_time(self):
        a = self.db.start(now=100)
        b = self.db.start(now=200)
        self.assertEqual(self.db.active()["id"], b)
        self.assertEqual({s["id"]: s["ended"] for s in self.db.recent()}[a], 200)
        self.db.stop(now=300)
        self.assertIsNone(self.db.active())

    def test_unchanged_snapshots_are_not_stored_twice(self):
        s = self.db.start(now=0)
        self.assertTrue(self.db.record(s, 1000, 5, {"T4_WOOD": 3}, "Yew Wood", now=1))
        self.assertFalse(self.db.record(s, 1000, 5, {"T4_WOOD": 3}, "Yew Wood", now=2))
        self.assertTrue(self.db.record(s, 1000, 5, {"T4_WOOD": 4}, "Yew Wood", now=3))
        self.assertEqual([x["held"] for x in self.db.snapshots(s)], [{"T4_WOOD": 3}, {"T4_WOOD": 4}])

    def test_goals_are_validated(self):
        self.db.add_goal("profit", 1_000_000, "today")
        for bad in (("fame", 1, "today"), ("profit", 0, "today"), ("profit", 1, "year")):
            with self.assertRaises(ValueError):
                self.db.add_goal(*bad)
        self.assertEqual([g["kind"] for g in self.db.goals()], ["profit"])


class Summary(unittest.TestCase):
    SESSION = {"id": 1, "started": 0, "ended": 2 * HOUR}

    def snap(self, at, silver, held, zone=None):
        return {"at": at, "silver": silver, "gold": 7, "held": held, "zone": zone, "source": "eye"}

    def test_profit_is_silver_plus_items_gained(self):
        snaps = [self.snap(0, 10_000, {"T4_WOOD": 10}, "Yew Wood"),
                 self.snap(HOUR, 12_000, {"T4_WOOD": 30, "T5_FISH": 2}, "Yew Wood"),
                 self.snap(2 * HOUR, 15_000, {"T4_WOOD": 30, "T5_FISH": 2}, "Lymhurst")]
        s = sessions.summarize(self.SESSION, snaps, PRICES.get)
        self.assertEqual((s["silver"], s["loot_value"], s["profit"]), (5_000, 4_000, 9_000))
        self.assertEqual(s["per_hour"], 4_500)
        self.assertEqual(s["zones"], ["Yew Wood", "Lymhurst"])
        self.assertEqual([(x["id"], x["change"]) for x in s["loot"]], [("T4_WOOD", 20), ("T5_FISH", 2)])

    def test_selling_loot_is_not_profit(self):
        snaps = [self.snap(0, 10_000, {"T5_FISH": 5}), self.snap(HOUR, 15_000, {})]  # sold 5 fish at 1,000
        self.assertEqual(sessions.summarize(self.SESSION, snaps, PRICES.get)["profit"], 0)

    def test_unpriced_items_are_listed_but_not_counted(self):
        snaps = [self.snap(0, 0, {}), self.snap(HOUR, 0, {"QUESTITEM_X": 1})]
        s = sessions.summarize(self.SESSION, snaps, PRICES.get)
        self.assertEqual((s["loot"][0]["value"], s["profit"]), (None, 0))

    def test_silver_ignores_snapshots_without_it(self):
        snaps = [self.snap(0, 1_000, {}), self.snap(1, None, {}), self.snap(2, 4_000, {})]
        s = sessions.summarize(self.SESSION, snaps, PRICES.get)
        self.assertEqual((s["silver"], s["balance"]["silver"]), (3_000, 4_000))

    def test_no_snapshots_yet(self):
        s = sessions.summarize({"id": 1, "started": 0, "ended": None}, [], PRICES.get, now=30)
        self.assertEqual((s["profit"], s["per_hour"], s["balance"]["silver"]), (0, None, None))  # under a minute: no rate


class Goals(unittest.TestCase):
    NOW = 1_790_000_000

    def summary(self, started, hours, profit):
        return {"started": started, "hours": hours, "profit": profit, "silver": profit, "loot_value": 0}

    def test_session_goals_use_the_newest_session(self):
        summaries = [self.summary(self.NOW - 10, 1, 300), self.summary(self.NOW - 99_999, 1, 900)]
        g = sessions.goal_progress({"kind": "profit", "target": 600, "period": "session"}, summaries, self.NOW)
        self.assertEqual((g["value"], g["fraction"]), (300, .5))

    def test_today_sums_sessions_since_local_midnight(self):
        start = sessions.period_start("today", self.NOW, 0)
        summaries = [self.summary(start + 60, 1, 300), self.summary(start + 5, 1, 500), self.summary(start - 5, 1, 900)]
        g = sessions.goal_progress({"kind": "profit", "target": 1000, "period": "today"}, summaries, self.NOW)
        self.assertEqual(g["value"], 800)
        rate = sessions.goal_progress({"kind": "per_hour", "target": 100, "period": "today"}, summaries, self.NOW)
        self.assertEqual((rate["value"], rate["fraction"]), (400, 1.0))  # capped at done

    def test_week_starts_monday_in_local_time(self):
        start = sessions.period_start("week", self.NOW, -420)  # UTC+7
        from datetime import datetime, timedelta, timezone
        local = datetime.fromtimestamp(start, timezone(timedelta(hours=7)))
        self.assertEqual((local.weekday(), local.hour, local.minute), (0, 0, 0))


class Watch(unittest.TestCase):
    def test_records_what_the_eye_reads_until_stopped(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            db = sessions.Sessions(Path(tmp) / "s.db")
            run = db.start()
            reads = iter([None, SimpleNamespace(silver=1_000, gold=5, zone="Yew Wood",
                                                items=[{"id": "T4_WOOD", "count": 3}], equipment=[])])
            seen = threading.Event()

            def read_inventory(img, refs, reader):
                snap = next(reads, None)
                if snap is None and db.snapshots(run):
                    seen.set()
                return snap
            fake_eye = SimpleNamespace(read_inventory=read_inventory,
                                       Screen=lambda: SimpleNamespace(capture=lambda: "frame", close=lambda: None))
            watch = sessions.EyeWatch(db, every=.01)
            watch._eye = (fake_eye, None, None)
            watch.start(run)
            self.assertTrue(seen.wait(5))
            watch.stop()
            self.assertEqual([(s["silver"], s["held"], s["zone"]) for s in db.snapshots(run)],
                             [(1_000, {"T4_WOOD": 3}, "Yew Wood")])
            self.assertEqual(watch.status, {"state": "off"})


class Held(unittest.TestCase):
    def test_bag_and_worn_items_add_up(self):
        snap = SimpleNamespace(items=[{"id": "T4_WOOD", "count": 3}, {"id": "T4_WOOD", "count": 2}],
                               equipment=[{"id": "T4_BAG@1", "count": 1}])
        self.assertEqual(sessions.held_from(snap), {"T4_WOOD": 5, "T4_BAG@1": 1})


if __name__ == "__main__":
    unittest.main()
