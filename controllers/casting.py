"""Timed cast: hold the left mouse button in the Roblox window for a fixed time."""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from core.input_controller import InputManager
from core.window_manager import WindowInfo, WindowState, get_window_info

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CastSettings:
    hold_seconds: float = field(default=0.5, metadata={"min": 0.05, "max": 5.0})
    recast_delay: float = field(default=2.0, metadata={"max": 30.0})     # after a catch result, before casting again
    recast_timeout: float = field(default=60.0, metadata={"max": 600.0})  # cast again if no minigame this long
                                                                           # after a cast; 0 = never


def cast(inputs: InputManager, window: WindowInfo, settings: CastSettings, should_continue: Callable[[], bool]) -> bool:
    """Holds the mouse at the client center. Returns False if interrupted (pause, stop, Roblox lost focus)."""
    x, y = window.x + window.width // 2, window.y + window.height // 2
    if not inputs.mouse_down("left", x, y):
        return False
    log.info("Casting: holding %.2fs", settings.hold_seconds)
    try:
        end = time.perf_counter() + settings.hold_seconds
        while time.perf_counter() < end:
            if not should_continue():
                log.info("Cast interrupted")
                return False
            if get_window_info(window.hwnd).state is not WindowState.ACTIVE:
                log.warning("Roblox lost focus during cast; cast cancelled")
                return False
            time.sleep(0.01)
    finally:
        inputs.mouse_up("left")
    log.info("Cast released")
    return True
