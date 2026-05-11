#!/bin/bash
# ============================================================
#  MediaSync — Fresh Install Script
#  Jalankan: sudo bash install.sh
# ============================================================
set -e
GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
step() { echo -e "\n${CYAN}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓ $1${NC}"; }

[[ $EUID -ne 0 ]] && { echo "Jalankan: sudo bash install.sh"; exit 1; }

INSTALL_DIR="/opt/mediasync"
KIOSK_USER="mediasync"
MOUNT_POINT="/mnt/sdcard_tmp"

# ── Dependencies ──────────────────────────────────────────────
step "Install dependencies"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    sox libsox-fmt-mp3 alsa-utils \
    curl wget git \
    ffmpeg imagemagick \
    dosfstools util-linux
ok "Dependencies terinstall"

# ── rclone ────────────────────────────────────────────────────
step "Install rclone"
if ! command -v rclone &>/dev/null; then
    curl -fsSL https://rclone.org/install.sh | bash
    ok "rclone terinstall"
else
    ok "rclone sudah ada: $(rclone version | head -1)"
fi

# ── User ──────────────────────────────────────────────────────
step "Buat user $KIOSK_USER"
if ! id "$KIOSK_USER" &>/dev/null; then
    useradd -m -s /bin/bash -d /home/$KIOSK_USER $KIOSK_USER
    ok "User $KIOSK_USER dibuat"
else
    ok "User $KIOSK_USER sudah ada"
fi

# Tambah ke group yang dibutuhkan
usermod -aG disk,plugdev,dialout "$KIOSK_USER" 2>/dev/null || true
ok "User $KIOSK_USER ditambah ke group: disk, plugdev, dialout"

# Sudoers lengkap
cat > /etc/sudoers.d/mediasync-all << SUDOEOF
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/mount
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/umount
$KIOSK_USER ALL=(ALL) NOPASSWD: /usr/bin/umount
$KIOSK_USER ALL=(ALL) NOPASSWD: /sbin/losetup
$KIOSK_USER ALL=(ALL) NOPASSWD: /usr/sbin/losetup
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/sync
$KIOSK_USER ALL=(ALL) NOPASSWD: /usr/bin/sync
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/chown
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/chmod
$KIOSK_USER ALL=(ALL) NOPASSWD: /usr/local/bin/sdcard-detect.sh
SUDOEOF
chmod 440 /etc/sudoers.d/mediasync-all
visudo -c -f /etc/sudoers.d/mediasync-all > /dev/null \
    && ok "Sudoers mediasync dikonfigurasi" \
    || echo "ERROR: sudoers syntax error!"

# Fix permission folder
chown -R "$KIOSK_USER:$KIOSK_USER" "$INSTALL_DIR" "$MEDIA_DIR" 2>/dev/null || true
chmod -R 755 "$INSTALL_DIR" "$MEDIA_DIR" 2>/dev/null || true
touch "$LOG_FILE"
chown "$KIOSK_USER:$KIOSK_USER" "$LOG_FILE"
chmod 664 "$LOG_FILE"
ok "Permission folder difix"

# ── Copy files ────────────────────────────────────────────────
step "Copy file ke $INSTALL_DIR"
mkdir -p $INSTALL_DIR/templates $INSTALL_DIR/static/sounds
cp app.py config.py requirements.txt $INSTALL_DIR/
cp templates/index.html $INSTALL_DIR/templates/
[[ -f static/sounds/insert.wav ]]  && cp static/sounds/insert.wav  $INSTALL_DIR/static/sounds/
[[ -f static/sounds/success.wav ]] && cp static/sounds/success.wav $INSTALL_DIR/static/sounds/
ok "File di-copy"

# ── Python packages ───────────────────────────────────────────
step "Install Python packages"
pip3 install -q flask flask-socketio eventlet
ok "Python packages terinstall"

# ── Generate suara jika belum ada ────────────────────────────
step "Generate file suara notifikasi"
python3 -c "
import struct, math, wave, os
def gen(fname, freqs):
    sr=44100; amp=20000; frames=b''
    for f,dur in freqs:
        for i in range(int(sr*dur)):
            frames+=struct.pack('<h',int(amp*math.sin(2*math.pi*f*i/sr)))
    with wave.open(fname,'w') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(frames)
os.makedirs('$INSTALL_DIR/static/sounds', exist_ok=True)
gen('$INSTALL_DIR/static/sounds/insert.wav',  [(440,.1),(880,.2)])
gen('$INSTALL_DIR/static/sounds/success.wav', [(600,.15),(800,.15),(1000,.2)])
print('Suara dibuat')
"
ok "File suara dibuat"

# ── Folder media ──────────────────────────────────────────────
step "Buat folder media"
DEST_BASE=$(grep "^DEST_BASE" $INSTALL_DIR/config.py | awk -F'"' '{print $2}')
mkdir -p "$DEST_BASE" "$MOUNT_POINT"
chown -R $KIOSK_USER:$KIOSK_USER "$DEST_BASE"
ok "Folder: $DEST_BASE"

# ── Sudoers ───────────────────────────────────────────────────
step "Setup sudoers"
cat > /etc/sudoers.d/mediasync-mount << EOF
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/umount
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/mount
$KIOSK_USER ALL=(ALL) NOPASSWD: /sbin/losetup
$KIOSK_USER ALL=(ALL) NOPASSWD: /bin/sync
$KIOSK_USER ALL=(ALL) NOPASSWD: /usr/bin/sync
EOF
chmod 440 /etc/sudoers.d/mediasync-mount
ok "Sudoers dikonfigurasi"

# ── udev rule ─────────────────────────────────────────────────
step "Setup udev rule"
PORT=$(grep "^PORT" $INSTALL_DIR/config.py | awk -F'=' '{print $2}' | tr -d ' ')
cat > /usr/local/bin/sdcard-detect.sh << SHEOF
#!/bin/bash
DEVICE="\$1"
MOUNT_POINT="$MOUNT_POINT"
DASHBOARD="http://localhost:${PORT:-8080}"
LOG="/var/log/sdcard-import.log"
exec >> \$LOG 2>&1
echo "===== SD CARD \$(date) ====="
mkdir -p "\$MOUNT_POINT"
umount "\$MOUNT_POINT" 2>/dev/null || true
mount "\$DEVICE" "\$MOUNT_POINT" || exit 1
FILE_COUNT=\$(find "\$MOUNT_POINT" -type f \( \
    -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o \
    -iname "*.mp4" -o -iname "*.mov" -o -iname "*.avi" -o \
    -iname "*.mkv" -o -iname "*.cr2" -o -iname "*.raw" \
\) | wc -l)
curl -s -X POST \$DASHBOARD/api/sdcard-event \
  -H "Content-Type: application/json" \
  -d "{\"event\":\"detected\",\"device\":\"\$DEVICE\",\"mount_point\":\"\$MOUNT_POINT\",\"file_count\":\$FILE_COUNT}"
SHEOF
chmod +x /usr/local/bin/sdcard-detect.sh

cat > /etc/udev/rules.d/85-sdcard-import.rules << 'UDEVEOF'
ACTION=="add", KERNEL=="sd?1", SUBSYSTEM=="block", \
  RUN+="/bin/bash -c '/usr/local/bin/sdcard-detect.sh /dev/%k >> /tmp/sdcard-udev.log 2>&1 &'"
UDEVEOF
udevadm control --reload-rules
ok "udev rule dikonfigurasi"

# ── systemd service ───────────────────────────────────────────
step "Setup systemd service"
cp systemd/mediasync.service /etc/systemd/system/
chown -R $KIOSK_USER:$KIOSK_USER $INSTALL_DIR
touch /var/log/sdcard-import.log
chown $KIOSK_USER:$KIOSK_USER /var/log/sdcard-import.log
systemctl daemon-reload
systemctl enable mediasync
systemctl restart mediasync
sleep 3
ok "Service aktif"

IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  ✅  MEDIASYNC TERINSTALL!${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  🌐 Dashboard : ${CYAN}http://$IP:${PORT:-8080}${NC}"
echo -e "  ⚙️  Config    : ${CYAN}sudo nano $INSTALL_DIR/config.py${NC}"
echo -e "  📋 Log       : ${CYAN}tail -f /var/log/sdcard-import.log${NC}"
echo -e "  🧪 Testing   : ${CYAN}sudo bash simulate-sdcard.sh${NC}"
echo ""
echo -e "  ${YELLOW}⚠️  Jangan lupa ganti PIN_CODE di config.py!${NC}"
echo ""
