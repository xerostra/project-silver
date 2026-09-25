"""Gold price: hourly points from the data project, summarised for the dashboard."""
from .api import parse_ts

DAY = 86400


def parse_points(raw):
    """API rows -> [(epoch, price)] oldest first, one per timestamp; bad rows are skipped."""
    points = {}
    for r in raw:
        if not isinstance(r, dict):
            continue
        at, price = parse_ts(r.get("timestamp")), r.get("price")
        if at and isinstance(price, int) and not isinstance(price, bool) and price > 0:
            points[at] = price
    return sorted(points.items())


def _change(points, now_price, at):
    """Relative change from the last price at or before `at`, or None if the series is shorter."""
    before = [p for t, p in points if t <= at]
    return (now_price - before[-1]) / before[-1] if before else None


def summary(points):
    """Current price and its movement, measured back from the newest point (not the clock),
    so a feed that stalled for a few hours still compares like with like."""
    if not points:
        return None
    last_at, last = points[-1]
    week = [p for t, p in points if t > last_at - 7 * DAY]
    return {
        "price": last, "at": last_at,
        "change_24h": _change(points, last, last_at - DAY),
        "change_7d": _change(points, last, last_at - 7 * DAY),
        "high_7d": max(week), "low_7d": min(week),
        "avg_7d": round(sum(week) / len(week)),
        "avg_all": round(sum(p for _, p in points) / len(points)),
        "days": round((last_at - points[0][0]) / DAY, 1),
    }
