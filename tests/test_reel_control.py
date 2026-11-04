"""Reel controller decisions and input safety, with a fake InputManager. No real inputs are sent."""

import csv
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from controllers.reeling import CALIBRATE_HOLD, ReelControlSettings, ReelController
from core.window_manager import WindowState
from detection.reel_tracking import Observation

SETTINGS = ReelControlSettings(kp=1.0, kd=0.2, deadzone=0.02, hysteresis=0.02, prediction_time=0.0,
                               min_input_interval=0.05, lost_grace=0.0, sticky_distance=0.0)


def obs(fish=0.5, bar=0.5, fish_v=0.0, bar_v=0.0, valid=True):
    return Observation(0.0, fish if valid else None, fish_v, 0.9, None, bar - 0.1, bar + 0.1, bar, bar_v, 0.8,
                       None, "ok" if valid else "fish not found")


class FakeInputs:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.buttons = set()
        self.downs = []

    @property
    def held(self):
        return frozenset(), frozenset(self.buttons)

    def mouse_down(self, button="left", x=None, y=None):
        if not self.enabled:
            return False
        self.buttons.add(button)
        self.downs.append((x, y))
        return True

    def mouse_up(self, button="left"):
        self.buttons.discard(button)


def frame(state=WindowState.ACTIVE):
    return SimpleNamespace(window=SimpleNamespace(state=state, hwnd=1, x=100, y=50, width=800, height=600))


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.c = ReelController(None, SETTINGS)

    def test_hold_when_fish_right_release_when_left(self):
        self.assertTrue(self.c.decide(obs(fish=0.8, bar=0.4), 1.0).hold)
        self.assertFalse(self.c.decide(obs(fish=0.1, bar=0.5), 2.0).hold)

    def test_deadzone_and_hysteresis_keep_the_current_output(self):
        self.c.decide(obs(fish=0.8, bar=0.4), 1.0)  # holding
        d = self.c.decide(obs(fish=0.49, bar=0.5), 2.0)  # tiny error inside the deadzone
        self.assertEqual((d.hold, d.output), (True, 0.0))
        d = self.c.decide(obs(fish=0.47, bar=0.5), 3.0)  # output -0.03: past the hysteresis band
        self.assertFalse(d.hold)
        d = self.c.decide(obs(fish=0.515, bar=0.5, fish_v=0.05), 4.0)  # output +0.01: inside hysteresis band
        self.assertFalse(d.hold)

    def test_brakes_when_the_bar_would_coast_past_the_fish(self):
        c = ReelController(None, replace(SETTINGS, brake_decel=0.8))
        c.decide(obs(fish=0.8, bar=0.3), 1.0)  # holding, bar far left
        self.assertTrue(c.decide(obs(fish=0.6, bar=0.4, bar_v=0.3), 2.0).hold)   # slow: stops in 0.06, keep going
        d = c.decide(obs(fish=0.6, bar=0.4, bar_v=0.6), 3.0)                     # stops in 0.225 > 0.2: let go now
        self.assertEqual((d.hold, d.reason), (False, "braking before the fish"))
        self.assertTrue(c.decide(obs(fish=0.4, bar=0.6, bar_v=-0.6), 4.0).hold)  # same coming from the right
        no_brake = ReelController(None, replace(SETTINGS, brake_decel=0.0))
        no_brake.decide(obs(fish=0.8, bar=0.3), 1.0)
        self.assertTrue(no_brake.decide(obs(fish=0.6, bar=0.4, bar_v=0.6), 3.0).hold)  # 0 turns it off

    def test_no_spam_clicking_while_the_bar_is_moving_fast(self):
        c = ReelController(None, replace(SETTINGS, sticky_distance=0.05))
        self.assertTrue(c.decide(obs(fish=0.52, bar=0.5), 1.0).sticky)
        d = c.decide(obs(fish=0.52, bar=0.5, bar_v=-0.3), 2.0)  # close, but sliding away fast
        self.assertFalse(d.sticky)
        self.assertTrue(d.hold)  # a steady push back, not half-presses

    def test_derivative_and_prediction_brake_before_reaching_the_fish(self):
        self.c.decide(obs(fish=0.8, bar=0.4), 1.0)
        # bar still left of the fish but rushing toward it: the damping term says let go
        self.assertFalse(self.c.decide(obs(fish=0.6, bar=0.55, bar_v=1.0), 2.0).hold)
        predicting = ReelController(None, replace(SETTINGS, kd=0.0, prediction_time=0.1))
        self.assertTrue(predicting.decide(obs(fish=0.5, bar=0.5, fish_v=1.0), 1.0).hold)  # fish heading right

    def test_min_input_interval(self):
        self.assertTrue(self.c.decide(obs(fish=0.8, bar=0.4), 1.0).hold)
        d = self.c.decide(obs(fish=0.1, bar=0.5), 1.01)
        self.assertEqual((d.hold, d.reason), (True, "min input interval"))
        self.assertFalse(self.c.decide(obs(fish=0.1, bar=0.5), 1.1).hold)

    def test_reversed_direction(self):
        c = ReelController(None, replace(SETTINGS, hold_moves_right=False))
        self.assertFalse(c.decide(obs(fish=0.8, bar=0.4), 1.0).hold)
        self.assertTrue(c.decide(obs(fish=0.1, bar=0.5), 2.0).hold)

    def test_lost_tracking_releases_immediately(self):
        self.c.decide(obs(fish=0.8, bar=0.4), 1.0)
        for lost in (None, obs(valid=False)):
            self.assertFalse(self.c.decide(lost, 1.001).hold)  # ignores min_input_interval

    def test_sticky_zone_spams_and_pd_resumes_outside_it(self):
        c = ReelController(None, replace(SETTINGS, sticky_distance=0.05))
        d = c.decide(obs(fish=0.52, bar=0.5), 1.0)
        self.assertTrue(d.sticky)
        self.assertIn("sticky click", d.reason)
        self.assertTrue(c.decide(obs(fish=0.8, bar=0.5), 2.0).hold)   # too far right: steady hold to catch up
        self.assertFalse(c.decide(obs(fish=0.8, bar=0.5), 2.0).sticky)
        self.assertFalse(c.decide(obs(fish=0.2, bar=0.5), 3.0).hold)  # too far left: release

    def test_spam_duty_eases_toward_the_fish_and_mode_has_hysteresis(self):
        c = ReelController(None, replace(SETTINGS, sticky_distance=0.05))
        centered, right, left = (c.decide(obs(fish=f, bar=0.5), 1.0).duty for f in (0.5, 0.52, 0.48))
        self.assertAlmostEqual(centered, 0.5)
        self.assertGreater(right, 0.5)  # fish a bit right: press longer
        self.assertLess(left, 0.5)
        self.assertTrue(c.decide(obs(fish=0.565, bar=0.5), 1.1).sticky)    # past entry, inside exit: stays
        self.assertFalse(c.decide(obs(fish=0.58, bar=0.5), 1.2).sticky)    # past 1.5x: steer
        self.assertFalse(c.decide(obs(fish=0.555, bar=0.5), 1.3).sticky)   # must come back inside entry
        self.assertTrue(c.decide(obs(fish=0.54, bar=0.5), 1.4).sticky)
        reversed_ = ReelController(None, replace(SETTINGS, sticky_distance=0.05, hold_moves_right=False))
        self.assertLess(reversed_.decide(obs(fish=0.52, bar=0.5), 1.0).duty, 0.5)

    def test_brief_dropout_keeps_the_last_output(self):
        c = ReelController(None, replace(SETTINGS, lost_grace=0.15))
        c.decide(obs(fish=0.8, bar=0.4), 1.0)
        self.assertTrue(c.decide(obs(valid=False), 1.05).hold)   # one bad frame: keep holding
        self.assertTrue(c.decide(obs(fish=0.8, bar=0.4), 1.1).hold)
        c.decide(obs(valid=False), 2.0)
        self.assertFalse(c.decide(obs(valid=False), 2.2).hold)   # lost longer than the grace: release


@mock.patch("controllers.reeling.get_window_info", return_value=SimpleNamespace(state=WindowState.ACTIVE))
class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.inputs = FakeInputs()
        self.c = ReelController(self.inputs, SETTINGS)
        self.dir = tempfile.TemporaryDirectory()
        self.trace = Path(self.dir.name) / "reel_trace.csv"
        patcher = mock.patch("controllers.reeling.TRACE_PATH", self.trace)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.c.stop, "test over")  # closes the trace before the folder is removed

    def test_spam_clicks_between_frames_and_stops_when_the_fish_gets_away(self, _):
        clicks = []
        down = self.inputs.mouse_down
        self.inputs.mouse_down = lambda *a, **k: clicks.append(1) or down(*a, **k)
        self.c.settings = replace(SETTINGS, sticky_distance=0.05, sticky_interval=0.005)
        self.c.active = True
        self.c.update(obs(fish=0.52, bar=0.5), frame())
        time.sleep(0.2)  # no new frames meanwhile: the spam thread keeps clicking on its own
        self.assertGreater(len(clicks), 8)
        self.c.update(obs(fish=0.9, bar=0.5), frame())  # fish got away: steer (steady hold)
        n = len(clicks)
        time.sleep(0.05)
        self.assertLessEqual(len(clicks), n + 1)
        self.assertTrue(self.inputs.buttons)
        self.c.update(obs(fish=0.51, bar=0.5), frame())  # back on the fish: spam again
        self.c.stop("reel minigame ended")
        self.assertFalse(self.inputs.buttons)
        self.assertIsNone(self.c._spam)

    def test_trace_has_one_row_per_frame(self, _):
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.c.update(obs(valid=False), frame())
        self.c.stop("reel minigame ended")
        with self.trace.open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["hold"] for r in rows], ["1", "0"])
        self.assertIn("tracking lost", rows[1]["reason"])

    def test_active_by_default(self, _):
        self.assertTrue(ReelController(None, SETTINGS).active)

    def test_debug_mode_sends_nothing(self, _):
        self.c.active = False
        self.assertTrue(self.c.update(obs(fish=0.8, bar=0.4), frame()).hold)
        self.assertEqual(self.inputs.downs, [])

    def test_active_holds_at_game_center_then_releases(self, _):
        self.c.active = True
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.assertEqual(self.inputs.downs, [(500, 350)])
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.assertEqual(len(self.inputs.downs), 1)  # still held, not re-pressed
        self.c.update(obs(valid=False), frame())
        self.assertFalse(self.inputs.buttons)
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.c.stop("reel minigame ended")
        self.assertFalse(self.inputs.buttons)

    def test_never_presses_when_unfocused_or_minimized(self, window_info):
        self.c.active = True
        for state in (WindowState.OPEN, WindowState.MINIMIZED):
            self.c.update(obs(fish=0.8, bar=0.4), frame(state))
        window_info.return_value = SimpleNamespace(state=WindowState.OPEN)  # focus lost after capture
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.assertEqual(self.inputs.downs, [])

    def test_focus_lost_while_holding_releases(self, _):
        self.c.active = True
        self.c.update(obs(fish=0.8, bar=0.4), frame())
        self.c.update(obs(fish=0.8, bar=0.4), frame(WindowState.OPEN))
        self.assertFalse(self.inputs.buttons)

    @mock.patch("controllers.reeling.write_section")
    @mock.patch("controllers.reeling.time.perf_counter")
    def test_direction_calibration(self, clock, write, _):
        clock.return_value = 10.0
        self.c.active = False
        self.c.request_direction_calibration()  # works in debug mode too: the user asked for it
        self.c.update(obs(bar=0.5), frame())
        self.assertTrue(self.inputs.buttons)
        clock.return_value = 10.0 + CALIBRATE_HOLD + 0.01
        self.c.update(obs(bar=0.4), frame())  # bar went left while held
        self.assertFalse(self.inputs.buttons)
        self.assertFalse(self.c.settings.hold_moves_right)
        self.assertFalse(write.call_args.args[1]["hold_moves_right"])


if __name__ == "__main__":
    unittest.main()
