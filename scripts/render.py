"""Shared renderer: turn a summary dict into a 960x680 e-ink image.

Produces the raw panel content (black ink on white), sized for the
Waveshare 13.3inch e-Paper (K). Used by both render_preview.py (desktop
PNG mockup) and display_epd.py (push to the real panel).
"""

from PIL import Image, ImageDraw, ImageFont

# Waveshare 13.3inch e-Paper (K)
WIDTH, HEIGHT = 960, 680
MARGIN = 40
INK = 0        # black
PAPER = 255    # white

# Font candidates per style (first that exists wins). Covers Pi (Liberation/
# DejaVu), macOS (Georgia) so the same code renders anywhere.
_SERIF = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
]
_SERIF_BOLD = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]
_SERIF_ITALIC = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
]


def _font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw, text, font, max_width):
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


def _draw_wrapped(draw, text, font, x, y, max_width, line_height, max_lines=None):
    lines = _wrap(draw, text, font, max_width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;") + "…"
    for line in lines:
        draw.text((x, y), line, font=font, fill=INK)
        y += line_height
    return y


def build_image(data):
    """Render the summary dict to an 'L' (grayscale) image, black on white."""
    img = Image.new("L", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    f_header = _font(_SERIF_BOLD, 22)
    f_title = _font(_SERIF_BOLD, 34)
    f_authors = _font(_SERIF_ITALIC, 19)
    f_body = _font(_SERIF, 25)
    f_bullet = _font(_SERIF, 22)
    f_small = _font(_SERIF, 20)

    content_w = WIDTH - 2 * MARGIN

    # Header bar: inverted strip with section name + date
    bar_h = 48
    draw.rectangle([0, 0, WIDTH, bar_h], fill=INK)
    draw.text((MARGIN, 11), "DAILY TURTLE SCIENCE", font=f_header, fill=PAPER)
    date_text = data.get("date", "")
    date_w = draw.textlength(date_text, font=f_header)
    draw.text((WIDTH - MARGIN - date_w, 11), date_text, font=f_header, fill=PAPER)
    y = bar_h + 26

    # Title + authors
    y = _draw_wrapped(draw, data["title"], f_title, MARGIN, y, content_w, 42, max_lines=3)
    y += 6
    y = _draw_wrapped(draw, data.get("authors", ""), f_authors, MARGIN, y, content_w, 26, max_lines=1)
    y += 14
    draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=INK, width=2)
    y += 22

    # Reserve the footer (why-it-matters) area first so body can't collide
    footer_lines = _wrap(draw, "WHY IT MATTERS:  " + data["why_it_matters"], f_small, content_w)[:3]
    footer_h = len(footer_lines) * 27 + 14
    footer_y = HEIGHT - MARGIN - footer_h

    # Summary
    y = _draw_wrapped(draw, data["summary"], f_body, MARGIN, y, content_w, 33, max_lines=5)
    y += 16

    # Key points (bulleted; stop before colliding with footer)
    for point in data.get("key_points", []):
        if y + 2 * 27 > footer_y - 10:
            break
        draw.ellipse([MARGIN + 2, y + 9, MARGIN + 10, y + 17], fill=INK)
        y = _draw_wrapped(draw, point, f_bullet, MARGIN + 24, y, content_w - 24, 27, max_lines=2)
        y += 10

    # Footer
    draw.line([MARGIN, footer_y, WIDTH - MARGIN, footer_y], fill=INK, width=1)
    ty = footer_y + 12
    for line in footer_lines:
        draw.text((MARGIN, ty), line, font=f_small, fill=INK)
        ty += 27

    return img
