# IBVAP — Intelligent Border Video Analytics Platform
## Deployment & User Guide

---

## 📋 Overview

IBVAP transforms standard IP-CCTV cameras into a software-definegid AI surveillance network.  
No dedicated smart-camera hardware required — runs entirely on a standard Windows PC with Python.

**Features:**
- 🎯 Person + Vehicle detection with persistent track IDs
- 🪪 Facial Recognition — Authorized persons (green) & Suspects/Wanted (red)
- 🔤 ANPR — Automatic Number Plate Recognition (all plate formats, super-resolution)
- 🚧 Virtual Restricted Zone with intrusion detection
- 🌙 Night-time movement detection (CLAHE enhancement)
- 🕵️ Loitering detection (behavior analytics)
- 👥 Crowd detection
- 📊 Real-time dashboard with live feeds, alerts log, analytics, and FRS management
- 📷 Multi-camera support (RTSP, webcam, video file)

---

## ⚙️ Requirements

| Component | Version |
|---|---|
| Python | 3.10+ |
| CUDA (optional, for GPU) | 12.1 |
| PyTorch | 2.5.1 |
| OpenCV (contrib) | 4.13+ |
| Ultralytics YOLO | 8.4+ |
| EasyOCR | 1.7+ |
| Flask | 3.x |

---

## 🚀 Setup

### 1. Install dependencies

This repo supports two deployment modes:

- Full ML backend (heavy dependencies): install `requirements-full.txt` on a server/VM that runs the models (recommended: GPU-equipped or sufficiently provisioned CPU host).
- Lightweight frontend + proxy (for Vercel): install `requirements.txt` which contains only minimal packages used by the proxy and static site.

To install full backend dependencies on your ML server:

```bash
pip install -r requirements-full.txt
```

To deploy the lightweight proxy (Vercel) which serves the SPA and forwards API calls to the ML backend:

```bash
pip install -r requirements.txt
```

Configure the proxy by setting the environment variable `ML_BACKEND_URL` to the public URL of your ML backend (see below).

### 2. Model files

Ensure these files are present:

```
project_root/
├── yolo26s.pt                          ← YOLO detection model
├── ESPCN_x4.pb                         ← Super-resolution model (for ANPR)
└── ibvap/
    └── models/
        ├── face_detection_yunet_2023mar.onnx
        └── face_recognition_sface_2021dec.onnx
```

### 3. Configure (optional)

Edit `ibvap/config.yaml` to adjust thresholds:

```yaml
frs_threshold: 0.38          # Similarity required to recognize authorized person
frs_suspect_threshold: 0.42  # Similarity required to flag as suspect
loitering_timeout_sec: 30    # Seconds before loitering alert fires
crowd_threshold: 5           # Person count to trigger crowd alert
night_luminance_threshold: 60 # Darkness level to activate night mode
```

---

## ▶️ Running the Server

```bash
cd ibvap
python app.py
```

Then open your browser at: **http://localhost:5000**

---

## 📸 Adding Cameras

### Via Dashboard (Live Feed Tab)
1. Enter `CAM ID`, `Source`, `Name`, `Location`
2. Click **ADD CAMERA**

### Source formats
| Type | Example |
|---|---|
| Webcam index | `0` or `1` |
| RTSP stream | `rtsp://admin:password@192.168.1.100:554/stream` |
| Video file | `C:/footage/clip.mp4` |

---

## 🛡️ FRS Registration

1. Go to **FRS tab**
2. Select role: **AUTHORIZED** (green) or **SUSPECT** (red)
3. Enter the person's name or ID
4. **Upload Photo** — select an image file
5. OR **Webcam** — start camera → capture → register
6. Click **REGISTER PROFILE**

Registered suspects will trigger a purple alarm banner and audio alert when detected.

---

## 🚧 Virtual Restricted Zone

1. Go to **Live Feed tab**
2. Select the camera in the zone dropdown
3. Click **Draw Zone**
4. Drag a rectangle over the video feed
5. Any unknown person entering this zone triggers an **INTRUSION** alert

---

## 📤 Batch Video Upload

1. Go to **Upload tab**
2. Drag & drop or select a video file
3. Click **START ANALYSIS**
4. Watch real-time AI events stream in as the video is processed

---

## 📊 Analytics

The **Analytics tab** provides:
- Alert type breakdown (donut chart)
- Authorized vs Suspect FRS split (pie chart)
- Plates read per hour (bar chart)
- 24-hour activity timeline (line chart)
- Full searchable plate number log

---

## 🗂️ Project Structure

```
ibvap/
├── app.py                  Flask API server
├── config.py               Config loader
├── config.yaml             All thresholds and settings
├── ibvap.db                SQLite database
├── core/
│   ├── database.py         DB layer (alerts, profiles, cameras, plates)
│   ├── detector.py         Main AI engine (YOLO + FRS + ANPR + behavior)
│   ├── stream_manager.py   Multi-camera thread pool
│   ├── ocr_engine.py       ANPR / OCR with super-resolution
│   ├── tracker.py          SORT multi-object tracker
│   ├── night_vision.py     Night enhancement (CLAHE + gamma)
│   └── behavior.py         Loitering + crowd detection
├── models/
│   ├── face_detection_yunet_2023mar.onnx
│   └── face_recognition_sface_2021dec.onnx
├── static/
│   ├── index.html          Dashboard (6-tab SPA)
│   ├── css/style.css       Tactical dark theme
│   └── js/app.js           Frontend logic
└── captures/
    ├── intruders/          Intrusion snapshots
    ├── suspects/           Suspect detection snapshots
    ├── plates/             Plate crop images
    └── suspicious/         Loitering snapshots
```

---

## 🔌 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/cameras` | GET | List all cameras |
| `/api/cameras` | POST | Add camera |
| `/api/cameras/<id>` | DELETE | Remove camera |
| `/video_feed/<cam_id>` | GET | Live MJPEG stream |
| `/events` | GET | SSE all events |
| `/api/alerts` | GET | Alert log (filterable) |
| `/api/alerts/export` | GET | CSV download |
| `/api/plates` | GET | Plate log |
| `/api/plates/search?q=` | GET | Search plates |
| `/api/analytics/summary` | GET | Stats summary |
| `/api/analytics/timeline` | GET | Hourly chart data |
| `/api/frs/profiles` | GET | List profiles |
| `/api/frs/register` | POST | Register face |
| `/api/frs/profiles/<name>` | DELETE | Remove profile |
| `/api/zone/<cam_id>` | POST | Set restricted zone |
| `/api/upload/video` | POST | Upload video for batch |

---

## 👨‍💻 Authors
 **Satyala Venkata Pavan**  

IBVAP — Intelligent Border Video Analytics Platform  
Built for the Ministry of Home Affairs / Border Security Forces  
