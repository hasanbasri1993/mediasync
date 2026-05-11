# 📷 MediaSync

> Sistem otomatis import foto & video dari SD Card ke local disk dan sync ke Google Drive.
> Dilengkapi dashboard realtime, PIN security, QR code Google Drive link.

---

## ✨ Fitur

- 💾 **Auto detect SD Card** via udev rule
- 🔐 **PIN Protection** — muncul saat SD Card dicolok, bukan saat buka dashboard
- ✂️ **Cut / Copy mode** — pilih mode transfer di Pengaturan (cut = pindahkan, copy = salin & file asli tetap di SD Card)
- ⏏ **Auto Eject** — SD Card otomatis di-eject setelah transfer selesai
- ☁️ **Auto Sync** — upload ke Google Drive via rclone
- 📊 **Realtime Progress** — cut progress + upload speed + ETA
- 📲 **QR Code** — scan QR untuk buka folder Google Drive langsung di HP
- 🔊 **Notif Suara** — bunyi saat SD Card masuk dan selesai upload
- ⚙️ **Halaman Pengaturan (PIN-gated)** — ganti PIN, pilih mode cut/copy, konfigurasi notifikasi
- 📩 **Notifikasi Telegram** — kirim pesan otomatis saat upload Google Drive selesai (lengkap dengan link folder)
- 📡 **Webhook HTTP Custom** — POST/GET/PUT/PATCH ke URL apapun dengan header & body JSON kustom (mendukung placeholder `{folder}`, `{file_count}`, `{gdrive_url}`, `{time}`, `{event}`)

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

## ⚙️ Pengaturan (Settings)

Klik tombol **⚙ Pengaturan** di topbar — masukkan PIN — buka 4 tab:

### 1. Umum
- **Mode Transfer**: `CUT` (default, file dipindahkan dari SD Card) atau `COPY` (file disalin, file asli tetap di SD Card)

### 2. PIN
- Ganti PIN 4 digit (perlu PIN lama untuk verifikasi)
- PIN disimpan di `settings.json` (tidak ikut di-commit ke git)

### 3. Telegram
- Aktifkan/non-aktifkan notifikasi
- Isi **Bot Token** dari [@BotFather](https://t.me/BotFather)
- Isi **Chat ID** (gunakan [@userinfobot](https://t.me/userinfobot) atau [@getidsbot](https://t.me/getidsbot))
- Tombol **Test** untuk uji kirim langsung

### 4. Webhook HTTP
Kirim notifikasi ke URL kustom saat upload selesai:
- **URL**, **Method** (POST/GET/PUT/PATCH)
- **Headers (JSON)**: misal `{"Authorization":"Bearer xyz"}`
- **Body (JSON)**: bebas, contoh:
  ```json
  {
    "folder":"{folder}",
    "files":{file_count},
    "link":"{gdrive_url}",
    "time":"{time}"
  }
  ```
- Placeholder didukung: `{folder}`, `{file_count}`, `{gdrive_url}`, `{time}`, `{event}`
- Jika body kosong, otomatis kirim payload default berisi semua field di atas

Semua pengaturan tersimpan di `settings.json` di folder project (di-gitignore otomatis).

---

## 📦 Install di PC Baru

Lihat [INSTALL.md](INSTALL.md) untuk panduan lengkap.

