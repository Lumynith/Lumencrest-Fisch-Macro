"""Global hotkeys via the keyboard library's low-level hook (works while Roblox is focused)."""

import logging
import time
from typing import Callable

import keyboard

log = logging.getLogger(__name__)


class HotkeyManager:
    def __init__(self, cooldown: float = 0.3):
        self.cooldown = cooldown
        self._hooks: dict[str, Callable] = {}

    def register(self, key: str, callback: Callable[[], None]) -> bool:
        """Fire callback once per physical press. Returns False if key is already registered."""
        key = key.lower()
        if key in self._hooks:
            log.warning("Hotkey %s already registered; ignoring", key.upper())
            return False
        held = False
        last = 0.0

        def on_event(event):
            nonlocal held, last
            if event.event_type == keyboard.KEY_UP:
                held = False
                return
            if held:  # Windows auto-repeat while the key is held
                return
            held = True
            now = time.monotonic()
            if now - last < self.cooldown:
                return
            last = now
            log.info("Hotkey %s pressed", key.upper())
            try:
                callback()
            except Exception:
                log.exception("Hotkey %s callback failed", key.upper())

        self._hooks[key] = keyboard.hook_key(key, on_event)
        log.info("Registered hotkey %s", key.upper())
        return True

    def unregister_all(self) -> None:
        for key, handle in self._hooks.items():
            try:
                keyboard.unhook(handle)
            except (KeyError, ValueError):
                pass
            log.info("Unregistered hotkey %s", key.upper())
        self._hooks.clear()
