"""Macro state, input release and hotkey debounce checks. No real inputs are sent."""

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from core.hotkey_manager import HotkeyManager
from core.input_controller import InputManager
from core.macro_engine import MacroEngine, MacroState


class TestMacroEngine(unittest.TestCase):
    def setUp(self):
        self.inputs = mock.Mock()
        self.engine = MacroEngine(self.inputs)

    def test_toggle_cycle_and_emergency_stop(self):
        e = self.engine
        enabled = self.inputs.set_enabled
        e.toggle(); self.assertIs(e.state, MacroState.RUNNING); enabled.assert_called_with(True)
        e.toggle(); self.assertIs(e.state, MacroState.PAUSED); enabled.assert_called_with(False)
        e.toggle(); self.assertIs(e.state, MacroState.RUNNING); enabled.assert_called_with(True)
        e.emergency_stop(); self.assertIs(e.state, MacroState.STOPPED); enabled.assert_called_with(False)

    def test_error_in_tick_stops_and_releases(self):
        self.engine.tick = mock.Mock(side_effect=RuntimeError("boom"))
        self.engine.start()
        self.engine.toggle()
        deadline = time.monotonic() + 1
        while self.engine.state is MacroState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        self.engine.shutdown()
        self.assertIs(self.engine.state, MacroState.STOPPED)
        self.inputs.set_enabled.assert_called_with(False)

    def test_stop_resets_cycle_and_running_steps_it(self):
        self.engine.cycle = mock.Mock()
        self.engine.cycle.step.side_effect = lambda: time.sleep(0.01)
        self.engine.start()
        self.engine.toggle()
        time.sleep(0.1)
        self.engine.emergency_stop()
        time.sleep(0.25)
        self.engine.shutdown()
        self.engine.cycle.step.assert_called()
        self.engine.cycle.reset.assert_called()


@mock.patch("core.input_controller.win32api", mock.Mock())
@mock.patch("core.input_controller.pyautogui")
@mock.patch("core.input_controller.keyboard")
class TestInputManager(unittest.TestCase):
    def test_disabling_releases_everything_held(self, kb, pag):
        inputs = InputManager()
        inputs.set_enabled(True)
        inputs.key_down("e")
        inputs.mouse_down("left")
        inputs.set_enabled(False)
        kb.release.assert_called_once_with("e")
        pag.mouseUp.assert_called_once_with(button="left")
        self.assertEqual(inputs.held, (frozenset(), frozenset()))

    def test_disabled_sends_nothing(self, kb, pag):
        inputs = InputManager()
        self.assertFalse(inputs.click(10, 10))
        self.assertFalse(inputs.mouse_down("left"))
        self.assertFalse(inputs.key_down("e"))
        pag.mouseDown.assert_not_called()
        kb.press.assert_not_called()

    def test_click_moves_then_presses_and_releases(self, kb, pag):
        inputs = InputManager()
        inputs.set_enabled(True)
        self.assertTrue(inputs.click(100, 200))
        pag.moveTo.assert_called_with(100, 200)
        pag.mouseDown.assert_called_once_with(button="left")
        pag.mouseUp.assert_called_once_with(button="left")
        self.assertEqual(inputs.held, (frozenset(), frozenset()))


class TestHotkeyManager(unittest.TestCase):
    def _register(self, manager, callback):
        with mock.patch("core.hotkey_manager.keyboard.hook_key") as hook:
            self.assertTrue(manager.register("F6", callback))
            return hook.call_args.args[1]

    def test_duplicate_registration_rejected(self):
        manager = HotkeyManager()
        with mock.patch("core.hotkey_manager.keyboard.hook_key"):
            self.assertTrue(manager.register("f6", lambda: None))
            self.assertFalse(manager.register("F6", lambda: None))

    def test_held_key_and_cooldown_fire_once(self):
        callback = mock.Mock()
        on_event = self._register(HotkeyManager(cooldown=10), callback)
        down, up = SimpleNamespace(event_type="down"), SimpleNamespace(event_type="up")
        for event in (down, down, down, up, down, up):  # auto-repeat, then a quick double-tap
            on_event(event)
        self.assertEqual(callback.call_count, 1)

    def test_separate_presses_fire_each_time(self):
        callback = mock.Mock()
        on_event = self._register(HotkeyManager(cooldown=0), callback)
        for event_type in ("down", "up", "down", "up"):
            on_event(SimpleNamespace(event_type=event_type))
        self.assertEqual(callback.call_count, 2)


if __name__ == "__main__":
    unittest.main()
