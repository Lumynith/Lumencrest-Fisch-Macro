"""Shake detection and click-controller checks on synthetic frames. No real inputs are sent."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from controllers.shaking import ShakeController, ShakeResult, ShakeSettings
from core.calibration import Region
from core.screen_capture import Frame
from core.window_manager import WindowInfo, WindowState
from detection.shake_detector import ShakeDetector

WINDOW = WindowInfo(WindowState.ACTIVE, hwnd=1, x=100, y=50, width=800, height=600)
# White strokes of a SHAKE button (text + outline); the rest of the button is translucent.
STROKES = np.zeros((40, 60), np.uint8)
cv2.rectangle(STROKES, (1, 1), (58, 38), 255, 2)
cv2.putText(STROKES, "SHAKE", (5, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 255, 2)


def frame_with_button(x: int, y: int, size=(800, 600), strokes=STROKES, background=(40, 40, 40)) -> Frame:
    image = np.full((size[1], size[0], 3), background, np.uint8)
    h, w = strokes.shape
    patch = (image[y:y + h, x:x + w] * 0.45).astype(np.uint8)  # dark translucent body tints the background
    patch[strokes > 0] = 245
    image[y:y + h, x:x + w] = patch
    window = WindowInfo(WindowState.ACTIVE, 1, 0, "", WINDOW.x, WINDOW.y, size[0], size[1])
    return Frame(image, time.perf_counter(), 1, window)


class ShakeTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.detector = ShakeDetector({"SHAKE": Region(0.1, 0.1, 0.8, 0.8)}, Path(self.dir.name))
        self.detector.capture_template(frame_with_button(300, 200), (300, 200, 60, 40))

    def tearDown(self):
        self.dir.cleanup()


class TestShakeDetector(ShakeTestCase):
    def test_finds_button_anywhere_in_region(self):
        for x, y in ((100, 80), (600, 450), (350, 300)):
            match = self.detector.detect(frame_with_button(x, y))
            self.assertGreater(match.confidence, 0.95)
            self.assertEqual((match.x, match.y), (x + 30, y + 20))  # center, client pixels

    def test_translucent_button_matches_on_any_background(self):
        # The template was captured over dark gray; the body color differs on each of these.
        for background in ((230, 170, 90), (60, 150, 60), (235, 235, 235)):
            match = self.detector.detect(frame_with_button(400, 300, background=background))
            self.assertGreater(match.confidence, 0.9, background)
            empty = Frame(np.full((600, 800, 3), background, np.uint8), time.perf_counter(), 1, WINDOW)
            self.assertLess(self.detector.detect(empty).confidence, 0.3, background)

    def test_scales_with_resolution(self):
        big = cv2.resize(STROKES, (120, 80), interpolation=cv2.INTER_NEAREST)
        self.detector.templates.clear()
        self.detector.capture_template(frame_with_button(300, 200, strokes=big), (300, 200, 120, 80))
        match = self.detector.detect(frame_with_button(150, 100, size=(400, 300)))  # half resolution
        self.assertGreater(match.confidence, 0.75)
        self.assertLess(abs(match.x - 180) + abs(match.y - 120), 4)

    def test_untagged_template_in_templates_folder_loads(self):
        cv2.imwrite(str(Path(self.dir.name) / "shake_1.png"), frame_with_button(0, 0).image[0:40, 0:60])
        self.detector.reload_templates()
        self.assertEqual(len(self.detector.templates), 2)
        self.assertIn(None, [t.captured_h for t in self.detector.templates])

    def test_no_button_scores_low_and_bad_inputs_are_safe(self):
        empty = Frame(np.full((600, 800, 3), 40, np.uint8), time.perf_counter(), 1, WINDOW)
        match = self.detector.detect(empty)
        self.assertTrue(match is None or match.confidence < 0.5)
        self.assertIsNone(ShakeDetector({}, Path(self.dir.name)).detect(empty))  # no SHAKE region
        with self.assertRaises(ValueError):
            self.detector.capture_template(empty, (10, 10, 50, 50))  # no white strokes
        with self.assertRaises(ValueError):
            self.detector.capture_template(empty, (790, 10, 50, 50))  # outside the window


@mock.patch("controllers.shaking.get_window_info")
class TestShakeController(ShakeTestCase):
    def controller(self, **settings):
        self.inputs = mock.Mock()
        self.inputs.click.return_value = True
        return ShakeController(self.detector, self.inputs, ShakeSettings(auto_click=True, click_delay=0, **settings))

    def test_clicks_screen_coordinates_once_per_button(self, window_info):
        window_info.return_value = frame_with_button(0, 0).window
        shake = self.controller()
        frame = frame_with_button(300, 200)
        shake.step(frame)
        self.inputs.click.assert_called_once_with(100 + 330, 50 + 220, settle=0.05, hold=0.06)  # origin + center
        shake.step(frame)  # the same frame again (captured before the click)
        time.sleep(0.06)
        shake.step(frame_with_button(300, 200))  # newer frame, button still fading at the same spot
        self.assertEqual(self.inputs.click.call_count, 1)
        shake.step(frame_with_button(500, 100))  # new position
        self.assertEqual(self.inputs.click.call_count, 2)

    def test_no_click_when_inactive_moved_or_debug(self, window_info):
        shake = self.controller()
        frame = frame_with_button(300, 200)
        window_info.return_value = WindowInfo(WindowState.OPEN, 1, 0, "", 100, 50, 800, 600)
        shake.step(frame)
        window_info.return_value = WindowInfo(WindowState.ACTIVE, 1, 0, "", 140, 50, 800, 600)  # moved
        shake.step(frame)
        window_info.return_value = frame.window
        shake.auto_click = False
        shake.step(frame)
        self.inputs.click.assert_not_called()

    def test_timeout_and_finish(self, window_info):
        window_info.return_value = frame_with_button(0, 0).window
        shake = self.controller(first_button_timeout=0.05, finish_after=0.05)
        empty = Frame(np.full((600, 800, 3), 40, np.uint8), time.perf_counter(), 1, WINDOW)
        time.sleep(0.06)
        self.assertIs(shake.step(empty), ShakeResult.TIMEOUT)
        shake.reset()
        self.assertIs(shake.step(frame_with_button(300, 200)), ShakeResult.SHAKING)
        time.sleep(0.06)
        self.assertIs(shake.step(empty), ShakeResult.FINISHED)


if __name__ == "__main__":
    unittest.main()
