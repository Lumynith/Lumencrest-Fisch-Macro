"""Calibration storage, scaling and canvas mouse mapping checks."""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from core.calibration import Region, load_calibration, save_calibration
from ui.calibration_window import RegionCanvas

app = QApplication.instance() or QApplication([])


class TestRegion(unittest.TestCase):
    def test_clamped_clips_to_viewport(self):
        self.assertEqual(Region(-0.1, 0.5, 0.3, 0.8).clamped(), Region(0.0, 0.5, 0.2, 0.5))
        self.assertIsNone(Region(1.2, 0.0, 0.1, 0.1).clamped())
        self.assertIsNone(Region(float("nan"), 0, 0.1, 0.1).clamped())

    def test_to_pixels_scales_with_resolution(self):
        region = Region(0.25, 0.5, 0.5, 0.25)
        self.assertEqual(region.to_pixels(1920, 1080), (480, 540, 960, 270))
        self.assertEqual(region.to_pixels(1280, 720), (320, 360, 640, 180))


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_round_trip_keeps_other_settings(self):
        self.path.write_text(json.dumps({"other": 1}))
        regions = {"SHAKE": Region(0.1, 0.2, 0.3, 0.4), "REEL": Region(0.0, 0.8, 1.0, 0.1)}
        save_calibration(regions, self.path)
        self.assertEqual(load_calibration(self.path), regions)
        self.assertEqual(json.loads(self.path.read_text())["other"], 1)

    def test_empty_missing_and_invalid_files(self):
        self.assertEqual(load_calibration(self.path), {})
        self.path.write_text("")
        self.assertEqual(load_calibration(self.path), {})
        self.path.write_text(json.dumps({"calibration": {"REEL": {"x": "bad"}, "SHAKE": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}}))
        self.assertEqual(load_calibration(self.path), {"SHAKE": Region(0, 0, 0.5, 0.5)})

    def test_corrupt_file_is_backed_up_on_save(self):
        self.path.write_text("{not json")
        self.assertEqual(load_calibration(self.path), {})
        save_calibration({"SHAKE": Region(0, 0, 0.5, 0.5)}, self.path)
        self.assertEqual(self.path.with_suffix(".json.bak").read_text(), "{not json")
        self.assertIn("SHAKE", load_calibration(self.path))


class TestCanvasMapping(unittest.TestCase):
    def setUp(self):
        self.canvas = RegionCanvas({})
        self.canvas.resize(1000, 400)  # wider than 16:9, so the screenshot is letterboxed left/right
        self.canvas.set_image(QImage(1920, 1080, QImage.Format.Format_BGR888))

    def _mouse(self, kind, x, y):
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.canvas, event)

    def test_scaled_mapping_round_trips(self):
        rect = self.canvas.image_rect()
        self.assertAlmostEqual(rect.height(), 400)
        self.assertAlmostEqual(rect.x(), (1000 - 400 * 16 / 9) / 2)
        self.assertEqual(self.canvas.to_norm(rect.topLeft()), (0.0, 0.0))
        region = Region(0.25, 0.5, 0.5, 0.25)
        mapped = self.canvas.to_widget(region)
        self.assertAlmostEqual(self.canvas.to_norm(mapped.topLeft())[0], 0.25)
        self.assertAlmostEqual(self.canvas.to_norm(mapped.bottomRight())[1], 0.75)

    def test_drag_draws_then_moves_inside_viewport(self):
        r = self.canvas.image_rect()
        x0, y0 = r.x() + r.width() * 0.1, r.y() + r.height() * 0.1
        x1, y1 = r.x() + r.width() * 0.3, r.y() + r.height() * 0.5
        self._mouse(QEvent.Type.MouseButtonPress, x0, y0)
        self._mouse(QEvent.Type.MouseMove, x1, y1)
        self._mouse(QEvent.Type.MouseButtonRelease, x1, y1)
        shake = self.canvas.regions["SHAKE"]
        self.assertAlmostEqual(shake.x, 0.1, places=3)
        self.assertAlmostEqual(shake.h, 0.4, places=3)
        # Drag the body far off to the bottom-right: it must stop at the viewport edge.
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self._mouse(QEvent.Type.MouseButtonPress, cx, cy)
        self._mouse(QEvent.Type.MouseMove, cx + 5000, cy + 5000)
        self._mouse(QEvent.Type.MouseButtonRelease, cx + 5000, cy + 5000)
        moved = self.canvas.regions["SHAKE"]
        self.assertAlmostEqual(moved.x + moved.w, 1.0)
        self.assertAlmostEqual(moved.y + moved.h, 1.0)
        self.assertAlmostEqual(moved.w, shake.w)


if __name__ == "__main__":
    unittest.main()
