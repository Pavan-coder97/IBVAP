"""
ibvap/core/night_vision.py
Night-time detection and frame enhancement for IBVAP.

Automatically detects low-light conditions by computing mean frame luminance,
and applies CLAHE + gamma correction to improve object detectability in the dark.
"""

import cv2
import numpy as np
import sys
import os

# Allow importing config from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from config import NIGHT_LUMINANCE_THRESHOLD
except ImportError:
    NIGHT_LUMINANCE_THRESHOLD = 60


def mean_luminance(frame: np.ndarray) -> float:
    """
    Compute the mean luminance (V channel in HSV) of a BGR frame.
    Returns a value in [0, 255].
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 2]))


def is_night(frame: np.ndarray, threshold: int = None) -> bool:
    """
    Returns True if the frame is considered night / low-light.

    Parameters
    ----------
    frame     : BGR numpy array
    threshold : Override the config threshold (optional)
    """
    t = threshold if threshold is not None else NIGHT_LUMINANCE_THRESHOLD
    return mean_luminance(frame) < t


def apply_clahe(frame: np.ndarray) -> np.ndarray:
    """
    Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    to the luminance channel in LAB colour space.
    Returns an enhanced BGR frame.
    """
    lab  = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_eq  = clahe.apply(l)
    lab_eq = cv2.merge([l_eq, a, b])
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


def apply_gamma(frame: np.ndarray, gamma: float = 1.8) -> np.ndarray:
    """
    Gamma correction brightening for dark frames.
    gamma > 1 brightens, gamma < 1 darkens.
    """
    inv_gamma = 1.0 / gamma
    table = np.array([
        ((i / 255.0) ** inv_gamma) * 255
        for i in range(256)
    ], dtype=np.uint8)
    return cv2.LUT(frame, table)


def enhance_frame(frame: np.ndarray) -> np.ndarray:
    """
    Full night-enhancement pipeline:
      1. Gamma correction  (brighten)
      2. CLAHE             (local contrast)

    Returns the enhanced BGR frame.
    """
    brightened = apply_gamma(frame, gamma=1.8)
    enhanced   = apply_clahe(brightened)
    return enhanced


def process_night(frame: np.ndarray) -> tuple[np.ndarray, bool]:
    """
    Convenience function: checks if it's night and enhances if so.

    Returns
    -------
    (processed_frame, is_night_flag)
    """
    night = is_night(frame)
    if night:
        return enhance_frame(frame), True
    return frame, False
