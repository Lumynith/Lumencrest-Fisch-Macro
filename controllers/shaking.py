"""Clicks detected SHAKE buttons. Detection lives in detection/shake_detector.py."""

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum

from core.input_controller import InputManager
from core.screen_capture import Frame
from core.window_manager import WindowState, get_window_info
from detection.shake_detector import ShakeDetector, ShakeMatch

log = logging.getLogger(__name__)

SETTLE = 0.05            # frames captured this soon after a click may still show the clicked button
DUPLICATE_WINDOW = 1.0   # a match at the last click's spot within this time is the same button


@dataclass(frozen=True)
class ShakeSettings:
    threshold: float = field(default=0.75, metadata={"max": 1.0})            # min confidence to count as a button
    click_delay: float = field(default=0.15, metadata={"max": 5.0})          # min seconds between clicks
    auto_click: bool = True                                                  # False = debug mode, highlight only
    first_button_timeout: float = field(default=8.0, metadata={"max": 60.0})  # after cast, give up if none
    finish_after: float = field(default=2.0, metadata={"max": 30.0})         # shaking done after no button this long
    move_settle: float = field(default=0.05, metadata={"max": 1.0})          # wait after moving, before pressing
    click_hold: float = field(default=0.06, metadata={"max": 1.0})           # how long the button is held down


class ShakeResult(Enum):
    SHAKING = "shaking"
    FINISHED = "finished"   # buttons were seen, then stopped appearing
    TIMEOUT = "timeout"     # no button ever appeared


class ShakeController:
    def __init__(self, detector: ShakeDetector, inputs: InputManager, settings: ShakeSettings):
        self.detector = detector
        self.inputs = inputs
        self.settings = settings
        self.auto_click = settings.auto_click  # runtime toggle (A in the preview)
        self.reset()

    def reset(self) -> None:
        self.started = time.perf_counter()
        self.last_seen: float | None = None
        self.last_click = 0.0
        self.last_click_pos: tuple[int, int] | None = None
        self.clicks = 0
        self._last_skip_log = 0.0
        self.last_confidence: float | None = None  # best match in the latest frame, for the debug page

    def step(self, frame: Frame | None) -> ShakeResult:
        now = time.perf_counter()
        if frame is not None and frame.window.is_usable:
            match = self.detector.detect(frame)
            self.last_confidence = match.confidence if match else None
            if match and match.confidence >= self.settings.threshold:
                self.last_seen = now
                self._maybe_click(frame, match)
            elif match:
                log.debug("No shake button (best %.2f < %.2f)", match.confidence, self.settings.threshold)
        if self.last_seen is None:
            if now - self.started > self.settings.first_button_timeout:
                log.debug("No shake button found within %.1fs", self.settings.first_button_timeout)
                return ShakeResult.TIMEOUT
        elif now - self.last_seen > self.settings.finish_after:
            log.info("Shaking finished after %d click(s)", self.clicks)
            return ShakeResult.FINISHED
        return ShakeResult.SHAKING

    def _maybe_click(self, frame: Frame, match: ShakeMatch) -> None:
        now = time.perf_counter()
        if frame.timestamp < self.last_click + SETTLE or now - self.last_click < self.settings.click_delay:
            return
        if self.last_click_pos and now - self.last_click < DUPLICATE_WINDOW and \
                math.dist(self.last_click_pos, (match.x, match.y)) < max(match.w, match.h) / 2:
            return
        current = get_window_info(frame.window.hwnd)
        if current.state is not WindowState.ACTIVE:
            self._log_skip("Shake button found but Roblox is not the active window; click into Roblox")
            return
        if (current.x, current.y, current.width, current.height) != \
                (frame.window.x, frame.window.y, frame.window.width, frame.window.height):
            log.debug("Roblox moved or resized since this frame; waiting for a fresh one")
            return
        sx, sy = current.x + match.x, current.y + match.y
        if not self.auto_click:
            log.info("Shake button at (%d, %d), confidence %.2f [debug mode: not clicking]", sx, sy, match.confidence)
        elif self.inputs.click(sx, sy, settle=self.settings.move_settle, hold=self.settings.click_hold):
            self.clicks += 1
            log.info("Clicked shake button at (%d, %d), confidence %.2f", sx, sy, match.confidence)
        else:
            self._log_skip("Shake button found but inputs are off (macro paused or stopped)")
            return
        self.last_click = time.perf_counter()
        self.last_click_pos = (match.x, match.y)

    def _log_skip(self, message: str) -> None:
        now = time.perf_counter()
        if now - self._last_skip_log > 2.0:  # once per 2s, not every frame
            log.warning(message)
            self._last_skip_log = now
