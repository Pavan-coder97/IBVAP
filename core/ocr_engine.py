"""
ibvap/core/ocr_engine.py
ANPR / OCR Engine for IBVAP.

Pipeline:
  1. Optional ESPCN x4 super-resolution (sharpens plate crop before OCR)
  2. EasyOCR text extraction
  3. Multi-format plate validation:
       - Indian standard  : AA99AA9999 or AA99A9999 etc.
       - Generic fallback : Any 4-12 char alphanumeric string
  4. Returns (plate_text, confidence) tuple
"""

import cv2
import re
import os
import sys
import numpy as np
import easyocr

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from config import OCR_CONFIDENCE, USE_SUPER_RESOLUTION, SR_MODEL
except ImportError:
    OCR_CONFIDENCE      = 0.4
    USE_SUPER_RESOLUTION = True
    SR_MODEL            = '../ESPCN_x4.pb'


# ── Plate format patterns (multi-format) ──────────────────────────────────

_PLATE_PATTERNS = [
    # Indian: MH12AB1234 / DL1C1234 / KA03MN1234
    re.compile(r'[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}'),
    # European-style: ABC1234 / AB12CDE
    re.compile(r'[A-Z]{1,3}\d{2,4}[A-Z]{0,3}'),
    # US-style: 1ABC234 / ABC1234
    re.compile(r'\d?[A-Z]{2,3}\d{3,4}'),
    # Generic alphanumeric 4-10 chars
    re.compile(r'[A-Z0-9]{4,10}'),
]


class OCREngine:
    def __init__(self):
        self.reader = easyocr.Reader(['en'], gpu=False)

        # Load ESPCN super-resolution model
        self._sr = None
        if USE_SUPER_RESOLUTION:
            sr_path = os.path.join(os.path.dirname(__file__), SR_MODEL)
            # Resolve relative path from ibvap root
            if not os.path.exists(sr_path):
                sr_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), '..', '..', 'ESPCN_x4.pb')
                )
            if os.path.exists(sr_path):
                try:
                    self._sr = cv2.dnn_superres.DnnSuperResImpl_create()
                    self._sr.readModel(sr_path)
                    self._sr.setModel('espcn', 4)
                    print("[OCR] ESPCN x4 super-resolution loaded.")
                except Exception as e:
                    print(f"[OCR] SR model load failed: {e}. Running without SR.")
                    self._sr = None
            else:
                print("[OCR] ESPCN_x4.pb not found. Running without super-resolution.")

    # ── Preprocessing ─────────────────────────────────────────────────────

    def _super_resolve(self, img: np.ndarray) -> np.ndarray:
        """Upscale image 4× using ESPCN if available."""
        if self._sr is None:
            return img
        try:
            return self._sr.upsample(img)
        except Exception:
            return img

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        """Grayscale + threshold to improve OCR on noisy plate crops."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Adaptive threshold
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )
        # Convert back to BGR for EasyOCR
        return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

    # ── Text cleaning ──────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        """Uppercase and remove spaces/special chars."""
        return re.sub(r'[^A-Z0-9]', '', text.upper())

    @staticmethod
    def _validate(text: str) -> str:
        """Try matching known plate patterns; return best match or raw text."""
        for pattern in _PLATE_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return text if len(text) >= 4 else ''

    # ── Main OCR entry point ───────────────────────────────────────────────

    def run_ocr(self, img_crop: np.ndarray) -> tuple[str, float]:
        """
        Full ANPR pipeline.

        Parameters
        ----------
        img_crop : BGR numpy array (plate region)

        Returns
        -------
        (plate_text, confidence)  — plate_text is '' if nothing reliable found
        """
        if img_crop is None or img_crop.size == 0:
            return '', 0.0

        # 1. Super-resolution upscale
        upscaled = self._super_resolve(img_crop)

        # 2. Preprocess
        processed = self._preprocess(upscaled)

        # 3. EasyOCR
        try:
            results = self.reader.readtext(processed)
        except Exception:
            return '', 0.0

        if not results:
            return '', 0.0

        # 4. Filter by confidence threshold
        accepted = [(text, conf) for (_, text, conf) in results if conf >= OCR_CONFIDENCE]
        if not accepted:
            return '', 0.0

        # 5. Join and clean
        raw_text  = ''.join(t for t, _ in accepted)
        avg_conf  = sum(c for _, c in accepted) / len(accepted)
        cleaned   = self._clean(raw_text)
        validated = self._validate(cleaned)

        return validated, round(avg_conf, 3)

    # ── Legacy compatibility ───────────────────────────────────────────────
    def run_ocr_legacy(self, img_crop: np.ndarray) -> str:
        """Returns plate text string only (backward-compat)."""
        text, _ = self.run_ocr(img_crop)
        return text


# Singleton instance
ocr = OCREngine()
