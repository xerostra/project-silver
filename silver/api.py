"""Clients for the Albion Online Data Project API and the official gameinfo API.

- Every request asks for gzip (responses are ~25x smaller).
- One process-wide rate limiter keeps us under 180 req/min and 300 req/5 min.
- Item lists are split into batches so no URL exceeds the 4096-char limit.
- Big responses are yielded a batch at a time (iter_batches), so callers can store them as
  they arrive.
"""
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import requests

from . import config

URL_LIMIT = 4000  # the API rejects URLs over 4096 chars; keep some headroom
ATTEMPTS = 6
TIMEOUT = 90


class APIError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # HTTP status for client errors, None for exhausted retries


class RateLimiter:
    """Sliding-window limiter over several (max_requests, seconds) windows."""

    def __init__(self, windows):
        self.windows = [(limit, secs, deque()) for limit, secs in windows]
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                wait = 0.0
                for limit, secs, stamps in self.windows:
                    while stamps and now - stamps[0] >= secs:
                        stamps.popleft()
                    if len(stamps) >= limit:
                        wait = max(wait, secs - (now - stamps[0]))
                if wait <= 0:
                    for _, _, stamps in self.windows:
                        stamps.append(now)
                    return
            time.sleep(wait + 0.05)


# Slightly under the published limits (180/min, 300/5min).
LIMITER = RateLimiter([(170, 60), (290, 300)])
# gameinfo publishes no limits; stay polite.
GAMEINFO_LIMITER = RateLimiter([(60, 60)])


def location_param(name):
    """'Fort Sterling' -> 'FortSterling', 'Black Market' -> 'BlackMarket'."""
    return name.replace(" ", "")


def market_params(locations, qualities):
    params = {"locations": ",".join(location_param(l) for l in locations)}
    if qualities:
        params["qualities"] = ",".join(str(q) for q in qualities)
    return params


def parse_ts(value):
    """API timestamp (naive UTC) -> epoch seconds, or None for 'no data' or garbage."""
    if not isinstance(value, str) or value.startswith("0001"):
        return None
    try:
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def parse_price_row(r):
    """Raw API row -> {item_id, city, quality, sell, sell_at, buy, buy_at}, or None if unusable.
    A price only counts when it comes with a valid timestamp."""
    try:
        item_id, city, quality = str(r["item_id"]), str(r["city"]), int(r["quality"])
    except (KeyError, TypeError, ValueError):
        return None
    sell, buy = positive_int(r.get("sell_price_min")), positive_int(r.get("buy_price_max"))
    sell_at = parse_ts(r.get("sell_price_min_date")) if sell else None
    buy_at = parse_ts(r.get("buy_price_max_date")) if buy else None
    sell, buy = (sell if sell_at else None), (buy if buy_at else None)
    if not sell and not buy:
        return None
    return {"item_id": item_id, "city": city, "quality": quality,
            "sell": sell, "sell_at": sell_at, "buy": buy, "buy_at": buy_at}


class JSONClient:
    """GET JSON with retries on network errors, 429/5xx and unparseable bodies."""
    user_agent = "project-silver (personal market tool)"

    def __init__(self, host, limiter=None):
        self.host = host.rstrip("/")
        self.limiter = limiter
        self._local = threading.local()  # requests.Session isn't guaranteed thread-safe

    @property
    def session(self):
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({"Accept-Encoding": "gzip", "User-Agent": self.user_agent})
            self._local.session = s
        return self._local.session

    def _get(self, path, params, expect=list):
        """GET path and return the JSON body, which must be an `expect` instance."""
        url = self.host + path
        last = "no attempt made"
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(min(2 ** attempt, 30))
            if self.limiter:
                self.limiter.acquire()
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last = f"network error: {exc}"
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                try:
                    time.sleep(min(float(resp.headers.get("Retry-After", 0)), 120))
                except ValueError:
                    pass
                continue
            if resp.status_code >= 400:
                raise APIError(f"HTTP {resp.status_code} for {url[:200]}", resp.status_code)
            try:
                data = resp.json()
            except ValueError:
                last = "response was not valid JSON"
                continue
            if isinstance(data, expect):
                return data
            last = f"response was not a JSON {expect.__name__}"
        raise APIError(f"API failed after {ATTEMPTS} attempts ({last}): {url[:200]}")


class AlbionAPI(JSONClient):
    """Albion Online Data Project: market prices, history and gold."""

    def __init__(self, host=None):
        super().__init__(host or config.HOST, LIMITER)

    def _batches(self, ids, endpoint, params):
        """Split ids so '{host}/api/v2/stats/{endpoint}/{a,b,c}.json?{params}' fits URL_LIMIT."""
        fixed = len(self.host) + len(f"/api/v2/stats/{endpoint}/.json?") + len(urlencode(params))
        batch, size = [], fixed
        for item_id in ids:
            cost = len(quote(item_id, safe="")) + 1
            if batch and size + cost > URL_LIMIT:
                yield batch
                batch, size = [], fixed
            batch.append(item_id)
            size += cost
        if batch:
            yield batch

    def iter_batches(self, ids, endpoint, params, workers=4, progress=None):
        """Yield each batch's rows in batch order, fetching up to `workers` batches ahead, so a
        caller can store big responses as they arrive instead of holding them all. A failed
        batch raises: partial data would silently skew the results."""
        batches = list(self._batches(sorted(set(ids)), endpoint, params))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            pending = deque()
            queued = iter(batches)
            for done in range(1, len(batches) + 1):
                while len(pending) < workers and (b := next(queued, None)) is not None:
                    pending.append(pool.submit(self._get, f"/api/v2/stats/{endpoint}/{','.join(b)}.json", params))
                rows = pending.popleft().result()
                if progress:
                    progress(done, len(batches))
                yield rows
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def gold(self, hours=750):
        """Hourly gold price in silver, newest first as the API returns it (it keeps about 30 days)."""
        return self._get("/api/v2/stats/gold.json", {"count": hours})


class GameInfo(JSONClient):
    """Official gameinfo API: kill events, players, guilds. Undocumented, so callers
    treat every field as optional."""
    PAGE = 51           # the largest page it accepts
    MAX_OFFSET = 1000   # deeper offsets are rejected: only the newest ~1,050 events are reachable

    def __init__(self, host=None):
        super().__init__(host or config.GAMEINFO_HOST, GAMEINFO_LIMITER)

    def events(self, offset=0):
        return self._get("/events", {"limit": self.PAGE, "offset": offset})

    def search(self, query):
        return self._get("/search", {"q": query}, expect=dict)

    def player(self, player_id):
        return self._get(f"/players/{quote(player_id, safe='')}", {}, expect=dict)

    def player_events(self, player_id, kind):
        """kind: 'kills' or 'deaths' (the API returns the most recent few)."""
        return self._get(f"/players/{quote(player_id, safe='')}/{kind}", {})

    def guild(self, guild_id):
        return self._get(f"/guilds/{quote(guild_id, safe='')}/data", {}, expect=dict)

    def guild_members(self, guild_id):
        return self._get(f"/guilds/{quote(guild_id, safe='')}/members", {})

