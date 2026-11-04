"""Thread-safe links from the macro to the UI: state changes and log lines arrive as Qt signals (queued
onto the GUI thread), and the auto-hide logic reacts to them."""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.macro_engine import MacroEngine, MacroState

log = logging.getLogger(__name__)


class MacroBridge(QObject):
    state_changed = Signal(object)  # MacroState; emitted from hotkey / engine threads

    def __init__(self, engine: MacroEngine):
        super().__init__()
        self.engine = engine
        self.started_once = False
        self._run_total = 0.0
        self._run_since: float | None = None
        engine.add_listener(self.state_changed.emit)
        self.state_changed.connect(self._track_time)

    @Slot(object)
    def _track_time(self, _state) -> None:
        running = self.engine.state is MacroState.RUNNING
        now = time.monotonic()
        if running and self._run_since is None:
            self._run_since, self.started_once = now, True
        elif not running and self._run_since is not None:
            self._run_total += now - self._run_since
            self._run_since = None

    def status(self) -> str:
        """Idle until the first start, then Running / Paused / Stopped."""
        return "Idle" if not self.started_once and self.engine.state is MacroState.STOPPED \
            else self.engine.state.name.capitalize()

    def runtime_seconds(self) -> float:
        """Time spent running this session (pauses don't count)."""
        return self._run_total + (time.monotonic() - self._run_since if self._run_since is not None else 0.0)


class LogHub(QObject, logging.Handler):
    """A logging handler that re-emits records as a Qt signal; keeps recent lines for views made later."""

    line = Signal(str, str, str)  # time, level, message

    def __init__(self, keep: int = 400):
        QObject.__init__(self)
        logging.Handler.__init__(self, logging.INFO)
        self.recent: deque[tuple[str, str, str]] = deque(maxlen=keep)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = (datetime.fromtimestamp(record.created).strftime("%H:%M:%S"), record.levelname,
                     record.getMessage())
            self.recent.append(entry)
            self.line.emit(*entry)
        except Exception:  # logging must never take the macro down
            self.handleError(record)


class VisibilityController(QObject):
    """Hides the window when the macro starts or resumes and restores it on pause / stop.

    Acts on the engine's current state, not the queued signal's value, and only on real transitions, so a
    burst of state changes can't make the window flicker or end up out of sync."""

    def __init__(self, engine: MacroEngine, hide_window: Callable[[], None], restore_window: Callable[[], None],
                 enabled: Callable[[], bool], focus_roblox: Callable[[], bool], roblox_active: Callable[[], bool],
                 schedule: Callable[[int, Callable[[], None]], None] = QTimer.singleShot, verify_ms: int = 500):
        super().__init__()
        self.engine = engine
        self.hide_window, self.restore_window = hide_window, restore_window
        self.enabled, self.focus_roblox, self.roblox_active = enabled, focus_roblox, roblox_active
        self.schedule, self.verify_ms = schedule, verify_ms
        self.hidden = False
        self._handled: MacroState | None = MacroState.STOPPED

    @Slot(object)
    def on_state_changed(self, _state=None) -> None:
        state = self.engine.state
        if state is self._handled:
            return  # duplicate or stale event
        self._handled = state
        if state is MacroState.RUNNING:
            if self.enabled() and not self.hidden:
                self.hidden = True
                self.hide_window()
                self.focus_roblox()
                self.schedule(self.verify_ms, self._verify_focus)
        elif self.hidden:
            self.hidden = False
            self.restore_window()

    def _verify_focus(self) -> None:
        if self.hidden and self.engine.state is MacroState.RUNNING and not self.roblox_active():
            log.warning("Couldn't bring Roblox to the front; pausing the macro")
            self.engine.toggle()  # RUNNING -> PAUSED: releases inputs; the state signal restores the window
