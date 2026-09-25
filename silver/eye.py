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
import os
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


def enchant_of(tile):
    """How many lit enchant diamonds sit along the bottom left of a slot."""
    h, w = tile.shape[:2]
    strip = cv2.cvtColor(tile[int(h * .84):, int(w * .08):int(w * .62)], cv2.COLOR_BGR2HSV)
    lit = ((strip[:, :, 1] > 90) & (strip[:, :, 2] > 120)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(lit)
    area = h * w
    # diamond-sized and diamond-shaped: not a sliver of glow or a speck of art
    return sum(1 for s in stats[1:] if .003 * area < s[4] < .03 * area and .5 < s[2] / s[3] < 2)


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

    def tier_of(self, tile):
        """The tier a slot's badge colour says, or None without references."""
        b = _badge(tile)
        return min(self.badges, key=lambda t: float(np.linalg.norm(self.badges[t] - b)), default=None)

    def identify(self, tile):
        """(item id, confidence 0-1) for one slot tile, or (None, 0) with no references."""
        if not self.ids:
            return None, 0.0
        scores = self.coarse @ _coarse(tile)
        best = int(np.argmax(scores))
        # The picture finds the item; the badge colour picks its tier and the diamonds its enchant.
        variants = self.families[family(self.ids[best])]
        on_tier = [i for i in variants if tier(self.ids[i]) == self.tier_of(tile)] or variants
        lit = enchant_of(tile)
        pool = [i for i in on_tier if enchant(self.ids[i]) == lit] or on_tier
        best = max(pool, key=lambda i: scores[i])
        return self.ids[best], float(scores[best])


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


def find_bag(img, cols=4):
    """The inventory bag grid, or None when no bag is open. The bag is the biggest set of
    same-size frames on one regular grid: equipment slots sit off it, and nothing in the game
    world lines up that way. No resolution or UI scale needs configuring."""
    squares = _squares(img)
    best, pitch = [], 0
    for size in sorted({b[2] for b in squares}):
        group = [b for b in squares if abs(b[2] - size) <= .08 * size]
        if len(group) > len(best):
            members, p = _grid_of(group)
            span = round((max(b[0] for b in members) - min(b[0] for b in members)) / p) + 1 if members else 0
            if len(members) > len(best) and span <= cols:  # the skill bar is a grid too, but ten wide
                best, pitch = members, p
    if len(best) < 3:
        return None
    w = int(np.median([b[2] for b in best]))
    h = int(np.median([b[3] for b in best]))
    x0, y0 = min(b[0] for b in best), min(b[1] for b in best)
    pitch = int(round(pitch))
    # Every row that fits: frames blend into some art, so the rows with detected frames can stop
    # short of the last item. Cells past the bag are empty or match nothing and get dropped.
    rows = max(1, min(12, (img.shape[0] - y0 - h) // pitch + 1))
    return Grid(x0, y0, pitch, w, h, cols, rows)


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
    # off the edges and tall enough: drops the ring's highlight and any glow or art
    keep = [i for i in range(1, n) if stats[i, 1] > 2 and stats[i, 1] + stats[i, 3] < H - 2
            and stats[i, 0] > 2 and stats[i, 0] + stats[i, 2] < W - 2 and stats[i, 3] > H * .2]
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
    items: list          # [{"row", "col", "id", "confidence", "count"}]
    market_value: int    # the game's own "Est. Market Value" for the bag, if shown
    balances: list       # the amounts beside the coin icons, top first
    weight: int          # load, in percent


def read_inventory(img, refs, reader):
    """Everything the eye can read from one screenshot with the inventory open, or None."""
    grid = find_bag(img)
    if grid is None:
        return None
    MIN_MATCH = .6   # below this nothing on the "bag" looks like any item: some other UI
    MIN_ITEM = .5    # a cell below this is UI past the bag, not an item
    items = []
    for r, c, x, y in grid.cells():
        tile = img[y:y + grid.h, x:x + grid.w]
        if tile.shape[:2] != (grid.h, grid.w) or is_empty(tile):
            continue
        item_id, confidence = refs.identify(tile)
        if confidence < MIN_ITEM:
            continue
        items.append({"row": r, "col": c, "id": item_id, "confidence": round(confidence, 3),
                      "count": reader.count(tile)})
    if not items or max(i["confidence"] for i in items) < MIN_MATCH:
        return None
    # The panel text sits around the bag: balances and load just above it, the estimate below.
    left, right = max(0, grid.x - grid.pitch), grid.x + (grid.cols + 1) * grid.pitch
    top = max(0, grid.y - 3 * grid.pitch)
    lines = reader.lines(img[top:, left:right])
    market_value, balances, weight = None, [], None
    for x, y, text in lines:
        y += top
        if m := re.search(r"Market\s*Value\W*([\d.,]+\s*[kmb]?)", text, re.I):
            market_value = parse_amount(m[1])
        elif (m := re.fullmatch(r"(\d{1,3})\s*%", text.strip())) and y < grid.y:
            weight = int(m[1])
        elif y < grid.y and y > grid.y - 1.5 * grid.pitch and (v := parse_amount(text)) is not None:
            balances.append((y, v))
    return Snapshot(items, market_value, [v for _, v in sorted(balances)], weight)


# Screen capture from WSL: PowerShell on the Windows side copies the screen to a PNG.
# DPI-aware so a scaled display (125%, 150%) is captured at its real resolution.
_CAPTURE = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type 'using System.Runtime.InteropServices; public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[void][Dpi]::SetProcessDPIAware()
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
[System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$path = Join-Path $env:TEMP 'silver-eye.png'
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$path
"""
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def capture():
    """The Windows primary screen as a BGR image. The game must be windowed or borderless:
    exclusive fullscreen can capture as black."""
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _CAPTURE],
                         capture_output=True, text=True, timeout=30, cwd="/mnt/c", check=True)
    win_path = out.stdout.strip().splitlines()[-1]
    path = subprocess.run(["wslpath", "-u", win_path], capture_output=True, text=True, check=True).stdout.strip()
    img = cv2.imread(path)
    os.remove(path)
    if img is None:
        raise RuntimeError("screen capture produced no image")
    return img
