"""
ibvap/app.py
Flask backend for IBVAP — Intelligent Border Video Analytics Platform.

All API endpoints:
  Camera management  — /api/cameras
  Live video feeds   — /video_feed/<cam_id>
  SSE event streams  — /events  &  /events/<cam_id>
  Alert log          — /api/alerts  (filterable, exportable)
  ANPR plate log     — /api/plates
  Analytics          — /api/analytics/*
  FRS management     — /api/frs/*
  Virtual zones      — /api/zone/<cam_id>
  Video upload       — /api/upload/video
"""

import os
import io
import csv
import time
import json
import threading

import cv2
import numpy as np
from flask import (Flask, Response, jsonify, request,
                   send_from_directory, stream_with_context)
from flask_cors import CORS

from core.database import (
    get_alerts, get_all_alert_types,
    get_stats, increment_stat,
    list_profiles, add_profile, delete_profile,
    add_camera as db_add_camera,
    remove_camera as db_remove_camera,
    list_cameras_db,
    get_plate_log, search_plate, log_plate,
    get_analytics_timeline, get_analytics_summary,
    log_alert,
)
from core.detector    import Detector
from core.stream_manager import stream

# ── App setup ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

os.makedirs('captures/intruders',  exist_ok=True)
os.makedirs('captures/suspects',   exist_ok=True)
os.makedirs('captures/plates',     exist_ok=True)
os.makedirs('captures/suspicious', exist_ok=True)
os.makedirs('uploads',             exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════
# Static / SPA
# ══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/captures/<path:filename>')
def serve_capture(filename):
    return send_from_directory('captures', filename)


# ══════════════════════════════════════════════════════════════════════════
# Dashboard stats
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/stats')
def api_stats():
    return jsonify(get_stats())


# ══════════════════════════════════════════════════════════════════════════
# Camera management
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/cameras', methods=['GET'])
def api_list_cameras():
    pool_status = stream.list_cameras()
    db_cams     = {c['cam_id']: c for c in list_cameras_db()}
    # Merge pool runtime status with DB metadata
    for cam in pool_status:
        meta = db_cams.get(cam['cam_id'], {})
        cam['location'] = meta.get('location', '')
    return jsonify(pool_status)


@app.route('/api/cameras', methods=['POST'])
def api_add_camera():
    data     = request.get_json(silent=True) or {}
    cam_id   = data.get('cam_id', f"CAM-{int(time.time())}")
    source   = data.get('source', 0)
    name     = data.get('name', cam_id)
    location = data.get('location', '')

    if isinstance(source, str) and source.isdigit():
        source = int(source)

    ok = stream.add_camera(cam_id, source, name, location)
    if ok:
        db_add_camera(cam_id, name, str(source), location)
        return jsonify({"success": True, "cam_id": cam_id})
    return jsonify({"success": False,
                    "error": "Max cameras reached or invalid source"}), 400


@app.route('/api/cameras/<cam_id>', methods=['DELETE'])
def api_remove_camera(cam_id):
    stream.remove_camera(cam_id)
    db_remove_camera(cam_id)
    return jsonify({"success": True})


@app.route('/api/camera/status')
def camera_status():
    """Legacy single-cam status."""
    return jsonify({"running": stream.running})


@app.route('/api/camera/start', methods=['POST'])
def camera_start():
    """Legacy single-cam start — adds as CAM-01."""
    data = request.get_json(silent=True) or {}
    src  = data.get('source', 0)
    if isinstance(src, str) and src.isdigit():
        src = int(src)
    stream.add_camera('CAM-01', src, name='Camera 1')
    db_add_camera('CAM-01', 'Camera 1', str(src))
    return jsonify({"success": True})


@app.route('/api/camera/stop', methods=['POST'])
def camera_stop():
    """Legacy stop all."""
    stream.stop_all()
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════
# Video feeds (MJPEG)
# ══════════════════════════════════════════════════════════════════════════

def _gen_frames(cam_id: str):
    while True:
        frame = stream.get_frame(cam_id)
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.03)


@app.route('/video_feed')
def video_feed_default():
    """Legacy default feed (CAM-01)."""
    if not stream.get_frame('CAM-01'):
        stream.add_camera('CAM-01', 0, name='Camera 1')
    return Response(_gen_frames('CAM-01'),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/video_feed/<cam_id>')
def video_feed(cam_id):
    return Response(_gen_frames(cam_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ══════════════════════════════════════════════════════════════════════════
# SSE Event streams
# ══════════════════════════════════════════════════════════════════════════

@app.route('/events')
def sse_events_all():
    def event_stream():
        while True:
            events = stream.get_events()
            for ev in events:
                yield f"data: {json.dumps(ev)}\n\n"
            time.sleep(0.4)
    return Response(stream_with_context(event_stream()),
                    mimetype="text/event-stream")


@app.route('/events/<cam_id>')
def sse_events_cam(cam_id):
    def event_stream():
        while True:
            events = stream.get_events(cam_id)
            for ev in events:
                yield f"data: {json.dumps(ev)}\n\n"
            time.sleep(0.4)
    return Response(stream_with_context(event_stream()),
                    mimetype="text/event-stream")


# ══════════════════════════════════════════════════════════════════════════
# Alerts log
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/alerts')
def api_alerts():
    limit      = request.args.get('limit',      100,  type=int)
    alert_type = request.args.get('alert_type', None)
    cam_id     = request.args.get('cam_id',     None)
    date_from  = request.args.get('date_from',  None)
    date_to    = request.args.get('date_to',    None)
    return jsonify(get_alerts(limit, alert_type, cam_id, date_from, date_to))


@app.route('/api/alerts/types')
def api_alert_types():
    return jsonify(get_all_alert_types())


@app.route('/api/alerts/export')
def api_alerts_export():
    """Export filtered alerts as CSV download."""
    limit      = request.args.get('limit',      5000, type=int)
    alert_type = request.args.get('alert_type', None)
    cam_id     = request.args.get('cam_id',     None)
    date_from  = request.args.get('date_from',  None)
    date_to    = request.args.get('date_to',    None)

    rows = get_alerts(limit, alert_type, cam_id, date_from, date_to)

    si = io.StringIO()
    writer = csv.DictWriter(
        si,
        fieldnames=['id', 'timestamp', 'alert_type',
                    'message', 'cam_id', 'location', 'image_path']
    )
    writer.writeheader()
    writer.writerows(rows)

    output = si.getvalue()
    return Response(
        output,
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment;filename=ibvap_alerts.csv"}
    )


# ══════════════════════════════════════════════════════════════════════════
# ANPR plate log
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/plates')
def api_plates():
    limit  = request.args.get('limit',  100, type=int)
    cam_id = request.args.get('cam_id', None)
    return jsonify(get_plate_log(limit, cam_id))


@app.route('/api/plates/search')
def api_plates_search():
    q     = request.args.get('q', '')
    limit = request.args.get('limit', 50, type=int)
    return jsonify(search_plate(q, limit))


# ══════════════════════════════════════════════════════════════════════════
# Analytics
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/analytics/summary')
def api_analytics_summary():
    return jsonify(get_analytics_summary())


@app.route('/api/analytics/timeline')
def api_analytics_timeline():
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_analytics_timeline(hours))


# ══════════════════════════════════════════════════════════════════════════
# Virtual zones
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/zone', methods=['POST'])
def set_zone_default():
    """Legacy zone set for CAM-01."""
    data = request.json
    det  = stream.get_detector('CAM-01')
    if det:
        det.set_virtual_zone(data['x'], data['y'], data['w'], data['h'])
    return jsonify({"success": True})


@app.route('/api/zone', methods=['DELETE'])
def clear_zone_default():
    det = stream.get_detector('CAM-01')
    if det:
        det.set_virtual_zone(None, None, None, None)
    return jsonify({"success": True})


@app.route('/api/zone/<cam_id>', methods=['POST'])
def set_zone(cam_id):
    data = request.json
    det  = stream.get_detector(cam_id)
    if not det:
        return jsonify({"success": False, "error": "Camera not found"}), 404
    det.set_virtual_zone(data['x'], data['y'], data['w'], data['h'])
    return jsonify({"success": True})


@app.route('/api/zone/<cam_id>', methods=['DELETE'])
def clear_zone(cam_id):
    det = stream.get_detector(cam_id)
    if det:
        det.set_virtual_zone(None, None, None, None)
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════
# FRS — Facial Recognition System
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/frs/profiles', methods=['GET'])
def api_frs_profiles():
    return jsonify(list_profiles())


@app.route('/api/frs/profiles/<name>', methods=['DELETE'])
def api_frs_delete(name):
    # Remove from all active detectors
    for cam_info in stream.list_cameras():
        det = stream.get_detector(cam_info['cam_id'])
        if det:
            det.remove_profile(name)
    # Also ensure DB deletion (in case no cameras running)
    delete_profile(name)
    return jsonify({"success": True})


@app.route('/api/frs/register', methods=['POST'])
def api_frs_register():
    name = request.form.get('name')
    role = request.form.get('role', 'authorized')   # 'authorized' | 'suspect'
    file = request.files.get('file')

    if not name or not file:
        return jsonify({"success": False,
                        "error": "Missing name or file"}), 400
    if role not in ('authorized', 'suspect'):
        role = 'authorized'

    npimg = np.frombuffer(file.read(), np.uint8)
    img   = cv2.imdecode(npimg, cv2.IMREAD_COLOR)

    # Use first available detector, or create a temporary one
    det = None
    for cam_info in stream.list_cameras():
        det = stream.get_detector(cam_info['cam_id'])
        if det:
            break
    if det is None:
        det = stream.get_detector('CAM-01')
    if det is None:
        # No cameras running — create temporary detector just for registration
        det = Detector()

    success = det.register_face(img, name, role)
    if success:
        # Propagate to all running detectors
        for cam_info in stream.list_cameras():
            d = stream.get_detector(cam_info['cam_id'])
            if d and d is not det:
                d.reload_profiles()
        return jsonify({"success": True, "role": role})
    return jsonify({"success": False, "error": "No face detected"}), 400


# ══════════════════════════════════════════════════════════════════════════
# Video upload for batch processing
# ══════════════════════════════════════════════════════════════════════════

_batch_jobs: dict[str, dict] = {}   # job_id → {status, progress, results}


@app.route('/api/upload/video', methods=['POST'])
def api_upload_video():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400

    f       = request.files['file']
    job_id  = str(int(time.time()))
    path    = os.path.join('uploads', f"{job_id}_{f.filename}")
    f.save(path)

    _batch_jobs[job_id] = {"status": "queued", "progress": 0, "results": []}

    t = threading.Thread(target=_process_batch, args=(job_id, path), daemon=True)
    t.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route('/api/upload/status/<job_id>')
def api_upload_status(job_id):
    job = _batch_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route('/api/upload/stream/<job_id>')
def api_upload_stream(job_id):
    """SSE progress stream for batch processing."""
    def gen():
        while True:
            job = _batch_jobs.get(job_id, {})
            yield f"data: {json.dumps(job)}\n\n"
            if job.get('status') in ('done', 'error'):
                break
            time.sleep(0.5)
    return Response(stream_with_context(gen()), mimetype='text/event-stream')


def _process_batch(job_id: str, video_path: str):
    """Background thread — runs detector on every frame of uploaded video."""
    job = _batch_jobs[job_id]
    job['status'] = 'processing'

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        job['status'] = 'error'
        job['error']  = 'Cannot open video file'
        return

    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    det    = Detector(cam_id=f"UPLOAD-{job_id}")
    skip   = max(1, int(cap.get(cv2.CAP_PROP_FPS) or 30) // 5)  # Process ~5 fps
    frame_no = 0
    results  = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_no += 1
        if frame_no % skip != 0:
            continue

        det.process_frame(frame)
        evs = det.get_events()
        results.extend(evs)

        job['progress'] = round((frame_no / total) * 100, 1)
        job['results']  = results[-50:]   # Keep last 50 events in memory

    cap.release()
    job['status']   = 'done'
    job['progress'] = 100
    job['results']  = results


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    try:
        from config import HOST, PORT, DEBUG
    except ImportError:
        HOST, PORT, DEBUG = '0.0.0.0', 5000, False

    print(f"\n  IBVAP Server starting on http://{HOST}:{PORT}\n")
    app.run(host=HOST, port=PORT, threaded=True, debug=DEBUG)
