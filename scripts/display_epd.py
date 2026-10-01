"""Render data/summary.json and show it on the Waveshare 13.3inch e-Paper (K).

Runs on the Raspberry Pi (with the panel attached via the e-Paper Driver HAT).
    python3 scripts/display_epd.py

Set WAVESHARE_LIB to override where the waveshare_epd library lives.
"""

import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # so we can import render.py

# Locate the waveshare_epd library
_lib = os.environ.get(
    "WAVESHARE_LIB",
    os.path.expanduser("~/e-Paper/RaspberryPi_JetsonNano/python/lib"),
)
if os.path.isdir(_lib):
    sys.path.insert(0, _lib)

from render import build_image  # noqa: E402


def main():
    with open(os.path.join(REPO, "data", "summary.json")) as f:
        data = json.load(f)

    image = build_image(data)  # 960x680 'L', black on white

    from waveshare_epd import epd13in3k

    epd = epd13in3k.EPD()
    logging.info("init + clear …")
    epd.init()
    epd.Clear()

    logging.info("displaying: %s", data.get("title", "")[:70])
    epd.display(epd.getbuffer(image))

    logging.info("sleep")
    epd.sleep()
    logging.info("done")


if __name__ == "__main__":
    main()
