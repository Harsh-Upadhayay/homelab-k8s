#!/usr/bin/env python3
"""One-shot Telegram -> Google Photos backfill.

Feeds media from a set of Telegram chats through the photos-relay handset's Google
Photos backup, exactly like the Immich relay does for Immich: download an original,
adb-push it to the phone, wait for Google Photos to confirm the upload, then delete
the local copy to reclaim phone storage. It is a ONE-SHOT: it walks each chat's full
backlog once, marks itself done, and stops (no continuous watch).

It is intentionally SELF-CONTAINED (its own adb + backup-oracle logic, no import of
the relay's relay.py) so it can run as a throwaway process inside the relay pod
without touching the committed relay. See README.md for the full runbook.

Design notes:
  * Runs INSIDE the relay pod (that pod owns the USB/adb claim on the handset — only
    one process can, so a separate pod would contend). The idle relay does not touch
    the phone unless a new Immich asset arrives, so the two coexist; this script uses
    a SEPARATE uiautomator dump file so it never races the relay's own oracle.
  * Resumable: a per-chat high-water mark (largest backed-up message id) lives in the
    state file, so a pod restart / device drop resumes instead of restarting. The
    detached process itself dies on a pod restart and must be relaunched by hand
    (see README) — that is the price of "no permanent workload".
  * Dedup across chats is left to Google Photos' content-hash dedup: the same clip in
    two chats is pushed twice and Google Photos collapses it to one.
  * mtime is set to the message date before push and adb preserves it, so Google
    Photos times the item to when it was posted when the file carries no EXIF (it
    uses embedded EXIF/metadata when present — uncompressed "document" media keeps
    its original capture date).

Config (JSON at $TG_CONFIG, default /state/tg-oneshot/config.json):
  {"api_id": "12345", "api_hash": "...", "session": "<StringSession>",
   "channels": [["<chat_id>", "Display Name"], ...]}   # ordered; small->large is nice
Never commit a filled-in config: api_hash + session are full-account credentials.
"""
import json, os, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CFG = json.load(open(os.environ.get("TG_CONFIG", "/state/tg-oneshot/config.json")))
API_ID = int(CFG["api_id"]); API_HASH = CFG["api_hash"]; SESSION = CFG["session"]
TARGETS = CFG["channels"]                  # ordered list of [id, name]
ADB = os.environ.get("ADB", "/opt/platform-tools/adb")
PUSH_DIR = os.environ.get("TG_PUSH_DIR", "/sdcard/DCIM/Camera")
PREFIX = "tg_"
STATE = os.environ.get("TG_STATE", "/state/tg-oneshot/tg-state.json")
STATUS_PATH = os.environ.get("TG_STATUS", "/state/tg-oneshot/status.json")
STATUS_PORT = int(os.environ.get("TG_STATUS_PORT", "8090"))
UI_DUMP = "/sdcard/ui_tg.xml"              # separate from the relay's /sdcard/ui.xml
BATCH_MAX_FILES = int(os.environ.get("TG_BATCH_MAX_FILES", "150"))
BATCH_MAX_BYTES = int(os.environ.get("TG_BATCH_MAX_BYTES", str(8 * 1024**3)))
BACKUP_POLL_S = 60
BACKUP_TIMEOUT_S = 6 * 3600
PHOTOS_PKG = os.environ.get("PHOTOS_PKG", "app.revanced.android.photos")
DISMISS = ("got it", "no thanks", "no, thanks", "not now", "skip", "dismiss",
           "maybe later", "done", "continue", "later")


def log(m): print(f"[tg] {time.strftime('%Y-%m-%d %H:%M:%S')} {m}", flush=True)


def safe_name(name):
    """Restrict a filename to shell- and MediaStore-safe characters. The push and
    reclaim run through `adb shell`, and a name with an apostrophe (e.g.
    "i'm-a-poor-guy.mp4") breaks the single-quoted `rm '<path>'` so the file is
    pushed but never deleted. Replacing everything outside [A-Za-z0-9._-] avoids
    every such quoting hazard while keeping the extension."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "")


# ── live status (served on :8090, exposed via a temp Tailscale Service) ──────────
_LOCK = threading.Lock()
STATUS = {"phase": "starting", "current_channel": None, "last_event": "",
          "started_at": time.time(), "updated_at": time.time(),
          "total_pushed": 0, "skipped": 0, "channels_total": len(TARGETS),
          "channels_done": 0, "done": False, "channels": []}


def set_status(**kw):
    with _LOCK:
        STATUS.update(kw); STATUS["updated_at"] = time.time(); snap = dict(STATUS)
    try:
        tmp = STATUS_PATH + ".tmp"; json.dump(snap, open(tmp, "w")); os.replace(tmp, STATUS_PATH)
    except OSError:
        pass


_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Telegram → Google Photos backfill</title><style>
:root{--bg:#0f1117;--card:#1a1d27;--line:#252936;--fg:#e6e8ee;--muted:#8b90a0;--ok:#3fb950;--acc:#4f8cff;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);padding:24px;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:620px;margin:0 auto}h1{font-size:17px;margin:0 0 2px}.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.big{font-size:30px;font-weight:700}.big .m{color:var(--muted);font-size:17px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}.card .v{font-size:18px;font-weight:600;margin-top:3px}
table{width:100%;border-collapse:collapse;margin-top:8px}td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:14px}
th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.badge{font-size:12px;padding:2px 8px;border-radius:20px}.done{background:rgba(63,185,80,.15);color:var(--ok)}
.cur{background:rgba(79,140,255,.15);color:var(--acc)}.pend{color:var(--muted)}
.foot{color:var(--muted);font-size:12px;margin-top:16px;text-align:center}</style></head><body><div class=wrap>
<h1>Telegram → Google Photos backfill</h1><div class=sub id=phase>loading…</div>
<div class=big><span id=pushed>–</span> <span class=m>media backed up</span></div>
<div class=grid>
<div class=card><div class=k>Channels</div><div class=v id=chans>–</div></div>
<div class=card><div class=k>Skipped</div><div class=v id=skip>–</div></div>
<div class=card><div class=k>Elapsed</div><div class=v id=elapsed>–</div></div></div>
<table><thead><tr><th>Channel</th><th>Status</th><th style=text-align:right>Pushed</th></tr></thead><tbody id=rows></tbody></table>
<div class=foot id=foot>–</div></div><script>
var PH={starting:"Starting…",connecting:"Connecting to Telegram…",channel:"Backfilling channel…",
pushing:"Pushing batch to phone…",backing_up:"Waiting for Google Photos backup…",done:"✅ All channels complete"};
function dur(s){if(!isFinite(s)||s<0)return"–";s=Math.round(s);var d=Math.floor(s/86400);s-=d*86400;var h=Math.floor(s/3600);s-=h*3600;var m=Math.floor(s/60);return(d?d+"d ":"")+(h||d?h+"h ":"")+m+"m";}
function $(i){return document.getElementById(i);}
async function tick(){try{var s=await(await fetch("status.json",{cache:"no-store"})).json();
$("phase").textContent=(PH[s.phase]||s.phase||"–")+(s.current_channel?" · "+s.current_channel:"")+(s.last_event?" · "+s.last_event:"");
$("pushed").textContent=(s.total_pushed||0).toLocaleString();
$("chans").textContent=(s.channels_done||0)+" / "+(s.channels_total||0);
$("skip").textContent=(s.skipped||0).toLocaleString();
$("elapsed").textContent=dur(s.updated_at-s.started_at);
var rows="";(s.channels||[]).forEach(function(c){var b=c.status=="done"?'<span class="badge done">done</span>':c.status=="current"?'<span class="badge cur">in progress</span>':'<span class="badge pend">pending</span>';
rows+="<tr><td>"+c.name+"</td><td>"+b+"</td><td style=text-align:right>"+(c.pushed||0).toLocaleString()+"</td></tr>";});
$("rows").innerHTML=rows;var age=Date.now()/1000-s.updated_at;
$("foot").innerHTML="updated "+dur(age)+" ago"+(age>300?' · <span style="color:var(--warn)">stale (process may be down)</span>':"")+" · auto-refresh 10s";
}catch(e){$("phase").textContent="status unavailable ("+e+")";}}
tick();setInterval(tick,10000);</script></body></html>"""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, code, ct, body):
        self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try: self.wfile.write(body)
        except BrokenPipeError: pass
    def do_GET(self):
        if self.path.startswith("/status.json"):
            with _LOCK: b = json.dumps(dict(STATUS)).encode()
            self._s(200, "application/json", b)
        elif self.path.startswith("/healthz"):
            self._s(200, "text/plain", b"ok")
        else:
            self._s(200, "text/html; charset=utf-8", _PAGE.encode())


def start_status_server():
    def serve():
        while True:
            try: ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), _H).serve_forever()
            except Exception as e: log(f"status server: {e}; retry 10s"); time.sleep(10)
    threading.Thread(target=serve, daemon=True, name="tg-status").start()
    log(f"status page on :{STATUS_PORT}")


# ── adb ──────────────────────────────────────────────────────────────────────────
def adb(*a, timeout=120, check=True):
    r = subprocess.run([ADB, *a], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(a)}: {r.stderr.decode('utf-8','replace')[:200]}")
    return r


def adb_shell(cmd, **kw): return adb("shell", cmd, **kw).stdout.decode("utf-8", "replace")


def device_online():
    try: return adb("get-state", timeout=15, check=False).stdout.decode().strip() == "device"
    except Exception: return False


def wait_for_device():
    """Block until the handset is present + booted. Self-heals the adb USB-claim
    race (a stale adb server holding the interface) by restarting the server."""
    misses = 0
    while True:
        try:
            adb("wait-for-device", timeout=BACKUP_POLL_S)
            if device_online() and adb_shell("getprop sys.boot_completed", timeout=30).strip() == "1":
                return
        except Exception as e: log(f"waiting for device: {e}")
        misses += 1
        set_status(last_event=f"waiting for phone ({misses})")
        if misses > 1:
            log("device absent — restarting adb server")
            adb("kill-server", timeout=30, check=False); time.sleep(2); adb("start-server", timeout=30, check=False)
        time.sleep(BACKUP_POLL_S)


def _tap_dialog(ui):
    for m in re.finditer(r'<node[^>]*\btext="([^"]*)"[^>]*\bbounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', ui):
        t = m.group(1).strip().lower()
        if t and t in DISMISS:
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(2, 6))
            adb_shell(f"input tap {(x1+x2)//2} {(y1+y2)//2}", timeout=30, check=False); return True
    return False


def backup_complete():
    """True once Google Photos shows 'Backup complete'. Dismisses promo dialogs
    first — they otherwise cover the status text and stall detection forever."""
    try:
        adb_shell("input keyevent KEYCODE_WAKEUP", timeout=30, check=False)
        adb_shell(f"monkey -p {PHOTOS_PKG} -c android.intent.category.LAUNCHER 1", timeout=30, check=False)
        time.sleep(6)
        adb_shell(f"uiautomator dump {UI_DUMP}", timeout=60, check=False)
        ui = adb_shell(f"cat {UI_DUMP}", timeout=30, check=False)
        for _ in range(3):
            if not _tap_dialog(ui): break
            time.sleep(2); adb_shell(f"uiautomator dump {UI_DUMP}", timeout=60, check=False)
            ui = adb_shell(f"cat {UI_DUMP}", timeout=30, check=False)
        return "backup complete" in ui.lower()
    except Exception as e:
        log(f"oracle read failed: {e}"); return False


def wait_for_backup():
    dl = time.time() + BACKUP_TIMEOUT_S
    while time.time() < dl:
        if backup_complete(): return True
        set_status(last_event="awaiting Google Photos backup")
        time.sleep(BACKUP_POLL_S)
    return False


# ── state ────────────────────────────────────────────────────────────────────────
def load_state():
    try: return json.load(open(STATE))
    except Exception: return {"hw": {}, "channels_done": [], "pushed": {}, "done": False, "skipped": 0}


def save_state(s):
    tmp = STATE + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, STATE)


def publish(state):
    """Rebuild the status page's channel table from state + config."""
    done = set(state.get("channels_done", [])); pushed = state.get("pushed", {})
    cur = STATUS.get("current_channel"); chans = []
    for cid, name in TARGETS:
        st = "done" if cid in done else ("current" if name == cur else "pending")
        chans.append({"name": name, "pushed": pushed.get(cid, 0), "status": st})
    set_status(channels=chans, channels_done=len(done),
               total_pushed=sum(pushed.values()), skipped=state.get("skipped", 0))


def item_of(msg):
    """A push item for a qualifying message (photo/video/image-or-video document),
    else None. Non-media documents (PDFs, zips, audio) are skipped."""
    f = getattr(msg, "file", None)
    if f is None: return None
    mime = f.mime_type or ""
    if not (mime.startswith("image/") or mime.startswith("video/")): return None
    name = safe_name(f.name or f"{msg.id}{f.ext or ''}")
    return {"msg": msg, "msg_id": msg.id, "name": name, "size": f.size or 0,
            "date": int(msg.date.timestamp()) if msg.date else None}


def push_confirm_reclaim(client, chan_id, batch, state):
    if not device_online(): raise RuntimeError("device gone")
    pushed = []
    for it in batch:
        remote = f"{PUSH_DIR}/{PREFIX}{chan_id}_{it['msg_id']}_{it['name']}"
        local = None
        try:
            # Telethon appends an extension when the path lacks one and returns the
            # ACTUAL path written — trust that for utime/push/cleanup.
            local = client.download_media(it["msg"], file=f"/tmp/{PREFIX}{chan_id}_{it['msg_id']}_{it['name']}")
            if not local or not os.path.exists(local): raise RuntimeError("download produced no file")
            if it["date"]: os.utime(local, (it["date"], it["date"]))
            adb("push", local, remote, timeout=900); pushed.append(remote)
        except Exception as e:
            log(f"skip {chan_id}/{it['msg_id']} ({it['name']}): {e}")
            state["skipped"] = state.get("skipped", 0) + 1; publish(state)
            if not device_online():
                if local and os.path.exists(local): os.remove(local)
                raise RuntimeError("device gone")
        finally:
            if local and os.path.exists(local): os.remove(local)
    if not pushed: return 0
    adb_shell("content call --uri content://media/external/file --method scan_volume --arg external_primary", timeout=120, check=False)
    set_status(phase="backing_up", last_event=f"awaiting backup of {len(pushed)}")
    log(f"pushed {len(pushed)} files; waiting for Google Photos backup")
    if not wait_for_backup(): raise RuntimeError("backup timeout")
    for p in pushed: adb_shell(f"rm -f '{p}'", timeout=60, check=False)
    adb_shell("content call --uri content://media/external/file --method scan_volume --arg external_primary", timeout=120, check=False)
    log(f"batch of {len(pushed)} backed up + reclaimed")
    return len(pushed)


def backfill_channel(client, entity, name, state):
    key = str(entity.id); hw = state["hw"].get(key, 0)
    set_status(phase="channel", current_channel=name, last_event=f"scanning from msg {hw}")
    publish(state)  # mark this channel "current" in the table immediately
    log(f"channel {name!r}: backfilling from msg_id>{hw}")
    batch, size, n = [], 0, 0
    def flush():
        nonlocal batch, size, n, hw
        if not batch: return
        set_status(phase="pushing", last_event=f"pushing {len(batch)}")
        c = push_confirm_reclaim(client, entity.id, batch, state)
        hw = max(i["msg_id"] for i in batch); state["hw"][key] = hw
        state.setdefault("pushed", {})[key] = state.get("pushed", {}).get(key, 0) + c
        save_state(state); publish(state)
        n += len(batch); log(f"channel {name!r}: {n} scanned this run; hw={hw}")
        batch, size = [], 0
    for msg in client.iter_messages(entity, reverse=True, min_id=hw):
        it = item_of(msg)
        if not it: continue
        batch.append(it); size += it["size"]
        if len(batch) >= BATCH_MAX_FILES or size >= BATCH_MAX_BYTES: flush()
    flush()
    state.setdefault("channels_done", []).append(key); save_state(state); publish(state)
    log(f"channel {name!r}: COMPLETE")


def run_once():
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    state = load_state(); publish(state)
    wait_for_device()
    set_status(phase="connecting")
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH); client.connect()
    try:
        if not client.is_user_authorized():
            set_status(last_event="SESSION NOT AUTHORIZED"); log("session not authorized"); return
        want = {int(cid): nm for cid, nm in TARGETS}; ents = {}
        # Resolving from the dialog list populates each entity's access_hash cache —
        # resolving a bare numeric id directly fails ("Could not find the input entity").
        for d in client.iter_dialogs():
            if d.entity.id in want: ents[d.entity.id] = d.entity
        log(f"resolved {len(ents)}/{len(want)} target chats")
        for cid, name in TARGETS:
            if cid in state.get("channels_done", []): continue
            e = ents.get(int(cid))
            if not e:
                log(f"channel {name!r} not in dialogs; skipping")
                state.setdefault("channels_done", []).append(cid); save_state(state); continue
            backfill_channel(client, e, name, state)
        state["done"] = True; save_state(state)
        set_status(phase="done", current_channel=None, done=True,
                   last_event=f"complete; {state.get('skipped',0)} skipped"); publish(state)
        log(f"ALL COMPLETE — skipped {state.get('skipped',0)}")
    finally:
        client.disconnect()


def main():
    start_status_server()
    log("tg one-shot backfill starting")
    while True:
        if load_state().get("done"):
            set_status(phase="done", done=True); log("already done"); return
        try:
            run_once()
        except Exception as ex:
            set_status(last_event=f"interrupted: {str(ex)[:80]}")
            log(f"run interrupted ({ex}); retry in 60s"); time.sleep(60)
        if load_state().get("done"): return


if __name__ == "__main__":
    main()
