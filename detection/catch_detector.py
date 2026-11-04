"""Decides whether a fishing attempt succeeded or failed after the reel minigame ends.

The user captures reference images of the success and failure indicators. Each one remembers where on
screen it was captured, and is only searched near that spot. Never sends input.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from core.config import APP_DIR
from core.screen_capture import Frame
from detection.image_utils import read_image, write_image

log = logging.getLogger(__name__)

TEMPLATE_DIR = APP_DIR / "assets" / "templates"
KINDS = ("success", "failed")
TEMPLATE_NAME = re.compile(r"catch_(success|failed)_h(\d+)_x([\d.]+)_y([\d.]+)_.*\.png$")
SEARCH_MARGIN_X = 0.5   # whole row: centered popup text shifts sideways with the fish name's length
SEARCH_MARGIN_Y = 0.15  # fraction of the client height above/below where the indicator was captured
WIN_MARGIN = 0.05     # the winning kind must beat the other kind's score by this much
MIN_SIZE = 10         # pixels per side
MIN_DETAIL = 8.0      # grayscale std dev; flat images correlate with anything


class CatchResult(Enum):
    CATCH_SUCCESS = "success"
    CATCH_FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CatchSettings:
    threshold: float = field(default=0.75, metadata={"max": 1.0})            # min score to count an indicator
    confirm_frames: int = field(default=3, metadata={"min": 1, "max": 300})  # consecutive frames that must agree
    verify_timeout: float = field(default=3.0, metadata={"max": 30.0})       # seconds before giving up (UNKNOWN)


@dataclass(frozen=True)
class CatchOutcome:
    attempt: int
    result: CatchResult
    success_score: float | None
    failed_score: float | None
    reason: str


@dataclass(frozen=True, eq=False)
class _Template:
    kind: str
    gray: np.ndarray
    captured_h: int
    cx: float  # center where it was captured, fractions of the client size
    cy: float


class CatchMatcher:
    """Scores frames against the success / failure reference images. Stateless, safe across threads."""

    def __init__(self, template_dir: Path = TEMPLATE_DIR):
        self.template_dir = template_dir
        self.templates: list[_Template] = []
        self.reload_templates()

    def has_templates(self) -> bool:
        return bool(self.templates)

    def reload_templates(self) -> None:
        templates = []
        for path in sorted(self.template_dir.glob("catch_*.png")):
            name = TEMPLATE_NAME.match(path.name)
            image = read_image(path)
            if name is None or image is None or min(image.shape[:2]) < MIN_SIZE:
                log.warning("Skipping unusable catch template %s", path.name)
                continue
            kind, h, cx, cy = name.group(1), int(name.group(2)), float(name.group(3)), float(name.group(4))
            templates.append(_Template(kind, cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), h, cx, cy))
        self.templates = templates  # swapped whole, so a score() in another thread sees old or new
        log.info("Catch templates: %d success, %d failed",
                 sum(t.kind == "success" for t in templates), sum(t.kind == "failed" for t in templates))

    def capture_template(self, frame: Frame | None, box: tuple[int, int, int, int], kind: str) -> Path:
        """Saves box (client x, y, w, h) as a success/failed reference. Raises ValueError if unusable."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if frame is None:
            raise ValueError("no current frame (is Roblox visible and not minimized?)")
        x, y, w, h = box
        fh, fw = frame.image.shape[:2]
        if w < MIN_SIZE or h < MIN_SIZE:
            raise ValueError(f"selection is smaller than {MIN_SIZE}px")
        if x < 0 or y < 0 or x + w > fw or y + h > fh:
            raise ValueError("selection is outside the Roblox window")
        crop = frame.image[y:y + h, x:x + w]
        if cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std() < MIN_DETAIL:
            raise ValueError("selection is almost a flat color; it would match anything")
        self.template_dir.mkdir(parents=True, exist_ok=True)
        cx, cy = (x + w / 2) / fw, (y + h / 2) / fh
        path = self.template_dir / f"catch_{kind}_h{fh}_x{cx:.4f}_y{cy:.4f}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if not write_image(path, crop):
            raise ValueError(f"could not encode {path.name}")
        log.info("Saved %s catch template %s", kind, path.name)
        self.reload_templates()
        return path

    def score(self, frame: Frame) -> dict[str, float | None]:
        """Best score per kind ("success"/"failed"); None for a kind with no usable template."""
        scores: dict[str, float | None] = {kind: None for kind in KINDS}
        if not self.templates:
            return scores
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        fh, fw = gray.shape
        for t in self.templates:
            try:
                score = self._score_one(gray, t)
            except cv2.error as e:  # unexpected sizes from odd UI states must not stop the macro
                log.warning("Catch matching failed: %s", e)
                continue
            if score is not None and (scores[t.kind] is None or score > scores[t.kind]):
                scores[t.kind] = score
        return scores

    @staticmethod
    def _score_one(gray: np.ndarray, t: _Template) -> float | None:
        fh, fw = gray.shape
        template = t.gray
        scale = fh / t.captured_h
        if abs(scale - 1) > 0.02:
            template = cv2.resize(template, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        th, tw = template.shape
        cx, cy, mx, my = t.cx * fw, t.cy * fh, SEARCH_MARGIN_X * fw, SEARCH_MARGIN_Y * fh
        x0, y0 = max(0, int(cx - tw / 2 - mx)), max(0, int(cy - th / 2 - my))
        x1, y1 = min(fw, int(cx + tw / 2 + mx)), min(fh, int(cy + th / 2 + my))
        if x1 - x0 < tw or y1 - y0 < th or tw < 4 or th < 4:
            return None
        result = cv2.matchTemplate(gray[y0:y1, x0:x1], template, cv2.TM_CCOEFF_NORMED)
        result[~np.isfinite(result) | (np.abs(result) > 1.0001)] = 0  # flat areas divide by ~0
        return min(max(float(result.max()), 0.0), 1.0)


class CatchVerifier:
    """Decides one attempt at a time: start() when the minigame ends, then update() each new frame until it
    returns an outcome. Returns each outcome exactly once."""

    def __init__(self, matcher: CatchMatcher, settings: CatchSettings):
        self.matcher = matcher
        self.settings = settings
        self.active = False
        self.attempt = 0
        self.scores: dict[str, float | None] = {kind: None for kind in KINDS}

    def start(self, attempt: int) -> None:
        self.active = True
        self.attempt = attempt
        self.started = time.perf_counter()
        self._streak_kind: str | None = None
        self._streak = 0

    def cancel(self) -> None:
        self.active = False

    def update(self, frame: Frame | None) -> CatchOutcome | None:
        if not self.active:
            return None
        if not self.matcher.has_templates():
            return self._finish(CatchResult.UNKNOWN, "no catch reference images (press G / F in the preview)")
        if frame is not None:
            self.scores = self.matcher.score(frame)
            s, f = self.scores["success"] or 0.0, self.scores["failed"] or 0.0
            threshold = self.settings.threshold
            kind = "success" if s >= threshold and s - f >= WIN_MARGIN else \
                "failed" if f >= threshold and f - s >= WIN_MARGIN else None
            self._streak = self._streak + 1 if kind and kind == self._streak_kind else (1 if kind else 0)
            self._streak_kind = kind
            if kind and self._streak >= self.settings.confirm_frames:
                result = CatchResult.CATCH_SUCCESS if kind == "success" else CatchResult.CATCH_FAILED
                return self._finish(result, f"{kind} indicator seen for {self._streak} frames")
        if time.perf_counter() - self.started > self.settings.verify_timeout:
            return self._finish(CatchResult.UNKNOWN, f"no clear indicator within {self.settings.verify_timeout:.1f}s")
        return None

    def _finish(self, result: CatchResult, reason: str) -> CatchOutcome:
        self.active = False
        return CatchOutcome(self.attempt, result, self.scores["success"], self.scores["failed"], reason)
