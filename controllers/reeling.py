"""Reel minigame controller: holds / releases the left mouse button to keep the control bar on the fish.

Works on tracking Observations (detection/reel_tracking.py) only; never looks at pixels itself.
Positions are fractions of the track (0 = left, 1 = right), velocities are track widths per second.
"""

import csv
import logging
import threading
import time
from dataclasses import asdict, dataclass, field, replace

from core.config import APP_DIR, write_section
from core.input_controller import InputManager
from core.screen_capture import Frame
from core.window_manager import WindowState, get_window_info
from detection.reel_tracking import Observation

log = logging.getLogger(__name__)

SECTION = "reel_control"
CALIBRATE_HOLD = 0.3   # seconds held while testing which way holding moves the bar
CALIBRATE_MIN_MOVE = 0.02  # track widths the bar must move for the test to count
LOG_EVERY = 2.0        # seconds between repeats of the same warning
STICKY_EXIT = 1.5      # spam mode starts within sticky_distance and ends past this many times it
DUTY_RANGE = (0.15, 0.85)  # share of each spam click spent pressed; never fully on or off
STICKY_MAX_SPEED = 0.2  # track widths/s relative to the fish; faster than this, steer steadily instead of spam
BRAKE_MIN_SPEED = 0.1   # track widths/s; slower approaches never need braking (and the gap near 0 would chatter)
TRACE_PATH = APP_DIR / "logs" / "reel_trace.csv"  # last minigame, for tuning


@dataclass(frozen=True)
class ReelControlSettings:
    kp: float = field(default=1.0, metadata={"max": 100.0})                  # pushes toward the fish
    kd: float = field(default=0.2, metadata={"max": 100.0})                  # damps relative speed (overshoot)
    deadzone: float = field(default=0.02, metadata={"max": 0.5})             # position error ignored below this
    hysteresis: float = field(default=0.02, metadata={"max": 1.0})           # output must pass +-this to switch
    prediction_time: float = field(default=0.08, metadata={"max": 1.0})      # seconds of motion to look ahead
    min_input_interval: float = field(default=0.03, metadata={"max": 1.0})   # min seconds between switches
    sticky_distance: float = field(default=0.05, metadata={"max": 0.5})      # this close: spam click to hover
    sticky_interval: float = field(default=0.015, metadata={"min": 0.005, "max": 1.0})  # s per press / release
    lost_grace: float = field(default=0.15, metadata={"max": 2.0})           # keep the last output through
                                                                             # tracking dropouts this short
    brake_decel: float = field(default=0.8, metadata={"max": 20.0})          # how fast the bar slows when pushed
                                                                             # back (track widths/s^2); 0 = off
    hold_moves_right: bool = True                                            # set by direction calibration (D)


@dataclass(frozen=True)
class ControlDecision:
    hold: bool
    fish_x: float | None
    bar_center: float | None
    error: float | None    # predicted fish - predicted bar; positive = fish is to the right
    output: float | None   # PD output, positive = hold
    confidence: float      # the weaker of the fish / bar tracking confidences
    reason: str
    sticky: bool = False   # spam clicking (on its own thread) instead of a steady hold / release
    duty: float | None = None  # while spam clicking: share of each click spent pressed


class ReelController:
    """decide() is pure control logic; update() also sends the input when active (the default). Debug mode
    (active = False) decides and displays but never presses. Runs on the engine thread; the preview may set
    `active`, `settings` and request direction calibration from the Qt thread (plain attribute swaps)."""

    def __init__(self, inputs: InputManager | None, settings: ReelControlSettings):
        self.inputs = inputs
        self.settings = settings
        self.active = True
        self.last: ControlDecision | None = None
        self._calibrate_requested = False
        self._spam: tuple[threading.Thread, threading.Event] | None = None
        self.reset()

    def reset(self) -> None:
        self.holding = False          # the decided state (kept in debug mode too)
        self.sticky = False
        self.duty = 0.5  # read by the spam thread
        self._last_switch = 0.0
        self._positioned = False      # cursor moved into the game this minigame
        self._calibration: tuple[float, float] | None = None  # (started, bar_center) while testing
        self._last_log: dict[str, float] = {}
        self._debug_noted = False
        self._lost_since: float | None = None
        self._trace = None  # (file, csv writer) for this minigame

    def decide(self, obs: Observation | None, now: float) -> ControlDecision:
        s = self.settings
        if obs is None or not obs.valid:
            reason = "no observation" if obs is None else f"tracking lost: {obs.reason}"
            conf = 0.0 if obs is None else min(obs.fish_confidence, obs.bar_confidence)
            self._lost_since = now if self._lost_since is None else self._lost_since
            if now - self._lost_since < s.lost_grace:
                reason += " (brief, keeping last output)"  # one bad frame must not drop the bar
            else:
                self.holding = self.sticky = False  # really lost: release, ignoring min_input_interval
            return ControlDecision(self.holding, obs and obs.fish_x, obs and obs.bar_center, None, None, conf, reason,
                                   self.sticky)
        self._lost_since = None
        fish_v, bar_v = obs.fish_velocity or 0.0, obs.bar_velocity or 0.0
        error = (obs.fish_x + fish_v * s.prediction_time) - (obs.bar_center + bar_v * s.prediction_time)
        conf = min(obs.fish_confidence, obs.bar_confidence)
        gap, closing = obs.fish_x - obs.bar_center, bar_v - fish_v  # closing > 0: the bar moves right of the fish
        # The bar coasts: pushed back it only slows at about brake_decel, so once it would slide past the fish
        # before stopping, push back now instead of when it arrives.
        if s.brake_decel > 0 and abs(closing) > BRAKE_MIN_SPEED and gap * closing > 0 \
                and closing * closing / (2 * s.brake_decel) >= abs(gap):
            self.sticky = False
            want = (closing < 0) == s.hold_moves_right  # moving left: push right, and the reverse
            reason = "braking before the fish"
            if want != self.holding:
                if now - self._last_switch < s.min_input_interval:
                    want, reason = self.holding, "min input interval"
                else:
                    self._last_switch = now
            self.holding = want
            return ControlDecision(want, obs.fish_x, obs.bar_center, error, None, conf, reason)
        # Wider exit than entry, so a fish sitting on the boundary doesn't flip modes every frame. Not while the
        # bar is moving fast: half-presses push too weakly to stop it.
        self.sticky = abs(error) < s.sticky_distance * (STICKY_EXIT if self.sticky else 1.0) \
            and abs(closing) < STICKY_MAX_SPEED
        if self.sticky:
            # Fast clicking cancels the bar's fall so it hovers; longer presses when the fish is a bit to the
            # right (shorter to the left) keep easing it onto the fish. Too far: steer below.
            push = error / max(s.sticky_distance, 1e-6) * (1 if s.hold_moves_right else -1)
            self.duty = min(DUTY_RANGE[1], max(DUTY_RANGE[0], 0.5 + 0.5 * push))
            self.holding = False  # steering restarts from released when the fish gets away
            return ControlDecision(False, obs.fish_x, obs.bar_center, error, None, conf,
                                   f"sticky click, {self.duty:.0%} pressed", True, self.duty)
        if abs(error) < s.deadzone:
            error_term = 0.0  # close enough: only damp the relative motion
        else:
            error_term = error
        output = s.kp * error_term + s.kd * (fish_v - bar_v)
        if not s.hold_moves_right:
            output = -output
        want = True if output > s.hysteresis else False if output < -s.hysteresis else self.holding
        reason = "in hysteresis band" if want == self.holding else "switch"
        if want != self.holding:
            if now - self._last_switch < s.min_input_interval:
                want, reason = self.holding, "min input interval"
            else:
                self._last_switch = now
        self.holding = want
        return ControlDecision(want, obs.fish_x, obs.bar_center, error, output, conf, reason)

    def update(self, obs: Observation | None, frame: Frame | None) -> ControlDecision:
        now = time.perf_counter()
        if self._calibrate_requested or self._calibration:
            self.last = self._calibrate_step(obs, frame, now)
            return self.last
        decision = self.decide(obs, now)
        self.last = decision
        self._record(obs, decision, now)
        if not self.active and not self._debug_noted:
            self._debug_noted = True
            log.info("Reel control is in DEBUG mode: deciding only, no mouse input (press M in the preview)")
        if decision.sticky and self.active and frame is not None and self._focused(frame):
            self._start_spam(frame)
            return decision
        self._stop_spam()
        if not decision.hold or not self.active:
            self._release()
            if self.active and obs is not None and not obs.valid and not decision.hold:
                self._log("lost", "Reel tracking lost (%s); mouse released", obs.reason)
            return decision
        if frame is None or not self._focused(frame):
            self._release()
            self._log("focus", "Roblox is not the active window; not reeling")
            return decision
        self._press(frame)
        return decision

    def stop(self, reason: str) -> None:
        """Minigame over, paused, stopped or tracking reset: release and forget this minigame."""
        self._stop_spam()
        if self._held():
            log.info("Reel control stopped (%s); mouse released", reason)
        self._release()
        if self._trace:
            self._trace[0].close()
            log.info("Reel trace saved to %s", TRACE_PATH)
        self.last = None
        self.reset()

    def _start_spam(self, frame: Frame) -> None:
        if self._spam:
            return
        if not self._positioned:  # first input of this minigame: put the cursor in the game
            self._press(frame)
            self._release()
        stop = threading.Event()
        thread = threading.Thread(target=self._spam_loop, args=(stop,), name="ReelSpam", daemon=True)
        self._spam = (thread, stop)
        thread.start()

    def _spam_loop(self, stop: threading.Event) -> None:
        # Runs between frames, so the click rate isn't limited by the capture rate. time.sleep, not
        # stop.wait: Event waits tick at ~15 ms on Windows, sleep has a high-resolution timer.
        while not stop.is_set():
            period, duty = 2 * self.settings.sticky_interval, self.duty
            if not self.inputs.mouse_down("left"):
                return  # macro paused or stopped: InputManager refuses and has released everything
            time.sleep(period * duty)
            self.inputs.mouse_up("left")
            time.sleep(period * (1 - duty))

    def _stop_spam(self) -> None:
        if self._spam:
            thread, stop = self._spam
            stop.set()
            thread.join(1.0)
            self._spam = None
            self._release()

    def _record(self, obs: Observation | None, d: ControlDecision, now: float) -> None:
        """One row per frame of the current minigame: what tracking saw and what was decided."""
        try:
            if self._trace is None:
                TRACE_PATH.parent.mkdir(exist_ok=True)
                f = TRACE_PATH.open("w", newline="", encoding="utf-8")
                self._trace = (f, csv.writer(f))
                self._trace[1].writerow(["t", "frame_age_ms", "fish_x", "fish_v", "fish_conf", "bar_center", "bar_v",
                                         "bar_conf", "error", "output", "hold", "pressed", "reason",
                                         "bar_left", "bar_right", "tracking"])
            r = lambda v: "" if v is None else round(v, 4)
            self._trace[1].writerow([
                round(now, 4), "" if obs is None else round((now - obs.timestamp) * 1000, 1),
                r(obs and obs.fish_x), r(obs and obs.fish_velocity), r(obs and obs.fish_confidence),
                r(obs and obs.bar_center), r(obs and obs.bar_velocity), r(obs and obs.bar_confidence),
                r(d.error), r(d.output), "spam" if d.sticky else int(d.hold), int(self._held()), d.reason,
                r(obs and obs.bar_left), r(obs and obs.bar_right), "" if obs is None else obs.reason])
        except OSError as e:
            self._log("trace", "Could not write the reel trace: %s", e)

    def request_direction_calibration(self) -> None:
        self._calibrate_requested = True
        log.info("Direction calibration requested: it runs during the next reel minigame while the macro "
                 "is running (F6). It holds the mouse for %.1fs and watches which way the bar moves.",
                 CALIBRATE_HOLD)

    def _calibrate_step(self, obs: Observation | None, frame: Frame | None, now: float) -> ControlDecision:
        waiting = ControlDecision(self._held(), obs and obs.fish_x, obs and obs.bar_center, None, None,
                                  0.0 if obs is None else min(obs.fish_confidence, obs.bar_confidence),
                                  "calibrating hold direction")
        if self.inputs is None or not self.inputs.enabled:
            self._log("calib", "Direction calibration waiting: start the macro with F6")
            return waiting
        if obs is None or not obs.valid or frame is None or not self._focused(frame):
            if self._calibration:
                log.warning("Direction calibration cancelled: tracking lost or Roblox lost focus; "
                            "it will retry")
                self._calibration = None
                self._release()
            return waiting
        if self._calibration is None:
            self._calibrate_requested = False
            self._calibration = (now, obs.bar_center)
            self._press(frame)
            return waiting
        started, start_bar = self._calibration
        if now - started < CALIBRATE_HOLD:
            return waiting
        self._release()
        self._calibration = None
        moved = obs.bar_center - start_bar
        if abs(moved) < CALIBRATE_MIN_MOVE:
            log.warning("Direction calibration inconclusive (bar moved %+.3f); press D again with the bar "
                        "away from the track's edges", moved)
            return waiting
        self.settings = replace(self.settings, hold_moves_right=moved > 0)
        try:
            write_section(SECTION, asdict(self.settings))
        except OSError as e:
            log.warning("Could not save reel control settings: %s", e)
        log.info("Direction calibration: holding moves the bar %s (moved %+.3f)",
                 "RIGHT" if moved > 0 else "LEFT", moved)
        return waiting

    def _focused(self, frame: Frame) -> bool:
        if frame.window.state is not WindowState.ACTIVE:
            return False
        return self._held() or get_window_info(frame.window.hwnd).state is WindowState.ACTIVE

    def _press(self, frame: Frame) -> None:
        if self._held():
            return
        w = frame.window
        if self._positioned:
            pressed = self.inputs.mouse_down("left")
        else:  # first press of this minigame: put the cursor in the game so the hold lands there
            pressed = self.inputs.mouse_down("left", w.x + w.width // 2, w.y + w.height // 2)
            self._positioned = pressed
        if not pressed:
            self._log("refused", "Reel hold refused (macro not running)")

    def _release(self) -> None:
        if self._held():
            self.inputs.mouse_up("left")

    def _held(self) -> bool:
        # Read from InputManager: a pause / stop releases the button without telling this controller.
        return self.inputs is not None and "left" in self.inputs.held[1]

    def _log(self, key: str, message: str, *args) -> None:
        now = time.perf_counter()
        if now - self._last_log.get(key, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[key] = now
            log.warning(message, *args)
