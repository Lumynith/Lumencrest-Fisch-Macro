"""Auto-hide decisions, rod profiles and engine state events. No window is shown and no input is sent."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.macro_engine import MacroEngine, MacroState
from core.profiles import delete_profile, list_profiles, load_profile, save_profile
from ui.bridge import VisibilityController


class FakeEngine:
    def __init__(self):
        self.state = MacroState.STOPPED
        self.toggles = 0

    def toggle(self):
        self.toggles += 1
        self.state = MacroState.PAUSED if self.state is MacroState.RUNNING else MacroState.RUNNING


class TestAutoHide(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.window = mock.Mock()
        self.enabled = True
        self.focused = True
        self.pending = []
        self.vis = VisibilityController(self.engine, self.window.hide, self.window.restore,
                                        enabled=lambda: self.enabled, focus_roblox=lambda: self.focused,
                                        roblox_active=lambda: self.focused,
                                        schedule=lambda _ms, fn: self.pending.append(fn))

    def event(self, state):
        self.engine.state = state
        self.vis.on_state_changed(state)

    def test_hides_on_start_and_restores_on_pause_and_stop_once_each(self):
        self.event(MacroState.RUNNING)
        self.vis.on_state_changed(MacroState.RUNNING)  # duplicate event
        self.assertEqual(self.window.hide.call_count, 1)
        self.event(MacroState.PAUSED)
        self.event(MacroState.RUNNING)
        self.event(MacroState.STOPPED)
        self.vis.on_state_changed(MacroState.STOPPED)
        self.assertEqual((self.window.hide.call_count, self.window.restore.call_count), (2, 2))

    def test_stale_queued_event_uses_the_current_state(self):
        self.engine.state = MacroState.PAUSED  # RUNNING then PAUSED happened before the queue was processed
        self.vis.on_state_changed(MacroState.RUNNING)
        self.window.hide.assert_not_called()

    def test_disabled_never_hides(self):
        self.enabled = False
        self.event(MacroState.RUNNING)
        self.event(MacroState.PAUSED)
        self.window.hide.assert_not_called()
        self.window.restore.assert_not_called()

    def test_roblox_not_focused_pauses_and_restores(self):
        self.focused = False
        self.event(MacroState.RUNNING)
        for fn in self.pending:
            fn()
        self.assertIs(self.engine.state, MacroState.PAUSED)
        self.vis.on_state_changed(MacroState.PAUSED)  # the engine's own event for that pause
        self.window.restore.assert_called_once()

    def test_focus_check_does_nothing_if_already_paused(self):
        self.focused = False
        self.event(MacroState.RUNNING)
        self.event(MacroState.PAUSED)  # user paused before the check ran
        for fn in self.pending:
            fn()
        self.assertEqual(self.engine.toggles, 0)


class TestEngineEvents(unittest.TestCase):
    def test_listener_gets_each_change_once(self):
        engine = MacroEngine(mock.Mock())
        seen = []
        engine.add_listener(seen.append)
        engine.add_listener(lambda _: 1 / 0)  # a broken listener must not break the engine
        engine.toggle()
        engine.emergency_stop()
        engine.emergency_stop()  # already stopped: no event
        self.assertEqual(seen, [MacroState.RUNNING, MacroState.STOPPED])


class TestProfiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.config, self.profiles = root / "config.json", root / "profiles"
        self.config.write_text(json.dumps({
            "reel_tracking": {"fish_color": {"hex": "#434B5B", "tolerance": 30}},
            "reel_control": {"kp": 1.0}, "shake": {"threshold": 0.75}}))

    def tearDown(self):
        self.dir.cleanup()

    def test_save_load_delete_round_trip(self):
        save_profile("Rod of the Depths", self.config, self.profiles)
        self.assertEqual(list_profiles(self.profiles), ["Rod of the Depths"])
        config = json.loads(self.config.read_text())
        config["reel_control"]["kp"] = 3.0
        config["shake"]["threshold"] = 0.5
        self.config.write_text(json.dumps(config))
        applied = load_profile("Rod of the Depths", self.config, self.profiles)
        config = json.loads(self.config.read_text())
        self.assertEqual(config["reel_control"]["kp"], 1.0)    # rod setting restored
        self.assertEqual(config["shake"]["threshold"], 0.5)    # not part of a profile: untouched
        self.assertEqual(applied, ["reel_tracking", "reel_control"])
        delete_profile("Rod of the Depths", self.profiles)
        self.assertEqual(list_profiles(self.profiles), [])

    def test_unsafe_names_rejected(self):
        for name in ("", "../config", "a/b", "x" * 41):
            with self.assertRaises(ValueError):
                save_profile(name, self.config, self.profiles)


if __name__ == "__main__":
    unittest.main()
