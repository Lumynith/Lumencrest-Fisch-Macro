"""Catch success / failure detection and the VERIFYING phase, on synthetic frames."""

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
from core.screen_capture import Frame
from core.state_machine import FishingCycle, Phase
from core.window_manager import CLOSED, WindowState
from detection.catch_detector import CatchMatcher, CatchOutcome, CatchResult, CatchSettings, CatchVerifier
from detection.reel_detector import ReelDetection, ReelState

SUCCESS_BOX = (330, 200, 140, 50)  # where the "caught" popup appears in the 800x600 test frames
FAILED_BOX = (340, 420, 120, 40)


def popup(text: str, size) -> np.ndarray:
    image = np.full((size[1], size[0], 3), (40, 160, 60) if text == "CAUGHT" else (40, 40, 200), np.uint8)
    cv2.putText(image, text, (8, size[1] * 2 // 3), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return image


def scene(success=False, failed=False, size=(800, 600), shift=(0, 0), seed=0) -> Frame:
    rng = np.random.default_rng(seed)
    image = cv2.GaussianBlur(rng.integers(0, 255, (size[1], size[0], 3), np.uint8), (0, 0), 4)
    scale = size[1] / 600
    for show, (x, y, w, h), text in ((success, SUCCESS_BOX, "CAUGHT"), (failed, FAILED_BOX, "LOST")):
        if show:
            x, y = int((x + shift[0]) * scale), int((y + shift[1]) * scale)
            w, h = int(w * scale), int(h * scale)
            image[y:y + h, x:x + w] = cv2.resize(popup(text, (140 if text == "CAUGHT" else 120, 50 if text == "CAUGHT" else 40)), (w, h))
    return Frame(image, time.perf_counter(), seed, CLOSED)


class CatchTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.matcher = CatchMatcher(Path(self.dir.name))
        self.settings = CatchSettings(confirm_frames=3, verify_timeout=0.2)

    def tearDown(self):
        self.dir.cleanup()

    def capture_both(self):
        self.matcher.capture_template(scene(success=True), SUCCESS_BOX, "success")
        self.matcher.capture_template(scene(failed=True), FAILED_BOX, "failed")


class TestCatchMatcher(CatchTestCase):
    def test_scores_each_kind_near_where_it_was_captured(self):
        self.capture_both()
        s = self.matcher.score(scene(success=True, shift=(40, -20), seed=1))  # popup a bit off its usual spot
        self.assertGreater(s["success"], 0.9)
        self.assertLess(s["failed"], 0.5)
        f = self.matcher.score(scene(failed=True, seed=2))
        self.assertGreater(f["failed"], 0.9)
        self.assertLess(f["success"], 0.5)
        n = self.matcher.score(scene(seed=3))
        self.assertLess(max(n.values()), 0.5)

    def test_other_resolution(self):
        self.capture_both()
        self.assertGreater(self.matcher.score(scene(success=True, size=(1200, 900), seed=4))["success"], 0.85)

    def test_bad_captures_rejected(self):
        flat = Frame(np.full((600, 800, 3), 90, np.uint8), 0, 1, CLOSED)
        for frame, box, kind in ((None, SUCCESS_BOX, "success"), (flat, SUCCESS_BOX, "success"),
                                 (scene(), (790, 10, 50, 50), "success"), (scene(), SUCCESS_BOX, "maybe")):
            with self.assertRaises(ValueError):
                self.matcher.capture_template(frame, box, kind)


class TestCatchVerifier(CatchTestCase):
    def test_confirms_after_consistent_frames_once(self):
        self.capture_both()
        verifier = CatchVerifier(self.matcher, self.settings)
        verifier.start(7)
        results = [verifier.update(scene(failed=True, seed=i)) for i in range(3)]
        self.assertEqual(results[:2], [None, None])
        self.assertEqual((results[2].attempt, results[2].result), (7, CatchResult.CATCH_FAILED))
        self.assertIsNone(verifier.update(scene(failed=True, seed=9)))  # no second event for the same attempt

    def test_flicker_restarts_the_streak(self):
        self.capture_both()
        verifier = CatchVerifier(self.matcher, self.settings)
        verifier.start(1)
        for frame in (scene(success=True, seed=1), scene(success=True, seed=2), scene(seed=3)):
            self.assertIsNone(verifier.update(frame))
        for i in range(2):
            self.assertIsNone(verifier.update(scene(success=True, seed=4 + i)))
        self.assertIs(verifier.update(scene(success=True, seed=6)).result, CatchResult.CATCH_SUCCESS)

    def test_unknown_on_timeout_or_without_templates(self):
        verifier = CatchVerifier(self.matcher, self.settings)
        verifier.start(1)
        self.assertIs(verifier.update(scene()).result, CatchResult.UNKNOWN)  # no templates: immediately
        self.capture_both()
        verifier.start(2)
        self.assertIsNone(verifier.update(scene(seed=1)))
        time.sleep(0.25)
        outcome = verifier.update(scene(seed=2))
        self.assertIs(outcome.result, CatchResult.UNKNOWN)
        self.assertIn("within", outcome.reason)


def fake_frame(frame_id):
    return SimpleNamespace(frame_id=frame_id, window=SimpleNamespace(state=WindowState.ACTIVE))


class TestVerifyingPhase(unittest.TestCase):
    def setUp(self):
        ids = iter(range(1, 1000))
        capture = mock.Mock()
        capture.get_frame.side_effect = lambda: fake_frame(next(ids))
        self.shake, self.reel, self.tracker, self.verifier = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
        self.shake.step.return_value = ShakeResult.SHAKING
        self.verifier.update.return_value = None
        self.tracker.observe.return_value.valid = False  # tracking doesn't keep the minigame alive here
        self.cycle = FishingCycle(capture, mock.Mock(), CastSettings(), self.shake, self.reel, self.tracker,
                                  self.verifier, lambda: True)
        self.cycle.phase = Phase.SHAKING

    def set_reel(self, state):
        self.reel.update.return_value = ReelDetection(state, 0.9, 0.9, "test")

    def finish_reel(self):
        self.set_reel(ReelState.REEL_ACTIVE)
        self.cycle.step()
        self.set_reel(ReelState.REEL_INACTIVE)
        self.cycle.step()

    def test_reel_end_verifies_records_once_and_casts_again(self):
        self.finish_reel()
        self.assertIs(self.cycle.phase, Phase.VERIFYING)
        self.verifier.start.assert_called_once_with(1)
        self.assertEqual(self.tracker.reset.call_count, 2)  # on entering REELING and after it ended
        self.assertIsNone(self.cycle.observation)
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.VERIFYING)  # verifier still undecided
        outcome = CatchOutcome(1, CatchResult.CATCH_SUCCESS, 0.9, 0.1, "test")
        self.verifier.update.return_value = outcome
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.CASTING)  # replay: cast -> shake -> reel -> result -> cast
        self.assertIs(self.cycle.last_outcome, outcome)
        self.cycle._record(outcome)  # a duplicate event for the same attempt is ignored
        self.assertEqual(self.cycle.counts[CatchResult.CATCH_SUCCESS], 1)

    def test_minigame_coming_back_cancels_verification(self):
        self.finish_reel()
        self.set_reel(ReelState.REEL_ACTIVE)
        self.cycle.step()
        self.assertIs(self.cycle.phase, Phase.REELING)
        self.verifier.cancel.assert_called_once()
        self.assertEqual(sum(self.cycle.counts.values()), 0)

    def test_separate_counts(self):
        for attempt, result in enumerate((CatchResult.CATCH_SUCCESS, CatchResult.CATCH_FAILED,
                                          CatchResult.CATCH_FAILED, CatchResult.UNKNOWN), start=1):
            self.cycle._record(CatchOutcome(attempt, result, None, None, "test"))
        self.assertEqual([self.cycle.counts[r] for r in CatchResult], [1, 2, 1])


if __name__ == "__main__":
    unittest.main()
