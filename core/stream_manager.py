"""
ibvap/core/stream_manager.py
Multi-Camera Pool for IBVAP.

Manages N concurrent RTSP / webcam / video-file streams.
Each camera runs in its own daemon thread with its own Detector instance.
"""

import cv2
import threading
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from config import MAX_CAMERAS, STREAM_FPS_LIMIT
except ImportError:
    MAX_CAMERAS      = 16
    STREAM_FPS_LIMIT = 30

from core.detector import Detector


class CameraStream:
    """A single camera stream with its own detector."""

    def __init__(self, cam_id: str, source, name: str = '', location: str = ''):
        self.cam_id   = cam_id
        self.source   = source
        self.name     = name or cam_id
        self.location = location

        self.cap           = None
        self.running       = False
        self.thread        = None
        self.current_frame = None
        self.lock          = threading.Lock()
        self.fps           = 0
        self.error         = ''
        self.detector      = Detector(cam_id=cam_id)

        self._frame_interval = 1.0 / STREAM_FPS_LIMIT

    def start(self):
        if self.running:
            return

        # Open capture
        if isinstance(self.source, int) or (
                isinstance(self.source, str) and self.source.isdigit()):
            src = int(self.source)
            self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(src)
        else:
            self.cap = cv2.VideoCapture(self.source)

        if not self.cap.isOpened():
            self.error   = f"Cannot open source: {self.source}"
            self.running = False
            print(f"[{self.cam_id}] ERROR: {self.error}")
            return

        self.error   = ''
        self.running = True
        
        # Dedicated reader thread to eliminate webcam buffer lag
        self.latest_raw_frame = None
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

        self.thread  = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()
        print(f"[{self.cam_id}] Stream started ➡ {self.source}")

    def stop(self):
        self.running = False
        if hasattr(self, 'reader_thread') and self.reader_thread:
            self.reader_thread.join(timeout=3)
        if self.thread:
            self.thread.join(timeout=3)
        if self.cap:
            self.cap.release()
            self.cap = None
        print(f"[{self.cam_id}] Stream stopped.")

    def _reader_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self.latest_raw_frame = frame
            else:
                time.sleep(0.01)

    def _process_loop(self):
        prev_time = time.time()
        while self.running:
            raw_frame = self.latest_raw_frame
            if raw_frame is None:
                time.sleep(0.01)
                continue
            
            frame = raw_frame.copy()

            # AI processing
            processed = self.detector.process_frame(frame)

            # HUD overlay
            h, w = processed.shape[:2]
            # Corner brackets
            for pts in [((0,20),(20,0)), ((w,20),(w-20,0)),
                        ((0,h-20),(20,h)), ((w,h-20),(w-20,h))]:
                cv2.line(processed, pts[0], pts[1], (0, 255, 0), 2)

            # Timestamp + cam label
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(processed, f"REC // {ts}  [{self.cam_id}]",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 0), 1)

            # FPS calculation
            now     = time.time()
            elapsed = now - prev_time
            if elapsed > 0:
                self.fps = round(1.0 / elapsed, 1)
            prev_time = now

            # Encode JPEG
            ret2, buf = cv2.imencode('.jpg', processed,
                                     [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret2:
                with self.lock:
                    self.current_frame = buf.tobytes()

            # FPS limiter
            sleep_t = self._frame_interval - (time.time() - now)
            if sleep_t > 0:
                time.sleep(sleep_t)

    def get_frame_bytes(self) -> bytes | None:
        with self.lock:
            return self.current_frame

    def get_status(self) -> dict:
        return {
            "cam_id":   self.cam_id,
            "name":     self.name,
            "source":   str(self.source),
            "location": self.location,
            "running":  self.running,
            "fps":      self.fps,
            "error":    self.error,
        }


class CameraPool:
    """
    Manages multiple CameraStream instances.
    Thread-safe add / remove / query.
    """

    def __init__(self):
        self._cameras: dict[str, CameraStream] = {}
        self._lock = threading.Lock()

    # ── Single-camera compat alias (used by old API endpoints) ────────────
    @property
    def running(self) -> bool:
        with self._lock:
            return any(c.running for c in self._cameras.values())

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add_camera(self, cam_id: str, source,
                   name: str = '', location: str = '') -> bool:
        with self._lock:
            if len(self._cameras) >= MAX_CAMERAS:
                print(f"[Pool] Max cameras ({MAX_CAMERAS}) reached.")
                return False
            if cam_id in self._cameras:
                # Re-use / restart existing entry
                self._cameras[cam_id].stop()

            cam = CameraStream(cam_id, source, name, location)
            self._cameras[cam_id] = cam
            cam.start()
            return True

    def remove_camera(self, cam_id: str):
        with self._lock:
            if cam_id in self._cameras:
                self._cameras[cam_id].stop()
                del self._cameras[cam_id]

    def list_cameras(self) -> list[dict]:
        with self._lock:
            return [c.get_status() for c in self._cameras.values()]

    def get_frame(self, cam_id: str) -> bytes | None:
        with self._lock:
            cam = self._cameras.get(cam_id)
        return cam.get_frame_bytes() if cam else None

    def get_detector(self, cam_id: str) -> Detector | None:
        with self._lock:
            cam = self._cameras.get(cam_id)
        return cam.detector if cam else None

    def get_events(self, cam_id: str = None) -> list:
        """Drain events from one or all detectors."""
        with self._lock:
            cams = ([self._cameras[cam_id]]
                    if cam_id and cam_id in self._cameras
                    else list(self._cameras.values()))
        events = []
        for cam in cams:
            events.extend(cam.detector.get_events())
        return events

    def stop_all(self):
        with self._lock:
            for cam in self._cameras.values():
                cam.stop()
            self._cameras.clear()

    # ── Backward-compat shims (legacy single-cam API) ─────────────────────

    def start(self, source=0):
        """Legacy single-camera start (defaults to CAM-01)."""
        self.add_camera('CAM-01', source, name='Camera 1')

    def stop(self):
        """Legacy stop all."""
        self.stop_all()

    def get_frame_bytes(self) -> bytes | None:
        """Legacy single-cam frame getter (returns CAM-01 frame)."""
        return self.get_frame('CAM-01')


# Singleton
stream = CameraPool()
