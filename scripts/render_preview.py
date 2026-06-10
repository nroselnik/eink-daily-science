"""Render summary.json as an 800x480 e-ink display mockup (PNG).

Usage: python3 scripts/render_preview.py [output.png]
Reads data/summary.json from the repo; simulates a Waveshare 7.5" B/W panel.
"""

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 480
MARGIN = 28
PAPER = (228, 226, 218)  # e-ink "white" is slightly warm gray
INK = (18, 18, 18)

GEORGIA = "/System/Library/Fonts/Supplemental/Georgia.ttf"
GEORGIA_BOLD = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
GEORGIA_ITALIC = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def wrap(draw, text, font, max_width):
    """Greedy word-wrap; returns list of lines."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_wrapped(draw, text, font, x, y, max_width, line_height, max_lines=None):
    lines = wrap(draw, text, font, max_width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;") + "..."
    for line in lines:
        draw.text((x, y), line, font=font, fill=INK)
        y += line_height
    return y


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "data", "summary.json")) as f:
        data = json.load(f)
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(repo, "preview.png")

    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    f_header = load_font(GEORGIA_BOLD, 17)
    f_title = load_font(GEORGIA_BOLD, 25)
    f_authors = load_font(GEORGIA_ITALIC, 15)
    f_body = load_font(GEORGIA, 18)
    f_small = load_font(GEORGIA, 15)

    content_w = WIDTH - 2 * MARGIN
    y = MARGIN

    # Header bar: inverted strip with section name + date
    bar_h = 34
    draw.rectangle([0, 0, WIDTH, bar_h], fill=INK)
    draw.text((MARGIN, 7), "DAILY TURTLE SCIENCE", font=f_header, fill=PAPER)
    date_text = data["date"]
    date_w = draw.textlength(date_text, font=f_header)
    draw.text((WIDTH - MARGIN - date_w, 7), date_text, font=f_header, fill=PAPER)
    y = bar_h + 18

    # Title + authors
    y = draw_wrapped(draw, data["title"], f_title, MARGIN, y, content_w, 32, max_lines=3)
    y += 4
    y = draw_wrapped(draw, data["authors"], f_authors, MARGIN, y, content_w, 20, max_lines=1)
    y += 10
    draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=INK, width=2)
    y += 14

    # Reserve the footer area first so body content can't collide with it
    footer_font = f_small
    footer_lines = wrap(draw, "WHY IT MATTERS: " + data["why_it_matters"], footer_font, content_w)[:2]
    footer_y = HEIGHT - MARGIN - len(footer_lines) * 19 - 8

    # Summary
    y = draw_wrapped(draw, data["summary"], f_body, MARGIN, y, content_w, 24, max_lines=4)
    y += 10

    # Key points (max 3; skip any that would run into the footer)
    for point in data["key_points"][:3]:
        if y + 2 * 19 > footer_y - 8:
            break
        draw.text((MARGIN, y), "•", font=f_body, fill=INK)
        y = draw_wrapped(draw, point, f_small, MARGIN + 20, y + 2, content_w - 20, 19, max_lines=2)
        y += 6
    draw.line([MARGIN, footer_y, WIDTH - MARGIN, footer_y], fill=INK, width=1)
    ty = footer_y + 8
    for line in footer_lines:
        draw.text((MARGIN, ty), line, font=footer_font, fill=INK)
        ty += 19

    # Threshold to 1-bit like a real B/W e-ink panel (solid ink, no dithering),
    # then tint the white level back to e-paper gray for the mockup
    img = img.convert("L").point(lambda p: 0 if p < 140 else 224).convert("RGB")

    # Put the panel in a simple device bezel for the mockup
    bezel = 26
    framed = Image.new("RGB", (WIDTH + 2 * bezel, HEIGHT + 2 * bezel), (240, 240, 240))
    fd = ImageDraw.Draw(framed)
    fd.rounded_rectangle(
        [4, 4, framed.width - 4, framed.height - 4], radius=14, fill=(252, 252, 252),
        outline=(180, 180, 180), width=2,
    )
    framed.paste(img, (bezel, bezel))
    fd.rectangle([bezel - 1, bezel - 1, bezel + WIDTH, bezel + HEIGHT], outline=(120, 120, 120))

    framed.save(out_path)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
