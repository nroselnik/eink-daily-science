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


def _draw_battery(draw, batt, x, y, font):
    """Small battery glyph + percentage for the bottom-left page margin.

    Deliberately understated: it sits in the empty margin strip below the
    footer rule so it never competes with the paper content.
    """
    pct = batt["percent"]
    bw, bh = 26, 13
    draw.rectangle([x, y, x + bw, y + bh], outline=INK, width=1)
    draw.rectangle([x + bw + 2, y + 4, x + bw + 4, y + bh - 4], fill=INK)  # terminal nub
    fill_w = int(round((bw - 4) * pct / 100.0))
    if fill_w > 0:
        draw.rectangle([x + 2, y + 2, x + 2 + fill_w, y + bh - 2], fill=INK)

    label = f"{pct:.0f}%"
    if batt.get("charging"):
        label += "  charging"
    draw.text((x + bw + 11, y - 3), label, font=font, fill=INK)


def build_image(data, battery=None, host=None):
    """Render the summary dict to an 'L' (grayscale) image, black on white.

    battery: optional dict from ups.read(); omitted entirely when None.
    """
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

    # Battery, tucked into the bottom margin. footer_lines is bottom-anchored so
    # its last line always ends near y=638, leaving this strip clear.
    if battery:
        _draw_battery(draw, battery, MARGIN, HEIGHT - 26, _font(_SERIF, 16))

    # Where to go to change the slide interval. Right-aligned on the battery's
    # baseline so the two share the bottom margin strip without colliding.
    if host:
        f_host = _font(_SERIF, 16)
        draw.text((WIDTH - MARGIN - draw.textlength(host, font=f_host), HEIGHT - 29),
                  host, font=f_host, fill=INK)

    return img


def build_setup_image(ap_ssid, ap_pass, url, timeout_min=5):
    """Render the 'no known Wi-Fi' setup-instructions screen."""
    img = Image.new("L", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    f_header = _font(_SERIF_BOLD, 22)
    f_h1 = _font(_SERIF_BOLD, 40)
    f_body = _font(_SERIF, 27)
    f_step = _font(_SERIF, 26)
    f_val = _font(_SERIF_BOLD, 30)
    f_small = _font(_SERIF_ITALIC, 20)

    bar_h = 48
    draw.rectangle([0, 0, WIDTH, bar_h], fill=INK)
    draw.text((MARGIN, 11), "WI-FI SETUP", font=f_header, fill=PAPER)
    draw.text((WIDTH - MARGIN - draw.textlength("no connection", font=f_header), 11),
              "no connection", font=f_header, fill=PAPER)

    y = bar_h + 30
    draw.text((MARGIN, y), "No known network found", font=f_h1, fill=INK)
    y += 58
    y = _draw_wrapped(draw, "Connect a phone or laptop to set up Wi-Fi:",
                      f_body, MARGIN, y, WIDTH - 2 * MARGIN, 34)
    y += 24

    steps = [
        ("1.  Join this Wi-Fi hotspot:", f"{ap_ssid}   (password: {ap_pass})"),
        ("2.  Open a browser to:", url),
        ("3.  Enter your Wi-Fi name & password.", None),
    ]
    for label, value in steps:
        draw.text((MARGIN, y), label, font=f_step, fill=INK)
        y += 34
        if value:
            draw.text((MARGIN + 34, y), value, font=f_val, fill=INK)
            y += 44
        y += 8

    footer = (f"Waiting {timeout_min} min for setup, then showing saved "
              f"papers in offline mode.")
    ty = HEIGHT - MARGIN - 27
    draw.line([MARGIN, ty - 12, WIDTH - MARGIN, ty - 12], fill=INK, width=1)
    for line in _wrap(draw, footer, f_small, WIDTH - 2 * MARGIN)[:2]:
        draw.text((MARGIN, ty), line, font=f_small, fill=INK)
        ty += 25

    return img


def build_lowbatt_image(batt):
    """Render the 'shutting down, out of charge' screen.

    E-ink holds its last image with no power, so this is what the panel will
    be showing while the Pi is off -- it has to explain itself to someone who
    walks up to a dark device, and say what to do about it.
    """
    img = Image.new("L", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    f_header = _font(_SERIF_BOLD, 22)
    f_h1 = _font(_SERIF_BOLD, 40)
    f_body = _font(_SERIF, 27)
    f_small = _font(_SERIF_ITALIC, 20)

    bar_h = 48
    draw.rectangle([0, 0, WIDTH, bar_h], fill=INK)
    draw.text((MARGIN, 11), "BATTERY EMPTY", font=f_header, fill=PAPER)
    label = "powered down"
    draw.text((WIDTH - MARGIN - draw.textlength(label, font=f_header), 11),
              label, font=f_header, fill=PAPER)

    y = bar_h + 40
    draw.text((MARGIN, y), "Out of charge", font=f_h1, fill=INK)
    y += 64

    pct = batt["percent"] if batt else 0.0
    volts = batt["volts"] if batt else 0.0
    _draw_battery(draw, {"percent": pct, "charging": False},
                  MARGIN, y + 4, _font(_SERIF, 16))
    draw.text((MARGIN + 120, y - 3), f"{volts:.2f} V per cell",
              font=f_body, fill=INK)
    y += 62

    y = _draw_wrapped(
        draw,
        "The display shut itself down cleanly to protect the SD card. "
        "Plug the charger into the USB-C socket on the UPS board, then press "
        "the Pi's power button once to start it up again.",
        f_body, MARGIN, y, WIDTH - 2 * MARGIN, 36)

    footer = ("Nothing is lost — the paper archive and Wi-Fi settings are "
              "saved on disk.")
    ty = HEIGHT - MARGIN - 27
    draw.line([MARGIN, ty - 12, WIDTH - MARGIN, ty - 12], fill=INK, width=1)
    for line in _wrap(draw, footer, f_small, WIDTH - 2 * MARGIN)[:2]:
        draw.text((MARGIN, ty), line, font=f_small, fill=INK)
        ty += 25

    return img
