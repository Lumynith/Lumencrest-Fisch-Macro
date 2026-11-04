"""Builds the macro (engine, capture, detectors, controllers, hotkeys) and keeps the running objects in
sync with config.json. No UI; the window and the OpenCV preview both work through this."""

import logging
from dataclasses import asdict, dataclass

from controllers.casting import CastSettings
from controllers.reeling import SECTION as REEL_CONTROL, ReelControlSettings, ReelController
from controllers.shaking import ShakeController, ShakeSettings
from core.calibration import load_calibration
from core.config import CONFIG_PATH, load_settings, write_section
from core.hotkey_manager import HotkeyManager
from core.input_controller import InputManager
from core.macro_engine import MacroEngine, MacroState
from core.screen_capture import ScreenCapture
from core.state_machine import FishingCycle
from detection.catch_detector import CatchMatcher, CatchSettings, CatchVerifier
from detection.reel_detector import ReelMatcher, ReelSettings, ReelTracker
from detection.reel_tracking import MinigameTracker, TrackingCalibration, TrackingSettings
from detection.shake_detector import ShakeDetector

log = logging.getLogger(__name__)

HOTKEY_TOGGLE = "f6"  # start / pause / resume
HOTKEY_STOP = "f8"    # emergency stop


@dataclass(frozen=True)
class AppSettings:
    auto_hide: bool = True  # hide the window while the macro runs, restore it on pause / stop


SECTIONS = {"cast": CastSettings, "shake": ShakeSettings, "reel": ReelSettings, "tracking": TrackingSettings,
            "catch": CatchSettings, REEL_CONTROL: ReelControlSettings, "app": AppSettings}


class Runtime:
    def __init__(self):
        self.regions = load_calibration()
        log.info("Loaded calibration: %s", ", ".join(self.regions) or "none")
        self.settings = {name: load_settings(name, cls) for name, cls in SECTIONS.items()}
        s = self.settings
        self.inputs = InputManager()
        self.capture = ScreenCapture()
        self.engine = MacroEngine(self.inputs)
        self.shake = ShakeController(ShakeDetector(self.regions), self.inputs, s["shake"])
        self.reel_matcher = ReelMatcher(self.regions)
        self.tracking = TrackingCalibration(self.regions)
        self.catch = CatchMatcher()
        self.reeler = ReelController(self.inputs, s[REEL_CONTROL])
        self.cycle = FishingCycle(self.capture, self.inputs, s["cast"], self.shake,
                                  ReelTracker(self.reel_matcher, s["reel"]),
                                  MinigameTracker(self.tracking, s["tracking"]),
                                  CatchVerifier(self.catch, s["catch"]),
                                  is_running=lambda: self.engine.state is MacroState.RUNNING, reeler=self.reeler)
        self.engine.cycle = self.cycle
        # The preview's own trackers, so it never disturbs the engine's frame history.
        self.preview_reel = ReelTracker(self.reel_matcher, s["reel"], log_changes=False)
        self.preview_tracker = MinigameTracker(self.tracking, s["tracking"])
        self.hotkeys = HotkeyManager()
        self._mtime = self._config_mtime()

    def start(self) -> None:
        self.engine.start()
        self.hotkeys.register(HOTKEY_TOGGLE, self.engine.toggle)
        self.hotkeys.register(HOTKEY_STOP, self.engine.emergency_stop)
        self.capture.start()

    def shutdown(self) -> None:
        self.hotkeys.unregister_all()
        self.engine.shutdown()  # stops and releases every held input
        self.capture.stop()

    def save(self, section: str, settings) -> None:
        write_section(section, asdict(settings))
        self.reload()

    def poll(self) -> None:
        """Applies hand edits to config.json; call every second or so."""
        if self._config_mtime() != self._mtime:
            self.reload()

    def reload(self) -> None:
        self._mtime = self._config_mtime()
        for name, cls in SECTIONS.items():
            new, old = load_settings(name, cls), self.settings[name]
            if new != old:
                self.settings[name] = new
                self._apply(name, new, old)
                log.info("%s settings updated", name.replace("_", " ").capitalize())
        self.tracking.reload()

    def _apply(self, name: str, new, old) -> None:
        c = self.cycle
        if name == "cast":
            c.cast_settings = new
        elif name == "shake":
            self.shake.settings = new
            if new.auto_click != old.auto_click:
                self.shake.auto_click = new.auto_click
        elif name == "reel":
            c.reel.settings = self.preview_reel.settings = new
        elif name == "tracking":
            c.tracker.settings = self.preview_tracker.settings = new
        elif name == "catch":
            c.verifier.settings = new
        elif name == REEL_CONTROL:
            self.reeler.settings = new

    @staticmethod
    def _config_mtime() -> float | None:
        try:
            return CONFIG_PATH.stat().st_mtime
        except OSError:
            return None
