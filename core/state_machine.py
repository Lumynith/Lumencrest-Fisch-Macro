"""Fishing cycle phases: cast -> shake -> reel minigame -> verify the catch -> cast again, repeating until the
macro is stopped. Driven by MacroEngine on its own thread."""

import logging
import time
from datetime import datetime
from enum import Enum
from typing import Callable

from controllers.casting import CastSettings, cast
from controllers.reeling import ReelController
from controllers.shaking import ShakeController, ShakeResult
from core.input_controller import InputManager
from core.screen_capture import Frame, ScreenCapture
from core.window_manager import WindowState
from detection.catch_detector import CatchOutcome, CatchResult, CatchVerifier
from detection.reel_detector import ReelDetection, ReelState, ReelTracker
from detection.reel_tracking import MinigameTracker, Observation

log = logging.getLogger(__name__)


class Phase(Enum):
    CASTING = "casting"
    SHAKING = "shaking"
    REELING = "reeling"      # minigame on screen; the reel controller holds / releases (when active)
    VERIFYING = "verifying"  # minigame gone; waiting for the success / failure indicator


class FishingCycle:
    def __init__(self, capture: ScreenCapture, inputs: InputManager, cast_settings: CastSettings,
                 shake: ShakeController, reel: ReelTracker, tracker: MinigameTracker | None,
                 verifier: CatchVerifier | None, is_running: Callable[[], bool],
                 reeler: ReelController | None = None):
        self.capture = capture
        self.inputs = inputs
        self.cast_settings = cast_settings
        self.shake = shake
        self.reel = reel
        self.tracker = tracker
        self.verifier = verifier
        self.reeler = reeler
        self.observation: Observation | None = None  # latest fish/bar tracking, only while REELING
        self.attempt = 0  # counts reel minigames; each gets at most one outcome
        self.counts = {result: 0 for result in CatchResult}
        self.last_outcome: CatchOutcome | None = None
        self.history: list[tuple[datetime, CatchOutcome]] = []  # every recorded attempt, for the statistics page
        self.reel_detection: ReelDetection | None = None        # latest, for the debug page
        self.is_running = is_running
        self.phase = Phase.CASTING
        self._last_frame_id = None
        self._waiting_logged = None
        self._kept_by_tracking = False  # logged once per minigame
        self._cast_after = 0.0          # monotonic time before which the next cast waits (recast delay)
        self._cast_at: float | None = None  # when the last cast was released

    def reset(self) -> None:
        if self.phase is not Phase.CASTING:
            log.debug("Fishing cycle reset")
        if self.verifier:
            self.verifier.cancel()
        if self.reeler:
            self.reeler.stop("macro stopped")
        self.observation = None
        self.phase = Phase.CASTING
        self._cast_after, self._cast_at = 0.0, None  # resuming casts straight away

    def step(self) -> None:
        if self.phase is Phase.CASTING:
            self._cast()
            return
        frame = self._new_frame()
        if frame is False:
            return
        reel = self.reel_detection = self.reel.update(frame)
        if self.phase is Phase.SHAKING:
            if reel.state is ReelState.REEL_ACTIVE:
                self.attempt += 1
                self._set_phase(Phase.REELING, f"attempt #{self.attempt}: reel minigame detected, "
                                               f"confidence {reel.confidence:.2f}")
                self._kept_by_tracking = False
                if self.tracker:
                    self.tracker.reset()  # motion history from a previous minigame doesn't apply
                if self.reeler:
                    self.reeler.stop("new minigame")
                return  # no shake detection or clicks while the minigame is up
            timeout = self.cast_settings.recast_timeout
            if timeout > 0 and self._cast_at is not None and time.monotonic() - self._cast_at > timeout:
                self._recast(f"no reel minigame within {timeout:.0f}s of casting")
                return
            if self.shake.step(frame) is not ShakeResult.SHAKING:
                self.shake.reset()  # no buttons for a while: keep watching for the next cast's buttons
        elif self.phase is Phase.REELING:
            if self.tracker and frame is not None:
                self.observation = self.tracker.observe(frame)
            # The reference image can stop matching while the minigame is still up (a rod whose bar shrinks and
            # restyles); as long as tracking sees the fish and bar, it isn't over.
            tracked = frame is not None and self.observation is not None and self.observation.valid
            if reel.state is ReelState.REEL_INACTIVE and not tracked:
                self._end_reeling()
                return
            if reel.state is ReelState.REEL_INACTIVE and not self._kept_by_tracking:
                self._kept_by_tracking = True
                log.info("Reel image no longer matches, but tracking still sees the fish and bar: still reeling")
            if self.reeler:
                self.reeler.update(self.observation if frame is not None else None, frame)
        elif reel.state is ReelState.REEL_ACTIVE:  # VERIFYING, but the minigame is back: it never ended
            self.verifier.cancel()
            self._set_phase(Phase.REELING, "reel minigame reappeared; attempt not finished")
        else:
            outcome = self.verifier.update(frame)
            if outcome:
                self._record(outcome)
                self._recast("attempt finished")

    def _end_reeling(self) -> None:
        if self.reeler:
            self.reeler.stop("reel minigame ended")
        self.observation = None
        if self.tracker:
            self.tracker.reset()
        if self.verifier is None:
            self._recast("reel minigame ended")
            return
        self.verifier.start(self.attempt)
        self._set_phase(Phase.VERIFYING, "reel minigame ended; checking whether the fish was caught")

    def _record(self, outcome: CatchOutcome) -> None:
        if self.last_outcome and self.last_outcome.attempt == outcome.attempt:
            return  # this attempt already has a result
        self.counts[outcome.result] += 1
        self.last_outcome = outcome
        self.history.append((datetime.now(), outcome))
        fmt = lambda v: "-" if v is None else f"{v:.2f}"
        log.info("Attempt #%d: %s (%s; success %s, failed %s) | totals: %d caught, %d failed, %d unknown",
                 outcome.attempt, outcome.result.name, outcome.reason, fmt(outcome.success_score),
                 fmt(outcome.failed_score), self.counts[CatchResult.CATCH_SUCCESS],
                 self.counts[CatchResult.CATCH_FAILED], self.counts[CatchResult.UNKNOWN])

    def _new_frame(self) -> Frame | None | bool:
        """The next unseen frame, None if there is no frame, or False if the latest was already processed."""
        frame = self.capture.get_frame()
        if frame is not None and frame.frame_id == self._last_frame_id:
            time.sleep(0.005)
            return False
        self._last_frame_id = frame.frame_id if frame else None
        return frame

    def _recast(self, why: str) -> None:
        delay = self.cast_settings.recast_delay
        self._cast_after = time.monotonic() + delay
        self._cast_at = None
        self._set_phase(Phase.CASTING, f"{why}; casting again in {delay:.1f}s")

    def _set_phase(self, phase: Phase, why: str) -> None:
        log.info("Phase: %s -> %s (%s)", self.phase.name, phase.name, why)
        self.phase = phase

    def _wait(self, message: str) -> None:
        if self._waiting_logged != message:
            log.info(message)
            self._waiting_logged = message
        time.sleep(0.2)

    def _cast(self) -> None:
        if time.monotonic() < self._cast_after:
            time.sleep(0.05)  # short steps, so pause / stop stay responsive during the delay
            return
        frame = self.capture.get_frame()
        if frame is None or frame.window.state is not WindowState.ACTIVE:
            self._wait("Waiting for Roblox to be the active window before casting")
            return
        if self.reel.update(frame).state is ReelState.REEL_ACTIVE:
            self._wait("Reel minigame is on screen; not casting")
            return
        self._waiting_logged = None
        if cast(self.inputs, frame.window, self.cast_settings, self.is_running):
            self.shake.reset()
            self._cast_at = time.monotonic()
            self._set_phase(Phase.SHAKING, "cast released")
