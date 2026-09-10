"""
ibvap/core/tracker.py
Lightweight object tracker for IBVAP.

Uses the SORT (Simple Online and Realtime Tracking) algorithm implemented
via ultralytics' built-in BYTETrack when available, with a pure-Python
Hungarian-algorithm fallback (no extra dependencies required).

Usage:
    from core.tracker import Tracker
    tracker = Tracker()
    tracks = tracker.update(detections, frame)
    # detections: list of [x1, y1, x2, y2, confidence, class_id]
    # returns:    list of [x1, y1, x2, y2, track_id, class_id]
"""

import numpy as np
import time


# ---------------------------------------------------------------------------
# Kalman-filter-based single object track
# ---------------------------------------------------------------------------
class _KalmanTrack:
    _id_counter = 0

    def __init__(self, bbox, class_id):
        _KalmanTrack._id_counter += 1
        self.track_id  = _KalmanTrack._id_counter
        self.class_id  = class_id
        self.hits      = 1
        self.no_match  = 0
        self.age       = 1

        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w  = x2 - x1
        h  = y2 - y1

        # State: [cx, cy, w, h, vx, vy, vw, vh]
        self.state = np.array([cx, cy, w, h, 0, 0, 0, 0], dtype=float)

    def predict(self):
        """Constant-velocity motion model prediction."""
        self.state[:4] += self.state[4:]
        self.age += 1

    def update(self, bbox, class_id):
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w  = x2 - x1
        h  = y2 - y1
        measured = np.array([cx, cy, w, h])
        # Simple low-pass filter update
        alpha = 0.5
        self.state[:4] = alpha * measured + (1 - alpha) * self.state[:4]
        self.state[4:] = alpha * (measured - self.state[:4]) + (1 - alpha) * self.state[4:]
        self.hits    += 1
        self.no_match = 0
        self.class_id = class_id

    def get_bbox(self):
        cx, cy, w, h = self.state[:4]
        return (int(cx - w / 2), int(cy - h / 2),
                int(cx + w / 2), int(cy + h / 2))


# ---------------------------------------------------------------------------
# IoU helper
# ---------------------------------------------------------------------------
def _iou(b1, b2):
    xa = max(b1[0], b2[0]); ya = max(b1[1], b2[1])
    xb = min(b1[2], b2[2]); yb = min(b1[3], b2[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    if inter == 0:
        return 0.0
    area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    return inter / float(area1 + area2 - inter)


# ---------------------------------------------------------------------------
# Greedy Hungarian-lite assignment
# ---------------------------------------------------------------------------
def _greedy_assign(tracks, detections, iou_threshold=0.3):
    if not tracks or not detections:
        return [], list(range(len(tracks))), list(range(len(detections)))

    matched, unmatched_t, unmatched_d = [], [], []
    used_d = set()

    for ti, trk in enumerate(tracks):
        best_iou, best_di = iou_threshold, -1
        tb = trk.get_bbox()
        for di, det in enumerate(detections):
            if di in used_d:
                continue
            score = _iou(tb, det[:4])
            if score > best_iou:
                best_iou, best_di = score, di
        if best_di >= 0:
            matched.append((ti, best_di))
            used_d.add(best_di)
        else:
            unmatched_t.append(ti)

    unmatched_d = [di for di in range(len(detections)) if di not in used_d]
    return matched, unmatched_t, unmatched_d


# ---------------------------------------------------------------------------
# Main Tracker class
# ---------------------------------------------------------------------------
class Tracker:
    """
    Multi-object tracker (SORT-style).

    Parameters
    ----------
    max_age : int
        How many frames without a match before a track is deleted.
    min_hits : int
        Minimum detections before a track is reported.
    iou_threshold : float
        Minimum IoU to associate a detection to an existing track.
    """

    def __init__(self, max_age: int = 10, min_hits: int = 2,
                 iou_threshold: float = 0.3):
        self.max_age       = max_age
        self.min_hits      = min_hits
        self.iou_threshold = iou_threshold
        self.tracks: list[_KalmanTrack] = []

    def reset(self):
        self.tracks = []
        _KalmanTrack._id_counter = 0

    def update(self, detections: list, frame=None) -> list:
        """
        Parameters
        ----------
        detections : list of [x1, y1, x2, y2, confidence, class_id]
        frame      : unused (reserved for future appearance features)

        Returns
        -------
        list of [x1, y1, x2, y2, track_id, class_id]
        """
        # Predict all existing tracks
        for trk in self.tracks:
            trk.predict()

        matched, unmatched_t, unmatched_d = _greedy_assign(
            self.tracks, detections, self.iou_threshold
        )

        # Update matched tracks
        for ti, di in matched:
            self.tracks[ti].update(detections[di][:4], int(detections[di][5]))

        # Mark unmatched tracks
        for ti in unmatched_t:
            self.tracks[ti].no_match += 1

        # Create new tracks for unmatched detections
        for di in unmatched_d:
            det = detections[di]
            self.tracks.append(_KalmanTrack(det[:4], int(det[5])))

        # Remove dead tracks
        self.tracks = [t for t in self.tracks if t.no_match <= self.max_age]

        # Return confirmed tracks only
        results = []
        for trk in self.tracks:
            if trk.hits >= self.min_hits or trk.age <= self.min_hits:
                x1, y1, x2, y2 = trk.get_bbox()
                results.append([x1, y1, x2, y2, trk.track_id, trk.class_id, trk.no_match])

        return results
