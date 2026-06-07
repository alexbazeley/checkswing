#!/usr/bin/env python3
"""
One-off generator for CheckSwing brand PNGs.

Renders the favicon (checkswing-icon.png) and the social-share Open Graph
image (og-image.png) into mockup/assets/. Both PNGs are committed to git;
this script is just the means of regeneration.

Run from repo root:
    python3 scripts/gen_brand_pngs.py

Source-of-truth designs are in mockup/assets/*.svg (hand-authored). This
script aims to match them visually using PIL + a system serif (Charter
or Times) since most CI images don't have Source Serif 4 installed.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "mockup" / "assets"

# Brand tokens — mirror mockup/index.html :root
BRAND = (139, 27, 44)        # #8B1B2C
BRAND_2 = (110, 19, 34)      # #6E1322
ACCENT = (184, 85, 43)       # #B8552B
BG = (250, 250, 246)         # #FAFAF6
INK = (26, 25, 22)            # #1A1916
INK_2 = (74, 72, 66)          # #4A4842
INK_3 = (122, 118, 105)       # #7A7669
RULE = (201, 196, 180)        # #C9C4B4

SERIF_CANDIDATES = [
    "/Library/Fonts/SourceSerif4-Bold.otf",
    "/System/Library/Fonts/Supplemental/Charter.ttc",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Times.ttc",
]
SERIF_MEDIUM_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Charter.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Times.ttc",
]
SANS_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
MONO_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Courier.ttc",
]


def load_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def rounded_rect(draw, xy, radius, fill):
    """PIL has rounded_rectangle in 8.2+ — use it directly."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def render_icon(size: int = 512) -> Image.Image:
    """App-icon / favicon mark: a bold crimson "C" that doubles as a baseball
    (with cream seam stitching), on a cream rounded tile.

    Pure geometry (no font), mirroring mockup/assets/checkswing-icon.svg. The SVG
    is transparent; this opaque, cream-tiled raster is what the apple-touch icon,
    PNG favicon fallback, and OG mini-mark use (so they have a defined shape and
    good contrast). The "C" is an outer disc minus a concentric inner disc, with
    a wedge cutting the opening to the right.
    """
    S = size
    sc = S / 64.0  # design grid is 64×64, same as the SVG viewBox
    TILE = (251, 250, 245)     # #FBFAF5 cream (brand paper)
    CRIMSON = (139, 27, 44)    # #8B1B2C
    CREAM = (250, 247, 240)    # #FAF7F0 (seams)

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Cream rounded tile.
    tile_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(15 * sc), fill=255)
    img.paste(Image.new("RGBA", (S, S), TILE + (255,)), (0, 0), tile_mask)

    # Crimson baseball "C": outer disc − inner disc − opening wedge.
    cmask = Image.new("L", (S, S), 0)
    cd = ImageDraw.Draw(cmask)

    def ell(cx, cy, r, fill):
        cd.ellipse(((cx - r) * sc, (cy - r) * sc, (cx + r) * sc, (cy + r) * sc), fill=fill)

    ell(32, 32, 24, 255)
    ell(32, 32, 13.6, 0)
    cd.polygon([(32 * sc, 32 * sc), (66 * sc, 17 * sc), (66 * sc, 47 * sc)], fill=0)
    img.paste(Image.new("RGBA", (S, S), CRIMSON + (255,)), (0, 0), cmask)

    # Baseball seam stitching (cream): a seam arc crossed by short ticks, near
    # each terminal. Mirrors the paths in the SVG.
    d = ImageDraw.Draw(img)
    w = max(1, round(1.5 * sc))

    def line(x1, y1, x2, y2):
        d.line([(x1 * sc, y1 * sc), (x2 * sc, y2 * sc)], fill=CREAM + (255,), width=w)

    def seam_arc(a0, a1, r=18.0, n=10):
        pts = []
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            pts.append(((32 + r * math.cos(a)) * sc, (32 + r * math.sin(a)) * sc))
        d.line(pts, fill=CREAM + (255,), width=w, joint="curve")

    seam_arc(-68.9, -27.7)                 # upper seam line
    line(39.6, 13.9, 41.3, 17.7)           # upper ticks
    line(43.0, 15.0, 44.4, 18.9)
    line(46.0, 17.2, 47.0, 21.2)
    seam_arc(68.9, 27.7)                    # lower seam line
    line(39.6, 50.1, 41.3, 46.3)           # lower ticks
    line(43.0, 49.0, 44.4, 45.1)
    line(46.0, 46.8, 47.0, 42.8)
    return img


def render_og() -> Image.Image:
    """1200x630 social-share card."""
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Top brand rule
    d.rectangle((0, 0, W, 8), fill=BRAND)

    # Mini icon tile top-left
    tile_size = 96
    tile_x = 96
    tile_y = 88
    icon = render_icon(tile_size)
    img.paste(icon, (tile_x, tile_y), icon)

    # Wordmark
    wm_font = load_font(SERIF_CANDIDATES, 128)
    d.text((96, 240), "CheckSwing", font=wm_font, fill=INK)

    # Deck
    deck_font = load_font(SERIF_MEDIUM_CANDIDATES, 40)
    d.text(
        (96, 392),
        "Where MLB's owners spend in politics.",
        font=deck_font,
        fill=INK_2,
    )

    # Stat strip — sans, semibold-ish (PIL just uses whatever weight Helvetica.ttc resolves to)
    stat_font = load_font(SANS_CANDIDATES, 22)
    stats = [
        (96, "36 owners"),
        (280, "Federal + state"),
        (520, "2000–present"),
        (760, "Sourced from official filings"),
    ]
    for x, text in stats:
        d.text((x, 490), text, font=stat_font, fill=INK)

    # Bottom rule
    d.rectangle((96, 540, 1104, 541), fill=RULE)

    # Footer mono
    mono_font = load_font(MONO_CANDIDATES, 20)
    d.text((96, 568), "checkswing.pages.dev", font=mono_font, fill=INK_3)
    right_text = "Federal + state political money"
    rb = d.textbbox((0, 0), right_text, font=mono_font)
    rw = rb[2] - rb[0]
    d.text((1104 - rw, 568), right_text, font=mono_font, fill=INK_3)

    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    icon = render_icon(512)
    icon_path = ASSETS / "checkswing-icon.png"
    icon.save(icon_path, "PNG", optimize=True)
    print(f"wrote {icon_path} ({icon_path.stat().st_size / 1024:.1f} KB)")

    og = render_og()
    og_path = ASSETS / "og-image.png"
    og.save(og_path, "PNG", optimize=True)
    print(f"wrote {og_path} ({og_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
