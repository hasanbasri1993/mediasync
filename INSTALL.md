# 📦 Panduan Instalasi MediaSync — PC Baru

## Prerequisites

Pastikan sudah punya:
- [ ] Debian/Ubuntu/Raspberry Pi OS fresh install
- [ ] Akses internet
- [ ] Akun Google Drive
- [ ] rclone sudah di-authorize ke Google Drive

---

## Step 1 — Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/mediasync.git
cd mediasync
```

---

## Step 2 — Jalankan Installer Otomatis

```bash
sudo bash install.sh
```

Script ini akan otomatis:
1. Install semua dependency (Python, Flask, rclone, sox, dll)
2. Buat user `mediasync`
3. Copy file ke `/opt/mediasync`
4. Buat folder media `/media/mediafiles`
5. Generate file suara notifikasi
6. Setup udev rule untuk SD Card detection
7. Register & start systemd service

---

## Step 3 — Setup rclone Google Drive

Jika rclone belum di-authorize:

```bash
# Jalankan sebagai user biasa (bukan root)
rclone config

# Pilih:
# n) New remote
# name: gdrive
# Storage: Google Drive (pilih nomor yang sesuai)
# client_id: (kosongkan, tekan Enter)
# client_secret: (kosongkan, tekan Enter)
# scope: 1 (full access)
# Ikuti URL untuk authorize di browser
```

Test koneksi:
```bash
rclone ls gdrive:
```

---

## Step 4 — Edit Konfigurasi

```bash
sudo nano /opt/mediasync/config.py
```

Sesuaikan:
```python
PORT          = 8080              # Port dashboard
PIN_CODE      = "1234"            # PIN 4 digit (WAJIB GANTI)
DEST_BASE     = "/media/mediafiles"  # Folder lokal
GDRIVE_REMOTE = "gdrive:MediaTeam"   # Nama remote:folder GDrive
SECRET_KEY    = "random-string-panjang-isi-bebas"
```

Restart setelah edit:
```bash
sudo systemctl restart mediasync
```

---

## Step 5 — Setup udev Rule (SD Card auto detect)

Sudah otomatis dibuat oleh `install.sh`. Cek:
```bash
cat /etc/udev/rules.d/85-sdcard-import.rules
```

Jika perlu reload:
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## Step 6 — Verifikasi

```bash
# Cek service jalan
sudo systemctl status mediasync

# Buka dashboard
# http://IP_ADDRESS:8080

# Test simulasi SD Card
sudo bash simulate-sdcard.sh
```

---

## 🔑 Default Credentials

| Setting | Default |
|---|---|
| Port | 8080 |
| PIN | 1234 |
| Folder lokal | /media/mediafiles |
| Mount point | /mnt/sdcard_tmp |
| Log | /var/log/sdcard-import.log |

**⚠️ Wajib ganti PIN_CODE di config.py sebelum dipakai!**

---

## 🐛 Troubleshooting

### Service tidak mau start
```bash
journalctl -u mediasync -n 50 --no-pager
```

### SD Card tidak terdeteksi
```bash
# Cek udev log
sudo udevadm monitor --environment --udev

# Test manual trigger
sudo bash /usr/local/bin/sdcard-detect.sh /dev/sdb1
```

### Permission denied saat cut/eject
```bash
sudo chown mediasync:mediasync /media/mediafiles
cat /etc/sudoers.d/mediasync-mount
```

### rclone gagal upload
```bash
# Test koneksi GDrive
rclone ls gdrive:MediaTeam

# Lihat log detail
tail -f /var/log/sdcard-import.log
```

### Port sudah dipakai
```bash
sudo lsof -i :8080
# Edit PORT di config.py lalu restart
```

