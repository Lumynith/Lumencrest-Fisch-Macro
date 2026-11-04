"""Screen capture self-checks that do not need Roblox running."""

import time
import unittest

import numpy as np

from core.screen_capture import Frame, ScreenCapture
from core.window_manager import CLOSED, WindowManager


class TestScreenCapture(unittest.TestCase):
    def test_no_window_gives_no_frame_and_stops_cleanly(self):
        capture = ScreenCapture(WindowManager("definitely_not_running.exe"))
        capture.start()
        time.sleep(0.3)
        self.assertIsNone(capture.get_frame())
        capture.stop()
        self.assertFalse(capture.is_running)

    def test_stale_frame_is_rejected(self):
        capture = ScreenCapture(max_frame_age=0.25)
        image = np.zeros((1, 1, 3), np.uint8)
        capture._frame = Frame(image, time.perf_counter(), 1, CLOSED)
        self.assertIsNotNone(capture.get_frame())
        capture._frame = Frame(image, time.perf_counter() - 1, 2, CLOSED)
        self.assertIsNone(capture.get_frame())
        self.assertIsNotNone(capture.get_frame(max_age=2))


if __name__ == "__main__":
    unittest.main()
