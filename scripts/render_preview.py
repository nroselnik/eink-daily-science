"""Render summary.json as a 960x680 e-ink display mockup (PNG).

Usage: python3 scripts/render_preview.py [output.png]
Simulates the Waveshare 13.3inch e-Paper (K) B/W panel in a device bezel.
"""

import json
import os
import sys

from PIL import Image, ImageDraw

from render import build_image, WIDTH, HEIGHT

PAPER_TINT = (228, 226, 218)  # e-ink "white" is a slightly warm gray


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "data", "summary.json")) as f:
        data = json.load(f)
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(repo, "preview.png")

    # 1-bit like a real B/W panel, then map to warm-gray/ink for the mockup
    mono = build_image(data).convert("1")
    tinted = Image.new("RGB", (WIDTH, HEIGHT))
    px = mono.load()
    tp = tinted.load()
    for yy in range(HEIGHT):
        for xx in range(WIDTH):
            tp[xx, yy] = PAPER_TINT if px[xx, yy] else (18, 18, 18)

    # Device bezel
    bezel = 30
    framed = Image.new("RGB", (WIDTH + 2 * bezel, HEIGHT + 2 * bezel), (240, 240, 240))
    fd = ImageDraw.Draw(framed)
    fd.rounded_rectangle(
        [4, 4, framed.width - 4, framed.height - 4], radius=16,
        fill=(252, 252, 252), outline=(180, 180, 180), width=2,
    )
    framed.paste(tinted, (bezel, bezel))
    fd.rectangle([bezel - 1, bezel - 1, bezel + WIDTH, bezel + HEIGHT], outline=(120, 120, 120))

    framed.save(out_path)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
