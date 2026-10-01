# Device runtime

Everything that turns a Raspberry Pi and a Waveshare 13.3" (K) e-paper panel
into the actual appliance: the display server, the Wi-Fi provisioning
fallback, and the battery guard. The `scripts/` directory at the repo root is
the *content* pipeline (fetch papers, summarise, render a preview); this
directory is the *device*.

## Hardware

- Raspberry Pi 5
- Waveshare 13.3inch e-Paper (K), 960x680, 1-bit
- Waveshare e-Paper Driver HAT (B), interface switch set to **0 (4-line SPI)**
- Waveshare UPS HAT (D) with a 21700 Li-ion cell (optional; the battery
  readout and low-battery shutdown are skipped if it is absent)

## Install

```sh
sudo raspi-config nointeractive do_spi 0        # or: dtparam=spi=on
sudo apt install -y python3-spidev python3-lgpio python3-gpiozero \
                    python3-pil python3-numpy python3-flask python3-smbus

git clone https://github.com/waveshare/e-Paper ~/e-Paper   # panel driver
git clone <this repo> ~/eink-daily-science

mkdir -p ~/eink
cp ~/eink-daily-science/device/server.py ~/eink/
cp ~/eink-daily-science/device/ups.py ~/eink/
cp ~/eink-daily-science/scripts/render.py ~/eink/          # shared with the pipeline
cp ~/eink-daily-science/data/archive.json ~/eink/          # 99 papers to start with
cp ~/eink-daily-science/device/settings.example.json ~/eink/settings.json
chmod 600 ~/eink/settings.json

sudo cp ~/eink-daily-science/device/eink-nmcli.sudoers /etc/sudoers.d/eink-nmcli
sudo cp ~/eink-daily-science/device/eink-poweroff.sudoers /etc/sudoers.d/eink-poweroff
sudo visudo -c                                             # validate before trusting

sudo cp ~/eink-daily-science/device/eink-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eink-web.service
```

The service unit and both sudoers files assume the user `nroselnik` and
`/home/nroselnik/eink`. Change `User=`, `WorkingDirectory=`, `ExecStart=`,
`Environment=HOME=`, and the username in both sudoers files if yours differs.

Then browse to `http://<pi-address>:8080`. The device also prints its own
address in the bottom-right of the panel, next to the battery readout.

## settings.json

Deployment-specific values live in `settings.json`, which is **gitignored** -
copy `settings.example.json` to start. Every key is optional and falls back to
the default below. An `EINK_<KEY>` environment variable overrides the file,
which is handy for provisioning without editing JSON.

| Key | Default | What it does |
|---|---|---|
| `ap_pass` | *(generated)* | Setup hotspot password. **Leave empty** and the device generates its own on first run and writes it back here, so no two devices share one. Minimum 8 characters if you set it yourself (WPA2). |
| `ap_ssid` | `TurtleSetup` | Setup hotspot name |
| `setup_url` | `http://10.42.0.1:8080` | Address shown on the setup screen. In AP mode the Pi is the gateway, so this is fixed. |
| `hotspot_con` | `Hotspot` | NetworkManager connection name for the AP |
| `repo_path` | `~/eink-daily-science` | Clone this server pulls new papers from |
| `grace_seconds` | `60` | How long at boot to wait for a known network before raising the hotspot |
| `setup_timeout` | `300` | Seconds in hotspot mode with no input before falling back to offline |
| `online_poll` | `30` | How often to re-check the link while online |
| `offline_retry` | `300` | How often to rescan for known networks while offline |
| `offline_ap_every` | `3` | Re-raise the setup hotspot every Nth offline round |
| `sync_interval` | `604800` | How often to auto-pull new papers (weekly) |
| `batt_poll` | `60` | Seconds between battery samples |
| `batt_warn_v` | `3.35` | Log a warning below this cell voltage |
| `batt_shutdown_v` | `3.2` | Sustained at or below this triggers a graceful poweroff |
| `batt_confirm` | `3` | Consecutive low samples before acting |

## First boot with no known Wi-Fi

1. The supervisor waits `grace_seconds` for a saved network.
2. Finding none, it raises the `ap_ssid` hotspot and paints setup instructions
   on the panel: the hotspot name, its password, and the URL to open.
3. You join the hotspot from a phone, browse to that URL, and enter your
   network name and password. The device writes an autoconnect profile,
   drops the hotspot, and joins as a client.
4. If nobody provisions within `setup_timeout`, it gives up and rotates the
   saved archive offline - then keeps retrying known networks every
   `offline_retry`, re-raising the hotspot every `offline_ap_every` rounds.

Provision from a **phone**. Desktop operating systems tend to bounce you off
the hotspot and back onto a higher-priority remembered network before you can
finish, and the window is only five minutes.

## Battery guard

If the UPS HAT is present, a thread samples the cell every `batt_poll`
seconds. Sustained voltage at or below `batt_shutdown_v` across
`batt_confirm` samples draws an explanatory screen, parks the panel, and
halts cleanly, which protects the SD card from repeated brownouts.

The decision deliberately uses **voltage plus a non-rising trend rather than
the charging flag**, so a mis-wired or inverted shunt cannot defeat it, and a
momentary voltage sag during a panel refresh cannot false-trigger it.

## Notes

- Only one process may hold the panel. `server.py` instantiates it at import,
  so do not import it to test helper functions - copy the function out or
  exercise it in a standalone script.
- `config.json` is runtime state (interval, index, paused) and is rewritten by
  the server. `settings.json` is configuration and is only written when
  generating the initial hotspot password.
