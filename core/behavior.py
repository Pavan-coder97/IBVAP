"""
ibvap/core/behavior.py
Behavioral analytics for IBVAP — Loitering Detection and Crowd Detection.

LoiteringDetector:
    Tracks dwell time per track_id in each virtual zone.
    Fires a SUSPICIOUS_LOITERING alert when a person stays
    in a zone longer than the configured threshold.

CrowdDetector:
    Counts persons in a zone per frame.
    Fires a CROWD_ALERT when count exceeds the configured threshold.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from config import LOITERING_TIMEOUT, CROWD_THRESHOLD
except ImportError:
    LOITERING_TIMEOUT = 30
    CROWD_THRESHOLD   = 5


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _bbox_center(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _point_in_rect(px, py, zone):
    """Check if point (px,py) is inside a (zx,zy,zw,zh) rectangle."""
    if zone is None:
        return False
    zx, zy, zw, zh = zone
    return zx <= px <= zx + zw and zy <= py <= zy + zh


def _bbox_in_zone(bbox, zone):
    """Returns True if the centre of bbox is inside the zone."""
    cx, cy = _bbox_center(bbox)
    return _point_in_rect(cx, cy, zone)


# ---------------------------------------------------------------------------
# Loitering Detector
# ---------------------------------------------------------------------------
class LoiteringDetector:
    """
    Tracks how long each track_id has spent inside a virtual zone.

    Usage
    -----
        detector = LoiteringDetector()

        # Call every frame for each detected person
        is_loitering = detector.update(track_id=42, bbox=(x1,y1,x2,y2), zone=(zx,zy,zw,zh))
    """

    def __init__(self, timeout_sec: int = None):
        self.timeout   = timeout_sec if timeout_sec is not None else LOITERING_TIMEOUT
        # {track_id: entry_timestamp}
        self._entry: dict[int, float] = {}
        # {track_id: bool} — already alerted this session?
        self._alerted: dict[int, bool] = {}

    def update(self, track_id: int, bbox: tuple, zone) -> bool:
        """
        Returns True if this track_id is loitering (exceeded timeout in zone).
        Only returns True once per loitering incident (resets when person leaves).
        """
        if zone is None:
            self._clear_track(track_id)
            return False

        in_zone = _bbox_in_zone(bbox, zone)

        if in_zone:
            if track_id not in self._entry:
                self._entry[track_id]   = time.time()
                self._alerted[track_id] = False

            dwell = time.time() - self._entry[track_id]
            if dwell >= self.timeout and not self._alerted.get(track_id, False):
                self._alerted[track_id] = True
                return True
        else:
            self._clear_track(track_id)

        return False

    def dwell_time(self, track_id: int) -> float:
        """Returns seconds the track has been in the zone (0 if not in zone)."""
        if track_id in self._entry:
            return time.time() - self._entry[track_id]
        return 0.0

    def _clear_track(self, track_id: int):
        self._entry.pop(track_id, None)
        self._alerted.pop(track_id, None)

    def cleanup_stale(self, active_ids: set):
        """Remove entries for tracks that no longer exist."""
        stale = [tid for tid in self._entry if tid not in active_ids]
        for tid in stale:
            self._clear_track(tid)


# ---------------------------------------------------------------------------
# Crowd Detector
# ---------------------------------------------------------------------------
class CrowdDetector:
    """
    Counts person detections inside a virtual zone per frame.
    Fires a crowd alert when count exceeds threshold.

    Usage
    -----
        detector = CrowdDetector()
        is_crowded, count = detector.update(person_bboxes, zone)
    """

    def __init__(self, threshold: int = None):
        self.threshold  = threshold if threshold is not None else CROWD_THRESHOLD
        self._last_fire = 0.0
        self._cooldown  = 10.0   # Seconds between repeat crowd alerts

    def update(self, person_bboxes: list, zone) -> tuple[bool, int]:
        """
        Parameters
        ----------
        person_bboxes : list of (x1, y1, x2, y2) for each detected person
        zone          : (zx, zy, zw, zh) or None

        Returns
        -------
        (should_alert: bool, count_in_zone: int)
        """
        if zone is None:
            # Count all persons in frame if no zone defined
            count = len(person_bboxes)
        else:
            count = sum(1 for b in person_bboxes if _bbox_in_zone(b, zone))

        now = time.time()
        if count >= self.threshold and (now - self._last_fire) >= self._cooldown:
            self._last_fire = now
            return True, count

        return False, count
