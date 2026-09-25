"""The eye: reads your inventory off a screenshot.

Pixels only. Nothing here touches the game, its files or its network traffic, and nothing sends
keyboard or mouse input; a screenshot is all it ever sees.

Items are named by template matching against the official item renders in data/icons: each
file is named by its item id, so the closest render *is* the answer. The renders are the same
tiles the game draws (frame, tier colour, numeral), so matching is a plain correlation, no model.
The tier comes from the badge colour (grey I, green III, blue IV, red V ... white VIII), learned
from the renders themselves: enchanted resources use one glowing tile at every tier, so the picture
alone can't tell T4 from T8. The enchant is the number of lit diamonds under the art (green .1,
blue .2, purple .3, gold .4); unenchanted items show dark ones. On screen the glow sits outside
the frame, so the diamonds, not the glow, are what the tile shows.
Numbers (stack counts, balances, the game's own market value estimate) are read by OCR.

Needs the `eye` extra: `uv sync --extra eye`.
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import icons as icons_mod

COARSE = 40   # compare every render at this size...
FINE = 64     # ...then the best few candidates at this one

# Corners that change from stack to stack: the count circle, the status badge, the enchant dots.
_COARSE_MASK = np.ones((COARSE, COARSE, 1), np.float32)
_COARSE_MASK[int(COARSE * .66):, int(COARSE * .58):] = 0
_COARSE_MASK[:int(COARSE * .30), int(COARSE * .72):] = 0
_COARSE_MASK[int(COARSE * .88):, :] = 0


def _coarse(tile):
    f = cv2.resize(tile, (COARSE, COARSE), interpolation=cv2.INTER_AREA).astype(np.float32)
    f = (f - (f * _COARSE_MASK).sum() / (_COARSE_MASK.sum() * 3)) * _COARSE_MASK
    return (f / (np.linalg.norm(f) + 1e-6)).ravel()


def _badge(tile):
    """Colour of the tier badge circle (top left), in Lab, leaving out the light numeral strokes."""
    t = cv2.resize(tile, (FINE, FINE), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(t[4:16, 4:16], cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    return lab[lab[:, 0] < np.percentile(lab[:, 0], 70)].mean(0)


def tier(item_id):
    m = re.match(r"T(\d)_", item_id)
    return int(m[1]) if m else None


DIAMONDS = [.25 + .105 * k for k in range(4)]  # socket centres across the slot, as fractions of its width


def enchant_of(img, x, y, w, h):
    """Lit enchant diamonds on the slot at (x, y): 0 to 4. They sit in a row of four sockets
    along the bottom left, lit from the first one, all one colour (green .1, blue .2, purple .3,
    gold .4). So a bright blob only counts at a socket position, in line with and the colour of
    the first: blue boot soles or glowing art beside the row don't. The band searched runs a few
    pixels past the slot, so a slightly-off position can't cut the row off."""
    top = y + int(h * .78)
    band = img[top:y + h + max(3, h // 20), x:x + w]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    lit = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 120)).astype(np.uint8)
    k = max(2, round(w * .03))  # opening strips thin glow lines that can fuse onto a diamond
    lit = cv2.morphologyEx(lit, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    _, labels, stats, centres = cv2.connectedComponentsWithStats(lit)
    H, W = lit.shape
    small, big = max(2, w * .04), w * .16
    blobs = [(cx, cy, float(np.median(hsv[:, :, 0][labels == i])))
             for i, ((bx, by, bw, bh, area), (cx, cy)) in enumerate(zip(stats, centres)) if i
             and small <= bw <= big and small <= bh <= big and area >= .35 * bw * bh
             and bx > 0 and by > 0 and bx + bw < W and by + bh < H]
    near = lambda k, shift: [b for b in blobs if abs(b[0] - (w * DIAMONDS[k] + shift)) <= w * .04]
    first = near(0, 0) or near(0, w * .04) or near(0, -w * .04)
    if not first:
        return 0
    cx0, cy0, hue0 = first[0]
    shift, count = cx0 - w * DIAMONDS[0], 1
    for k in (1, 2, 3):
        if not any(abs(cy - cy0) <= 3 and min(abs(hue - hue0), 180 - abs(hue - hue0)) <= 12 for _, cy, hue in near(k, shift)):
            break
        count += 1
    return count


def enchant(item_id):
    return int(item_id.split("@")[1]) if "@" in item_id else 0


def render_tile(rgba):
    """The frame part of a 64px render. Art that spills past the frame (a fish tail, a staff tip)
    is left out, so every render is cropped to the same box the game draws."""
    opaque = rgba[:, :, 3] > 200
    rows, cols = np.where(opaque.mean(1) > .6)[0], np.where(opaque.mean(0) > .6)[0]
    if not len(rows) or not len(cols):
        return None
    return rgba[rows.min():rows.max() + 1, cols.min():cols.max() + 1, :3]


def family(item_id):
    """What an item is, whatever its tier and enchant: T4_WOOD_LEVEL1@1 and T8_WOOD -> WOOD."""
    return re.sub(r"_LEVEL\d", "", re.sub(r"^T\d_", "", item_id.split("@")[0]))


class References:
    """Every cached render, ready to compare against a screen tile."""

    def __init__(self, directory=icons_mod.DIR):
        self.ids, coarse, self.tiles = [], [], []
        for path in sorted(Path(directory).glob("*.png")):
            rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
                continue
            tile = render_tile(rgba)
            if tile is None:
                continue
            self.ids.append(path.stem)
            coarse.append(_coarse(tile))
            self.tiles.append(tile)
        self.coarse = np.stack(coarse) if coarse else np.zeros((0, COARSE * COARSE * 3), np.float32)
        self.families = {}
        for i, item_id in enumerate(self.ids):
            self.families.setdefault(family(item_id), []).append(i)
        # Each tier's badge colour, from plain (unenchanted) renders, whose badges carry it.
        by_tier = {}
        for item_id, tile in zip(self.ids, self.tiles):
            if tier(item_id) and "@" not in item_id and "_LEVEL" not in item_id:
                by_tier.setdefault(tier(item_id), []).append(_badge(tile))
        self.badges = {t: np.median(v, 0) for t, v in by_tier.items()}

    def settle(self, img, x, y, w, h, reach=3, shortlist=30):
        """(x, y) nudged up to `reach` px to where the slot's art lines up best with the renders
        it most resembles. Worn gear sits at measured offsets that can be a couple of pixels out,
        and the match is sensitive to that; the bag grid is measured from its own frames."""
        tile = img[y:y + h, x:x + w]
        if tile.shape[:2] != (h, w):
            return x, y
        near = np.argsort(-(self.coarse @ _coarse(tile)))[:shortlist]
        best, where = -1.0, (x, y)
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                t = img[y + dy:y + dy + h, x + dx:x + dx + w]
                if t.shape[:2] == (h, w) and (score := float((self.coarse[near] @ _coarse(t)).max())) > best:
                    best, where = score, (x + dx, y + dy)
        return where

    def tier_of(self, tile):
        """The tier a slot's badge colour says, or None without references."""
        b = _badge(tile)
        return min(self.badges, key=lambda t: float(np.linalg.norm(self.badges[t] - b)), default=None)

    def identify(self, tile, lit=0):
        """(item id, confidence 0-1) for one slot tile, or (None, 0) with no references.
        `lit` is the enchant diamonds counted on screen. Confidence is how well the item's
        best-matching variant fits: an enchanted item on screen lacks its render's glow, so its
        own variant fits a little less well."""
        if not self.ids:
            return None, 0.0
        scores = self.coarse @ _coarse(tile)
        best = int(np.argmax(scores))
        confidence = float(scores[best])
        # The picture finds the item; the badge colour picks its tier and the diamonds its enchant.
        variants = self.families[family(self.ids[best])]
        on_tier = [i for i in variants if tier(self.ids[i]) == self.tier_of(tile)] or variants
        pool = [i for i in on_tier if enchant(self.ids[i]) == lit] or on_tier
        best = max(pool, key=lambda i: scores[i])
        return self.ids[best], confidence


@dataclass
class Grid:
    """The bag's slot grid on screen: top-left cell, spacing and cell size, in pixels."""
    x: int
    y: int
    pitch: int
    w: int
    h: int
    cols: int
    rows: int

    def cells(self):
        for r in range(self.rows):
            for c in range(self.cols):
                yield r, c, self.x + c * self.pitch, self.y + r * self.pitch


def _squares(img):
    """Boxes of grey-metal squares: every filled slot's frame, plus whatever else looks like one."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    metal = ((hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 50) & (hsv[:, :, 2] < 170)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(metal, connectivity=8)
    return [tuple(int(v) for v in s[:4]) for s in stats[1:] if 24 <= s[2] <= 240 and .85 < s[2] / s[3] < 1.2]


def _grid_of(boxes):
    """The largest set of boxes on one regular grid, with its spacing, or ([], 0)."""
    w = int(np.median([b[2] for b in boxes]))
    gaps = [abs(a[0] - b[0]) for a in boxes for b in boxes if abs(a[1] - b[1]) < 4 and w < abs(a[0] - b[0]) < 1.4 * w]
    if not gaps:
        return [], 0
    pitch = float(np.median(gaps))

    def on_grid(anchor, b):
        dx, dy = (b[0] - anchor[0]) / pitch, (b[1] - anchor[1]) / pitch
        return abs(dx - round(dx)) * pitch <= 3 and abs(dy - round(dy)) * pitch <= 3
    anchor = max(boxes, key=lambda a: sum(on_grid(a, b) for b in boxes))
    members = [b for b in boxes if on_grid(anchor, b)]
    # Refine the spacing over the widest spans: a pixel off per cell adds up over a tall bag.
    spans = [(abs(a[i] - b[i]), round(abs(a[i] - b[i]) / pitch)) for a in members for b in members for i in (0, 1)]
    far = max(n for _, n in spans)
    return members, (np.median([d / n for d, n in spans if n == far]) if far else pitch)


def bag_candidates(img, cols=4):
    """Grids that could be the inventory bag, likeliest first: same-size frames on one regular
    grid, at most four wide (the skill bar is a grid too, but ten wide). Menu buttons can form
    such a grid as well, so the reader keeps the first whose tiles turn out to be items.
    No resolution, UI scale or panel position needs configuring."""
    squares = _squares(img)
    found = []
    for size in sorted({b[2] for b in squares}):
        group = [b for b in squares if abs(b[2] - size) <= .08 * size]
        members, pitch = _grid_of(group) if len(group) >= 3 else ([], 0)
        if len(members) < 3 or any(m in f[1] for f in found for m in members):
            continue  # too few, or the same grid again at a neighbouring size
        if round((max(b[0] for b in members) - min(b[0] for b in members)) / pitch) + 1 > cols:
            continue
        w, h = int(np.median([b[2] for b in members])), int(np.median([b[3] for b in members]))
        x0, y0, pitch = min(b[0] for b in members), min(b[1] for b in members), int(round(pitch))
        # Every row that fits: frames blend into some art, so the rows with detected frames can
        # stop short of the last item. Cells past the bag are empty or match nothing and get dropped.
        rows = max(1, min(12, (img.shape[0] - y0 - h) // pitch + 1))
        found.append((Grid(x0, y0, pitch, w, h, cols, rows), members))
    return sorted(found, key=lambda f: -len(f[1]))


def find_bag(img):
    """The likeliest bag grid by shape alone (read_inventory also checks its tiles are items)."""
    found = bag_candidates(img)
    return found[0][0] if found else None


# Worn gear: a paper doll above the bag, at fixed offsets in slot pitches from the bag's first
# cell (measured at 1080p; offsets in pitches follow the UI scale). The middle column sits higher.
# The mount is drawn in grey, so neither its picture nor its tier colour can be matched: not read.
EQUIPMENT = {
    "bag": (.52, -4.20), "head": (1.62, -4.35), "cape": (2.75, -4.20),
    "weapon": (.52, -3.19), "armor": (1.62, -3.34), "offhand": (2.75, -3.19),
    "potion": (.52, -2.19), "shoes": (1.62, -2.33), "food": (2.75, -2.19),
}


def is_empty(tile):
    """An empty slot is bare parchment: warm, light and plain. A filled one is framed art."""
    hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
    parchment = (hsv[:, :, 0] >= 5) & (hsv[:, :, 0] <= 30) & (hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 140)
    return parchment.mean() > .6


_AMOUNT = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*([kmb])?\s*$", re.I)


def parse_amount(text):
    """'1,750' -> 1750, '19m' -> 19000000, '1.24 m' -> 1240000; None if it isn't an amount."""
    m = _AMOUNT.match(text.replace(" ", ""))
    if not m:
        return None
    value = float(m[1].replace(",", ""))
    return round(value * {None: 1, "k": 1e3, "m": 1e6, "b": 1e9}[m[2] and m[2].lower()])


class Reader:
    """OCR for the numbers on screen (RapidOCR, runs on the CPU in well under a second)."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR  # heavy import: only when the eye runs
        self.ocr = RapidOCR()

    def lines(self, img):
        """[(x, y, text)] for every line of text found in img."""
        result, _ = self.ocr(img)
        return [(int(min(p[0] for p in box)), int(min(p[1] for p in box)), text) for box, text, _ in result or []]

    def count(self, tile):
        """The stack count: white digits in the dark circle at the bottom right (1 if unreadable).
        Faint strokes make any single threshold fragile, so a few vote, weighted by OCR confidence."""
        h, w = tile.shape[:2]
        circle = tile[int(h * .66):int(h * .98), int(w * .58):int(w * .98)]
        hsv = cv2.cvtColor(circle, cv2.COLOR_BGR2HSV).astype(np.int16)
        white = np.clip(hsv[:, :, 2] - hsv[:, :, 1], 0, 255).astype(np.uint8)   # digits: bright and colourless
        white = cv2.resize(white, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
        votes = {}
        for level in (115, 125, 135, 150):
            page = _digit_line(white > level)
            if page is None:
                continue
            result, _ = self.ocr(page, use_det=False, use_cls=False)
            if result and (text := result[0][0].strip()).isdigit():
                votes[int(text)] = votes.get(int(text), 0) + float(result[0][1])
        return max(votes, key=votes.get) if votes else 1


def _digit_line(ink):
    """Digit-sized blobs of a binary mask, cropped and padded into a black-on-white text line
    (what OCR reads best), or None when there are none."""
    ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # rejoin thin strokes
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    H, W = ink.shape
    # off the edges, tall enough, and near the circle's middle (digits are centred in it):
    # drops the ring's highlight and any glow or art poking into the corner
    keep = [i for i in range(1, n) if stats[i, 1] > 2 and stats[i, 1] + stats[i, 3] < H - 2
            and stats[i, 0] > 2 and stats[i, 0] + stats[i, 2] < W - 2 and stats[i, 3] > H * .2
            and abs(stats[i, 0] + stats[i, 2] / 2 - W * .65) < W * .23]
    if not keep:
        return None
    on = np.isin(labels, keep)
    ys, xs = np.where(on)
    glyphs = np.where(on, 0, 255).astype(np.uint8)[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    glyphs = cv2.resize(glyphs, None, fx=40 / glyphs.shape[0], fy=40 / glyphs.shape[0], interpolation=cv2.INTER_AREA)
    page = cv2.copyMakeBorder(glyphs, 12, 12, 24, 24, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)


@dataclass
class Snapshot:
    items: list          # bag: [{"row", "col", "id", "confidence", "count"}]
    equipment: list      # worn: [{"slot", "id", "confidence", "count"}]
    silver: int          # the balances beside the coins: silver below...
    gold: int            # ...gold above
    market_value: int    # the game's own "Est. Market Value" of the bag (exact while its tooltip shows)
    gear_value: int      # the game's value of the worn gear: only while that tooltip shows
    weight: int          # load, in percent
    zone: str            # from the location bar, bottom right; None on a cropped screenshot


MIN_MATCH = .6   # a bag's tiles match item renders at least this well; menu buttons don't
MIN_ITEM = .65   # a cell below this is UI past the bag (or a tooltip over it), not an item


def _tile(img, grid, x, y):
    tile = img[y:y + grid.h, x:x + grid.w]
    return None if tile.shape[:2] != (grid.h, grid.w) or is_empty(tile) else tile


def locate_bag(img, refs):
    """The bag grid: the likeliest candidate whose frames hold items, or None."""
    for grid, members in bag_candidates(img):
        sample = [refs.identify(t)[1] for b in members[:6] if (t := _tile(img, grid, b[0], b[1])) is not None]
        if sample and np.median(sample) >= MIN_MATCH:
            return grid
    return None


def read_inventory(img, refs, reader):
    """Everything the eye can read from one screenshot with the inventory open, or None."""
    grid = locate_bag(img, refs)
    if grid is None:
        return None
    items = []
    for r, c, x, y in grid.cells():
        if (tile := _tile(img, grid, x, y)) is None:
            continue
        item_id, confidence = refs.identify(tile, enchant_of(img, x, y, grid.w, grid.h))
        if confidence >= MIN_ITEM:
            items.append({"row": r, "col": c, "id": item_id, "confidence": round(confidence, 3),
                          "count": reader.count(tile)})
    equipment = []
    for slot, (dx, dy) in EQUIPMENT.items():
        x, y = round(grid.x + dx * grid.pitch), round(grid.y + dy * grid.pitch)
        if _tile(img, grid, x, y) is None:
            continue
        x, y = refs.settle(img, x, y, grid.w, grid.h)
        tile = img[y:y + grid.h, x:x + grid.w]
        item_id, confidence = refs.identify(tile, enchant_of(img, x, y, grid.w, grid.h))
        if confidence >= MIN_ITEM:
            equipment.append({"slot": slot, "id": item_id, "confidence": round(confidence, 3),
                              "count": reader.count(tile)})
    # The panel text sits around the bag: balances and load just above it, the estimate below.
    left, right = max(0, grid.x - grid.pitch), grid.x + (grid.cols + 1) * grid.pitch
    top = max(0, grid.y - 3 * grid.pitch)
    market_value, gear_value, exact, balances, weight = None, None, None, [], None
    for x, y, text in reader.lines(img[top:, left:right]):
        y += top
        if m := re.search(r"Market\s*Value\W*([\d.,]+\s*[kmb]?)", text, re.I):
            market_value = parse_amount(m[1])
        elif m := re.match(r"\s*Equipment\W*([\d,]+)", text, re.I):     # the value tooltip's lines
            gear_value = parse_amount(m[1])
        elif m := re.match(r"\s*Inventory\W*([\d,]+)\s*$", text, re.I):
            exact = parse_amount(m[1])
        elif (m := re.fullmatch(r"(\d{1,3})\s*%", text.strip())) and y < grid.y:
            weight = int(m[1])
        elif (grid.y - 1.5 * grid.pitch < y < grid.y and grid.x + .4 * grid.pitch < x + left < grid.x + 1.5 * grid.pitch
              and (v := parse_amount(text)) is not None):  # right of the coin icons, over the bag's first column
            balances.append((y, v))
    balances = [v for _, v in sorted(balances)]  # gold's line is above silver's
    gold, silver = (balances + [None, None])[:2] if len(balances) != 1 else (None, balances[0])
    return Snapshot(items, equipment, silver, gold, exact or market_value, gear_value, weight, read_zone(img, reader))


def read_zone(img, reader):
    """The zone name from the location bar at the bottom right ("161 | Lymhurst Portal 13:23"),
    or None when the image isn't a whole screen."""
    h, w = img.shape[:2]
    if w < 1000 or not 1.5 < w / h < 2.5:
        return None
    names = [t.strip() for _, _, t in reader.lines(img[int(h * .94):, int(w * .78):])
             if re.fullmatch(r"[A-Za-z][A-Za-z' -]{2,}", t.strip())]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", max(names, key=len)) if names else None  # OCR drops spaces


# Screen capture from WSL: silver/capture.ps1 runs in Windows PowerShell and copies the screen
# to a PNG whenever asked. It stays running, so a capture skips PowerShell's ~3 s startup.
CAPTURE_SCRIPT = Path(__file__).with_name("capture.ps1")
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


class Screen:
    """The Windows primary screen, captured on demand. The game must be windowed or borderless:
    exclusive fullscreen can capture as black."""

    def __init__(self):
        self.proc, self.path = None, None

    def capture(self):
        """The screen as a BGR image (restarts the capture process if it died)."""
        for attempt in (1, 2):
            try:
                if self.proc is None or self.proc.poll() is not None:
                    script = subprocess.run(["wslpath", "-w", str(CAPTURE_SCRIPT)], capture_output=True,
                                            text=True, check=True).stdout.strip()
                    self.proc = subprocess.Popen(
                        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, cwd="/mnt/c", bufsize=1)
                self.proc.stdin.write("\n")
                self.proc.stdin.flush()
                win_path = self.proc.stdout.readline().strip()
                if not win_path:
                    raise RuntimeError("capture process ended")
                self.path = self.path or subprocess.run(["wslpath", "-u", win_path], capture_output=True,
                                                        text=True, check=True).stdout.strip()
                img = cv2.imread(self.path)
                if img is not None:
                    return img
                raise RuntimeError("screen capture produced no image")
            except (OSError, RuntimeError, subprocess.CalledProcessError):
                self.close()
                if attempt == 2:
                    raise

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc = None


def capture():
    """One capture (starts and stops PowerShell; use Screen to capture repeatedly)."""
    screen = Screen()
    try:
        return screen.capture()
    finally:
        screen.close()
