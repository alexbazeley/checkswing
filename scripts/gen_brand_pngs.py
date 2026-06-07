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
    """Favicon mark: crimson tile + high-contrast serif "C" + warm accent ball.

    Pure geometry (no font) so it matches the source-of-truth vector exactly —
    mockup/assets/checkswing-icon.svg. The "C" is an outer disc minus a
    right-offset inner disc (thick left spine, thin toward the open right, like
    the Fraunces display face), with a wedge cutting the opening.
    """
    S = size
    sc = S / 64.0  # design grid is 64×64, same as the SVG viewBox
    TILE_TOP = (154, 34, 54)   # #9A2236
    TILE_BOT = (118, 21, 38)   # #761526
    CREAM = (250, 247, 240)    # #FAF7F0
    DOT = (197, 106, 58)       # #C56A3A

    # Crimson tile with a subtle vertical gradient, clipped to a rounded rect.
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        t = y / max(1, S - 1)
        grad.putpixel((0, y), tuple(int(TILE_TOP[i] + (TILE_BOT[i] - TILE_TOP[i]) * t) for i in range(3)))
    grad = grad.resize((S, S))
    tile_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(15 * sc), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), tile_mask)

    # High-contrast "C" built as a mask, then cream pasted through it.
    cmask = Image.new("L", (S, S), 0)
    cd = ImageDraw.Draw(cmask)

    def ellipse(cx, cy, r, fill):
        cd.ellipse(((cx - r) * sc, (cy - r) * sc, (cx + r) * sc, (cy + r) * sc), fill=fill)

    ellipse(30, 32, 18, 255)       # outer disc
    ellipse(33.5, 32, 10.6, 0)     # right-offset inner disc → thick-left contrast
    cd.polygon([(30 * sc, 32 * sc), (66 * sc, 12 * sc), (66 * sc, 52 * sc)], fill=0)  # opening wedge
    img.paste(Image.new("RGBA", (S, S), CREAM + (255,)), (0, 0), cmask)

    # Warm accent ball at the mouth of the C.
    ImageDraw.Draw(img).ellipse(
        ((49 - 4) * sc, (32 - 4) * sc, (49 + 4) * sc, (32 + 4) * sc), fill=DOT + (255,)
    )
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
