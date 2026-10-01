"""Daily Turtle Science - e-ink display + Wi-Fi manager.

One process that:
  * owns the Waveshare 13.3in (K) panel,
  * on boot, checks for a known Wi-Fi; if none and setup is enabled, starts a
    hotspot and shows setup instructions on the panel. After SETUP_TIMEOUT with
    no credentials entered, falls back to OFFLINE mode (rotate saved archive).
  * rotates through an archive of paper summaries,
  * serves a mobile web UI: display controls (online) or Wi-Fi setup (hotspot).

Modes: STARTING -> ONLINE | SETUP | OFFLINE
Run:  python3 server.py    (listens on 0.0.0.0:8080)
"""

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/e-Paper/RaspberryPi_JetsonNano/python/lib"))

from flask import Flask, jsonify, request, Response  # noqa: E402
from render import build_image, build_setup_image, build_lowbatt_image  # noqa: E402
from waveshare_epd import epd13in3k  # noqa: E402
import ups  # noqa: E402  (Waveshare UPS HAT (D) battery telemetry)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("eink")

ARCHIVE_PATH = os.path.join(HERE, "archive.json")
CONFIG_PATH = os.path.join(HERE, "config.json")       # runtime state, auto-saved
SETTINGS_PATH = os.path.join(HERE, "settings.json")   # deployment settings

# Deployment-specific values live in settings.json rather than in this file, so
# server.py can be published. Every key is optional and falls back to the
# default below; an EINK_<KEY> environment variable overrides the file, which
# is convenient for provisioning a device without editing JSON by hand.
try:
    with open(SETTINGS_PATH, encoding="utf-8") as _f:
        _SETTINGS = json.load(_f)
except (FileNotFoundError, ValueError):
    _SETTINGS = {}


def _s(key, default):
    env = os.environ.get("EINK_" + key.upper())
    if env not in (None, ""):
        if isinstance(default, bool):
            return env.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(env)
        if isinstance(default, float):
            return float(env)
        return env
    return _SETTINGS.get(key, default)


REPO = os.path.expanduser(_s("repo_path", "~/eink-daily-science"))

AP_SSID = _s("ap_ssid", "TurtleSetup")
AP_PASS = _s("ap_pass", "")
SETUP_URL = _s("setup_url", "http://10.42.0.1:8080")
HOTSPOT_CON = _s("hotspot_con", "Hotspot")
GRACE_SECONDS = _s("grace_seconds", 60)      # wait this long at boot for a known network
SETUP_TIMEOUT = _s("setup_timeout", 300)     # 5 min in hotspot with no input -> offline
ONLINE_POLL = _s("online_poll", 30)          # while ONLINE, re-check the link this often
OFFLINE_RETRY = _s("offline_retry", 300)     # while OFFLINE, rescan for known networks
OFFLINE_AP_EVERY = _s("offline_ap_every", 3) # re-raise the setup AP every Nth round
SYNC_INTERVAL = _s("sync_interval", 7 * 24 * 3600)  # auto-pull new papers (weekly)
BATT_POLL = _s("batt_poll", 60)              # seconds between battery samples
BATT_WARN_V = _s("batt_warn_v", 3.35)        # log a warning below this cell voltage
BATT_SHUTDOWN_V = _s("batt_shutdown_v", 3.20)  # sustained at/below -> graceful poweroff
BATT_CONFIRM = _s("batt_confirm", 3)         # consecutive low samples before acting

if not AP_PASS:
    # No shared default: a password baked into a public repo would be identical
    # on every device built from it. Generate one per device and persist it so
    # it stays stable across reboots. It is shown on the setup screen anyway,
    # so a random value costs the person standing at the panel nothing.
    AP_PASS = secrets.token_hex(4)           # 8 chars, the WPA2 minimum
    _SETTINGS["ap_pass"] = AP_PASS
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as _f:
            json.dump(_SETTINGS, _f, indent=2)
        log.info("generated a hotspot password and saved it to settings.json")
    except OSError as exc:
        log.warning("could not persist the generated hotspot password: %s", exc)
INTERVALS = [("30 sec", 30), ("1 min", 60), ("5 min", 300), ("10 min", 600)]

lock = threading.Lock()
wake = threading.Event()
epd_lock = threading.Lock()


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


archive = load_json(ARCHIVE_PATH, [])
_cfg = load_json(CONFIG_PATH, {})
state = {
    "mode": "STARTING",
    "index": min(_cfg.get("index", 0), max(0, len(archive) - 1)),
    "interval": _cfg.get("interval", 60),
    "paused": _cfg.get("paused", False),
    "wifi_setup_enabled": _cfg.get("wifi_setup_enabled", False),
    "pending_jump": None,
    "pending_delta": 0,
    "current_title": None,
    "battery": None,
}
epd = epd13in3k.EPD()
_setup_deadline = [0.0]  # wall-clock time when SETUP falls back to OFFLINE


def persist():
    save_json(CONFIG_PATH, {"interval": state["interval"], "paused": state["paused"],
                            "index": state["index"],
                            "wifi_setup_enabled": state["wifi_setup_enabled"]})


# ------------------------------------------------------------------ networking
def nmcli(args, sudo=False, timeout=45):
    cmd = (["sudo", "-n"] if sudo else []) + ["nmcli"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wlan_known_connected():
    """True if wlan0 is connected to a normal Wi-Fi (not our hotspot)."""
    try:
        r = nmcli(["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", "wlan0"])
    except Exception as e:
        log.warning("nmcli check failed: %s", e)
        return False
    st, con = "", ""
    for line in r.stdout.splitlines():
        if line.startswith("GENERAL.STATE:"):
            st = line.split(":", 1)[1]
        elif line.startswith("GENERAL.CONNECTION:"):
            con = line.split(":", 1)[1]
    return ("100" in st) and con not in ("", "--", HOTSPOT_CON)


def start_ap():
    log.info("starting hotspot %s", AP_SSID)
    return nmcli(["device", "wifi", "hotspot", "ifname", "wlan0",
                  "ssid", AP_SSID, "password", AP_PASS], sudo=True)


def stop_ap():
    nmcli(["connection", "down", HOTSPOT_CON], sudo=True)


def create_profile(ssid, pw):
    nmcli(["connection", "delete", ssid], sudo=True)  # ignore if absent
    r = nmcli(["connection", "add", "type", "wifi", "con-name", ssid, "ifname", "wlan0",
               "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw,
               "connection.autoconnect", "yes"], sudo=True)
    return r.returncode == 0, r.stderr.strip()


def connect_profile(ssid, tries=3):
    for _ in range(tries):
        nmcli(["connection", "up", ssid], sudo=True, timeout=45)
        time.sleep(3)
        if wlan_known_connected():
            return True
    return False


def saved_wifi_profiles():
    """[(profile_name, ssid)] for saved Wi-Fi profiles, excluding our own hotspot.

    The profile name is often not the SSID (netplan calls ours
    "netplan-wlan0-WiFi Repeater"), so look the SSID up per profile.
    """
    r = nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"])
    out = []
    for line in r.stdout.splitlines():
        name, _, typ = line.rpartition(":")
        if "wireless" not in typ or name == HOTSPOT_CON:
            continue
        s = nmcli(["-t", "-f", "802-11-wireless.ssid", "connection", "show", name])
        ssid = s.stdout.strip().split(":", 1)[-1] if s.stdout.strip() else ""
        out.append((name, ssid or name))
    return out


def try_known_networks():
    """Rescan and bring up any saved profile whose SSID is currently in range."""
    try:
        r = nmcli(["-t", "-f", "SSID", "device", "wifi", "list", "--rescan", "yes"],
                  timeout=60)
        in_range = {l.strip() for l in r.stdout.splitlines() if l.strip()}
    except Exception as e:
        log.warning("wifi scan failed: %s", e)
        return False
    for name, ssid in saved_wifi_profiles():
        if ssid in in_range:
            log.info("known network %r in range, connecting via %r", ssid, name)
            if connect_profile(name):
                return True
    return False


def set_mode(mode):
    with lock:
        state["mode"] = mode
    wake.set()
    log.info("mode -> %s", mode)


def local_ip():
    """Best-effort address of whichever interface currently carries traffic.

    Returns None when unnetworked (OFFLINE mode), so the panel simply omits
    the hint rather than printing something misleading. The UDP connect sends
    no packets - it only asks the kernel which source address it would use.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 1))      # TEST-NET-1, reserved, unroutable
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None


# -------------------------------------------------------------------- display
def render_paper(i):
    if not archive:
        return
    paper = archive[i % len(archive)]
    batt = ups.read()          # None if the UPS HAT isn't readable
    with epd_lock:
        ip = local_ip()
        epd.display(epd.getbuffer(
            build_image(paper, battery=batt, host=f"{ip}:8080" if ip else None)))
    state["current_title"] = paper.get("title", "")
    state["battery"] = batt
    log.info("displayed %d/%d: %s%s", i + 1, len(archive), paper.get("title", "")[:55],
             f"  [batt {batt['percent']:.0f}%]" if batt else "")


def shutdown_for_battery(batt):
    """Draw the explanation, park the panel, and halt."""
    log.error("battery critical (%.3fV) - shutting down", batt["volts"])
    try:
        with epd_lock:
            epd.display(epd.getbuffer(build_lowbatt_image(batt)))
            epd.sleep()
    except Exception as e:
        log.warning("could not draw the low-battery screen: %s", e)
    r = subprocess.run(["sudo", "-n", "poweroff"], capture_output=True, text=True)
    if r.returncode != 0:
        log.error("poweroff failed: %s", (r.stderr or "").strip())


def battery_guard():
    """Halt cleanly before the cell browns out.

    Decides on VOLTAGE plus a non-rising trend rather than the charging flag.
    A panel refresh sags the rail briefly, and a mis-wired shunt would make
    the flag lie outright (it did until 2026-08-06) -- but a cell that is low
    and not recovering is unambiguous either way.
    """
    history = []
    while True:
        time.sleep(BATT_POLL)
        batt = ups.read()
        if not batt:
            continue
        volts = batt["volts"]
        history.append(volts)
        del history[:-BATT_CONFIRM]

        if volts > BATT_SHUTDOWN_V:
            if volts < BATT_WARN_V:
                log.warning("battery low: %.3fV (%.0f%%)", volts, batt["percent"])
            continue
        if len(history) < BATT_CONFIRM:
            continue
        if history[-1] > history[0]:
            log.info("battery %.3fV but recovering; standing down", volts)
            continue
        shutdown_for_battery(batt)
        return


def render_setup():
    with epd_lock:
        epd.display(epd.getbuffer(build_setup_image(AP_SSID, AP_PASS, SETUP_URL,
                                                    SETUP_TIMEOUT // 60)))
    log.info("displayed setup screen")


def rotator():
    with epd_lock:
        epd.init()
        epd.Clear()
    last = None
    while True:
        with lock:
            mode = state["mode"]
            iv = state["interval"]
        if mode == "STARTING":
            wake.wait(timeout=5)
            wake.clear()
            continue

        woke = wake.wait(timeout=iv)
        wake.clear()
        with lock:
            mode = state["mode"]
            if mode == "SETUP":
                target = ("setup",)
            elif not archive:
                target = ("empty",)
            else:
                n = len(archive)
                old = state["index"]
                if state["pending_jump"] is not None:
                    state["index"] = state["pending_jump"] % n
                    state["pending_jump"] = None
                elif state["pending_delta"]:
                    state["index"] = (state["index"] + state["pending_delta"]) % n
                    state["pending_delta"] = 0
                elif not woke and not state["paused"]:
                    state["index"] = (state["index"] + 1) % n
                persist()
                target = ("paper", state["index"])
        try:
            if target == last and target[0] != "paper":
                pass
            elif target[0] == "setup":
                if last != target:
                    render_setup()
            elif target[0] == "paper":
                if last != target:
                    render_paper(target[1])
            last = target
        except Exception as e:
            log.exception("render failed: %s", e)


# --------------------------------------------------------------- supervisor
def supervisor():
    """Keep wlan0 on a known network, forever.

    At boot we wait GRACE_SECONDS for a known network; failing that we raise the
    setup AP. If nobody provisions within SETUP_TIMEOUT we drop to OFFLINE -- but
    we then keep retrying: rescan for known networks every OFFLINE_RETRY and
    re-raise the AP every OFFLINE_AP_EVERY rounds. This loop never exits, so a
    network that disappears (or a boot before the router is up) can no longer
    strand the Pi with neither Wi-Fi nor hotspot until it is power-cycled.
    """
    grace = GRACE_SECONDS
    offline_rounds = 0

    while True:
        # ---- phase 1: wait out the grace period for a known network
        deadline = time.time() + grace
        online = False
        while time.time() < deadline:
            if wlan_known_connected():
                online = True
                break
            time.sleep(4)

        if online:
            offline_rounds = 0
            set_mode("ONLINE")
            while wlan_known_connected():   # hold here while the link is good
                time.sleep(ONLINE_POLL)
            log.info("Wi-Fi link lost -> re-running provisioning")
            grace = GRACE_SECONDS
            continue

        # ---- phase 2: no known network in range
        with lock:
            enabled = state["wifi_setup_enabled"]

        if enabled and offline_rounds % OFFLINE_AP_EVERY == 0:
            start_ap()
            set_mode("SETUP")
            _setup_deadline[0] = time.time() + SETUP_TIMEOUT
            # _switch_to_client may refresh this deadline if a connect attempt fails
            while time.time() < _setup_deadline[0]:
                time.sleep(5)
                with lock:
                    if state["mode"] != "SETUP":
                        break
            with lock:
                timed_out = state["mode"] == "SETUP"
            if not timed_out:
                grace = 20      # setup completed; confirm the link on the next pass
                continue
            log.info("setup timed out -> OFFLINE")
            stop_ap()
        elif not enabled:
            log.info("no known Wi-Fi and setup DISABLED")

        # ---- phase 3: offline. The panel keeps rotating; retry quietly.
        set_mode("OFFLINE")
        offline_rounds += 1
        time.sleep(OFFLINE_RETRY)
        try_known_networks()
        grace = 20


def do_pull():
    """git pull the repo and merge its full archive.json into our local list."""
    try:
        subprocess.run(["git", "-C", REPO, "pull", "--quiet"], timeout=60,
                       capture_output=True, text=True)
    except Exception as e:
        log.warning("git pull failed: %s", e)
    repo_list = load_json(os.path.join(REPO, "data", "archive.json"), None)
    if not isinstance(repo_list, list):  # fall back to single-summary repos
        one = load_json(os.path.join(REPO, "data", "summary.json"), None)
        repo_list = [one] if (one and one.get("url")) else []
    added = 0
    with lock:
        known = {p.get("url") for p in archive}
        for p in repo_list:
            if p and p.get("url") and p["url"] not in known:
                archive.append(p)
                known.add(p["url"])
                added += 1
        if added:
            save_json(ARCHIVE_PATH, archive)
    return added


def syncer():
    """Periodically pull new papers from the repo (source updates ~fortnightly)."""
    while True:
        time.sleep(SYNC_INTERVAL)
        try:
            n = do_pull()
            if n:
                with lock:
                    state["pending_jump"] = len(archive) - 1
                wake.set()
                log.info("auto-sync added %d paper(s)", n)
        except Exception as e:
            log.warning("auto-sync failed: %s", e)


# --------------------------------------------------------------------- Flask
app = Flask(__name__)

CONTROL_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Turtle Science Display</title>
<style>
 :root{--bg:#0f1115;--card:#1a1d24;--ink:#e8e8e8;--mut:#9aa0aa;--acc:#2e7d6b;--accln:#3fa08a;--line:#2a2e37}
 @media(prefers-color-scheme:light){:root{--bg:#f2f2ee;--card:#fff;--ink:#1a1a1a;--mut:#666;--acc:#2e7d6b;--accln:#2e7d6b;--line:#e2e2dc}}
 *{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:16px;max-width:640px;margin:auto}
 h1{font-size:20px;margin:4px 0 2px}.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
 .now{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
 .title{font-size:17px;font-weight:600;margin:6px 0;line-height:1.3}.meta{color:var(--mut);font-size:13px}
 .row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
 button{flex:1;min-width:64px;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);font-size:15px;cursor:pointer}
 button.on{background:var(--acc);border-color:var(--acc);color:#fff}
 .lab{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
 .list{max-height:300px;overflow:auto}
 .item{display:flex;align-items:center;gap:10px;padding:10px;border-radius:10px;border:1px solid transparent;cursor:pointer}
 .item.cur{background:rgba(63,160,138,.14);border-color:var(--accln)}
 .item .t{font-size:14px;line-height:1.25}.item .n{color:var(--mut);font-size:12px;width:22px;text-align:right}
</style></head><body>
<h1>🐢 Daily Turtle Science</h1><div class=sub>13.3&Prime; e-ink display control</div>
<div class=card><div class=now>Now showing <span id=pos></span></div>
 <div class=title id=title>…</div><div class=meta id=authors></div>
 <div class=row><button onclick="act('prev')">◀ Prev</button>
  <button id=pausebtn onclick="act('pause_toggle')">Pause</button>
  <button onclick="act('next')">Next ▶</button></div></div>
<div class=card><div class=lab>Slide interval</div><div class=row id=intervals></div></div>
<div class=card><div class=lab>Archive <span id=count></span></div><div class=list id=list></div>
 <div class=row><button onclick="act('pull')">⤓ Sync papers from GitHub</button></div>
 <div class=meta id=msg></div></div>
<script>
const IVS=[["30 sec",30],["1 min",60],["5 min",300],["10 min",600]];let S={};
function el(i){return document.getElementById(i)}
async function refresh(){S=await (await fetch('api/status')).json();
 el('pos').textContent=`${S.index+1} / ${S.total}`;
 el('title').textContent=S.current?S.current.title:'(empty)';
 el('authors').textContent=S.current?S.current.authors:'';el('count').textContent=`(${S.total})`;
 el('pausebtn').textContent=S.paused?'▶ Resume':'⏸ Pause';el('pausebtn').className=S.paused?'on':'';
 el('intervals').innerHTML=IVS.map(([l,s])=>`<button class="${S.interval==s?'on':''}" onclick="setiv(${s})">${l}</button>`).join('');
 el('list').innerHTML=S.papers.map((t,i)=>`<div class="item ${i==S.index?'cur':''}" onclick="show(${i})"><span class=n>${i+1}</span><span class=t>${t}</span></div>`).join('');}
async function act(a){const r=await (await fetch('api/'+a,{method:'POST'})).json();if(r.msg)el('msg').textContent=r.msg;refresh()}
async function setiv(s){await fetch('api/interval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seconds:s})});refresh()}
async function show(i){await fetch('api/show',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:i})});refresh()}
refresh();setInterval(refresh,3000);
</script></body></html>"""

SETUP_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Wi-Fi Setup</title>
<style>body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8e8;margin:0;padding:20px;max-width:460px;margin:auto}
 @media(prefers-color-scheme:light){body{background:#f2f2ee;color:#1a1a1a}}
 h1{font-size:22px}p{color:#9aa0aa}label{display:block;margin:14px 0 6px;font-size:14px}
 input{width:100%;padding:13px;border-radius:10px;border:1px solid #444;background:transparent;color:inherit;font-size:16px}
 button{width:100%;margin-top:20px;padding:14px;border:0;border-radius:10px;background:#2e7d6b;color:#fff;font-size:17px}
 .msg{margin-top:14px}</style></head><body>
<h1>🐢 Wi-Fi Setup</h1><p>Enter the network for the display to join.</p>
<form onsubmit="save(event)">
 <label>Network name (SSID)</label><input id=ssid autocapitalize=off autocorrect=off required>
 <label>Password</label><input id=pw type=text autocapitalize=off autocorrect=off>
 <button>Save &amp; connect</button></form>
<div class=msg id=msg></div>
<script>async function save(e){e.preventDefault();document.getElementById('msg').textContent='Saving & connecting… this hotspot will disappear.';
 const r=await fetch('api/wifi',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({ssid:document.getElementById('ssid').value,password:document.getElementById('pw').value})});
 const j=await r.json();document.getElementById('msg').textContent=j.msg||'Done.';}</script>
</body></html>"""


@app.route("/")
def index():
    with lock:
        mode = state["mode"]
    return Response(SETUP_PAGE if mode == "SETUP" else CONTROL_PAGE, mimetype="text/html")


@app.route("/api/status")
def status():
    batt = ups.read()          # live read, outside the lock (I2C, few ms)
    with lock:
        cur = archive[state["index"]] if archive else None
        return jsonify({
            "mode": state["mode"], "index": state["index"], "total": len(archive),
            "interval": state["interval"], "paused": state["paused"],
            "current": {"title": cur.get("title", ""), "authors": cur.get("authors", "")} if cur else None,
            "papers": [p.get("title", "") for p in archive],
            "battery": batt,
        })


@app.route("/api/interval", methods=["POST"])
def set_interval():
    sec = int((request.get_json(silent=True) or {}).get("seconds", 60))
    with lock:
        state["interval"] = max(5, sec)
        persist()
    wake.set()
    return jsonify({"ok": True})


@app.route("/api/pause_toggle", methods=["POST"])
def pause_toggle():
    with lock:
        state["paused"] = not state["paused"]
        persist()
    wake.set()
    return jsonify({"ok": True})


@app.route("/api/next", methods=["POST"])
def nxt():
    with lock:
        state["pending_delta"] = 1
    wake.set()
    return jsonify({"ok": True})


@app.route("/api/prev", methods=["POST"])
def prev():
    with lock:
        state["pending_delta"] = -1
    wake.set()
    return jsonify({"ok": True})


@app.route("/api/show", methods=["POST"])
def show():
    i = int((request.get_json(silent=True) or {}).get("index", 0))
    with lock:
        state["pending_jump"] = i
    wake.set()
    return jsonify({"ok": True})


@app.route("/api/pull", methods=["POST"])
def pull():
    added = do_pull()
    if added:
        with lock:
            state["pending_jump"] = len(archive) - 1
        wake.set()
    return jsonify({"ok": True, "msg": f"Added {added} new paper(s)." if added else "Already up to date."})


@app.route("/api/wifi", methods=["POST"])
def wifi():
    d = request.get_json(silent=True) or {}
    ssid = (d.get("ssid") or "").strip()
    pw = d.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "msg": "SSID required."})
    ok, err = create_profile(ssid, pw)
    if not ok:
        return jsonify({"ok": False, "msg": f"Could not save: {err}"})
    threading.Thread(target=_switch_to_client, args=(ssid,), daemon=True).start()
    return jsonify({"ok": True, "msg": f"Saved '{ssid}'. Connecting… the hotspot will now close."})


def _switch_to_client(ssid):
    time.sleep(2)  # let the HTTP response flush to the phone first
    stop_ap()
    if connect_profile(ssid):
        _setup_deadline[0] = 0
        set_mode("ONLINE")
    else:
        log.warning("connect to %s failed; reopening hotspot", ssid)
        start_ap()
        _setup_deadline[0] = time.time() + SETUP_TIMEOUT
        set_mode("SETUP")


if __name__ == "__main__":
    threading.Thread(target=rotator, daemon=True).start()
    threading.Thread(target=supervisor, daemon=True).start()
    threading.Thread(target=syncer, daemon=True).start()
    threading.Thread(target=battery_guard, daemon=True).start()
    app.run(host="0.0.0.0", port=8080, threaded=True)
