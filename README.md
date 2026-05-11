# 📷 MediaSync

> Sistem otomatis import foto & video dari SD Card ke local disk dan sync ke Google Drive.
> Dilengkapi dashboard realtime, PIN security, QR code Google Drive link.

---

## ✨ Fitur

- 💾 **Auto detect SD Card** via udev rule
- 🔐 **PIN Protection** — muncul saat SD Card dicolok, bukan saat buka dashboard
- ✂️ **Auto Cut** — foto & video dipindahkan dari SD Card ke local disk
- ⏏ **Auto Eject** — SD Card otomatis di-eject setelah cut selesai
- ☁️ **Auto Sync** — upload ke Google Drive via rclone
- 📊 **Realtime Progress** — cut progress + upload speed + ETA
- 📲 **QR Code** — scan QR untuk buka folder Google Drive langsung di HP
- 🔊 **Notif Suara** — bunyi saat SD Card masuk dan selesai upload

---

## 🖥 Tech Stack

| Komponen | Teknologi |
|---|---|
| Backend | Python 3, Flask, Flask-SocketIO |
| Frontend | Vanilla JS, Socket.IO client |
| Storage sync | rclone → Google Drive |
| SD Card detect | udev rules |
| Sound | sox |
| QR Code | QRCode.js (CDN) |
| Service | systemd |

---

## 📋 Requirements

- OS: **Debian / Ubuntu / Raspberry Pi OS** (64-bit)
- Python 3.10+
- rclone (configured dengan Google Drive remote)
- sox, imagemagick, ffmpeg (untuk testing)
- Akses sudo

---

## ⚡ Quick Install (PC Baru)

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/mediasync.git
cd mediasync

# 2. Jalankan installer
sudo bash install.sh
```

Selesai! Dashboard bisa diakses di `http://IP_ADDRESS:8080`

---

## 🗂 Struktur Project
mediasync/
├── app.py # Flask backend + SocketIO
├── config.py # Konfigurasi (PORT, PIN, PATH)
├── requirements.txt # Python dependencies
├── install.sh # Installer otomatis
├── simulate-sdcard.sh # Testing: simulasi SD Card
├── templates/
│ └── index.html # Dashboard UI
├── static/
│ └── sounds/ # File suara (di-generate saat install)
└── systemd/
└── mediasync.service # Service file

text

---

## ⚙️ Konfigurasi (`config.py`)

```python
PORT        = 8080
PIN_CODE    = "1234"          # Ganti PIN di sini
DEST_BASE   = "/media/mediafiles"
GDRIVE_REMOTE = "gdrive:MediaTeam"
LOG_FILE    = "/var/log/sdcard-import.log"
SECRET_KEY  = "ganti-dengan-random-string"
```

---

## 🔧 Manual Commands

```bash
# Status service
sudo systemctl status mediasync

# Restart service
sudo systemctl restart mediasync

# Lihat log realtime
tail -f /var/log/sdcard-import.log

# Test simulasi SD Card
sudo bash simulate-sdcard.sh

# Eject manual
sudo umount /mnt/sdcard_tmp
```

---

## 📦 Install di PC Baru

Lihat [INSTALL.md](INSTALL.md) untuk panduan lengkap.

