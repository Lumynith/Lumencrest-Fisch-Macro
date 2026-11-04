"""Fish / control bar tracking checks on a synthetic reeling minigame."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from core.calibration import Region
from core.screen_capture import Frame
from core.window_manager import CLOSED
from detection.reel_tracking import MinigameTracker, TrackingCalibration, TrackingSettings, load_features

REEL = Region(0.25, 0.8, 0.5, 0.1)
BAR_W = 0.2


def layout(size):
    rx, ry, rw, rh = REEL.to_pixels(*size)
    fish_w, fish_h = max(8, int(rw * 0.04)), int(rh * 0.6)
    return rx, ry, rw, rh, fish_w, fish_h


def minigame(fish=0.7, bar=0.4, size=(800, 600), show_fish=True, bar_bgr=(255, 255, 255), t=None,
             frame_id=1, bar2_bgr=None) -> Frame:
    """Track with a translucent bar centered at `bar` and a fish centered at `fish` (fractions of the track)."""
    w, h = size
    rng = np.random.default_rng(frame_id)
    image = cv2.add(np.full((h, w, 3), (90, 120, 60), np.uint8),
                    cv2.GaussianBlur(rng.integers(0, 40, (h, w, 3), np.uint8), (0, 0), 5))
    rx, ry, rw, rh, fw, fh = layout(size)
    image[ry:ry + rh, rx:rx + rw] = (70, 40, 25)  # dark track
    bx0, bx1 = rx + int((bar - BAR_W / 2) * rw), rx + int((bar + BAR_W / 2) * rw)
    by0, by1 = ry + rh // 6, ry + rh - rh // 6
    patch = image[by0:by1, bx0:bx1].astype(np.float32)
    image[by0:by1, bx0:bx1] = (patch * 0.3 + np.array(bar_bgr) * 0.7).astype(np.uint8)  # translucent bar
    if bar2_bgr is not None:  # two-color bar: right half in the second color
        mid = (bx0 + bx1) // 2
        image[by0:by1, mid:bx1] = (patch[:, mid - bx0:] * 0.3 + np.array(bar2_bgr) * 0.7).astype(np.uint8)
    if show_fish:
        cx, cy = rx + int(fish * rw), ry + rh // 2
        cv2.ellipse(image, (cx, cy), (fw // 2, fh // 2), 0, 0, 360, (0, 0, 0), -1)
        cv2.ellipse(image, (cx, cy), (fw // 2 - 2, fh // 2 - 2), 0, 0, 360, (0, 140, 255), -1)
        cv2.circle(image, (cx, cy - fh // 5), max(1, fw // 6), (0, 0, 0), -1)
    return Frame(image, time.perf_counter() if t is None else t, frame_id, CLOSED)


def boxes(size=(800, 600), fish=0.7, bar=0.4):
    rx, ry, rw, rh, fw, fh = layout(size)
    cx, cy = rx + int(fish * rw), ry + rh // 2
    fish_box = (cx - fw // 2 - 1, cy - fh // 2 - 1, fw + 2, fh + 2)
    bx0, bx1 = rx + int((bar - BAR_W / 2) * rw), rx + int((bar + BAR_W / 2) * rw)
    return fish_box, (bx0, ry + rh // 6, bx1 - bx0, rh - 2 * (rh // 6))


class TrackingTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = Path(self.dir.name) / "config.json"
        self.calibration = TrackingCalibration({"REEL": REEL}, self.config)
        self.tracker = MinigameTracker(self.calibration, TrackingSettings())

    def tearDown(self):
        self.dir.cleanup()

    def calibrate(self, **kwargs):
        self.calibration.calibrate(minigame(**kwargs), *boxes())


class TestTracking(TrackingTestCase):
    def test_uncalibrated_is_invalid(self):
        obs = self.tracker.observe(minigame())
        self.assertFalse(obs.valid)
        self.assertIn("not calibrated", obs.reason)

    def test_positions_anywhere_on_the_track(self):
        self.calibrate()
        for fish, bar in ((0.1, 0.8), (0.5, 0.2), (0.9, 0.6), (0.45, 0.45)):  # last: fish on top of the bar
            self.tracker.reset()
            obs = self.tracker.observe(minigame(fish, bar))
            self.assertTrue(obs.valid, (fish, bar, obs.reason))
            self.assertAlmostEqual(obs.fish_x, fish, delta=0.01)
            self.assertAlmostEqual(obs.bar_center, bar, delta=0.01)
            self.assertAlmostEqual(obs.bar_right - obs.bar_left, BAR_W, delta=0.02)
            self.assertAlmostEqual(obs.distance, fish - bar, delta=0.02)
            self.assertGreater(obs.fish_confidence, 0.7)  # oval test fish; a straight line scores ~1.0
            self.assertGreater(obs.bar_confidence, 0.8)

    def test_velocity_uses_timestamps(self):
        self.calibrate()
        for i in range(4):
            obs = self.tracker.observe(minigame(fish=0.3 + 0.02 * i, bar=0.5 - 0.01 * i, t=10 + 0.05 * i, frame_id=i))
        self.assertAlmostEqual(obs.fish_velocity, 0.4, delta=0.08)   # 0.02 per 50 ms
        self.assertAlmostEqual(obs.bar_velocity, -0.2, delta=0.08)

    def test_hidden_fish_is_invalid_not_guessed(self):
        self.calibrate()
        obs = self.tracker.observe(minigame(show_fish=False))
        self.assertIsNone(obs.fish_x)
        self.assertFalse(obs.valid)
        self.assertIsNotNone(obs.bar_center)  # the bar is still reported on its own

    def test_one_frame_jump_needs_confirmation(self):
        self.calibrate()
        self.tracker.observe(minigame(fish=0.2, t=1.00))
        jumped = self.tracker.observe(minigame(fish=0.8, t=1.02))
        self.assertIsNone(jumped.fish_x)
        confirmed = self.tracker.observe(minigame(fish=0.8, t=1.04))
        self.assertAlmostEqual(confirmed.fish_x, 0.8, delta=0.01)

    def test_other_resolution(self):
        self.calibrate()
        obs = self.tracker.observe(minigame(fish=0.3, bar=0.7, size=(1200, 900)))
        self.assertTrue(obs.valid, obs.reason)
        self.assertAlmostEqual(obs.fish_x, 0.3, delta=0.01)
        self.assertAlmostEqual(obs.bar_center, 0.7, delta=0.01)

    def test_colored_bar(self):
        self.calibrate(bar_bgr=(30, 30, 255))
        obs = self.tracker.observe(minigame(bar=0.6, bar_bgr=(30, 30, 255)))
        self.assertAlmostEqual(obs.bar_center, 0.6, delta=0.01)
        self.tracker.reset()
        white = self.tracker.observe(minigame(bar=0.6))  # a white bar is not the calibrated red one
        self.assertIsNone(white.bar_center)

    @mock.patch("detection.reel_tracking.BAR_LOST_PATH")
    def test_bar_that_changes_color_mid_minigame_is_still_tracked(self, snapshot):
        snapshot.parent.mkdir = lambda **_: None
        self.calibrate()
        self.assertTrue(self.tracker.observe(minigame(fish=0.65, bar=0.4, t=1.0)).valid)  # learns the track color
        with mock.patch("detection.reel_tracking.write_image", return_value=True) as write:
            for i, bar in enumerate((0.38, 0.36, 0.34)):  # fish left the bar and the game turned it red
                obs = self.tracker.observe(minigame(fish=0.7, bar=bar, bar_bgr=(30, 30, 200), t=1.03 + 0.03 * i))
                self.assertTrue(obs.valid, obs.reason)
                self.assertAlmostEqual(obs.bar_center, bar, delta=0.01)
                self.assertIn("by contrast", obs.reason)
        write.assert_called_once()  # one snapshot per minigame
        self.tracker.reset()  # a new minigame must not reuse the old track color
        self.assertIsNone(self.tracker.observe(minigame(bar=0.5, bar_bgr=(30, 30, 200))).bar_center)

    def test_picking_new_colors_for_a_different_rod(self):
        self.calibrate()
        frame = minigame(fish=0.3, bar=0.6, bar_bgr=(40, 220, 40))  # green bar now
        self.assertIsNone(self.tracker.observe(frame).bar_center)
        rx, ry, rw, rh, _, _ = layout((800, 600))
        b, g, r = (int(v) for v in frame.image[ry + rh // 2, rx + int(0.6 * rw) - 20])  # as the eyedropper sees it
        self.calibration.set_color("bar", f"#{r:02x}{g:02x}{b:02x}")  # QColor.name() is lowercase
        self.tracker.reset()
        obs = self.tracker.observe(frame)
        self.assertAlmostEqual(obs.bar_center, 0.6, delta=0.01)
        self.assertAlmostEqual(obs.fish_x, 0.3, delta=0.01)
        self.assertEqual(load_features(self.config).bar_color, self.calibration.features.bar_color)

    def test_two_color_bar(self):
        red, blue = (30, 30, 255), (255, 60, 30)
        frame = lambda bar: minigame(fish=0.8, bar=bar, bar_bgr=red, bar2_bgr=blue)
        self.calibrate(bar_bgr=red)
        self.calibration.set_bar_style("two_color")
        self.calibration.calibrate(minigame(bar_bgr=red, bar2_bgr=blue), *boxes())  # halves give colors 1 and 2
        f = self.calibration.features
        self.assertNotEqual(f.bar_color.bgr, f.bar_color2.bgr)
        for bar in (0.2, 0.5):
            self.tracker.reset()
            obs = self.tracker.observe(frame(bar))
            self.assertAlmostEqual(obs.bar_center, bar, delta=0.01)
            self.assertAlmostEqual(obs.bar_right - obs.bar_left, BAR_W, delta=0.02)  # both halves, not one
        self.assertEqual(load_features(self.config), f)  # saved and reloaded
        self.calibration.set_bar_style("solid")  # only color 1: the blue half no longer counts
        self.tracker.reset()
        obs = self.tracker.observe(frame(0.5))
        self.assertTrue(obs.bar_center is None or obs.bar_right - obs.bar_left < BAR_W * 0.7)
        self.assertEqual(self.calibration.features.bar_color2, f.bar_color2)  # kept for switching back

    def test_rainbow_rod_gradient_bar_that_shrinks_and_patterned_fish(self):
        """Like a popular rod: bar shades yellow to blue top to bottom, the fish is a striped purple sword with a
        dark center line, and the bar shrinks while the fish is outside it."""
        size = (800, 600)
        rx, ry, rw, rh, _, _ = layout(size)
        top, bottom = np.array((103, 255, 238)), np.array((243, 146, 71))
        fish_w, rim = 20, 5

        def rod(fish, bar, bar_w=BAR_W, restyled=False):
            image = np.full((size[1], size[0], 3), (40, 60, 30), np.uint8)
            image[ry:ry + rh, rx:rx + rw] = (165, 141, 161)  # purple-gray track
            bx0, bx1 = rx + int((bar - bar_w / 2) * rw), rx + int((bar + bar_w / 2) * rw)
            by0, by1 = ry + rh // 6, ry + rh - rh // 6
            for y in range(by0, by1):
                image[y, bx0:bx1] = (70, 110, 60) if restyled else top + (bottom - top) * (y - by0) / (by1 - by0 - 1)
            fx0 = rx + int(fish * rw) - fish_w // 2
            image[ry:ry + rh, fx0 - rim:fx0 + fish_w + rim] = (231, 200, 196)  # light outline
            image[ry:ry + rh, fx0:fx0 + fish_w] = (170, 23, 98)
            image[ry + 2:ry + rh:6, fx0:fx0 + fish_w] = (231, 200, 196)  # chevron stripes
            image[ry:ry + rh, fx0 + fish_w // 2] = (60, 10, 40)            # dark center line
            return Frame(image, time.perf_counter(), 1, CLOSED), (fx0, ry, fish_w, rh), (bx0, by0, bx1 - bx0, by1 - by0)

        frame, fish_box, bar_box = rod(fish=0.45, bar=0.4)
        self.calibration.calibrate(frame, fish_box, bar_box)
        self.calibration.set_bar_style("gradient")
        self.calibration.calibrate(frame, fish_box, bar_box)
        self.assertLess(self.calibration.features.fish_fill, 0.9)
        self.calibration.set_bar_shrinks(True)
        for fish, bar, bar_w in ((0.6, 0.3, BAR_W), (0.85, 0.3, BAR_W * 0.35), (0.1, 0.7, BAR_W * 0.35)):
            self.tracker.reset()
            obs = self.tracker.observe(rod(fish, bar, bar_w)[0])
            self.assertTrue(obs.valid, (fish, bar, bar_w, obs.reason))
            self.assertAlmostEqual(obs.fish_x, fish, delta=0.01)
            self.assertAlmostEqual(obs.bar_center, bar, delta=0.01)
            self.assertAlmostEqual(obs.bar_right - obs.bar_left, bar_w, delta=0.02)
        self.assertEqual(load_features(self.config), self.calibration.features)  # gradient + shrink saved

        # The game shrinks the bar and turns it dull green while the fish is outside, here right against its edge.
        self.tracker.reset()
        self.assertTrue(self.tracker.observe(rod(0.5, 0.45)[0]).valid)  # full bar: learns the empty track
        small = BAR_W * 0.35
        edge = 0.45 + small / 2 + (fish_w / 2 + rim + 1) / rw  # fish's outline right against the bar
        for _ in range(3):
            obs = self.tracker.observe(rod(edge, 0.45, small, restyled=True)[0])
            self.assertTrue(obs.valid, obs.reason)
            self.assertIn("by contrast", obs.reason)
            self.assertAlmostEqual(obs.bar_center, 0.45, delta=0.01)  # the fish isn't counted as bar
            self.assertAlmostEqual(obs.bar_right - obs.bar_left, small, delta=0.02)
        self.calibration.set_bar_shrinks(False)  # a normal rod: a bar a third of its size is not the bar
        self.tracker.reset()
        self.assertIsNone(self.tracker.observe(rod(0.85, 0.3, BAR_W * 0.35)[0]).bar_center)

    def test_hand_edited_color_is_reloaded(self):
        self.calibrate()
        config = json.loads(self.config.read_text())
        config["reel_tracking"]["bar_color"] = {"hex": "#BA212A", "tolerance": 30}  # the translucent red bar as seen, RGB
        self.config.write_text(json.dumps(config))
        self.calibration.reload()
        self.assertEqual(self.calibration.features.bar_color.bgr, (42, 33, 186))
        obs = self.tracker.observe(minigame(bar=0.6, bar_bgr=(30, 30, 255)))
        self.assertAlmostEqual(obs.bar_center, 0.6, delta=0.01)


class TestRealLayout(TrackingTestCase):
    """Layout like the real game: a thin strip, a white bar, and the fish as a gray line spanning the strip."""

    def frame(self, fish, bar, t=None):
        f = minigame(fish, bar, show_fish=False, t=t)
        rx, ry, rw, rh, _, _ = layout((800, 600))
        x = rx + int(fish * rw)
        f.image[ry:ry + rh, x - 2:x + 3] = (110, 110, 110)
        return f

    def test_overhanging_selection_and_line_fish(self):
        rx, ry, rw, rh, _, _ = layout((800, 600))
        fish_x = rx + int(0.4 * rw)
        _, bar = boxes(bar=0.4)
        # The fish box overhangs the REEL strip above and below, as it does when dragged by hand.
        self.calibration.calibrate(self.frame(0.4, 0.4), (fish_x - 5, ry - 6, 11, rh + 12), bar)
        for fish, bar_at in ((0.4, 0.4), (0.8, 0.3), (0.15, 0.6)):  # on the bar, then off it to either side
            self.tracker.reset()
            obs = self.tracker.observe(self.frame(fish, bar_at))
            self.assertTrue(obs.valid, (fish, bar_at, obs.reason))
            self.assertAlmostEqual(obs.fish_x, fish, delta=0.01)
            self.assertAlmostEqual(obs.bar_center, bar_at, delta=0.01)


class TestCalibrationStorage(TrackingTestCase):
    def test_save_and_load(self):
        self.calibrate()
        self.assertEqual(load_features(self.config), self.calibration.features)

    def test_invalid_selection_and_storage(self):
        frame = minigame()
        fish, bar = boxes()
        with self.assertRaises(ValueError):
            self.calibration.calibrate(frame, (0, 0, 20, 20), bar)  # outside the REEL region
        with self.assertRaises(ValueError):
            self.calibration.calibrate(frame, (fish[0], fish[1] - 3 * fish[3], fish[2], 3 * fish[3]), bar)  # mostly
        with self.assertRaises(ValueError):
            self.calibration.calibrate(frame, (fish[0], fish[1], 2, 2), bar)  # too small
        self.calibrate()
        for bad in ({"fish_template": "old.png"}, {"fish_color": {"hex": "#12", "tolerance": 30}},
                    {"bar_color": {"hex": "#FFFFFF", "tolerance": 300}}):
            config = json.loads(self.config.read_text())
            config["reel_tracking"].update(bad)
            if "fish_template" in bad:
                del config["reel_tracking"]["fish_color"]
            self.config.write_text(json.dumps(config))
            self.assertIsNone(load_features(self.config), bad)
            self.calibrate()


if __name__ == "__main__":
    unittest.main()
