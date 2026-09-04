#!/usr/bin/env python3
"""Immich -> Google Photos relay.

Pulls originals from Immich over its HTTP API, feeds them to the handset's Google
Photos backup, waits for the batch to reach the cloud, then deletes the local
copies to reclaim phone storage. Repeats forever.

Safety invariant: Immich is the source of truth and the phone is disposable.
A file is only ever deleted from the phone after its batch reports "Backup
complete", and even a wrongly-deleted file is re-derivable from Immich — so no
original can be lost. The relay only deletes files it pushed (RELAY_PREFIX),
never the handset's own camera roll.

All device control is over adb: the phone is not rooted and Google Photos here is
the ReVanced build, so the app UI (via uiautomator) is the only backup-state
oracle. See docs/superpowers/specs/2026-09-02-immich-google-photos-relay-design.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── configuration (all overridable by env; defaults suit this homelab) ─────────
# Immich serves its REST API under /api (the same host also serves the web SPA at
# the root, so the /api prefix is load-bearing — without it every call 404s).
IMMICH_URL = os.environ.get(
    "IMMICH_URL", "http://immich-server.immich.svc.cluster.local:2283/api").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
STATE_PATH = os.environ.get("STATE_PATH", "/state/relay-state.json")

# Files are pushed into a folder Google Photos already backs up. The prefix makes
# every relay file identifiable so the delete step can never touch a real photo.
PUSH_DIR = os.environ.get("PUSH_DIR", "/sdcard/DCIM/Camera")
RELAY_PREFIX = os.environ.get("RELAY_PREFIX", "immich_")

# A batch is bounded by bytes so the phone never fills up mid-cycle. The Moto G13
# had ~50 GB free; 15 GB leaves generous headroom for Photos' own working space.
BATCH_MAX_BYTES = int(os.environ.get("BATCH_MAX_BYTES", str(15 * 1024**3)))
BATCH_MAX_FILES = int(os.environ.get("BATCH_MAX_FILES", "300"))

# How long to wait for a batch to finish backing up before giving up on it (it
# will simply be retried on the next pass — nothing is lost).
BACKUP_TIMEOUT_S = int(os.environ.get("BACKUP_TIMEOUT_S", str(6 * 3600)))
BACKUP_POLL_S = int(os.environ.get("BACKUP_POLL_S", "60"))
# Idle wait after a full pass finds nothing new, before reconciling again.
IDLE_INTERVAL_S = int(os.environ.get("IDLE_INTERVAL_S", str(3600)))

PHOTOS_PKG = os.environ.get("PHOTOS_PKG", "app.revanced.android.photos")
ADB = os.environ.get("ADB", "adb")

# Where the live-status snapshot is written and the read-only status page served.
# The page is exposed only on the tailnet (a tailscale LoadBalancer Service), so
# it carries no auth of its own — reaching it already requires being on the
# tailnet. It is strictly read-only: it renders progress, it cannot drive adb.
STATUS_PATH = os.environ.get("STATUS_PATH", "/state/status.json")
STATUS_PORT = int(os.environ.get("STATUS_PORT", "8080"))

# Liveness: /healthz reports unhealthy if the main loop hasn't heartbeated within
# this window, so Kubernetes restarts a genuinely stuck relay (deadlock, wedged
# adb). Generously larger than the longest legitimately-blocking step (a single
# adb push, capped at 600s) so a big-file transfer or the idle wait never trips it.
LIVENESS_MAX_AGE = int(os.environ.get("LIVENESS_MAX_AGE", "900"))


def log(msg: str) -> None:
    print(f"[relay] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


# ── live status (served as a read-only page over the tailnet) ───────────────────
# The relay already tracks everything the page needs; set_status just publishes a
# snapshot. It is deliberately best-effort: a status write or a page request must
# never be able to disrupt the actual mirroring loop.
_STATUS_LOCK = threading.Lock()
STATUS: dict = {
    "phase": "starting",       # coarse state machine, mapped to a label in the UI
    "last_event": "",          # the most recent human-readable line
    "started_at": time.time(),  # process start (rate/ETA are "since start")
    "updated_at": time.time(),
    "total": None,             # assets in the Immich library
    "done": None,              # assets confirmed in Google Photos (persisted set)
    "remaining": None,
    "batch_files": None,       # current batch, while one is in flight
    "batch_bytes": None,
    "batch_done": None,        # files pushed so far within the current batch
    "session_start_done": None,  # `done` when this process started, for rate calc
    "immich_ok": None,         # did the last library scan reach Immich?
    "attention": None,         # set when the phone needs a human (backup off, etc.)
}


def set_status(**kw) -> None:
    with _STATUS_LOCK:
        STATUS.update(kw)
        STATUS["updated_at"] = time.time()
        snap = dict(STATUS)
    try:  # mirror to disk so a restart (and any external reader) sees last state
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, STATUS_PATH)
    except OSError:
        pass


_LAST_HB = [0.0]


def heartbeat() -> None:
    """Cheap liveness tick for the long-running inner loops (big downloads, the
    idle wait). Throttled so it can't hammer the disk, it just refreshes
    updated_at so the liveness probe knows the main loop is alive."""
    now = time.time()
    if now - _LAST_HB[0] >= 10:
        _LAST_HB[0] = now
        set_status()


def idle_sleep(seconds: int) -> None:
    """Sleep in short chunks, heartbeating between them, so the idle wait never
    looks like a hang to the liveness probe."""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(30, max(1, end - time.time())))
        set_status()


# The page is a static shell; it fetches /status.json every few seconds and
# renders client-side, so the server never string-builds a whole page per hit.
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Immich → Google Photos relay</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--line:#252936;--fg:#e6e8ee;--muted:#8b90a0;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);padding:24px;
      font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
 .wrap{max-width:640px;margin:0 auto}
 h1{font-size:17px;font-weight:600;margin:0 0 2px}
 .sub{color:var(--muted);font-size:13px;margin-bottom:18px}
 .big{font-size:30px;font-weight:700;letter-spacing:-.5px}
 .big .m{color:var(--muted);font-size:17px;font-weight:600}
 .bar{height:20px;background:var(--line);border-radius:10px;overflow:hidden;margin:12px 0 6px}
 .fill{height:100%;width:0;transition:width .6s ease;
       background:linear-gradient(90deg,#4f8cff,#3fb950)}
 .grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:16px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
 .card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
 .card .v{font-size:17px;font-weight:600;margin-top:3px}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
 .foot{color:var(--muted);font-size:12px;margin-top:16px;text-align:center}
</style></head><body><div class="wrap">
 <h1>Immich → Google Photos relay</h1>
 <div class="sub" id="phase">loading…</div>
 <div id="attn" style="display:none;background:var(--bad);color:#fff;padding:10px 14px;border-radius:10px;margin-bottom:14px;font-weight:600"></div>
 <div class="big"><span id="done">–</span> <span class="m">/ <span id="total">–</span> assets · <span id="pct">–%</span></span></div>
 <div class="bar"><div class="fill" id="fill"></div></div>
 <div class="grid">
  <div class="card"><div class="k">Current batch</div><div class="v" id="batch">–</div></div>
  <div class="card"><div class="k">Remaining</div><div class="v" id="rem">–</div></div>
  <div class="card"><div class="k">Rate (since start)</div><div class="v" id="rate">–</div></div>
  <div class="card"><div class="k">ETA</div><div class="v" id="eta">–</div></div>
  <div class="card"><div class="k">Immich</div><div class="v" id="immich">–</div></div>
  <div class="card"><div class="k">Phase</div><div class="v" id="ph2">–</div></div>
 </div>
 <div class="foot" id="foot">–</div>
</div><script>
var PH={starting:"Starting…",waiting_device:"Waiting for phone…",
 scanning:"Scanning Immich library…",pushing:"Pushing batch to phone…",
 backing_up:"Waiting for Google Photos backup…",reclaiming:"Reclaiming phone storage…",
 idle:"Idle — watching for new assets"};
function dur(s){if(!isFinite(s)||s<0)return "–";s=Math.round(s);
 var d=Math.floor(s/86400);s-=d*86400;var h=Math.floor(s/3600);s-=h*3600;var m=Math.floor(s/60);
 if(d)return d+"d "+h+"h";if(h)return h+"h "+m+"m";return m+"m";}
function $(i){return document.getElementById(i);}
async function tick(){try{
 var s=await (await fetch("status.json",{cache:"no-store"})).json();
 var total=s.total,done=s.done;
 $("done").textContent=done==null?"–":done.toLocaleString();
 $("total").textContent=total==null?"–":total.toLocaleString();
 var pct=(total&&done!=null)?done/total*100:0;
 $("pct").textContent=pct.toFixed(1)+"%";$("fill").style.width=pct+"%";
 $("rem").textContent=(total!=null&&done!=null)?(total-done).toLocaleString():"–";
 var a=$("attn");if(s.attention){a.textContent="⚠ "+s.attention;a.style.display="block";}else{a.style.display="none";}
 $("phase").textContent=(PH[s.phase]||s.phase||"–")+(s.last_event?" · "+s.last_event:"");
 $("ph2").textContent=PH[s.phase]?s.phase:(s.phase||"–");
 $("batch").textContent=s.batch_files?((s.batch_done!=null?s.batch_done+"/":"")+s.batch_files+" files · "+(s.batch_bytes/1048576).toFixed(0)+" MiB"):"—";
 var rate="–",eta="–";
 if(s.session_start_done!=null&&done!=null){
  var el=s.updated_at-s.started_at,mv=done-s.session_start_done;
  if(el>60&&mv>0){rate=(mv/(el/3600)).toFixed(0)+" /hr";
   if(total!=null)eta=dur((total-done)/(mv/el));}}
 $("rate").textContent=rate;$("eta").textContent=eta;
 var io=s.immich_ok,c=io===false?"var(--bad)":(io?"var(--ok)":"var(--muted)");
 $("immich").innerHTML='<span class="dot" style="background:'+c+'"></span>'+
  (io===false?"unreachable":(io?"reachable":"unknown"));
 var age=Date.now()/1000-s.updated_at;
 $("foot").innerHTML="updated "+dur(age)+" ago"+(age>180?
  ' · <span style="color:var(--warn)">stale</span>':"")+" · auto-refresh 8s";
}catch(e){$("phase").textContent="status unavailable ("+e+")";}}
tick();setInterval(tick,8000);
</script></body></html>"""


class _StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep HTTP access noise out of the relay log
        pass

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's required name
        if self.path.startswith("/status.json"):
            with _STATUS_LOCK:
                body = json.dumps(dict(STATUS)).encode()
            self._send(200, "application/json", body)
        elif self.path.startswith("/healthz"):
            # Liveness: healthy only if the main loop heartbeated recently. The
            # status HTTP thread is a daemon independent of the loop, so a plain
            # "ok" here would mask a wedged loop — check the heartbeat instead.
            age = time.time() - STATUS.get("updated_at", 0)
            if age < LIVENESS_MAX_AGE:
                self._send(200, "text/plain", b"ok")
            else:
                self._send(503, "text/plain",
                           f"stale: no heartbeat for {int(age)}s".encode())
        else:
            self._send(200, "text/html; charset=utf-8", _PAGE.encode())


def start_status_server() -> None:
    def serve():
        while True:
            try:
                ThreadingHTTPServer(("0.0.0.0", STATUS_PORT),
                                    _StatusHandler).serve_forever()
            except Exception as e:  # noqa: BLE001 — page must never kill the relay
                log(f"status server error: {e}; retrying in 10s")
                time.sleep(10)
    threading.Thread(target=serve, daemon=True, name="status-http").start()
    log(f"status page serving on :{STATUS_PORT}")


# ── adb helpers ────────────────────────────────────────────────────────────────
def adb(*args: str, timeout: int = 120, check: bool = True,
        binary: bool = False) -> subprocess.CompletedProcess:
    out = subprocess.PIPE
    res = subprocess.run([ADB, *args], stdout=out,
                         stderr=subprocess.PIPE, timeout=timeout)
    if check and res.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: "
                           f"{res.stderr.decode('utf-8', 'replace')[:300]}")
    return res


def adb_shell(cmd: str, **kw) -> str:
    return adb("shell", cmd, **kw).stdout.decode("utf-8", "replace")


def wait_for_device() -> None:
    """Block until the handset is present and authorized. Survives phone reboots
    (e.g. after a system update) — the preserved adb key means no re-tap.

    Self-heals the USB-claim race: after a pod restart the fresh adb daemon can
    come up 'blind' when the previous pod's adb server had not yet released the
    device's USB interface, so `adb devices` stays empty even though passthrough
    is fine. When the device stays absent, restart the adb server (kill + start)
    to clear the stale claim instead of waiting for a human to nudge it.

    Also heartbeats the status page each poll so 'waiting for phone' never looks
    frozen (this is otherwise the one phase with no in-loop status write)."""
    misses = 0
    while True:
        try:
            adb("wait-for-device", timeout=BACKUP_POLL_S)
            state = adb_shell("getprop sys.boot_completed", timeout=30).strip()
            devs = adb("devices", timeout=30).stdout.decode()
            if "\tdevice" in devs and state == "1":
                return
            if "unauthorized" in devs:
                log("device present but UNAUTHORIZED — the adb key was not "
                    "accepted; a physical 'Always allow' tap is required once")
        except Exception as e:  # noqa: BLE001 — keep polling through any transient
            log(f"waiting for device: {e}")
        misses += 1
        set_status(phase="waiting_device",
                   last_event=f"waiting for phone… ({misses} polls)")
        # Clear a stale USB claim from the previous pod's adb server. Cheap and
        # safe: wait-for-device has already timed out, so no handshake is in
        # flight. Skip the very first miss so a phone that is merely mid-connect
        # gets one clean poll before we cycle the server under it.
        if misses > 1:
            log("device still absent — restarting adb server to clear any stale "
                "USB claim")
            adb("kill-server", timeout=30, check=False)
            time.sleep(2)
            adb("start-server", timeout=30, check=False)
        time.sleep(BACKUP_POLL_S)


# ── Immich API ─────────────────────────────────────────────────────────────────
def immich(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(IMMICH_URL + path, data=data, method=method,
                                 headers={"x-api-key": IMMICH_API_KEY,
                                          "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def list_all_assets() -> list[dict]:
    """Every non-trashed image/video, as {id, name, size}. Uses the metadata
    search endpoint which pages deterministically."""
    assets: list[dict] = []
    page = 1
    while True:
        # withExif gives us fileSizeInByte so batches can be bounded by bytes;
        # isTrashed:false keeps deleted-but-not-purged assets out of the mirror.
        res = immich("/search/metadata", "POST",
                     {"page": page, "size": 1000, "withExif": True,
                      "isTrashed": False})
        items = res.get("assets", {}).get("items", [])
        for a in items:
            assets.append({
                "id": a["id"],
                "name": a.get("originalFileName") or f"{a['id']}",
                "size": int(a.get("exifInfo", {}).get("fileSizeInByte") or 0),
            })
        nxt = res.get("assets", {}).get("nextPage")
        if not nxt:
            break
        page = int(nxt)
    return assets


def download_original(asset_id: str, dest: str) -> None:
    req = urllib.request.Request(f"{IMMICH_URL}/assets/{asset_id}/original",
                                 headers={"x-api-key": IMMICH_API_KEY})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            heartbeat()  # a multi-GB video download must not look like a hang


# ── Google Photos backup oracle ────────────────────────────────────────────────
# Promo/onboarding dialogs periodically cover the backup status text and would
# otherwise stall detection indefinitely. Tap these dismiss buttons to clear them.
DISMISS_LABELS = ("got it", "no thanks", "no, thanks", "not now", "skip",
                  "dismiss", "maybe later", "done", "continue", "later")
# Text that means backup is not actually running — needs a human, so surface it.
ATTENTION_MARKERS = ("backup is off", "turn on backup", "back up & sync is off",
                     "backup is paused", "sign in", "sign back in",
                     "couldn't back up", "storage full", "account storage full")


def _dump_ui() -> str:
    adb_shell("uiautomator dump /sdcard/ui.xml", timeout=60, check=False)
    return adb_shell("cat /sdcard/ui.xml", timeout=30, check=False)


def _dismiss_dialog(ui_xml: str) -> bool:
    """If a known promo-dialog button is on screen, tap its centre. uiautomator
    emits attributes in a fixed order (text before bounds within one <node>), so
    a per-node regex is enough. Returns True if it tapped something."""
    for m in re.finditer(
            r'<node[^>]*\btext="([^"]*)"[^>]*\bbounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            ui_xml):
        txt = m.group(1).strip().lower()
        if txt and any(lbl == txt for lbl in DISMISS_LABELS):
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(2, 6))
            adb_shell(f"input tap {(x1 + x2) // 2} {(y1 + y2) // 2}",
                      timeout=30, check=False)
            log(f"dismissed Google Photos dialog button: {txt!r}")
            return True
    return False


def backup_complete() -> bool:
    """True when Google Photos reports its backup queue is drained. The global
    'Backup complete' signal means every pushed item in the queue is in the
    cloud; there is no per-file oracle on this unrooted ReVanced build."""
    try:
        adb_shell("input keyevent KEYCODE_WAKEUP", timeout=30, check=False)
        adb_shell(f"monkey -p {PHOTOS_PKG} -c android.intent.category.LAUNCHER 1",
                  timeout=30, check=False)
        time.sleep(6)
        # Clear up to a few chained promo dialogs before trusting the status text.
        ui_xml = _dump_ui()
        for _ in range(3):
            if not _dismiss_dialog(ui_xml):
                break
            time.sleep(2)
            ui_xml = _dump_ui()
        ui = ui_xml.lower()
    except Exception as e:  # noqa: BLE001
        log(f"backup-oracle read failed: {e}")
        return False
    # Backup disabled / signed out / storage full: the pipeline is stuck until a
    # human acts, so raise it on the status page instead of waiting silently.
    for bad in ATTENTION_MARKERS:
        if bad in ui:
            set_status(attention=f"Google Photos needs a tap on the phone: '{bad}'")
            log(f"ATTENTION: Google Photos not backing up ('{bad}') — needs "
                "manual action on the handset")
            return False
    if "backup complete" in ui:
        set_status(attention=None)
        return True
    # Any of these means the queue is still draining.
    for pending in ("backing up", "backup in progress", "waiting for",
                    "getting ready", "preparing"):
        if pending in ui:
            set_status(attention=None)
            return False
    # Neither string present (Photos not on its home screen): treat as not done.
    return False


def wait_for_backup() -> bool:
    deadline = time.time() + BACKUP_TIMEOUT_S
    start = time.time()
    while time.time() < deadline:
        if backup_complete():
            return True
        # Heartbeat every poll so the status page stays fresh through the wait.
        set_status(last_event="awaiting Google Photos backup… "
                   f"{int(time.time() - start) // 60}m")
        time.sleep(BACKUP_POLL_S)
    return False


# ── state ──────────────────────────────────────────────────────────────────────
# The `done` set is the dedup source of truth: losing it makes the relay re-push
# the whole library (Google Photos' content-hash dedup means no DUPLICATES appear,
# but it is days of wasted work). Two guards: the volume runs multiple Longhorn
# replicas (survives a disk/node loss), and every save also writes a .bak so a
# torn or corrupt primary can be recovered here rather than starting from zero.
def load_state() -> dict:
    for path in (STATE_PATH, STATE_PATH + ".bak"):
        try:
            with open(path) as f:
                st = json.load(f)
            if path != STATE_PATH:
                log(f"primary state unreadable — restored from {path} "
                    f"({len(st.get('done', []))} already done)")
            return st
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {"done": []}


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)  # atomic: a crash never leaves a torn primary
    try:  # rolling backup for corruption/accidental-loss recovery
        shutil.copyfile(STATE_PATH, STATE_PATH + ".bak")
    except OSError:
        pass


# ── batch push / verify / reclaim ──────────────────────────────────────────────
class DeviceLost(Exception):
    """The handset vanished mid-pass. Raised to abort the pass immediately so the
    top-level loop re-enters wait_for_device() (which self-heals the adb server)
    instead of churning every remaining asset against an absent phone — the cause
    of the 2026-09-02 11-hour stall (a device drop turned into 14.5k failed
    pushes, each a wasted Immich download, until the pass finally drained)."""


def device_online() -> bool:
    """Authoritative presence check: adb reports the handset as 'device'. Used to
    tell a genuine device loss (abort the pass) apart from a single bad asset or
    Immich being down (skip and continue — the phone is fine)."""
    try:
        return adb("get-state", timeout=15,
                   check=False).stdout.decode().strip() == "device"
    except Exception:  # noqa: BLE001 — treat any failure to ask as "not online"
        return False


def push_batch(batch: list[dict]) -> list[str]:
    """Download each asset from Immich and adb-push it to the phone. Returns the
    remote paths actually pushed."""
    pushed: list[str] = []
    total = len(batch)
    # Don't download a whole batch to feed a phone that is already gone.
    if not device_online():
        raise DeviceLost("handset absent at batch start")
    for i, a in enumerate(batch, 1):
        local = f"/tmp/{RELAY_PREFIX}{a['id']}"
        safe_name = a["name"].replace("/", "_")
        remote = f"{PUSH_DIR}/{RELAY_PREFIX}{a['id']}_{safe_name}"
        # Per-file heartbeat: keeps the status page's updated_at fresh through the
        # long push loop (a 15 GiB batch takes many minutes) and shows which file
        # is in flight, so the page never looks frozen mid-batch.
        set_status(batch_done=i - 1,
                   last_event=f"pushing {i}/{total}: {a['name'][:48]}")
        try:
            download_original(a["id"], local)
            adb("push", local, remote, timeout=600)
            pushed.append(remote)
        except Exception as e:  # noqa: BLE001 — one bad asset must not stop the batch
            log(f"skip asset {a['id']} ({a['name']}): {e}")
            # A single bad asset is skipped, but if the HANDSET itself has gone,
            # abort the whole pass now rather than re-downloading every remaining
            # asset only to fail the push against nothing.
            if not device_online():
                raise DeviceLost(f"handset lost after {a['id']}") from e
        finally:
            if os.path.exists(local):
                os.remove(local)
    set_status(batch_done=total)
    if pushed:
        # One scan for the whole batch is far cheaper than one per file.
        adb_shell("content call --uri content://media/external/file "
                  "--method scan_volume --arg external_primary",
                  timeout=120, check=False)
    return pushed


def delete_pushed(paths: list[str]) -> None:
    """Delete only the files we pushed (RELAY_PREFIX guarantees this never hits a
    real camera photo), then rescan so MediaStore drops the rows."""
    for p in paths:
        adb_shell(f"rm -f '{p}'", timeout=60, check=False)
    adb_shell("content call --uri content://media/external/file "
              "--method scan_volume --arg external_primary",
              timeout=120, check=False)


def run_pass(state: dict) -> int:
    """One reconciliation pass. Returns how many assets were newly mirrored."""
    done = set(state["done"])
    set_status(phase="scanning", last_event="scanning Immich library")
    try:
        assets = list_all_assets()
    except Exception:  # surface Immich being unreachable on the status page
        set_status(immich_ok=False, last_event="Immich unreachable")
        raise
    todo = [a for a in assets if a["id"] not in done]
    set_status(total=len(assets), done=len(done), remaining=len(todo),
               immich_ok=True, last_event=f"{len(todo)} still to mirror")
    log(f"library has {len(assets)} assets; {len(todo)} still to mirror")
    mirrored = 0

    while todo:
        batch, size = [], 0
        while todo and len(batch) < BATCH_MAX_FILES and size < BATCH_MAX_BYTES:
            a = todo.pop(0)
            batch.append(a)
            size += a["size"] or 0
        set_status(phase="pushing", batch_files=len(batch), batch_bytes=size,
                   last_event=f"pushing {len(batch)} assets")
        log(f"pushing batch of {len(batch)} assets (~{size // 1024**2} MiB)")
        try:
            pushed = push_batch(batch)
        except DeviceLost as e:
            # End the pass so main() re-enters wait_for_device(), which restarts
            # the adb server and recovers the handset — turning a device blip into
            # a ~2-minute pause instead of an hours-long churn.
            set_status(phase="waiting_device", batch_files=None, batch_bytes=None,
                       batch_done=None, last_event="handset lost; recovering")
            log(f"handset lost mid-pass ({e}); ending pass to recover the device")
            break
        if not pushed:
            log("nothing pushed in this batch; moving on")
            continue

        set_status(phase="backing_up",
                   last_event=f"{len(pushed)} files pushed; awaiting backup")
        log(f"pushed {len(pushed)} files; waiting for Google Photos to back up")
        if not wait_for_backup():
            set_status(last_event="backup timed out; will retry next pass")
            log("backup did not complete before timeout — leaving local files "
                "in place and NOT marking done; will retry next pass")
            continue

        # Batch is in the cloud: safe to mark done and reclaim phone storage.
        for a in batch:
            if a["id"] not in done:
                done.add(a["id"])
                state["done"].append(a["id"])
        save_state(state)
        set_status(phase="reclaiming", last_event="reclaiming phone storage")
        delete_pushed(pushed)
        mirrored += len(batch)
        set_status(done=len(done), remaining=len(todo),
                   batch_files=None, batch_bytes=None, batch_done=None,
                   last_event=f"{mirrored} mirrored this pass")
        log(f"batch mirrored and reclaimed; {mirrored} done this pass")

    return mirrored


def main() -> int:
    # The status page comes up first so it is inspectable even while the relay is
    # idle for want of a key, or blocked waiting for the handset.
    start_status_server()

    if not IMMICH_API_KEY:
        set_status(phase="idle", last_event="waiting for IMMICH_API_KEY")
        log("IMMICH_API_KEY is empty — set the photos-relay-immich Secret "
            "(see the app README). Idling so the pod stays up for inspection.")
        while True:
            idle_sleep(300)

    base_done = len(load_state().get("done", []))
    set_status(phase="starting", done=base_done, session_start_done=base_done,
               last_event="relay starting")
    log(f"relay starting; Immich={IMMICH_URL} push_dir={PUSH_DIR}")
    while True:
        try:
            set_status(phase="waiting_device")
            wait_for_device()
            state = load_state()
            newly = run_pass(state)
            if newly == 0:
                set_status(phase="idle",
                           last_event=f"nothing new; idle {IDLE_INTERVAL_S // 60}m")
                log(f"nothing new; idling {IDLE_INTERVAL_S}s before next pass")
                idle_sleep(IDLE_INTERVAL_S)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001 — the loop must never die
            set_status(last_event=f"pass failed: {str(e)[:120]}")
            log(f"pass failed: {e}; retrying in {BACKUP_POLL_S}s")
            time.sleep(BACKUP_POLL_S)


if __name__ == "__main__":
    sys.exit(main())
