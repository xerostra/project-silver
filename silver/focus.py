"""Focus planner: where does a point of focus earn the most silver today?

Focus has three uses we can price:
- Refining/crafting: +59% production bonus, so a higher return rate and cheaper materials.
- Watering crops: extra seeds (focus bonus x seed value per 1,000 focus).
- Feeding animals with focus: extra offspring chance per feeding.

A use is ranked by the silver focus *adds*: profit with focus minus the profit you'd make
without it (or minus nothing, if without focus you wouldn't do it at all), per focus point.
Focus cost is the recipe's base cost reduced by your specs (`reduction`, 0..1); the game data
says each 100 focus efficiency points halves it.

Everything here is pure: prices arrive as a Market object, so results are deterministic.
"""
import time

from . import config, crafting, farming


class Market:
    """Price lookups over a snapshot: {item: {city: price row}} and {item: {city: history}}."""

    def __init__(self, prices, volumes, premium=True, max_age_hours=6, now=None):
        self.prices, self.volumes = prices, volumes
        self.tax = config.SALES_TAX_PREMIUM if premium else config.SALES_TAX_NO_PREMIUM
        self.oldest = (time.time() if now is None else now) - max_age_hours * 3600

    def ask(self, item, cities=config.CITIES):
        """Cheapest fresh sell order: what buying one costs now."""
        asks = [p["sell"] for c, p in self.prices.get(item, {}).items()
                if c in cities and p.get("sell") and (p.get("sell_at") or 0) >= self.oldest]
        return min(asks) if asks else None

    def sale(self, item):
        """Net silver for selling one over time: 7-day average listed as a sell order (tax +
        setup fee), else a fresh buy order (tax only). Best market wins."""
        best = None
        for city, h in self.volumes.get(item, {}).items():
            net = h["avg"] * (1 - self.tax - config.SETUP_FEE)
            best = net if best is None or net > best else best
        if best is None:
            for city, p in self.prices.get(item, {}).items():
                if p.get("buy") and (p.get("buy_at") or 0) >= self.oldest:
                    net = p["buy"] * (1 - self.tax)
                    best = net if best is None or net > best else best
        return best

    def daily_volume(self, item):
        vols = [h["vol"] for h in self.volumes.get(item, {}).values()]
        return max(vols) if vols else None


def return_rate(recipe, focus, daily_bonus=0.0):
    """Return rate in the recipe's specialty city (you'd craft where the bonus is)."""
    bonus = crafting.BASE_BONUS + daily_bonus
    if recipe.get("bonus_city"):
        bonus += crafting.REFINE_SPECIALTY if recipe["bonus"].startswith("refine:") else crafting.CRAFT_SPECIALTY
    if focus:
        bonus += crafting.FOCUS_BONUS
    return bonus / (1 + bonus)


def craft_use(recipe, market, reduction=0.0, station_fee=config.STATION_FEE):
    """Silver per focus for one recipe, or None if it can't be priced or focus doesn't pay."""
    if recipe.get("upgrade") or recipe.get("focus", 0) <= 0:
        return None
    sale = market.sale(recipe["id"])
    asks = {i: market.ask(i) for i, _, _ in recipe["inputs"]}
    if not sale or not all(asks.values()):
        return None

    def profit(rate):
        mats = sum(n * asks[i] * ((1 - rate) if returned else 1) for i, n, returned in recipe["inputs"])
        fee = recipe["value"] * crafting.NUTRITION_PER_VALUE * station_fee / 100
        return sale * recipe["amount"] - mats - fee - recipe["silver"]

    without, with_focus = profit(return_rate(recipe, False)), profit(return_rate(recipe, True))
    focus = recipe["focus"] * (1 - reduction)
    gain = with_focus - max(0.0, without)
    if with_focus <= 0 or gain <= 0 or focus <= 0:
        return None
    volume = market.daily_volume(recipe["id"])
    return {
        "kind": "Refine" if recipe["section"] == "Refining" else "Craft",
        "id": recipe["id"], "name": recipe["name"], "tier": recipe["tier"], "enchant": recipe["enchant"],
        "group": recipe["group"], "where": recipe.get("bonus_city") or "any city",
        "focus": focus, "gain": gain, "per_focus": gain / focus,
        "profit_with": with_focus, "profit_without": without,
        # crafts a day before we'd flood the market: a share of daily volume, in whole crafts
        "daily_cap": int(volume * config.VOLUME_CAPTURE / recipe["amount"]) if volume else None,
    }


def farm_uses(market, premium=True):
    """Watering and focused feeding, per action."""
    cat = farming.catalog()
    sell, buy = market.sale, market.ask
    out = []
    for plant in cat["plants"]:
        watered = farming.plant_result(plant, sell, buy, premium, water=True)
        dry = farming.plant_result(plant, sell, buy, premium, water=False)
        if not watered or not dry or watered["profit"] <= 0:
            continue
        gain = watered["profit"] - max(0.0, dry["profit"])
        if gain > 0 and plant["focus_cost"]:
            out.append({"kind": "Water", "id": plant["id"], "name": plant["name"], "tier": plant["tier"],
                        "enchant": 0, "group": plant["kind"], "where": "island farm",
                        "focus": plant["focus_cost"], "gain": gain, "per_focus": gain / plant["focus_cost"],
                        "profit_with": watered["profit"], "profit_without": dry["profit"], "daily_cap": None})
    for animal in cat["animals"]:
        fed = farming.animal_result(animal, sell, buy, cat["foods"], animal["feedings"])
        plain = farming.animal_result(animal, sell, buy, cat["foods"], 0)
        if not fed or not plain or fed["profit"] <= 0 or not fed["focus"]:
            continue
        gain = fed["profit"] - max(0.0, plain["profit"])
        if gain > 0 and animal["days"] > 0:
            # an animal spreads its feedings over days; count it per animal per day so it
            # competes fairly with daily actions (a craft now, a watering every 22 hours)
            days = animal["days"]
            out.append({"kind": "Breed", "id": animal["id"], "name": animal["name"], "tier": animal["tier"],
                        "enchant": 0, "group": "Animal", "where": "island pasture",
                        "focus": fed["focus"] / days, "gain": gain / days, "per_focus": gain / fed["focus"],
                        "profit_with": fed["profit"], "profit_without": plain["profit"], "daily_cap": None,
                        "days": days})
    return out


def rank(market, reduction=0.0, station_fee=config.STATION_FEE, premium=True):
    """Every priced focus use, best silver per focus first (ties broken by id: deterministic)."""
    uses = [u for r in crafting.recipes().values() if (u := craft_use(r, market, reduction, station_fee))]
    uses += farm_uses(market, premium)
    return sorted(uses, key=lambda u: (-u["per_focus"], u["id"]))


def plan(uses, daily_focus, farm_slots=0, pasture_slots=0):
    """Greedy: spend the day's focus on the best uses first. Each use is capped by what the
    market can absorb (crafts: a share of daily volume) or by your free island slots
    (crops/herbs on farm plots, animals on pastures). Crafts with unknown volume are skipped."""
    left = daily_focus
    free = {"Water": farm_slots, "Breed": pasture_slots}
    steps = []
    for u in uses:
        cap = free[u["kind"]] if u["kind"] in free else (u["daily_cap"] or 0)
        times = min(cap, int(left // u["focus"])) if u["focus"] > 0 else 0
        if times <= 0:
            continue
        if u["kind"] in free:
            free[u["kind"]] -= times
        left -= times * u["focus"]
        steps.append({**u, "times": times, "focus_used": times * u["focus"], "silver": times * u["gain"]})
    return {"steps": steps, "focus_used": daily_focus - left, "silver": sum(s["silver"] for s in steps)}


def item_ids():
    """Every item the planner needs a price for."""
    ids = set(farming.item_ids())
    for r in crafting.recipes().values():
        if not r.get("upgrade") and r.get("focus", 0) > 0:
            ids.add(r["id"])
            ids.update(i for i, _, _ in r["inputs"])
    return sorted(ids)
