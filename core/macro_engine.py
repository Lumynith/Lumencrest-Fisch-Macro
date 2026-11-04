"""Macro run state (STOPPED / RUNNING / PAUSED) and the worker thread that acts on it."""

import logging
import threading
import time
from enum import Enum
from typing import Callable

from core.input_controller import InputManager

log = logging.getLogger(__name__)


class MacroState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class MacroEngine:
    """State changes are lock-protected, so hotkey threads can call toggle()/emergency_stop() safely."""

    def __init__(self, inputs: InputManager, cycle=None):
        self.inputs = inputs
        self.cycle = cycle  # has step() and reset(); both only ever run on the engine thread
        self._state = MacroState.STOPPED
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list[Callable[[MacroState], None]] = []

    def add_listener(self, callback: Callable[[MacroState], None]) -> None:
        """Called with the new state after every change, on whichever thread made it (hotkey or engine)."""
        self._listeners.append(callback)

    @property
    def state(self) -> MacroState:
        return self._state

    def toggle(self) -> None:
        """F6: start when stopped, pause when running, resume when paused."""
        with self._lock:
            if self._state is MacroState.RUNNING:
                self._set_state(MacroState.PAUSED)
            else:
                self._set_state(MacroState.RUNNING)

    def emergency_stop(self, reason: str = "emergency stop") -> None:
        """F8: always stops and releases inputs, even if already stopped."""
        with self._lock:
            if self._state is not MacroState.STOPPED:
                log.warning("Stopping macro: %s", reason)
            self._set_state(MacroState.STOPPED)

    def _set_state(self, new: MacroState) -> None:
        changed = new is not self._state
        if changed:
            log.info("Macro state: %s -> %s", self._state.name, new.name)
        self._state = new
        self.inputs.set_enabled(new is MacroState.RUNNING)  # disabling also releases held inputs
        if new is MacroState.RUNNING:
            self._running.set()
        else:
            self._running.clear()
        if changed:
            for callback in self._listeners:
                try:
                    callback(new)
                except Exception:
                    log.exception("Macro state listener failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="MacroEngine", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        self.emergency_stop("shutdown")
        self._shutdown.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def tick(self) -> None:
        """One step of macro work, called repeatedly while RUNNING."""
        if self.cycle:
            self.cycle.step()
        else:
            time.sleep(0.05)

    def _run(self) -> None:
        while not self._shutdown.is_set():
            if not self._running.wait(0.1):
                if self._state is MacroState.STOPPED and self.cycle:
                    self.cycle.reset()  # next start begins with a fresh cast
                continue
            try:
                self.tick()
            except Exception:
                log.exception("Macro error")
                self.emergency_stop("error")
