"""OpenCV live preview: the frame with every detector's overlay, plus the keyboard / mouse tools that
capture reference images, calibrate tracking and pick colors. Opened on demand from the main window."""

import logging
import time
from contextlib import contextmanager

import cv2
import numpy as np

from controllers.reeling import ControlDecision, ReelController
from core.calibration import Region
from core.macro_engine import MacroState
from core.runtime import Runtime
from core.screen_capture import Frame
from core.state_machine import FishingCycle, Phase
from detection.catch_detector import CatchResult
from detection.reel_detector import ReelDetection, ReelState
from detection.reel_tracking import Observation
from detection.shake_detector import ShakeMatch

log = logging.getLogger(__name__)

PREVIEW_WINDOW = "FischMacro Preview"
PREVIEW_MAX_WIDTH = 960
REEL_COLORS = {ReelState.REEL_ACTIVE: (0, 255, 0), ReelState.REEL_INACTIVE: (0, 0, 255),
               ReelState.UNKNOWN: (200, 200, 200)}


def _put_text(image: np.ndarray, text: str, y: int, color: tuple[int, int, int]) -> None:
    cv2.putText(image, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)


def _preview_scale(frame: Frame) -> float:
    return min(1.0, PREVIEW_MAX_WIDTH / frame.image.shape[1])


def draw_preview(frame: Frame, fps: float, macro_state: str) -> np.ndarray:
    h, w = frame.image.shape[:2]
    scale = _preview_scale(frame)
    if scale < 1:
        image = cv2.resize(frame.image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        image = frame.image.copy()
    _put_text(image, f"{fps:.1f} FPS  {w}x{h}  {frame.window.state.value}  macro: {macro_state}", 25, (0, 255, 0))
    return image


def draw_shake(image: np.ndarray, scale: float, region: Region | None, match: ShakeMatch | None,
               threshold: float) -> None:
    if region:
        left, top, w, h = region.to_pixels(image.shape[1], image.shape[0])
        cv2.rectangle(image, (left, top), (left + w, top + h), (0, 200, 255), 1)
    if match:
        color = (0, 255, 0) if match.confidence >= threshold else (160, 160, 160)
        x0, y0 = round((match.x - match.w / 2) * scale), round((match.y - match.h / 2) * scale)
        x1, y1 = round((match.x + match.w / 2) * scale), round((match.y + match.h / 2) * scale)
        cv2.rectangle(image, (x0, y0), (x1, y1), color, 2)
        cv2.putText(image, f"{match.confidence:.2f}", (x0, max(12, y0 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    color, 2, cv2.LINE_AA)


def draw_reel(image: np.ndarray, region: Region | None, reel: ReelDetection) -> None:
    color = REEL_COLORS[reel.state]
    if region:
        left, top, w, h = region.to_pixels(image.shape[1], image.shape[0])
        cv2.rectangle(image, (left, top), (left + w, top + h), color, 2)
    score = "-" if reel.score is None else f"{reel.score:.2f}"
    _put_text(image, f"REEL {reel.state.name} {reel.confidence:.2f}  score={score}  {reel.reason}", 75, color)


def draw_tracking(image: np.ndarray, scale: float, obs: Observation | None, ms: float) -> None:
    if obs is None:
        _put_text(image, "TRACK idle (waiting for REEL_ACTIVE)", 100, (200, 200, 200))
        return
    fmt = lambda v, sign=False: "-" if v is None else (f"{v:+.2f}" if sign else f"{v:.2f}")
    s = lambda v: round(v * scale)
    centers = []
    for box, x, color in ((obs.fish_box, obs.fish_x, (255, 255, 0)), (obs.bar_box, obs.bar_center, (255, 0, 255))):
        if box is None:
            continue
        bx, by, bw, bh = box
        cv2.rectangle(image, (s(bx), s(by)), (s(bx + bw), s(by + bh)), color if x is not None else (128, 128, 128), 2)
        if x is not None:
            cx, cy = s(bx + bw / 2), s(by + bh / 2)
            cv2.line(image, (cx, s(by) - 6), (cx, s(by + bh) + 6), color, 2)  # center marker
            centers.append((cx, cy))
    if len(centers) == 2 and obs.valid:
        cv2.line(image, centers[0], centers[1], (0, 255, 255), 1)
    _put_text(image, f"FISH x={fmt(obs.fish_x)} v={fmt(obs.fish_velocity, True)}/s conf {obs.fish_confidence:.2f}  "
                     f"BAR c={fmt(obs.bar_center)} [{fmt(obs.bar_left)}-{fmt(obs.bar_right)}] "
                     f"v={fmt(obs.bar_velocity, True)}/s conf {obs.bar_confidence:.2f}", 100, (255, 255, 0))
    _put_text(image, f"distance {fmt(obs.distance, True)}  {'VALID' if obs.valid else 'INVALID'}  {obs.reason}  "
                     f"({ms:.1f} ms)", 125, (0, 255, 255) if obs.valid else (0, 0, 255))


def draw_catch(image: np.ndarray, cycle: FishingCycle, scores: dict[str, float | None]) -> None:
    fmt = lambda v: "-" if v is None else f"{v:.2f}"
    last = cycle.last_outcome
    last_text = f"#{last.attempt} {last.result.name}" if last else "none yet"
    counts = cycle.counts
    _put_text(image, f"state: {cycle.phase.name}  last: {last_text}  caught {counts[CatchResult.CATCH_SUCCESS]}  "
                     f"failed {counts[CatchResult.CATCH_FAILED]}  unknown {counts[CatchResult.UNKNOWN]}  "
                     f"| live success {fmt(scores['success'])} failed {fmt(scores['failed'])}", 150, (255, 200, 0))


def draw_control(image: np.ndarray, d: ControlDecision | None, mode: str, source: str) -> None:
    if d is None:
        _put_text(image, f"REEL CONTROL {mode}  idle", 175, (200, 200, 200))
        return
    fmt = lambda v, sign=False: "-" if v is None else (f"{v:+.3f}" if sign else f"{v:.3f}")
    _put_text(image, f"REEL CONTROL {mode} [{source}]  {f'SPAM {d.duty:.0%}' if d.sticky else 'HOLD' if d.hold else 'RELEASE'}  fish {fmt(d.fish_x)}  "
                     f"bar {fmt(d.bar_center)}  error {fmt(d.error, True)}  output {fmt(d.output, True)}  "
                     f"conf {d.confidence:.2f}  {d.reason}", 175, (255, 255, 0) if d.sticky else (0, 255, 0) if d.hold else (0, 140, 255))


class Preview:
    """OpenCV preview, ticked by a Qt timer so it shares the event loop with the main window. Closed until
    open(); the capture tools below work whether or not it is open."""

    def __init__(self, runtime: Runtime, open_regions):
        self.capture = runtime.capture
        self.engine = runtime.engine
        self.open_regions = open_regions  # C key: the region calibration window
        self.shake = runtime.shake
        self.catch = runtime.catch
        self.reel = runtime.preview_reel  # the preview's own trackers, so it never disturbs the engine's history
        self.tracker = runtime.preview_tracker
        self.reeler = runtime.reeler  # the engine's; the preview decides with its own copy when it isn't reeling
        self.preview_control = ReelController(None, self.reeler.settings)
        self.not_before = 0.0  # captures ignore frames older than this (the main window was still on screen)
        self._picking = False
        self.tracking_error: str | None = None  # why the last fish & bar calibration failed, shown in the UI
        self._resume = False
        self.reel_detection = self.reel.update(None)
        self.observation: Observation | None = None
        self.track_ms = 0.0
        self._reel_frame_id = None
        self.waiting = np.zeros((270, 480, 3), np.uint8)
        cv2.putText(self.waiting, "Waiting for Roblox...", (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (255, 255, 255), 2, cv2.LINE_AA)
        self.last_id = None
        self.last_state = None
        self.closed = True

    @property
    def is_open(self) -> bool:
        return not self.closed

    def open(self) -> None:
        if self.closed:
            cv2.namedWindow(PREVIEW_WINDOW, cv2.WINDOW_NORMAL)
            self.closed, self.last_id = False, None
            print("Preview keys: C regions, S shake image, R reel image, T fish/bar tracking, P / B fish / bar "
                  "color, G / F catch success / failed image, A auto-click, M reel input, D hold direction, Q close")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            cv2.destroyWindow(PREVIEW_WINDOW)

    def suspend(self) -> None:
        """Closes it while the macro runs hidden (it would cover Roblox and end up in the capture)."""
        self._resume = self.is_open
        self.close()

    def resume(self) -> None:
        if self._resume:
            self._resume = False
            self.open()

    def tick(self) -> None:
        if self.closed or self._picking:
            return  # the eyedropper owns the window; waitKey can dispatch this timer while it runs
        self.preview_control.settings = self.reeler.settings
        frame = self.capture.get_frame()
        state = self.engine.state.name
        if frame is None:
            if self.last_id != 0:
                cv2.imshow(PREVIEW_WINDOW, self.waiting)
                self.last_id = 0
        elif frame.frame_id != self.last_id or state != self.last_state:
            cv2.imshow(PREVIEW_WINDOW, self._render(frame, state))
            self.last_id = frame.frame_id
        self.last_state = state
        key = cv2.waitKey(1) & 0xFF
        if key == ord("c"):
            self.open_regions()
        elif key == ord("s"):
            self.capture_shake_template()
        elif key == ord("r"):
            self.capture_reel_template()
        elif key == ord("t"):
            self.calibrate_tracking()
        elif key in (ord("p"), ord("b")):
            self.pick_track_color("fish" if key == ord("p") else "bar")
        elif key in (ord("g"), ord("f")):
            self.capture_catch_template("success" if key == ord("g") else "failed")
        elif key == ord("m"):
            self.reeler.active = not self.reeler.active
            log.info("Reel control %s", "ACTIVE: holds / releases the mouse" if self.reeler.active
                     else "DEBUG: decisions only, no input")
            self.last_id = None
        elif key == ord("d"):
            self.reeler.request_direction_calibration()
        elif key == ord("a"):
            self.shake.auto_click = not self.shake.auto_click
            log.info("Shake auto-click %s", "ON" if self.shake.auto_click else "OFF (debug mode)")
            self.last_id = None
        elif key in (27, ord("q")) or cv2.getWindowProperty(PREVIEW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            self.close()

    def _render(self, frame: Frame, state: str) -> np.ndarray:
        image = draw_preview(frame, self.capture.fps, state)
        # Shake and catch checks cost ~50 ms a frame and can't happen mid-minigame; skipping them keeps
        # the CPU for capture and the reel controller.
        reeling = self.reel_detection.state is ReelState.REEL_ACTIVE
        match = None if reeling else self.shake.detector.detect(frame)  # display only; the engine detects itself
        threshold = self.shake.settings.threshold
        draw_shake(image, _preview_scale(frame), self.shake.detector.regions.get("SHAKE"), match, threshold)
        auto = "ON" if self.shake.auto_click else "OFF (debug)"
        score = f"{match.confidence:.2f}" if match else "-"
        _put_text(image, f"phase: {self.engine.cycle.phase.name}  auto-click: {auto}  shake: {score} "
                         f"(need {threshold:.2f})", 50, (0, 200, 255))
        if frame.frame_id != self._reel_frame_id:  # each frame counts once toward streaks and velocities
            self.reel_detection = self.reel.update(frame)
            if self.reel_detection.state is ReelState.REEL_ACTIVE:
                started = time.perf_counter()
                self.observation = self.tracker.observe(frame)
                self.track_ms = (time.perf_counter() - started) * 1000
            elif self.observation is not None:
                self.observation = None
                self.tracker.reset()
                self.preview_control.reset()
            self._reel_frame_id = frame.frame_id
        draw_reel(image, self.reel.matcher.regions.get("REEL"), self.reel_detection)
        draw_tracking(image, _preview_scale(frame), self.observation, self.track_ms)
        scores = {"success": None, "failed": None} if reeling else self.catch.score(frame)
        draw_catch(image, self.engine.cycle, scores)  # live scores, for tuning the threshold
        running, phase = self.engine.state is MacroState.RUNNING, self.engine.cycle.phase
        if not running:
            mode = "NO INPUT: macro not running (F6)"
        elif not self.reeler.active:
            mode = "NO INPUT: DEBUG mode (M = ACTIVE)"
        elif phase is not Phase.REELING:
            mode = f"ACTIVE, waiting for the engine to see the minigame (phase {phase.name})"
        else:
            mode = "ACTIVE: sending input"
        if running and phase is Phase.REELING and self.reeler.last:
            draw_control(image, self.reeler.last, mode, "engine")
        else:
            last = self.preview_control.decide(self.observation, time.perf_counter()) if self.observation else None
            draw_control(image, last, mode, "preview")
        return image

    def _frame(self, what: str) -> Frame | None:
        frame = self.capture.get_frame(max_age=1.0)
        if frame is None or frame.timestamp < self.not_before:
            log.warning("Could not %s: no current frame (is Roblox visible?)", what)
            return None
        return frame

    @contextmanager
    def _window(self):
        """The capture tools draw in the preview window; open it for them and close it again after."""
        was_open = self.is_open
        cv2.namedWindow(PREVIEW_WINDOW, cv2.WINDOW_NORMAL)
        try:
            yield
        finally:
            if not was_open:
                cv2.destroyWindow(PREVIEW_WINDOW)
            self.last_id = None  # redraw the live preview afterwards

    def capture_reel_template(self) -> None:
        try:
            self.reel.matcher.capture_template(self._frame("save the reel image"))
        except (ValueError, OSError) as e:
            log.warning("Could not save reel template: %s", e)

    def pick_track_color(self, part: str) -> None:
        features = self.tracker.calibration.features
        if features is None:
            log.warning("Calibrate tracking (T) before picking colors")
            return
        frame = self._frame(f"pick the {part} color")
        if frame is None:
            return
        two = features.two_color_bar
        what = {"fish": "the middle of the FISH line", "bar2": "the RIGHT side of the CONTROL BAR",
                "bar": "the LEFT side of the CONTROL BAR" if two else "a plain part of the CONTROL BAR"}[part]
        with self._window():
            color = self._eyedropper(frame, f"Click {what} (right-click or Esc cancels).")
        if color is None:
            log.info("Color pick cancelled")
            return
        try:
            self.tracker.calibration.set_color(part, color)
        except (ValueError, OSError) as e:
            log.warning("Could not set the %s color: %s", part, e)

    def _eyedropper(self, frame: Frame, prompt: str) -> str | None:
        """Shows the frozen frame without any HUD; returns the #RRGGBB of the clicked pixel, or None."""
        scale = _preview_scale(frame)
        h, w = frame.image.shape[:2]
        shown = cv2.resize(frame.image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        mouse = {"pos": None, "click": None, "cancel": False}

        def on_mouse(event, x, y, _flags, _param):
            mouse["pos"] = (x, y)
            if event == cv2.EVENT_LBUTTONDOWN:
                mouse["click"] = (x, y)
            elif event == cv2.EVENT_RBUTTONDOWN:
                mouse["cancel"] = True

        def color_at(x, y):  # the real pixel, not the downscaled preview's blend
            b, g, r = frame.image[min(h - 1, int(y / scale)), min(w - 1, int(x / scale))]
            return (int(b), int(g), int(r)), f"#{r:02X}{g:02X}{b:02X}"

        print(prompt)
        self._picking = True
        cv2.setMouseCallback(PREVIEW_WINDOW, on_mouse)
        try:
            while True:
                if mouse["click"]:
                    return color_at(*mouse["click"])[1]
                key = cv2.waitKey(15) & 0xFF
                if mouse["cancel"] or key == 27 or cv2.getWindowProperty(PREVIEW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    return None
                image = shown.copy()
                _put_text(image, prompt, 28, (0, 230, 255))
                if mouse["pos"]:
                    x, y = mouse["pos"]
                    bgr, hex_color = color_at(x, y)
                    cv2.drawMarker(image, (x, y), (255, 255, 255), cv2.MARKER_CROSS, 12, 1)
                    sx = x + 14 if x + 120 < image.shape[1] else x - 120  # swatch stays on screen
                    sy = max(0, min(image.shape[0] - 24, y + 14))
                    cv2.rectangle(image, (sx, sy), (sx + 22, sy + 22), bgr, -1)
                    cv2.rectangle(image, (sx, sy), (sx + 22, sy + 22), (0, 0, 0), 1)
                    cv2.putText(image, hex_color, (sx + 28, sy + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3,
                                cv2.LINE_AA)
                    cv2.putText(image, hex_color, (sx + 28, sy + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (255, 255, 255), 1, cv2.LINE_AA)
                cv2.imshow(PREVIEW_WINDOW, image)
        finally:
            cv2.setMouseCallback(PREVIEW_WINDOW, lambda *_: None)
            self._picking = False
            self.last_id = None  # redraw the live preview afterwards

    def capture_catch_template(self, kind: str) -> None:
        frame = self._frame(f"save the {kind} image")
        if frame is None:
            return
        with self._window():
            box = self._select_box(frame, f"Drag a box around the catch {kind.upper()} indicator")
        if box is None:
            log.info("Catch template capture cancelled")
            return
        try:
            self.catch.capture_template(frame, box, kind)
        except (ValueError, OSError) as e:
            log.warning("Could not save %s template: %s", kind, e)

    def _select_box(self, frame: Frame, prompt: str) -> tuple[int, int, int, int] | None:
        """Lets the user drag a box on the frozen frame; returns it in client pixels, or None if cancelled."""
        scale = _preview_scale(frame)
        h, w = frame.image.shape[:2]
        shown = cv2.resize(frame.image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        print(f"{prompt}, then press Enter or Space (C cancels).")
        _put_text(shown, prompt, 28, (0, 230, 255))  # on the image: the console isn't visible with the UI
        _put_text(shown, "Then press ENTER or SPACE to confirm.  C cancels.", 54, (255, 255, 255))
        x, y, bw, bh = cv2.selectROI(PREVIEW_WINDOW, shown, showCrosshair=False)
        self.last_id = None  # redraw the live preview afterwards
        if bw == 0 or bh == 0:
            return None
        return round(x / scale), round(y / scale), round(bw / scale), round(bh / scale)

    def capture_shake_template(self) -> None:
        frame = self._frame("save the shake image")
        if frame is None:
            return
        with self._window():
            box = self._select_box(frame, "Drag a box tightly around the SHAKE button")
        if box is None:
            log.info("Shake template capture cancelled")
            return
        try:
            self.shake.detector.capture_template(frame, box)
        except (ValueError, OSError) as e:
            log.warning("Could not save shake template: %s", e)

    def calibrate_tracking(self) -> None:
        frame = self._frame("calibrate tracking")
        if frame is None:
            return
        engine_reel = self.engine.cycle.reel_detection
        if ReelState.REEL_ACTIVE not in (self.reel_detection.state, engine_reel and engine_reel.state):
            log.warning("The reel minigame doesn't look active; make sure the fish and bar are on screen")
        with self._window():
            fish = self._select_box(frame, "Drag a box tightly around the FISH indicator")
            bar = fish and self._select_box(frame, "Drag a box around the whole player CONTROL BAR")
        if not bar:
            self.tracking_error = "Calibration cancelled: drag each box, then press Enter or Space to confirm it."
            log.info("Tracking calibration cancelled")
            return
        try:
            self.tracker.calibration.calibrate(frame, fish, bar)
            self.tracking_error = None
        except (ValueError, OSError) as e:
            self.tracking_error = f"Calibration failed: {e}."
            log.warning("Could not calibrate tracking: %s", e)
