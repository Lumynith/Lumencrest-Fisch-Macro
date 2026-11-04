"""Reeling minigame detection: compares the REEL region against saved reference images.

Matching uses edge strength instead of colors, so the world showing through a translucent bar does not
matter, and a slightly shrunken template so small shifts still match.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from core.calibration import Region
from core.config import APP_DIR
from core.screen_capture import Frame
from detection.image_utils import crop_region, read_image, write_image

log = logging.getLogger(__name__)

TEMPLATE_DIR = APP_DIR / "assets" / "templates"
SHIFT_TOLERANCE = 0.05   # the bar may move this fraction of the region and still match
ASPECT_TOLERANCE = 0.15  # templates from a differently shaped REEL region are skipped
MIN_SIZE = 16            # pixels per side
MIN_DETAIL = 8.0         # grayscale std dev; flat images correlate with anything
EDGE_FLOOR = 60          # Sobel magnitude (~15 brightness levels across a pixel) that counts as an edge


class ReelState(Enum):
    REEL_ACTIVE = "active"
    REEL_INACTIVE = "inactive"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReelSettings:
    active_threshold: float = field(default=0.55, metadata={"max": 1.0})    # score counted as minigame visible
    inactive_threshold: float = field(default=0.30, metadata={"max": 1.0})  # score counted as minigame gone
    confirm_frames: int = field(default=5, metadata={"min": 1, "max": 300})            # frames to confirm ACTIVE
    inactive_confirm_frames: int = field(default=10, metadata={"min": 1, "max": 300})  # frames to confirm INACTIVE


@dataclass(frozen=True)
class ReelDetection:
    state: ReelState
    confidence: float     # 0-1, how sure the reported state is
    score: float | None   # best template match this frame, None if not computed
    reason: str


def _edges(image: np.ndarray) -> np.ndarray:
    """Strong edges only (bar outline, slider); faint background texture seen through the bar is dropped."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    magnitude = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    strong = (magnitude > EDGE_FLOOR).astype(np.float32)
    return cv2.GaussianBlur(strong, (0, 0), 1.5)  # blur so 1-2px offsets still overlap


class ReelMatcher:
    """Scores how much a frame's REEL region looks like the minigame. Stateless, safe across threads."""

    def __init__(self, regions: dict[str, Region], template_dir: Path = TEMPLATE_DIR):
        self.regions = regions
        self.template_dir = template_dir
        self.templates: list[np.ndarray] = []  # edge images
        self.reload_templates()

    def reload_templates(self) -> None:
        templates = []
        for path in sorted(self.template_dir.glob("reel*.png")):
            image = read_image(path)
            if image is None or min(image.shape[:2]) < MIN_SIZE:
                log.warning("Skipping unusable reel template %s", path.name)
            else:
                templates.append(_edges(image))
        self.templates = templates  # swapped whole, so a score() in another thread sees old or new
        log.info("Reel templates: %d", len(templates))

    def capture_template(self, frame: Frame | None) -> Path:
        """Saves the current REEL region as a reference image. Raises ValueError if it can't be used."""
        if frame is None:
            raise ValueError("no current frame (is Roblox visible and not minimized?)")
        region = self.regions.get("REEL")
        if region is None:
            raise ValueError("no REEL region; calibrate first")
        crop = crop_region(frame.image, region)
        if crop is None or min(crop.shape[:2]) < MIN_SIZE:
            raise ValueError(f"REEL region is smaller than {MIN_SIZE}px")
        if cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std() < MIN_DETAIL:
            raise ValueError("REEL region is almost a flat color; it would match anything")
        self.template_dir.mkdir(parents=True, exist_ok=True)
        path = self.template_dir / f"reel_{time.strftime('%Y%m%d_%H%M%S')}_{frame.frame_id}.png"
        if not write_image(path, crop):
            raise ValueError(f"could not encode {path.name}")
        log.info("Saved reel template %s", path.name)
        self.reload_templates()
        return path

    def score(self, frame: Frame) -> tuple[float | None, str]:
        """(best match 0-1, reason). Score is None when it can't be computed."""
        if not self.templates:
            return None, "no reel templates"
        region = self.regions.get("REEL")
        if region is None:
            return None, "no REEL region"
        crop = crop_region(frame.image, region)
        if crop is None or min(crop.shape[:2]) < 8:
            return None, "REEL region too small"
        ch, cw = crop.shape[:2]
        best = None
        for template in self.templates:
            th, tw = template.shape[:2]
            if abs((cw / ch) / (tw / th) - 1) > ASPECT_TOLERANCE:
                continue
            live = _edges(cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA))
            mx, my = max(1, int(tw * SHIFT_TOLERANCE)), max(1, int(th * SHIFT_TOLERANCE))
            result = cv2.matchTemplate(live, template[my:th - my, mx:tw - mx], cv2.TM_CCOEFF_NORMED)
            result[~np.isfinite(result) | (np.abs(result) > 1.0001)] = 0  # flat areas divide by ~0
            score = float(result.max())
            best = score if best is None else max(best, score)
        if best is None:
            return None, "reel templates do not fit REEL region; recapture"
        return min(max(best, 0.0), 1.0), "ok"


class ReelTracker:
    """Turns per-frame scores into a confirmed state. Feed it each new frame once; one tracker per thread."""

    def __init__(self, matcher: ReelMatcher, settings: ReelSettings, log_changes: bool = True):
        self.matcher = matcher
        self.settings = settings
        self.log_changes = log_changes
        self.state = ReelState.UNKNOWN
        self._active = 0    # consecutive frames above active_threshold
        self._inactive = 0  # consecutive frames below inactive_threshold

    def update(self, frame: Frame | None) -> ReelDetection:
        score, reason = self.matcher.score(frame) if frame is not None else (None, "no frame")
        s = self.settings
        if score is None:
            self._active = self._inactive = 0
            return self._report(ReelState.UNKNOWN, score, reason)
        if score >= s.active_threshold:
            self._active, self._inactive = self._active + 1, 0
        elif score < s.inactive_threshold:
            self._active, self._inactive = 0, self._inactive + 1
        else:
            self._active = self._inactive = 0  # ambiguous frame: keep the confirmed state
        state = self.state
        if self._active >= s.confirm_frames:
            state = ReelState.REEL_ACTIVE
        elif self._inactive >= s.inactive_confirm_frames:
            state = ReelState.REEL_INACTIVE
        if state is self.state and 0 < self._active < s.confirm_frames:
            reason = f"confirming active {self._active}/{s.confirm_frames}"
        elif state is self.state and 0 < self._inactive < s.inactive_confirm_frames:
            reason = f"confirming inactive {self._inactive}/{s.inactive_confirm_frames}"
        return self._report(state, score, reason)

    def _report(self, state: ReelState, score: float | None, reason: str) -> ReelDetection:
        if state is ReelState.REEL_ACTIVE:
            confidence = score or 0.0
        elif state is ReelState.REEL_INACTIVE:
            confidence = 1.0 - (score or 0.0)
        else:
            confidence = 0.0
        if state is not self.state:
            if self.log_changes:
                log.info("Reel: %s -> %s (score %s, %s)", self.state.name, state.name,
                         "-" if score is None else f"{score:.2f}", reason)
            self.state = state
        return ReelDetection(state, confidence, score, reason)
