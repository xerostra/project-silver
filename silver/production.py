"""Make or buy: the cheapest way to produce an item when its ingredients can be made too.

A Master's Broadsword needs T6 bars and leather. Each can be bought, or refined from ore/hide and
the tier-below bar/leather, which can themselves be bought or refined, and so on. Every step
returns part of its returnable inputs (the return rate of the city it is done in, plus focus and
any daily bonus) and costs a station fee, so making an ingredient is often cheaper than buying it.
The planner picks, for every ingredient at every level, whichever is cheaper per unit.

Intermediate steps are done in their specialty city (refine ore in Thetford, ...) when
best_city is set: hauling materials between royal cities is safe. The final step is done where
the caller says.
"""
from collections import defaultdict

from .crafting import BASE_BONUS, CRAFT_SPECIALTY, FOCUS_BONUS, NUTRITION_PER_VALUE, REFINE_SPECIALTY


def return_rate(recipe, city, focus=False, daily=0.0):
    """Share of returnable inputs given back: B / (1 + B) (see crafting.py)."""
    b = BASE_BONUS + daily
    if recipe.get("bonus_city") and recipe["bonus_city"] == city:
        b += REFINE_SPECIALTY if recipe["bonus"].startswith("refine:") else CRAFT_SPECIALTY
    if focus:
        b += FOCUS_BONUS
    return b / (1 + b)


class Planner:
    """ways: {item id: [station recipes that make it]}; buy(item id) -> (unit price, where) or None."""

    def __init__(self, ways, buy, city, focus=False, daily=0.0, station_fee=300, best_city=True, max_depth=16):
        self.ways, self.buy = ways, buy
        self.city, self.focus, self.daily, self.station_fee = city, focus, daily, station_fee
        self.best_city, self.max_depth = best_city, max_depth
        self.memo, self.visiting = {}, set()

    def item(self, item_id, depth=0):
        """Cheapest way to get one unit: {"unit", "focus", "how": "buy", "where"} or
        {"unit", "focus", "how": "make", "city", "rate", "recipe", "inputs"}; None if impossible."""
        if item_id in self.memo:
            return self.memo[item_id]
        options = []
        bought = self.buy(item_id)
        if bought:
            options.append({"unit": bought[0], "focus": 0, "how": "buy", "where": bought[1]})
        if depth < self.max_depth and item_id not in self.visiting:
            self.visiting.add(item_id)  # a recipe that (indirectly) needs its own product can't help
            try:
                options += [m for r in self.ways.get(item_id, ()) if (m := self.make(r, depth=depth))]
            finally:
                self.visiting.discard(item_id)
        # ties go to buying: fewer steps. Chains are at most ~8 deep (T8 down to T1), so the depth
        # limit is only a guard against malformed data and never shapes a memoised answer.
        best = min(options, key=lambda o: (o["unit"], o["how"] == "make")) if options else None
        self.memo[item_id] = best
        return best

    def make(self, recipe, city=None, depth=0):
        """Cost of one unit made with this recipe, each ingredient got the cheapest way."""
        city = city or (recipe.get("bonus_city") if self.best_city and recipe.get("bonus_city") else self.city)
        rate = return_rate(recipe, city, self.focus, self.daily)
        total, focus, inputs = 0.0, (recipe.get("focus") or 0) if self.focus else 0, []
        for item_id, count, returned in recipe["inputs"]:
            got = self.item(item_id, depth + 1)
            if not got:
                return None
            used = count * ((1 - rate) if returned else 1)
            total += used * got["unit"]
            focus += used * got["focus"]
            inputs.append((item_id, used, got))
        fee = recipe.get("value", 0) * NUTRITION_PER_VALUE * self.station_fee / 100
        amount = recipe.get("amount") or 1
        return {"unit": (total + fee + recipe.get("silver", 0)) / amount, "focus": focus / amount, "how": "make",
                "city": city, "rate": rate, "recipe": recipe, "inputs": inputs}


def summarize(plan):
    """A made plan -> what to do per unit of product: {"cost", "focus", "buys": [{id, units,
    price, where}], "makes": [{id, units, city, rate}]}, biggest spends first."""
    buys, makes = defaultdict(float), defaultdict(float)
    meta = {}

    def walk(node, units):
        for item_id, used, got in node["inputs"]:
            n = units * used
            if got["how"] == "buy":
                buys[item_id] += n
                meta[("buy", item_id)] = got
            else:
                per_craft = got["recipe"].get("amount") or 1
                makes[(item_id, got["city"])] += n
                meta[("make", item_id, got["city"])] = got
                walk(got, n / per_craft)

    walk(plan, 1)
    return {
        "cost": plan["unit"], "focus": plan["focus"],
        "buys": sorted(({"id": i, "units": round(n, 3), "price": meta[("buy", i)]["unit"], "where": meta[("buy", i)]["where"]}
                        for i, n in buys.items()), key=lambda b: -b["units"] * b["price"]),
        "makes": sorted(({"id": i, "units": round(n, 3), "city": c, "rate": round(meta[("make", i, c)]["rate"], 4)}
                         for (i, c), n in makes.items()), key=lambda m: -m["units"]),
    }
