"""Single place that sends inputs, so everything held can be released at once."""

import logging
import threading
import time

import keyboard
import pyautogui
import win32api
import win32con

log = logging.getLogger(__name__)

# pyautogui sleeps 0.1s after every call by default; the macro needs instant inputs.
pyautogui.PAUSE = 0
# Roblox updates hover once per game frame (~16ms at 60 FPS), so a click needs time after the move,
# and a hold long enough to span at least one frame.
MOVE_SETTLE = 0.05
CLICK_HOLD = 0.06


class InputManager:
    """Presses are refused until set_enabled(True). Disabling releases everything under the same lock,
    so a pause or stop can never race with a press and leave a button held."""

    def __init__(self):
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        self._buttons: set[str] = set()
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def held(self) -> tuple[frozenset[str], frozenset[str]]:
        with self._lock:
            return frozenset(self._keys), frozenset(self._buttons)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            if not enabled:
                self._release_all()

    # Track before sending, so a press that fails halfway still gets released.
    def key_down(self, key: str) -> bool:
        with self._lock:
            if not self._enabled:
                return False
            self._keys.add(key)
            keyboard.press(key)
            return True

    def key_up(self, key: str) -> None:
        with self._lock:
            keyboard.release(key)
            self._keys.discard(key)

    def mouse_down(self, button: str = "left", x: int | None = None, y: int | None = None,
                   settle: float = MOVE_SETTLE) -> bool:
        with self._lock:
            if not self._enabled:
                return False
            if x is not None and y is not None:
                self._move(x, y, settle)
            self._buttons.add(button)
            pyautogui.mouseDown(button=button)
            return True

    def mouse_up(self, button: str = "left") -> None:
        with self._lock:
            pyautogui.mouseUp(button=button)
            self._buttons.discard(button)

    def click(self, x: int, y: int, button: str = "left", settle: float = MOVE_SETTLE,
              hold: float = CLICK_HOLD) -> bool:
        """Click at screen coordinates. Returns False (and sends nothing) when disabled."""
        with self._lock:
            if not self._enabled:
                return False
            self._move(x, y, settle)
            self._buttons.add(button)
            try:
                pyautogui.mouseDown(button=button)
                time.sleep(hold)
            finally:
                pyautogui.mouseUp(button=button)
                self._buttons.discard(button)
            return True

    @staticmethod
    def _move(x: int, y: int, settle: float) -> None:
        pyautogui.moveTo(x, y)
        # A cursor teleport is not movement to Roblox; relative move events make it register hover.
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, -1, 0, 0, 0)
        time.sleep(settle)

    def release_all(self) -> None:
        with self._lock:
            self._release_all()

    def _release_all(self) -> None:
        for key in self._keys:
            try:
                keyboard.release(key)
            except Exception:
                log.exception("Failed to release key %r", key)
        for button in self._buttons:
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                log.exception("Failed to release mouse %r", button)
        if self._keys or self._buttons:
            log.info("Released inputs: keys=%s mouse=%s", sorted(self._keys), sorted(self._buttons))
        self._keys.clear()
        self._buttons.clear()
