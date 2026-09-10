"""
ibvap/core/detector.py
Core AI detection engine for IBVAP.

Capabilities:
  • Person detection with persistent track IDs (SORT tracker)
  • Vehicle detection and classification (Car / Truck / Motorcycle / Bus)
  • Face detection + Recognition (YuNet + SFace)
  • Dual-role FRS: Authorized (green) vs Suspect (red)
  • ANPR / OCR with super-resolution
  • Virtual zone intrusion detection (rectangle)
  • Loitering detection (behavior analytics)
  • Crowd detection (behavior analytics)
  • Night-time enhancement (CLAHE + gamma)
"""

import cv2
import numpy as np
import os
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from config import (FRS_THRESHOLD, FRS_SUSPECT_THRESHOLD,
                        ALERT_COOLDOWN, YOLO_MODEL)
except ImportError:
    FRS_THRESHOLD         = 0.38
    FRS_SUSPECT_THRESHOLD = 0.42
    ALERT_COOLDOWN        = 6
    YOLO_MODEL            = '../yolo26s.pt'

from ultralytics import YOLO
from core.database    import (log_alert, increment_stat, load_profiles,
                               log_plate, add_profile, delete_profile,
                               list_profiles)
from core.ocr_engine  import ocr
from core.tracker     import Tracker
from core.night_vision import process_night, is_night
from core.behavior    import LoiteringDetector, CrowdDetector


# ── Vehicle class mapping ──────────────────────────────────────────────────
_VEHICLE_CLASSES = {
    'car': 'Car', 'truck': 'Truck', 'bus': 'Bus',
    'motorcycle': 'Motorcycle', 'motorbike': 'Motorcycle',
    'bicycle': 'Bicycle', 'auto': 'Auto-Rickshaw',
    'vehicle': 'Vehicle', 'van': 'Van',
}

_PERSON_CLASSES  = {'person', 'rider', 'pedestrian', 'human'}
_PLATE_CLASSES   = {'plate', 'number plate', 'license plate', 'numberplate'}

# ── Colours ────────────────────────────────────────────────────────────────
_CLR_AUTH    = (0,   255,  0)    # Green  — authorized person
_CLR_SUSPECT = (0,   0,   255)   # Red    — suspect / intruder
_CLR_UNKNOWN = (128, 128, 128)   # Grey   — unknown person
_CLR_VEHICLE = (255, 200,  0)    # Amber  — vehicle
_CLR_PLATE   = (255, 255,  0)    # Yellow — plate text
_CLR_ZONE    = (0,   0,   255)   # Red    — restricted zone boundary
_CLR_NIGHT   = (0,   128, 255)   # Blue   — night mode indicator


class Detector:
    def __init__(self, yolo_path: str = None, cam_id: str = 'CAM-01'):
        self.cam_id = cam_id

        # ── YOLO ──────────────────────────────────────────────────────────
        path = yolo_path or YOLO_MODEL
        try:
            self.yolo = YOLO(path)
            self.demo_mode = False
            print(f"[Detector:{cam_id}] YOLO loaded from {path}")
        except Exception:
            print(f"[Detector:{cam_id}] YOLO not found — DEMO MODE")
            self.yolo      = None
            self.demo_mode = True

        # ── FRS Models ────────────────────────────────────────────────────
        _base = os.path.join(os.path.dirname(__file__), '..', 'models')
        self.yunet = cv2.FaceDetectorYN.create(
            os.path.join(_base, "face_detection_yunet_2023mar.onnx"),
            "", (320, 320), 0.45, 0.3, 5000
        )
        self.sface = cv2.FaceRecognizerSF.create(
            os.path.join(_base, "face_recognition_sface_2021dec.onnx"), ""
        )
        self.profiles: dict = {}      # {name: (feature, role)}
        self.reload_profiles()

        # ── Tracker ───────────────────────────────────────────────────────
        self.tracker = Tracker(max_age=10, min_hits=2, iou_threshold=0.3)

        # ── Behavior ──────────────────────────────────────────────────────
        self.loitering = LoiteringDetector()
        self.crowd     = CrowdDetector()

        # ── Virtual Zone ──────────────────────────────────────────────────
        self.virtual_zone = None   # (x, y, w, h)

        # ── Plate Detector ────────────────────────────────────────────────
        plate_cascade_path = os.path.join(_base, "haarcascade_russian_plate_number.xml")
        self.plate_cascade = cv2.CascadeClassifier(plate_cascade_path)

        # ── Event Bus ─────────────────────────────────────────────────────
        self.events: list = []
        self.track_identities = {}  # tid -> (identity, score, role)

        # ── Cooldown timers ───────────────────────────────────────────────
        self._last: dict[str, float] = {}   # {event_type: last_fire_time}

    # ── Profile management ────────────────────────────────────────────────

    def reload_profiles(self):
        self.profiles = load_profiles()

    def register_face(self, frame: np.ndarray, name: str,
                      role: str = 'authorized') -> bool:
        h, w = frame.shape[:2]
        self.yunet.setInputSize((w, h))
        _, faces = self.yunet.detect(frame)
        if faces is not None and len(faces) > 0:
            feature = self._extract_feature(frame, faces[0])
            add_profile(name, feature, role)
            self.reload_profiles()
            return True
        return False

    def remove_profile(self, name: str):
        delete_profile(name)
        self.reload_profiles()

    # ── FRS helpers ───────────────────────────────────────────────────────

    def _extract_feature(self, frame: np.ndarray, face_box) -> np.ndarray:
        aligned = self.sface.alignCrop(frame, face_box)
        return self.sface.feature(aligned)

    def _identify_face(self, feature: np.ndarray) -> tuple[str | None, float, str]:
        """
        Returns (name, score, role).
        Checks suspects first (higher priority), then authorized.
        """
        best_name  = None
        best_score = -1.0
        best_role  = 'unknown'

        for name, (prof_feat, role) in self.profiles.items():
            score = self.sface.match(feature, prof_feat,
                                     cv2.FaceRecognizerSF_FR_COSINE)
            threshold = (FRS_SUSPECT_THRESHOLD if role == 'suspect'
                         else FRS_THRESHOLD)
            if score >= threshold and score > best_score:
                best_score = score
                best_name  = name
                best_role  = role

        if best_name is not None:
            return best_name, best_score, best_role
        
        return None, 0.0, 'unknown'

    # ── Zone helpers ──────────────────────────────────────────────────────

    def set_virtual_zone(self, x, y, w, h):
        if x is None:
            self.virtual_zone = None
        else:
            self.virtual_zone = (int(x), int(y), int(w), int(h))

    def _in_zone(self, x1, y1, x2, y2) -> bool:
        if not self.virtual_zone:
            return False
        zx, zy, zw, zh = self.virtual_zone
        return not (x2 < zx or x1 > zx + zw or y2 < zy or y1 > zy + zh)

    # ── Cooldown helper ───────────────────────────────────────────────────

    def _can_fire(self, event_type: str, cooldown: float = None) -> bool:
        cd  = cooldown if cooldown is not None else ALERT_COOLDOWN
        now = time.time()
        if now - self._last.get(event_type, 0) >= cd:
            self._last[event_type] = now
            return True
        return False

    # ── Snapshot helper ───────────────────────────────────────────────────

    def _save_snapshot(self, crop: np.ndarray, subfolder: str) -> str:
        folder = f"captures/{subfolder}"
        os.makedirs(folder, exist_ok=True)
        path = f"{folder}/{int(time.time())}.jpg"
        cv2.imwrite(path, crop)
        return path

    # ── Main frame processing ─────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        self.yunet.setInputSize((w, h))

        # ── Night-vision enhancement ──────────────────────────────────────
        frame, night_flag = process_night(frame)
        if night_flag:
            cv2.putText(frame, "🌙 NIGHT MODE", (w - 200, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, _CLR_NIGHT, 2)
            if self._can_fire('night_movement', cooldown=30):
                increment_stat('night_alerts')
                log_alert("NIGHT_MOVEMENT",
                          "Night-time activity detected",
                          cam_id=self.cam_id)
                self.events.append({'type': 'night',
                                    'message': 'Night movement detected'})

        # ── Demo mode (no YOLO) ───────────────────────────────────────────
        if self.demo_mode:
            cv2.putText(frame, "DEMO MODE — NO YOLO MODEL",
                        (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            self._run_frs_on_frame(frame)
            self._draw_zone(frame)
            return frame

        # ── YOLO inference ────────────────────────────────────────────────
        results = self.yolo(frame, verbose=False)

        raw_persons  = []   # [(x1,y1,x2,y2, conf, cls_id)]
        raw_vehicles = []
        raw_plates   = []

        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf  = float(box.conf[0])
                cls   = int(box.cls[0])
                name  = self.yolo.names[cls].lower()

                if any(p in name for p in _PERSON_CLASSES) or cls == 0:
                    raw_persons.append([x1, y1, x2, y2, conf, cls])
                elif any(k in name for k in _VEHICLE_CLASSES):
                    raw_vehicles.append((x1, y1, x2, y2, conf, name))
                elif any(p in name for p in _PLATE_CLASSES):
                    raw_plates.append((x1, y1, x2, y2))

        # ── Detect Plates using Haar Cascade ──────────────────────────────
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # We can detect plates on the whole frame, or inside vehicle bounding boxes.
        # Doing it on the whole frame is simpler and often robust enough with size constraints.
        hc_plates = self.plate_cascade.detectMultiScale(gray_frame, scaleFactor=1.1, minNeighbors=4, minSize=(30, 10))
        for (px, py, pw, ph) in hc_plates:
            # Check for overlap with existing YOLO plates to avoid duplicates
            overlap = False
            for (yx1, yy1, yx2, yy2) in raw_plates:
                # Basic overlap check (centroid inside box)
                cx, cy = px + pw//2, py + ph//2
                if yx1 <= cx <= yx2 and yy1 <= cy <= yy2:
                    overlap = True
                    break
            if not overlap:
                # Expand box slightly for better OCR
                pad_x, pad_y = 5, 5
                x1 = max(0, px - pad_x)
                y1 = max(0, py - pad_y)
                x2 = min(w, px + pw + pad_x)
                y2 = min(h, py + ph + pad_y)
                raw_plates.append((x1, y1, x2, y2))

        # ── Track persons ─────────────────────────────────────────────────
        tracks = self.tracker.update(raw_persons, frame)
        person_bboxes = []

        active_ids = set()
        for trk in tracks:
            tx1, ty1, tx2, ty2, tid, _, no_match = trk
            active_ids.add(tid)
            
            # If track was lost for more than 2 frames, clear its identity
            # to prevent a new person from inheriting a ghost track's identity
            if no_match > 2 and tid in self.track_identities:
                del self.track_identities[tid]
                
            person_bboxes.append((tx1, ty1, tx2, ty2))
            self._process_person(frame, tx1, ty1, tx2, ty2, tid, w, h)

        self.loitering.cleanup_stale(active_ids)
        self.track_identities = {k: v for k, v in self.track_identities.items() if k in active_ids}
        increment_stat('persons_detected')

        # ── Crowd check ───────────────────────────────────────────────────
        crowd_alert, crowd_count = self.crowd.update(person_bboxes,
                                                      self.virtual_zone)
        if crowd_alert and self._can_fire('crowd', cooldown=15):
            increment_stat('crowd_alerts')
            msg = f"CROWD ALERT — {crowd_count} persons in restricted zone"
            log_alert("CROWD_ALERT", msg, cam_id=self.cam_id)
            self.events.append({'type': 'crowd', 'message': msg,
                                 'count': crowd_count})

        # ── Vehicles ──────────────────────────────────────────────────────
        for (vx1, vy1, vx2, vy2, vconf, vname) in raw_vehicles:
            vtype = _VEHICLE_CLASSES.get(vname, 'Vehicle')
            cv2.rectangle(frame, (vx1, vy1), (vx2, vy2), _CLR_VEHICLE, 2)
            cv2.putText(frame, vtype, (vx1, vy1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, _CLR_VEHICLE, 2)
            increment_stat('total_vehicles')

        # ── Plates / ANPR ─────────────────────────────────────────────────
        for (px1, py1, px2, py2) in raw_plates:
            crop = frame[max(0, py1):py2, max(0, px1):px2]
            if crop.size > 0:
                plate_txt, conf = ocr.run_ocr(crop)
                if plate_txt and len(plate_txt) >= 4:
                    increment_stat('plates_read')
                    img_path = self._save_snapshot(crop, 'plates')
                    log_plate(plate_txt, conf, self.cam_id, img_path)
                    self.events.append({'type': 'plate', 'data': plate_txt,
                                        'confidence': conf})
                    cv2.rectangle(frame, (px1, py1), (px2, py2),
                                  _CLR_PLATE, 2)
                    cv2.putText(frame, plate_txt, (px1, py1 - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                                _CLR_PLATE, 2)

        # ── Draw zone ─────────────────────────────────────────────────────
        self._draw_zone(frame)

        return frame

    # ── Person sub-processor ──────────────────────────────────────────────

    def _process_person(self, frame, x1, y1, x2, y2, tid, fw, fh):
        # 1. Look up cached identity for this track
        identity, score, role = self.track_identities.get(tid, (None, 0.0, 'unknown'))

        # Clip coordinates to frame boundaries to prevent numpy slice issues
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(fw, x2), min(fh, y2)

        # 2. Try to run FRS on the face area (top 60% of bounding box) continuously
        # This handles track ID merges and improves score over time
        cy2         = y1 + int((y2 - y1) * 0.6)
        person_crop = frame[y1:cy2, x1:x2]

        if person_crop.shape[0] > 20 and person_crop.shape[1] > 20:
            self.yunet.setInputSize((person_crop.shape[1], person_crop.shape[0]))
            _, faces = self.yunet.detect(person_crop)
            if faces is not None:
                for face in faces:
                    feature = self._extract_feature(person_crop, face)
                    new_id, new_score, new_role = self._identify_face(feature)
                    if new_id:
                        # Update if we recognized someone new, or the confidence improved
                        if identity is None or new_id != identity or new_score > score:
                            identity, score, role = new_id, new_score, new_role
                            self.track_identities[tid] = (identity, score, role)
                            break
            self.yunet.setInputSize((fw, fh))

        track_label = f"ID:{tid}"

        if identity:
            if role == 'suspect':
                # ── SUSPECT detected ──────────────────────────────────
                colour = _CLR_SUSPECT
                label  = f"☠ SUSPECT: {identity} [{score*100:.0f}%]"
                cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 3)
                cv2.putText(frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
                cv2.putText(frame, track_label, (x1, y2 + 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
                if self._can_fire(f'suspect_{identity}_{self.cam_id}', cooldown=10):
                    increment_stat('suspect_alerts')
                    img_path = self._save_snapshot(
                        frame[y1:y2, x1:x2], 'suspects'
                    )
                    log_alert("SUSPECT_DETECTED",
                              f"WANTED PERSON DETECTED: {identity}",
                              img_path, self.cam_id)
                    self.events.append({
                        'type': 'suspect', 'data': identity,
                        'confidence': score, 'cam_id': self.cam_id,
                        'img_path': img_path
                    })
            else:
                # ── AUTHORIZED ────────────────────────────────────────
                colour = _CLR_AUTH
                label  = f"✓ {identity} (AUTHORISED) [{score*100:.0f}%]"
                cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
                cv2.putText(frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
                cv2.putText(frame, track_label, (x1, y2 + 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
                
                # Silently capture authorized person for training data
                if self._can_fire(f'auth_{identity}_{self.cam_id}', cooldown=10):
                    self._save_snapshot(frame[y1:y2, x1:x2], 'authorized')
        else:
            # ── UNKNOWN person ────────────────────────────────────────
            colour = _CLR_UNKNOWN
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(frame, "UNKNOWN", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
            cv2.putText(frame, track_label, (x1, y2 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)

            # ── Zone intrusion check ──────────────────────────────────
            if self._in_zone(x1, y1, x2, y2):
                cv2.rectangle(frame, (x1, y1), (x2, y2),
                              _CLR_SUSPECT, 3)
                cv2.putText(frame, f"⚠ INTRUDER ID:{tid}",
                            (x1, y2 + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            _CLR_SUSPECT, 2)
                if self._can_fire(f'intrusion_{self.cam_id}', cooldown=10):
                    increment_stat('intrusion_alerts')
                    img_path = self._save_snapshot(
                        frame[y1:y2, x1:x2], 'intruders'
                    )
                    log_alert("INTRUSION",
                              f"UNKNOWN PERSON IN RESTRICTED ZONE (Track {tid})",
                              img_path, self.cam_id)
                    self.events.append({
                        'type': 'alarm',
                        'message': 'ENEMY DETECTED IN RESTRICTED ZONE',
                        'track_id': tid, 'cam_id': self.cam_id,
                        'img_path': img_path
                    })

            # ── Loitering check ───────────────────────────────────────
            if self.loitering.update(tid, (x1, y1, x2, y2),
                                     self.virtual_zone):
                if self._can_fire(f'loiter_{tid}', cooldown=20):
                    increment_stat('suspicious_count')
                    dwell = int(self.loitering.dwell_time(tid))
                    msg   = (f"SUSPICIOUS LOITERING — Track {tid} "
                             f"in zone for {dwell}s")
                    img_path = self._save_snapshot(
                        frame[y1:y2, x1:x2], 'suspicious'
                    )
                    log_alert("SUSPICIOUS_LOITERING", msg,
                              img_path, self.cam_id)
                    self.events.append({
                        'type': 'suspicious', 'message': msg,
                        'track_id': tid, 'cam_id': self.cam_id
                    })

    # ── FRS on whole frame (demo mode) ────────────────────────────────────

    def _run_frs_on_frame(self, frame):
        _, faces = self.yunet.detect(frame)
        if faces is not None:
            for face in faces:
                fx, fy, fw, fh = map(int, face[:4])
                feature = self._extract_feature(frame, face)
                identity, score, role = self._identify_face(feature)
                colour = _CLR_AUTH if role == 'authorized' else (
                    _CLR_SUSPECT if role == 'suspect' else _CLR_UNKNOWN
                )
                cv2.rectangle(frame, (fx, fy), (fx + fw, fy + fh), colour, 2)
                label = f"{identity} ({score:.0%})" if identity else "UNKNOWN"
                cv2.putText(frame, label, (fx, fy - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)

    # ── Zone drawing ──────────────────────────────────────────────────────

    def _draw_zone(self, frame):
        if self.virtual_zone:
            zx, zy, zw, zh = self.virtual_zone
            cv2.rectangle(frame, (zx, zy), (zx + zw, zy + zh),
                          _CLR_ZONE, 2, cv2.LINE_AA)
            cv2.putText(frame, "VIRTUAL RESTRICTED ZONE",
                        (zx + 5, zy + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, _CLR_ZONE, 2)

    # ── Event bus ─────────────────────────────────────────────────────────

    def get_events(self) -> list:
        evs = self.events.copy()
        self.events.clear()
        return evs


# Default singleton (single-camera fallback)
detector = Detector()
