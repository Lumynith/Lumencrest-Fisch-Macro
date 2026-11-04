"""Finds SHAKE buttons anywhere inside the calibrated SHAKE region by template matching.

The button is translucent, so its colors change with whatever is behind it. Only its white text and
outline are constant, so matching uses a white-pixel image and ignores everything away from the strokes.
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.calibration import Region
from core.config import APP_DIR
from core.screen_capture import Frame
from detection.image_utils import crop_region, read_image, write_image

log = logging.getLogger(__name__)

TEMPLATE_DIR = APP_DIR / "assets" / "templates"
HEIGHT_TAG = re.compile(r"shake_h(\d+)_")  # client height at capture; untagged files assume the current one
MIN_SIZE = 10           # pixels per side
MIN_WHITE_PIXELS = 20   # a template needs some white text/outline to match on
STROKE_MARGIN = 7       # pixels around the white strokes that are compared too (the dark edge around them)
COARSE_WIDTH = 640      # the region is first searched at this width, then refined at full resolution


def white_pixels(image: np.ndarray) -> np.ndarray:
    """255 where a pixel is bright and colorless (the button's text and ring), else 0."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 2] > 200) & (hsv[..., 1] < 40)).astype(np.uint8) * 255


@dataclass(frozen=True)
class ShakeMatch:
    x: int  # button center, client-area pixels
    y: int
    w: int
    h: int
    confidence: float  # TM_CCOEFF_NORMED score, 0-1


@dataclass(frozen=True)
class _Template:
    white: np.ndarray
    mask: np.ndarray
    captured_h: int | None


class ShakeDetector:
    """Pure detection; never sends input. Safe to call from several threads."""

    def __init__(self, regions: dict[str, Region], template_dir: Path = TEMPLATE_DIR):
        self.regions = regions  # shared with calibration, so SHAKE edits apply immediately
        self.template_dir = template_dir
        self.templates: list[_Template] = []
        self.reload_templates()

    def reload_templates(self) -> None:
        templates = []
        for path in sorted(self.template_dir.glob("shake*.png")):
            image = read_image(path)
            if image is None or min(image.shape[:2]) < MIN_SIZE:
                log.warning("Skipping unreadable shake template %s", path.name)
                continue
            white = white_pixels(image)
            if cv2.countNonZero(white) < MIN_WHITE_PIXELS:
                log.warning("Skipping shake template %s: no white text or outline in it", path.name)
                continue
            tag = HEIGHT_TAG.match(path.name)
            mask = cv2.dilate(white, np.ones((STROKE_MARGIN, STROKE_MARGIN), np.uint8))
            templates.append(_Template(white, mask, int(tag.group(1)) if tag else None))
        self.templates = templates  # swapped whole, so a detect() in another thread sees old or new
        log.info("Shake templates: %d (from %s)", len(templates), self.template_dir)

    def capture_template(self, frame: Frame | None, box: tuple[int, int, int, int]) -> Path:
        """Saves box (client-area x, y, w, h) of frame as a template. Raises ValueError if unusable."""
        if frame is None:
            raise ValueError("no current frame (is Roblox visible and not minimized?)")
        x, y, w, h = box
        fh, fw = frame.image.shape[:2]
        if w < MIN_SIZE or h < MIN_SIZE:
            raise ValueError(f"selection is smaller than {MIN_SIZE}px")
        if x < 0 or y < 0 or x + w > fw or y + h > fh:
            raise ValueError("selection is outside the Roblox window")
        crop = frame.image[y:y + h, x:x + w]
        if cv2.countNonZero(white_pixels(crop)) < MIN_WHITE_PIXELS:
            raise ValueError("selection has no white text or outline to match on")
        self.template_dir.mkdir(parents=True, exist_ok=True)
        path = self.template_dir / f"shake_h{fh}_{time.strftime('%Y%m%d_%H%M%S')}_{frame.frame_id}.png"
        if not write_image(path, crop):
            raise ValueError(f"could not encode {path.name}")
        log.info("Saved shake template %s (%dx%d)", path.name, w, h)
        self.reload_templates()
        return path

    def detect(self, frame: Frame) -> ShakeMatch | None:
        """Best match in the SHAKE region (even if weak), or None if there is nothing to match."""
        region = self.regions.get("SHAKE")
        if region is None or not self.templates:
            return None
        crop = crop_region(frame.image, region)
        if crop is None:
            return None
        fh, fw = frame.image.shape[:2]
        left, top, _, _ = region.to_pixels(fw, fh)
        white = white_pixels(crop)
        coarse = min(1.0, COARSE_WIDTH / white.shape[1])
        small = cv2.resize(white, None, fx=coarse, fy=coarse, interpolation=cv2.INTER_AREA) if coarse < 1 else white
        best = None
        for t in self.templates:
            # shortcut: assumes the button scales with client height, recapture if Roblox UI scales differently
            scale = fh / t.captured_h if t.captured_h else 1.0
            tpl, mask = (_resize(t.white, scale), _resize(t.mask, scale)) if abs(scale - 1) > 0.02 else (t.white, t.mask)
            found = _match(white, small, coarse, tpl, mask)
            if found and (best is None or found[0] > best.confidence):
                score, mx, my = found
                th, tw = tpl.shape[:2]
                best = ShakeMatch(left + mx + tw // 2, top + my + th // 2, tw, th, score)
        return best


def _resize(image: np.ndarray, scale: float) -> np.ndarray:
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def _best(image: np.ndarray, template: np.ndarray, mask: np.ndarray) -> tuple[float, int, int]:
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    # Masked matching divides by ~0 on flat areas and returns nan or huge numbers; those are not matches.
    result[~np.isfinite(result) | (np.abs(result) > 1.0001)] = 0
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    return score, x, y


def _match(white: np.ndarray, small: np.ndarray, coarse: float, template: np.ndarray,
           mask: np.ndarray) -> tuple[float, int, int] | None:
    """(score, x, y) of template's best top-left position in white, or None if it can't be matched."""
    th, tw = template.shape[:2]
    if th < 4 or tw < 4 or th > white.shape[0] or tw > white.shape[1] or not mask.any():
        return None
    x0, y0, x1, y1 = 0, 0, white.shape[1], white.shape[0]
    small_t, small_m = _resize(template, coarse), _resize(mask, coarse)
    if coarse < 1 and min(small_t.shape[:2]) >= 6 and small_m.any():
        _, sx, sy = _best(small, small_t, small_m)
        pad = int(2 / coarse) + 2  # covers the coarse search's position error
        x0, y0 = max(0, round(sx / coarse) - pad), max(0, round(sy / coarse) - pad)
        x1, y1 = min(white.shape[1], round(sx / coarse) + tw + pad), min(white.shape[0], round(sy / coarse) + th + pad)
    score, x, y = _best(white[y0:y1, x0:x1], template, mask)
    return min(max(score, 0.0), 1.0), x0 + x, y0 + y
