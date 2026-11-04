"""Reel minigame detection and fishing-cycle phase checks on synthetic frames. No real inputs are sent."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from controllers.casting import CastSettings
from controllers.shaking import ShakeResult
from core.calibration import Region
from core.screen_capture import Frame
from core.state_machine import FishingCycle, Phase
from core.window_manager import CLOSED, WindowState
from detection.reel_detector import ReelDetection, ReelMatcher, ReelSettings, ReelState, ReelTracker

REEL = Region(0.25, 0.8, 0.5, 0.1)
SETTINGS = ReelSettings(confirm_frames=3, inactive_confirm_frames=3)


def reel_frame(show=True, slider=0.3, background=(40, 40, 40), size=(800, 600), seed=0) -> Frame:
    noise = cv2.GaussianBlur(np.random.default_rng(seed).integers(0, 80, (size[1], size[0], 3), np.uint8), (0, 0), 6)
    image = cv2.add(np.full((size[1], size[0], 3), background, np.uint8), noise)
    if show:
        left, top, w, h = REEL.to_pixels(size[0], size[1])
        x0, y0, x1, y1 = left + w // 20, top + h // 4, left + w - w // 20, top + h - h // 4
        image[y0:y1, x0:x1] = (image[y0:y1, x0:x1] * 0.4).astype(np.uint8)  # translucent dark bar
        cv2.rectangle(image, (x0, y0), (x1, y1), (245, 245, 245), max(1, h // 30))
        sx = x0 + int((x1 - x0 - w // 6) * slider)
        cv2.rectangle(image, (sx, y0 + 3), (sx + w // 6, y1 - 3), (230, 230, 230), -1)  # player slider
    return Frame(image, time.perf_counter(), 1, CLOSED)


class TestReelDetection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.matcher = ReelMatcher({"REEL": REEL}, Path(self.dir.name))
        self.tracker = ReelTracker(self.matcher, SETTINGS, log_changes=False)

    def tearDown(self):
        self.dir.cleanup()

    def feed(self, frames) -> list[ReelDetection]:
        return [self.tracker.update(f) for f in frames]

    def test_unknown_without_templates(self):
        detection = self.tracker.update(reel_frame())
        self.assertIs(detection.state, ReelState.UNKNOWN)
        self.assertEqual(detection.reason, "no reel templates")
        self.assertIs(self.tracker.update(None).state, ReelState.UNKNOWN)

    def test_active_on_any_background_and_slider_position_then_inactive(self):
        self.matcher.capture_template(reel_frame())
        frames = [reel_frame(slider=s, background=b, seed=i)
                  for i, (s, b) in enumerate([(0.1, (230, 170, 90)), (0.6, (60, 150, 60)), (0.9, (200, 200, 200))])]
        detections = self.feed(frames)
        self.assertEqual([d.state for d in detections], [ReelState.UNKNOWN, ReelState.UNKNOWN, ReelState.REEL_ACTIVE])
        for d in detections:
            self.assertGreaterEqual(d.score, SETTINGS.active_threshold)
        gone = self.feed([reel_frame(show=False, background=b, seed=10 + i)
                          for i, b in enumerate([(40, 40, 40), (230, 170, 90), (200, 200, 200)])])
        self.assertEqual(gone[-1].state, ReelState.REEL_INACTIVE)
        self.assertIs(gone[0].state, ReelState.REEL_ACTIVE)  # one frame is not enough to drop it
        for d in gone:
            self.assertLess(d.score, SETTINGS.inactive_threshold)

    def test_other_resolution(self):
        self.matcher.capture_template(reel_frame())
        score, _ = self.matcher.score(reel_frame(size=(1200, 900), slider=0.5, seed=3))
        self.assertGreaterEqual(score, SETTINGS.active_threshold)

    def test_bad_inputs_are_safe(self):
        with self.assertRaises(ValueError):
            self.matcher.capture_template(None)
        with self.assertRaises(ValueError):
            self.matcher.capture_template(Frame(np.full((600, 800, 3), 50, np.uint8), 0, 1, CLOSED))  # flat
        self.matcher.capture_template(reel_frame())
        self.matcher.regions.clear()
        self.assertEqual(self.matcher.score(reel_frame()), (None, "no REEL region"))


def fake_frame(frame_id):
    return SimpleNamespace(frame_id=frame_id, window=SimpleNamespace(state=WindowState.ACTIVE))


class TestFishingCyclePhases(unittest.TestCase):
    def setUp(self):
        self.ids = iter(range(1, 1000))
        self.capture = mock.Mock()
        self.capture.get_frame.side_effect = lambda: fake_frame(next(self.ids))
        self.shake, self.reel, self.tracker = mock.Mock(), mock.Mock(), mock.Mock()
        self.shake.step.return_value = ShakeResult.SHAKING
        self.tracker.observe.return_value.valid = False  # tracking doesn't see the minigame unless a test says so
        self.cycle = FishingCycle(self.capture, mock.Mock(), CastSettings(), self.shake, self.reel, self.tracker,
                                  None, lambda: True)  # no catch verifier: reel end goes straight to SHAKING

    def reel_state(self, state):
        self.reel.update.return_value = ReelDetection(state, 0.9, 0.9, "test")

    def test_reel_active_stops_shaking(self):
        self.cycle.phase = Phase.SHAKING
        self.reel_state(ReelState.REEL_ACTIVE)
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.REELING)
        self.tracker.reset.assert_called_once()
        self.cycle.step()
        self.shake.step.assert_not_called()
        self.assertIs(self.cycle.observation, self.tracker.observe.return_value)  # tracking only while reeling

    @mock.patch("core.state_machine.time.sleep")
    @mock.patch("core.state_machine.time.monotonic")
    @mock.patch("core.state_machine.cast", return_value=True)
    def test_reel_end_casts_again_after_the_delay(self, cast, clock, _sleep):
        clock.return_value = 100.0
        self.cycle.phase = Phase.REELING
        self.reel_state(ReelState.UNKNOWN)
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.REELING)
        self.reel_state(ReelState.REEL_INACTIVE)
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.CASTING)
        self.cycle.step()  # within the recast delay: the catch popup is still clearing
        cast.assert_not_called()
        clock.return_value = 100.0 + CastSettings().recast_delay + 0.01
        self.cycle.step()
        cast.assert_called_once()
        self.assertIs(self.cycle.phase, Phase.SHAKING)

    @mock.patch("core.state_machine.time.monotonic")
    def test_recasts_when_no_minigame_follows_a_cast(self, clock):
        self.cycle.phase, self.cycle._cast_at = Phase.SHAKING, 100.0
        self.reel_state(ReelState.REEL_INACTIVE)
        clock.return_value = 100.0 + CastSettings().recast_timeout - 1
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.SHAKING)  # a fish may still bite
        clock.return_value = 100.0 + CastSettings().recast_timeout + 1
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.CASTING)

    def test_reeling_continues_while_tracking_sees_the_minigame(self):
        reeler = mock.Mock()
        self.cycle.reeler = reeler
        self.cycle.phase = Phase.REELING
        self.reel_state(ReelState.REEL_INACTIVE)  # the reference image stopped matching
        self.tracker.observe.return_value.valid = True  # but the fish and bar are still tracked
        for _ in range(3):
            self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.REELING)
        self.assertEqual(reeler.update.call_count, 3)
        self.tracker.observe.return_value.valid = False  # tracking lost too: the minigame is over
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.CASTING)
        reeler.stop.assert_called_with("reel minigame ended")

    def test_reel_controller_runs_only_while_reeling(self):
        reeler = mock.Mock()
        self.cycle.reeler = reeler
        self.cycle.phase = Phase.SHAKING
        self.reel_state(ReelState.REEL_INACTIVE)
        self.cycle.step()
        reeler.update.assert_not_called()
        self.reel_state(ReelState.REEL_ACTIVE)
        self.cycle.step()
        self.cycle.step()
        reeler.update.assert_called_once()
        self.reel_state(ReelState.REEL_INACTIVE)
        self.cycle.step()
        reeler.stop.assert_called_with("reel minigame ended")
        self.cycle.reset()
        reeler.stop.assert_called_with("macro stopped")

    def test_shake_timeout_keeps_watching(self):
        self.cycle.phase = Phase.SHAKING
        self.reel_state(ReelState.REEL_INACTIVE)
        self.shake.step.return_value = ShakeResult.TIMEOUT
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.SHAKING)
        self.shake.reset.assert_called_once()

    @mock.patch("core.state_machine.cast")
    @mock.patch("core.state_machine.time.sleep")
    def test_no_cast_while_reel_active(self, _sleep, cast):
        self.reel_state(ReelState.REEL_ACTIVE)
        self.cycle.step()
        cast.assert_not_called()
        self.reel_state(ReelState.REEL_INACTIVE)
        cast.return_value = True
        self.cycle.step()
        cast.assert_called_once()
        self.assertIs(self.cycle.phase, Phase.SHAKING)


if __name__ == "__main__":
    unittest.main()
