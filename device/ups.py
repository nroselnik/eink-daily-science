"""Battery telemetry for the Waveshare UPS HAT (D).

The HAT monitors the cell with an INA219 on the I2C-1 header bus at 0x43
(there is also an MCU at 0x2D which we do not need). One 21700 Li-ion cell,
so 3.0V empty -> 4.2V full.

Current is derived from the shunt-voltage register rather than the INA219's
calibrated current register: that needs no calibration constant and, being
signed, tells us charge vs discharge direction for free.

read() never raises -- if the HAT is absent, I2C is disabled, or the bus
errors, it returns None and the renderer simply omits the indicator.
"""

import logging

log = logging.getLogger("eink.ups")

I2C_BUS = 1
ADDR = 0x43
R_SHUNT = 0.1           # ohms, on-board sense resistor

_REG_CONFIG = 0x00
_REG_SHUNT = 0x01
_REG_BUS = 0x02

# 32V range, PGA /8, 12-bit shunt+bus, continuous. This is also the chip's
# reset default, so writing it just guarantees a known state.
_CONFIG = 0x399F

CELL_EMPTY = 3.0
CELL_FULL = 4.2

_warned = [False]


def _read_word(bus, reg):
    """INA219 registers are big-endian; smbus read_word_data is little-endian."""
    raw = bus.read_word_data(ADDR, reg)
    return ((raw << 8) & 0xFF00) | (raw >> 8)


def _signed(value):
    return value - 0x10000 if value > 0x7FFF else value


def read():
    """Return {'percent','volts','current_ma','charging'} or None if unavailable."""
    try:
        from smbus2 import SMBus
    except ImportError:
        try:
            from smbus import SMBus          # type: ignore
        except ImportError:
            return _quiet_fail("no smbus module")

    try:
        with SMBus(I2C_BUS) as bus:
            bus.write_word_data(ADDR, _REG_CONFIG,
                                ((_CONFIG << 8) & 0xFF00) | (_CONFIG >> 8))
            volts = (_read_word(bus, _REG_BUS) >> 3) * 0.004      # 4mV per LSB
            shunt_v = _signed(_read_word(bus, _REG_SHUNT)) * 1e-5  # 10uV per LSB
    except Exception as e:
        return _quiet_fail(f"i2c read failed: {e}")

    if volts <= 0.5:                 # nothing plugged in / bogus reading
        return _quiet_fail(f"implausible bus voltage {volts:.2f}V")

    # Sign: this board wires the INA219's shunt inputs so a POSITIVE raw
    # reading means current leaving the cell. Negate so callers get the
    # intuitive "positive = charging". Verified 2026-08-06: with the charger
    # connected the raw value held a rock-steady -221mA (the constant-current
    # phase of a charge) while the cell climbed 3.476V -> 3.608V over 17min
    # under constant load -- only possible while charging.
    current_ma = -(shunt_v / R_SHUNT) * 1000.0

    # Normally 1S, but infer so a different pack can't show a nonsense number.
    cells = max(1, round(volts / 3.7))
    per_cell = volts / cells
    percent = (per_cell - CELL_EMPTY) / (CELL_FULL - CELL_EMPTY) * 100.0
    percent = max(0.0, min(100.0, percent))

    _warned[0] = False
    return {
        "percent": percent,
        "volts": volts,
        "current_ma": current_ma,
        "charging": current_ma > 20.0,   # small deadband around idle/float
    }


def _quiet_fail(msg):
    """Log the first failure only -- this runs on every panel refresh."""
    if not _warned[0]:
        log.warning("UPS unavailable (%s); hiding battery indicator", msg)
        _warned[0] = True
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = read()
    if r is None:
        print("UPS not readable")
    else:
        print(f"{r['percent']:.1f}%  {r['volts']:.3f}V  "
              f"{r['current_ma']:+.0f}mA  charging={r['charging']}")
