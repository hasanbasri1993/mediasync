from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import subprocess, threading, os, time, re, json, shutil
from urllib import request as urlrequest, error as urlerror
import config

# ── Settings persistence ──────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "pin": config.PIN_CODE,
    "transfer_mode": "cut",          # "cut" | "copy"
    "telegram": {
        "enabled":  False,
        "bot_token": "",
        "chat_id":   ""
    },
    "webhook": {
        "enabled": False,
        "url":     "",
        "method":  "POST",           # POST | GET | PUT
        "headers": {},               # dict: {"Authorization": "Bearer ..."}
        "body":    {}                # dict; nilai dapat berisi placeholder
    }
}

def _deep_merge(base, override):
    """Merge dict secara rekursif (override menang)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        return _deep_merge(DEFAULT_SETTINGS, data)
    except Exception as e:
        print(f"⚠ Gagal load settings.json: {e}")
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠ Gagal simpan settings.json: {e}")
        return False

def get_current_pin():
    return load_settings().get("pin", config.PIN_CODE)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

# Pastikan settings.json ada
load_settings()

state = {
    "running":          False,
    "phase":            "idle",
    "folder":           "",
    "device":           "",
    "mount_point":      "/mnt/sdcard_tmp",
    "file_count":       0,
    "cut_done":         0,        # realtime counter saat cut
    "cut_total":        0,        # total file akan di-cut
    "percent":          0,
    "speed":            "",
    "transferred":      "",
    "total_size":       "",
    "eta":              "",
    "last_sync":        "Belum pernah",
    "sdcard_connected": False,
    "error":            "",
    "syncing_folder":   ""        # folder yang sedang di-sync
}

# ── PIN ───────────────────────────────────────────────────────
@app.route("/api/verify-pin", methods=["POST"])
def verify_pin():
    data    = request.get_json() or {}
    purpose = data.get("purpose", "import")   # import | view_qr | settings
    if data.get("pin") == get_current_pin():
        session[f"pin_{purpose}"] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

# ── Settings API ──────────────────────────────────────────────
def _sanitize_for_client(s):
    """Hilangkan field sensitif (PIN penuh, bot token) sebelum kirim ke client."""
    out = json.loads(json.dumps(s))   # deep copy
    out.pop("pin", None)
    if isinstance(out.get("telegram"), dict):
        bt = out["telegram"].get("bot_token", "")
        out["telegram"]["bot_token_set"] = bool(bt)
        # Jangan kirim token mentah ke client; cukup masked
        if bt:
            out["telegram"]["bot_token"] = bt[:4] + "…" + bt[-4:] if len(bt) > 10 else "●●●●"
    return out

@app.route("/api/settings", methods=["GET"])
def get_settings():
    if not session.get("pin_settings"):
        return jsonify({"error": "PIN diperlukan"}), 401
    return jsonify({"success": True, "settings": _sanitize_for_client(load_settings())})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    if not session.get("pin_settings"):
        return jsonify({"error": "PIN diperlukan"}), 401
    payload = request.get_json() or {}
    current = load_settings()

    # transfer_mode
    mode = payload.get("transfer_mode")
    if mode in ("cut", "copy"):
        current["transfer_mode"] = mode

    # telegram
    tg = payload.get("telegram") or {}
    if isinstance(tg, dict):
        cur_tg = current.get("telegram", {})
        if "enabled" in tg:   cur_tg["enabled"]   = bool(tg["enabled"])
        # Hanya update token jika user mengirim nilai baru (bukan masked)
        if "bot_token" in tg and tg["bot_token"] and "…" not in tg["bot_token"] and tg["bot_token"] != "●●●●":
            cur_tg["bot_token"] = tg["bot_token"].strip()
        if "chat_id" in tg:   cur_tg["chat_id"]   = str(tg["chat_id"]).strip()
        current["telegram"] = cur_tg

    # webhook
    wh = payload.get("webhook") or {}
    if isinstance(wh, dict):
        cur_wh = current.get("webhook", {})
        if "enabled" in wh: cur_wh["enabled"] = bool(wh["enabled"])
        if "url"     in wh: cur_wh["url"]     = str(wh["url"]).strip()
        if "method"  in wh:
            m = str(wh["method"]).upper().strip()
            cur_wh["method"] = m if m in ("GET","POST","PUT","PATCH") else "POST"
        if "headers" in wh:
            cur_wh["headers"] = wh["headers"] if isinstance(wh["headers"], dict) else {}
        if "body" in wh:
            cur_wh["body"] = wh["body"] if isinstance(wh["body"], dict) else {}
        current["webhook"] = cur_wh

    if save_settings(current):
        # Sekali simpan, invalidasi session pin_settings
        return jsonify({"success": True, "settings": _sanitize_for_client(current)})
    return jsonify({"error": "Gagal menyimpan settings"}), 500

@app.route("/api/settings/change-pin", methods=["POST"])
def change_pin():
    if not session.get("pin_settings"):
        return jsonify({"error": "PIN diperlukan"}), 401
    data = request.get_json() or {}
    old_pin = str(data.get("old_pin", ""))
    new_pin = str(data.get("new_pin", ""))
    if old_pin != get_current_pin():
        return jsonify({"error": "PIN lama salah"}), 400
    if not (new_pin.isdigit() and len(new_pin) == 4):
        return jsonify({"error": "PIN baru harus 4 digit angka"}), 400
    s = load_settings()
    s["pin"] = new_pin
    if not save_settings(s):
        return jsonify({"error": "Gagal menyimpan PIN"}), 500
    log("🔐 PIN berhasil diganti")
    return jsonify({"success": True})

@app.route("/api/settings/test-notif", methods=["POST"])
def test_notif():
    if not session.get("pin_settings"):
        return jsonify({"error": "PIN diperlukan"}), 401
    data = request.get_json() or {}
    channel = data.get("channel", "telegram")
    sample = {
        "folder":     "TEST_" + time.strftime("%Y-%m-%d_%H:%M:%S"),
        "file_count": 0,
        "gdrive_url": "https://drive.google.com/",
        "time":       time.strftime("%Y-%m-%d %H:%M:%S"),
        "event":      "test"
    }
    s = load_settings()
    if channel == "telegram":
        ok, msg = send_telegram(s.get("telegram", {}), sample, is_test=True)
    elif channel == "webhook":
        ok, msg = send_webhook(s.get("webhook", {}), sample, is_test=True)
    else:
        return jsonify({"error": "Channel tidak dikenal"}), 400
    return jsonify({"success": ok, "message": msg})

@app.route("/")
def index():
    return render_template("index.html")

# ── Status ────────────────────────────────────────────────────
@app.route("/api/status")
def get_status():
    return jsonify({**state, "recent_folders": get_folder_list()})

def get_folder_list():
    result = []
    if not os.path.exists(config.DEST_BASE):
        return result
    synced      = load_synced_markers()
    gdrive_urls = load_gdrive_urls()
    for folder in sorted(os.listdir(config.DEST_BASE), reverse=True)[:10]:
        fpath = os.path.join(config.DEST_BASE, folder)
        if not os.path.isdir(fpath):
            continue
        files = [f for f in os.listdir(fpath)
                 if os.path.isfile(os.path.join(fpath, f))]
        size  = sum(os.path.getsize(os.path.join(fpath, f))
                    for f in files
                    if os.path.isfile(os.path.join(fpath, f)))
        # Status folder: syncing / synced / pending
        if folder == state.get("syncing_folder") and state.get("running"):
            status = "syncing"
        elif folder in synced:
            status = "synced"
        else:
            status = "pending"
        result.append({
            "folder":     folder,
            "count":      len(files),
            "size_mb":    round(size / 1024 / 1024, 1),
            "status":     status,
            "gdrive_url": gdrive_urls.get(folder, "")
        })
    return result

def load_synced_markers():
    mf = os.path.join(config.DEST_BASE, ".synced_folders")
    if not os.path.exists(mf):
        return set()
    with open(mf) as f:
        return set(l.strip() for l in f if l.strip())

def mark_as_synced(folder_name):
    mf = os.path.join(config.DEST_BASE, ".synced_folders")
    synced = load_synced_markers()
    synced.add(folder_name)
    with open(mf, "w") as f:
        f.write("\n".join(synced) + "\n")

def load_gdrive_urls():
    mf = os.path.join(config.DEST_BASE, ".gdrive_urls")
    if not os.path.exists(mf):
        return {}
    urls = {}
    with open(mf) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                urls[k.strip()] = v.strip()
    return urls

def save_gdrive_url(folder_name, url):
    mf = os.path.join(config.DEST_BASE, ".gdrive_urls")
    urls = load_gdrive_urls()
    urls[folder_name] = url
    with open(mf, "w") as f:
        for k, v in urls.items():
            f.write(f"{k}={v}\n")

def get_gdrive_url(folder_name):
    """Ambil URL Google Drive untuk folder ini via rclone link"""
    try:
        result = subprocess.run(
            ["rclone", "link", f"{config.GDRIVE_REMOTE}/{folder_name}"],
            capture_output=True, text=True, timeout=15
        )
        url = result.stdout.strip()
        if url.startswith("http"):
            save_gdrive_url(folder_name, url)
            return url
    except Exception as e:
        log(f"⚠ Gagal ambil GDrive URL: {e}")
    # Fallback: generate URL manual dari remote config
    return ""

# ── GDrive URL endpoint (butuh PIN view_qr) ───────────────────
@app.route("/api/gdrive-url/<folder_name>")
def gdrive_url(folder_name):
    if not session.get("pin_view_qr"):
        return jsonify({"error": "PIN diperlukan"}), 401
    session.pop("pin_view_qr", None)

    # Cek cache dulu
    urls = load_gdrive_urls()
    if folder_name in urls:
        return jsonify({"success": True, "url": urls[folder_name]})

    # Ambil live dari rclone
    url = get_gdrive_url(folder_name)
    if url:
        return jsonify({"success": True, "url": url})
    return jsonify({"error": "URL tidak tersedia, coba sync ulang"}), 404

# ── SD Card event ─────────────────────────────────────────────
@app.route("/api/sdcard-event", methods=["POST"])
def sdcard_event():
    data  = request.get_json()
    event = data.get("event", "")
    if event == "detected":
        state.update({
            "phase":            "waiting_pin",
            "device":           data.get("device", ""),
            "mount_point":      data.get("mount_point", "/mnt/sdcard_tmp"),
            "file_count":       data.get("file_count", 0),
            "sdcard_connected": True,
            "error":            ""
        })
        socketio.emit("sdcard_detected", {
            "file_count": state["file_count"],
            "device":     state["device"]
        })
        socketio.emit("play_sound", {"sound": "insert"})
        log(f"💾 SD Card: {state['device']} ({state['file_count']} file)")
    elif event == "error":
        socketio.emit("import_error", {"message": data.get("message", "Error")})
    return jsonify({"success": True})

# ── Start import ──────────────────────────────────────────────
@app.route("/api/start-import", methods=["POST"])
def start_import():
    if not session.get("pin_import"):
        return jsonify({"error": "PIN belum diverifikasi"}), 401
    if state["phase"] not in ("waiting_pin", "idle"):
        return jsonify({"error": "Tidak ada SD card menunggu"}), 400
    if state["running"]:
        return jsonify({"error": "Proses sedang berjalan"}), 400
    session.pop("pin_import", None)
    threading.Thread(target=run_import_pipeline, daemon=True).start()
    return jsonify({"success": True})

# ── Manual sync ───────────────────────────────────────────────
@app.route("/api/sync", methods=["POST"])
def manual_sync():
    folder = request.get_json().get("folder", "")
    if not folder:
        return jsonify({"error": "Folder tidak valid"}), 400
    if state["running"]:
        return jsonify({"error": "Proses sedang berjalan"}), 400
    threading.Thread(target=sync_only, args=(folder,), daemon=True).start()
    return jsonify({"success": True})

# ── Manual eject ──────────────────────────────────────────────
@app.route("/api/eject", methods=["POST"])
def eject_sdcard():
    ok, msg = do_eject()
    return jsonify({"success": ok, "message": msg} if ok
                   else {"success": False, "error": msg})

# ── Core eject ────────────────────────────────────────────────
def do_eject():
    mount_pt = state.get("mount_point", "/mnt/sdcard_tmp")
    device   = state.get("device", "")
    try:
        subprocess.run(["sudo", "sync"], check=True)
        time.sleep(0.5)
        r = subprocess.run(["sudo", "umount", mount_pt],
                           capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["sudo", "umount", "-l", mount_pt], check=True)
        if device and device.startswith("/dev/loop"):
            subprocess.run(["sudo", "losetup", "-d", device],
                           capture_output=True)
            log(f"⏏ Loop device dilepas: {device}")
        state.update({
            "sdcard_connected": False,
            "device":           "",
            "phase":            "idle"
        })
        socketio.emit("sdcard_status", {"connected": False})
        socketio.emit("play_sound", {"sound": "eject"})
        log("✅ SD Card aman dilepas")
        return True, "SD Card aman dilepas"
    except Exception as e:
        log(f"⚠ Gagal eject: {e}")
        return False, str(e)

# ── Import pipeline ───────────────────────────────────────────
def run_import_pipeline():
    global state
    mount_pt = state.get("mount_point", "/mnt/sdcard_tmp")

    if not os.path.ismount(mount_pt):
        msg = f"Mount point tidak valid: {mount_pt}"
        log(f"❌ {msg}")
        socketio.emit("import_error", {"message": msg})
        state.update({"running": False, "phase": "idle"})
        return

    # ── Phase 1: CUT dengan progress realtime ─────────────────
    state.update({
        "running":   True,
        "phase":     "cutting",
        "percent":   0,
        "cut_done":  0,
        "cut_total": 0,
        "speed":     "",
        "error":     ""
    })
    socketio.emit("phase_change", {"phase": "cutting"})
    emit_state()

    folder_name = time.strftime("%Y-%m-%d_%H:%M:%S")
    dest_dir    = os.path.join(config.DEST_BASE, folder_name)
    os.makedirs(dest_dir, exist_ok=True)
    state.update({"folder": folder_name, "syncing_folder": folder_name})
    log(f"📂 Folder tujuan: {dest_dir}")

    extensions = [
        "*.jpg","*.jpeg","*.png","*.mp4","*.mov",
        "*.avi","*.mkv","*.cr2","*.raw","*.dng","*.heic","*.arw"
    ]

    # Hitung total file dulu untuk progress bar
    find_expr = " -o ".join(f'-iname "{e}"' for e in extensions)
    count_result = subprocess.run(
        f'find "{mount_pt}" -type f \\( {find_expr} \\) | wc -l',
        shell=True, capture_output=True, text=True
    )
    cut_total = int(count_result.stdout.strip() or "0")
    state["cut_total"] = cut_total
    log(f"✂️  Total file akan dipindahkan: {cut_total}")
    emit_state()

    # Cut file satu per satu agar bisa track progress
    find_list = subprocess.run(
        f'find "{mount_pt}" -type f \\( {find_expr} \\)',
        shell=True, capture_output=True, text=True
    )
    files_to_move = [l.strip() for l in find_list.stdout.splitlines() if l.strip()]

    # Pilih mode transfer berdasarkan settings (cut / copy)
    settings   = load_settings()
    mode       = settings.get("transfer_mode", "cut")
    log(f"🔀 Mode transfer: {mode.upper()}")

    cut_done = 0
    for fpath in files_to_move:
        fname = os.path.basename(fpath)
        dest_path = os.path.join(dest_dir, fname)
        try:
            if mode == "copy":
                shutil.copy2(fpath, dest_path)
            else:
                try:
                    os.rename(fpath, dest_path)
                except OSError:
                    # Cross-device: fallback ke mv
                    subprocess.run(["mv", fpath, dest_dir + "/"], capture_output=True)
        except Exception as e:
            log(f"⚠ Gagal transfer {fname}: {e}")
        cut_done += 1
        state["cut_done"]  = cut_done
        state["percent"]   = int(cut_done * 100 / cut_total) if cut_total else 0
        # Emit tiap 1 file atau tiap 5% agar tidak flood
        if cut_done == 1 or cut_done % max(1, cut_total // 20) == 0 or cut_done == cut_total:
            socketio.emit("cut_progress", {
                "done":    cut_done,
                "total":   cut_total,
                "percent": state["percent"],
                "current": fname
            })

    file_count = len([f for f in os.listdir(dest_dir)
                      if os.path.isfile(os.path.join(dest_dir, f))])
    state["file_count"] = file_count
    verb = "disalin" if mode == "copy" else "dipindahkan"
    log(f"✅ {file_count} file berhasil {verb}")
    socketio.emit("cut_done", {"count": file_count, "folder": folder_name, "mode": mode})
    emit_state()

    # ── Phase 2: EJECT ────────────────────────────────────────
    state["phase"] = "ejecting"
    socketio.emit("phase_change", {"phase": "ejecting"})
    emit_state()
    log("⏏ Eject SD Card otomatis...")
    do_eject()

    # ── Phase 3: SYNC ─────────────────────────────────────────
    state.update({
        "phase":       "syncing",
        "percent":     0,
        "speed":       "",
        "transferred": "",
        "total_size":  "",
        "eta":         ""
    })
    socketio.emit("phase_change", {"phase": "syncing"})
    # Update folder list: tampilkan "syncing"
    socketio.emit("folder_list", {"folders": get_folder_list()})
    emit_state()

    src = dest_dir
    dst = f"{config.GDRIVE_REMOTE}/{folder_name}"
    log(f"☁ Sync ke Google Drive: {dst}")

    cmd = [
        "rclone", "copy", src, dst,
        "--stats=1s",
        "--stats-one-line",
        "--transfers=4",
        "--drive-chunk-size=64M",
        "--log-level=INFO"
    ]

    pat_progress = re.compile(
        r"(?:.*?\s)?([\d.]+\s*\S+)\s*/\s*([\d.]+\s*\S+),\s*(\d+)%,\s*([\d.]+\s*\S+/s),\s*ETA\s*(\S+)"
    )
    # Pattern fallback: "Transferred: X files, Y, 100%"
    pat_done = re.compile(r'Transferred:\s+\d+\s*/\s*\d+,\s*100%')

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    last_pct = 0
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        m = pat_progress.search(line)
        if m:
            pct = int(m.group(3))
            # Pastikan persen tidak turun (rclone bisa emit ulang)
            if pct >= last_pct:
                last_pct = pct
                state.update({
                    "transferred": m.group(1),
                    "total_size":  m.group(2),
                    "percent":     pct,
                    "speed":       m.group(4),
                    "eta":         m.group(5)
                })
                socketio.emit("sync_update", {
                    "percent":     pct,
                    "speed":       state["speed"],
                    "transferred": state["transferred"],
                    "total_size":  state["total_size"],
                    "eta":         state["eta"]
                })
        else:
            clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
            if clean:
                socketio.emit("log_line", {"text": clean})

    process.wait()

    # Pastikan 100% saat selesai
    state.update({
        "percent":   100,
        "speed":     "",
        "eta":       "Selesai",
        "running":   False,
        "phase":     "done",
        "last_sync": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    mark_as_synced(folder_name)

    # Ambil GDrive URL di background
    threading.Thread(
        target=lambda: get_gdrive_url(folder_name), daemon=True
    ).start()

    log(f"🎉 Selesai! {folder_name}")
    socketio.emit("sync_update", {"percent": 100, "speed": "", "eta": "Selesai",
                                  "transferred": state["transferred"],
                                  "total_size":  state["total_size"]})
    socketio.emit("all_done", {
        "folder": folder_name,
        "time":   state["last_sync"],
        "count":  file_count
    })
    socketio.emit("play_sound", {"sound": "success"})

    # ── Notifikasi (Telegram / Webhook) ───────────────────────────
    threading.Thread(
        target=notify_upload_done,
        args=(folder_name, file_count),
        daemon=True
    ).start()

    # Refresh folder list setelah selesai
    time.sleep(1)
    socketio.emit("folder_list", {"folders": get_folder_list()})
    emit_state()

    # Auto reset state ke idle setelah 5 detik
    time.sleep(5)
    state.update({
        "phase":       "idle",
        "folder":      "",
        "percent":     0,
        "speed":       "",
        "transferred": "",
        "total_size":  "",
        "eta":         "",
        "cut_done":    0,
        "cut_total":   0,
        "syncing_folder": ""
    })
    socketio.emit("state_reset", {})
    emit_state()

# ── Sync only ─────────────────────────────────────────────────
def sync_only(folder_name):
    global state
    src = os.path.join(config.DEST_BASE, folder_name)
    dst = f"{config.GDRIVE_REMOTE}/{folder_name}"
    state.update({
        "running": True, "phase": "syncing",
        "folder": folder_name, "syncing_folder": folder_name,
        "percent": 0, "speed": "", "transferred": "",
        "total_size": "", "eta": ""
    })
    socketio.emit("phase_change", {"phase": "syncing"})
    socketio.emit("folder_list", {"folders": get_folder_list()})
    emit_state()
    log(f"☁ Manual sync: {folder_name}")

    cmd = ["rclone", "copy", src, dst,
           "--stats=1s", "--stats-one-line",
           "--transfers=4", "--drive-chunk-size=64M",
           "--log-level=INFO"]
    pat = re.compile(
        r"(?:.*?\s)?([\d.]+\s*\S+)\s*/\s*([\d.]+\s*\S+),\s*(\d+)%,\s*([\d.]+\s*\S+/s),\s*ETA\s*(\S+)"
    )
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    last_pct = 0
    for line in process.stdout:
        line = line.strip()
        if not line: continue
        m = pat.search(line)
        if m:
            pct = int(m.group(3))
            if pct >= last_pct:
                last_pct = pct
                state.update({
                    "transferred": m.group(1), "total_size": m.group(2),
                    "percent": pct, "speed": m.group(4), "eta": m.group(5)
                })
                socketio.emit("sync_update", state)
        else:
            clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
            if clean: socketio.emit("log_line", {"text": clean})
    process.wait()
    state.update({
        "running": False, "phase": "done", "percent": 100,
        "speed": "", "last_sync": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    mark_as_synced(folder_name)
    threading.Thread(target=lambda: get_gdrive_url(folder_name), daemon=True).start()
    socketio.emit("sync_update", {"percent": 100, "speed": "", "eta": "Selesai",
                                  "transferred": state["transferred"],
                                  "total_size":  state["total_size"]})
    src_count = len([f for f in os.listdir(src)
                     if os.path.isfile(os.path.join(src, f))]) if os.path.exists(src) else 0
    socketio.emit("all_done", {"folder": folder_name,
                               "time": state["last_sync"], "count": src_count})
    socketio.emit("play_sound", {"sound": "success"})

    # Notifikasi juga saat manual sync selesai
    threading.Thread(
        target=notify_upload_done,
        args=(folder_name, src_count),
        daemon=True
    ).start()
    time.sleep(1)
    socketio.emit("folder_list", {"folders": get_folder_list()})
    emit_state()

# ── Notifier: Telegram + Webhook ────────────────────────────────
def _resolve_placeholders(obj, ctx):
    """Ganti placeholder {folder}, {file_count}, {gdrive_url}, {time}, {event}
    secara rekursif (untuk dict/list/str)."""
    if isinstance(obj, str):
        try:
            return obj.format(**ctx)
        except Exception:
            return obj
    if isinstance(obj, list):
        return [_resolve_placeholders(x, ctx) for x in obj]
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, ctx) for k, v in obj.items()}
    return obj

def send_telegram(cfg, ctx, is_test=False):
    if not cfg:
        return False, "Konfigurasi kosong"
    if not cfg.get("enabled") and not is_test:
        return False, "Telegram tidak aktif"
    token   = (cfg or {}).get("bot_token", "").strip()
    chat_id = str((cfg or {}).get("chat_id", "")).strip()
    if not token or not chat_id:
        return False, "Token / Chat ID belum di-set"

    folder     = ctx.get("folder", "-")
    file_count = ctx.get("file_count", 0)
    gdrive_url = ctx.get("gdrive_url", "") or "(belum tersedia)"
    waktu      = ctx.get("time", "")
    head       = "🧪 <b>TEST notifikasi MediaSync</b>" if is_test else "🎉 <b>Upload Google Drive selesai</b>"
    text = (
        f"{head}\n\n"
        f"📁 Folder: <code>{folder}</code>\n"
        f"🖼 Jumlah file: <b>{file_count}</b>\n"
        f"🔗 Link: {gdrive_url}\n"
        f"⏰ Waktu: {waktu}"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }).encode("utf-8")
    try:
        req = urlrequest.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            if resp.status == 200:
                return True, "Telegram terkirim"
            return False, f"Telegram HTTP {resp.status}: {body[:200]}"
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        return False, f"Telegram HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return False, f"Telegram error: {e}"

def send_webhook(cfg, ctx, is_test=False):
    if not cfg or (not cfg.get("enabled") and not is_test):
        return False, "Webhook tidak aktif"
    url = (cfg or {}).get("url", "").strip()
    if not url:
        return False, "URL webhook belum di-set"
    method  = (cfg.get("method") or "POST").upper()
    headers = cfg.get("headers") or {}
    body    = cfg.get("body") or {}

    # Resolve placeholder pada url, headers, body
    url     = _resolve_placeholders(url,     ctx)
    headers = _resolve_placeholders(headers, ctx) if isinstance(headers, dict) else {}
    body    = _resolve_placeholders(body,    ctx)

    # Default body: kirim ctx penuh jika user tidak set body
    if not body:
        body = ctx

    data = None
    h    = {str(k): str(v) for k, v in headers.items()}
    if method in ("POST", "PUT", "PATCH"):
        data = json.dumps(body).encode("utf-8")
        h.setdefault("Content-Type", "application/json")

    try:
        req = urlrequest.Request(url, data=data, headers=h, method=method)
        with urlrequest.urlopen(req, timeout=15) as resp:
            txt = resp.read().decode("utf-8", errors="ignore")
            if 200 <= resp.status < 300:
                return True, f"Webhook OK ({resp.status})"
            return False, f"Webhook HTTP {resp.status}: {txt[:200]}"
    except urlerror.HTTPError as e:
        txt = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        return False, f"Webhook HTTP {e.code}: {txt[:200]}"
    except Exception as e:
        return False, f"Webhook error: {e}"

def notify_upload_done(folder_name, file_count):
    """Kirim notifikasi setelah upload Google Drive selesai. Ambil GDrive URL dulu."""
    settings = load_settings()
    gdrive_url = ""
    try:
        urls = load_gdrive_urls()
        gdrive_url = urls.get(folder_name) or get_gdrive_url(folder_name) or ""
    except Exception as e:
        log(f"⚠ Gagal ambil GDrive URL untuk notif: {e}")

    ctx = {
        "folder":     folder_name,
        "file_count": file_count,
        "gdrive_url": gdrive_url,
        "time":       time.strftime("%Y-%m-%d %H:%M:%S"),
        "event":      "upload_done"
    }

    tg = settings.get("telegram", {})
    if tg.get("enabled"):
        ok, msg = send_telegram(tg, ctx)
        log(("📩 Telegram: " if ok else "⚠ Telegram: ") + msg)

    wh = settings.get("webhook", {})
    if wh.get("enabled"):
        ok, msg = send_webhook(wh, ctx)
        log(("📡 Webhook: " if ok else "⚠ Webhook: ") + msg)

def emit_state():
    socketio.emit("state_update", state)

def log(msg):
    ts   = time.strftime("%H:%M:%S")
    full = f"[{ts}] {msg}"
    socketio.emit("log_line", {"text": full})
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write(full + "\n")
    except: pass

@socketio.on("connect")
def on_connect():
    emit("state_update", state)
    emit("folder_list", {"folders": get_folder_list()})

if __name__ == "__main__":
    os.makedirs(config.DEST_BASE, exist_ok=True)
    if not os.path.exists(config.LOG_FILE):
        open(config.LOG_FILE, "w").close()
    socketio.run(app, host="0.0.0.0", port=config.PORT,
                 debug=False, allow_unsafe_werkzeug=True)
