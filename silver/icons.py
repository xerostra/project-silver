"""Item icons, cached on disk (data/icons/) so each is fetched from the render service once.

The render service is slow (1 to 60 s for an image it hasn't rendered lately, as long for a
missing one) and its CDN keeps images a day. So an icon we have is served from disk with a
year-long browser cache; one we don't have yet is fetched in the background while the browser
is sent to the render service for now. Pages also queue the icons they list (want), so they are
usually on disk before anyone scrolls to them. Icons a browser is waiting for jump the queue.

One image per item: quality only changes the frame, and every quality would be another slow render.
"""
import itertools
import queue
import re
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote

import requests

from .paths import DATA_DIR

DIR = DATA_DIR / "icons"
RENDER = "https://render.albiononline.com/v1/item/{}.png?quality=1&size=64"
ITEM_ID = re.compile(r"[A-Z0-9_]+(@[1-4])?")  # also keeps request paths out of anywhere but DIR
PNG = b"\x89PNG\r\n\x1a\n"
RETRY_MISSING = 7 * 86400  # the render service learns new items after a patch
WORKERS = 6


def render_url(item_id):
    return RENDER.format(quote(item_id, safe=""))


URGENT, SOON = 0, 1  # queue priorities: a browser is showing it / a page listed it


class Icons:
    def __init__(self, directory=DIR, fetch=None, workers=WORKERS):
        self.dir = Path(directory)
        self.fetch = fetch or self._download
        self.lock = threading.Lock()
        self.pending = {}  # item id -> best priority it is queued at
        self.queue = queue.PriorityQueue()
        self.order = itertools.count()  # first come, first served within a priority
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True, name="icons").start()

    def path(self, item_id):
        """The cached PNG, or None if it isn't on disk (yet)."""
        p = self.dir / f"{item_id}.png"
        return p if p.exists() else None

    def missing(self, item_id):
        """True if the render service said it has no such icon, recently."""
        p = self.dir / f"{item_id}.missing"
        return p.exists() and time.time() - p.stat().st_mtime < RETRY_MISSING

    def want(self, ids, priority=SOON):
        """Queue downloads for icons not on disk and not known missing. An icon already queued
        is queued again only at a more urgent priority (the worker skips the stale entry)."""
        for item_id in ids:
            if not item_id or not ITEM_ID.fullmatch(item_id) or self.path(item_id) or self.missing(item_id):
                continue
            with self.lock:
                if self.pending.get(item_id, priority + 1) <= priority:
                    continue
                self.pending[item_id] = priority
            self.queue.put((priority, next(self.order), item_id))

    def join(self):
        """Wait until every queued icon has been handled (for tests)."""
        self.queue.join()

    def _work(self):
        while True:
            priority, _, item_id = self.queue.get()
            try:
                with self.lock:
                    stale = self.pending.get(item_id) != priority
                if not stale:
                    self._get(item_id)
            finally:
                self.queue.task_done()

    def _get(self, item_id):
        try:
            data = self.fetch(item_id)
            self.dir.mkdir(parents=True, exist_ok=True)
            if data is None:
                (self.dir / f"{item_id}.missing").touch()
            else:
                tmp = self.dir / f"{item_id}.png.{threading.get_ident()}.tmp"
                tmp.write_bytes(data)
                tmp.replace(self.dir / f"{item_id}.png")  # atomic: a reader never sees half a file
        except Exception:  # network trouble: not cached, so the next page view queues it again
            traceback.print_exc()
        finally:
            with self.lock:
                self.pending.pop(item_id, None)

    @staticmethod
    def _download(item_id):
        """PNG bytes, or None if the render service has no icon for it. Raises on errors worth
        retrying later (network, 5xx, a body that isn't a PNG)."""
        resp = requests.get(render_url(item_id), timeout=90)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        if not resp.content.startswith(PNG):
            raise ValueError(f"icon {item_id}: not a PNG")
        return resp.content
