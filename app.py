from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import subprocess, threading, os, time, re
import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

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
    data    = request.get_json()
    purpose = data.get("purpose", "import")   # import | view_qr
    if data.get("pin") == config.PIN_CODE:
        session[f"pin_{purpose}"] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

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

    cut_done = 0
    for fpath in files_to_move:
        fname = os.path.basename(fpath)
        try:
            os.rename(fpath, os.path.join(dest_dir, fname))
        except Exception:
            # Cross-device: fallback ke mv
            subprocess.run(["mv", fpath, dest_dir + "/"],
                           capture_output=True)
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
    log(f"✅ {file_count} file berhasil dipindahkan")
    socketio.emit("cut_done", {"count": file_count, "folder": folder_name})
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
    socketio.emit("all_done", {"folder": folder_name,
                               "time": state["last_sync"], "count": 0})
    socketio.emit("play_sound", {"sound": "success"})
    time.sleep(1)
    socketio.emit("folder_list", {"folders": get_folder_list()})
    emit_state()

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
