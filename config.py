"""
ibvap/config.py
Loads config.yaml at startup and exposes all settings as module-level constants.
All other modules should import from here instead of hard-coding values.
"""

import os
import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')

def _load():
    with open(_CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

_cfg = _load()

# --- AI Models ---
YOLO_MODEL          = _cfg.get('yolo_model', '../yolo26s.pt')
SR_MODEL            = _cfg.get('sr_model', '../ESPCN_x4.pb')

# --- FRS ---
FRS_THRESHOLD         = float(_cfg.get('frs_threshold', 0.38))
FRS_SUSPECT_THRESHOLD = float(_cfg.get('frs_suspect_threshold', 0.42))

# --- Behavior ---
LOITERING_TIMEOUT     = int(_cfg.get('loitering_timeout_sec', 30))
CROWD_THRESHOLD       = int(_cfg.get('crowd_threshold', 5))

# --- Night Vision ---
NIGHT_LUMINANCE_THRESHOLD = int(_cfg.get('night_luminance_threshold', 60))

# --- OCR ---
OCR_CONFIDENCE        = float(_cfg.get('ocr_confidence', 0.4))
USE_SUPER_RESOLUTION  = bool(_cfg.get('use_super_resolution', True))

# --- Alerts ---
ALERT_COOLDOWN        = float(_cfg.get('alert_cooldown_sec', 6))
INTRUSION_SNAPSHOT    = bool(_cfg.get('intrusion_snapshot', True))

# --- Camera Pool ---
MAX_CAMERAS           = int(_cfg.get('max_cameras', 16))
STREAM_FPS_LIMIT      = int(_cfg.get('stream_fps_limit', 30))

# --- Server ---
HOST  = _cfg.get('host', '0.0.0.0')
PORT  = int(_cfg.get('port', 5000))
DEBUG = bool(_cfg.get('debug', False))
