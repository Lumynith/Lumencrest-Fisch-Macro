"""Tracks the fish indicator and the player's control bar inside the reeling minigame.

Both are found by color, so a rod with a different line / bar color only needs new colors, not new images.
Calibration (T) boxes the fish and the bar on one screenshot to learn their colors and sizes; P / B in the
preview re-pick one color with an eyedropper, and the colors can be edited in config.json (reel_tracking.fish_color /
bar_color: "hex" + "tolerance"). Positions are fractions of the REEL region width. Never sends input.
"""

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from core.calibration import Region
from core.config import APP_DIR, CONFIG_PATH, read_config, write_section
from core.screen_capture import Frame
from detection.image_utils import crop_region, write_image

log = logging.getLogger(__name__)

SECTION = "reel_tracking"
MIN_SIZE = 4             # pixels per side of a selection
HISTORY_MAX_AGE = 0.25   # seconds; older positions are not used for velocity or jump checks
FISH_TOLERANCE = 30      # default per-channel color tolerance when a color is picked
BAR_TOLERANCE = 40
FILL = 0.6               # fraction of a column's pixels that must match for the column to count
TRACK_TOLERANCE = 30     # the empty track's color, learned while the bar is visible, +- this
FISH_FILL_SHARE = 0.5    # a patterned fish's columns count from this share of its fullest column
FISH_GAP = 3             # columns; bridges a thin dark line through a patterned fish
MIN_FISH_FILL = 0.25     # below this the fish selection has no usable color
BAR_STYLES = ("solid", "two_color", "gradient")
PROFILE_ROWS = 24        # a gradient bar is stored as this many colors, top to bottom
MIN_SHRINK = 0.15        # a shrinking bar is accepted down to this share of its calibrated width
BAR_LOST_PATH = APP_DIR / "logs" / "bar_lost.png"


@dataclass(frozen=True)
class TrackingSettings:
    fish_threshold: float = field(default=0.6, metadata={"max": 1.0})  # min fish confidence
    bar_threshold: float = field(default=0.5, metadata={"max": 1.0})   # min bar confidence
    max_jump: float = field(default=0.3, metadata={"max": 1.0})        # track fraction; bigger moves need 2 frames


@dataclass(frozen=True)
class TrackColor:
    bgr: tuple[int, int, int]
    tolerance: int  # max difference per channel, 0-255

    @property
    def hex(self) -> str:
        b, g, r = self.bgr
        return f"#{r:02X}{g:02X}{b:02X}"

    @classmethod
    def from_hex(cls, text, tolerance) -> "TrackColor":
        text = str(text).strip().lstrip("#")
        if len(text) != 6:
            raise ValueError(f"color {text!r} is not #RRGGBB")
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
        if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
            raise ValueError(f"tolerance {tolerance!r} is not a whole number 0-255")
        return cls((b, g, r), tolerance)

    @classmethod
    def median(cls, pixels: np.ndarray, tolerance: int) -> "TrackColor":
        return cls(tuple(int(v) for v in np.median(pixels.reshape(-1, 3), axis=0)), tolerance)

    @classmethod
    def sample(cls, pixels: np.ndarray, tolerance: int) -> "TrackColor":
        """Color of the middle third of a selection's columns (a box around a thin line is mostly background)."""
        w = pixels.shape[1]
        return cls.median(pixels[:, w // 3: max(w // 3 + 1, 2 * w // 3)], tolerance)

    def mask(self, bgr: np.ndarray) -> np.ndarray:
        return np.abs(bgr.astype(np.int16) - np.array(self.bgr, np.int16)).max(axis=-1) <= self.tolerance

    def to_config(self) -> dict:
        return {"hex": self.hex, "tolerance": self.tolerance}


@dataclass(frozen=True)
class TrackFeatures:
    region_width: int                # REEL region width in pixels when calibrated
    fish_band: tuple[float, float]   # fish's vertical span, as fractions of the REEL region height
    bar_band: tuple[float, float]
    fish_width: float                # fractions of the REEL region width
    bar_width: float
    fish_color: TrackColor
    bar_color: TrackColor            # the whole bar, its left side (two_color) or its average (gradient)
    bar_color2: TrackColor | None = None  # right side; kept while unused so the pick isn't lost
    bar_style: str = "solid"         # one of BAR_STYLES
    bar_profile: tuple[TrackColor, ...] | None = None  # gradient: color per row, top to bottom of bar_band
    bar_shrinks: bool = False        # the bar narrows while the fish is outside it
    fish_fill: float = 1.0           # matching share of the fish's fullest column (< 1 if patterned)

    @property
    def two_color_bar(self) -> bool:
        return self.bar_style == "two_color"

    @property
    def gradient_bar(self) -> bool:
        return self.bar_style == "gradient" and bool(self.bar_profile)

    @property
    def bar_colors(self) -> tuple[TrackColor, ...]:
        if self.gradient_bar:
            return self.bar_profile
        return (self.bar_color, self.bar_color2) if self.two_color_bar and self.bar_color2 else (self.bar_color,)

    def bar_mask(self, bgr: np.ndarray) -> np.ndarray:
        """Bar pixels in `bgr`, which must be the whole bar band (a gradient is matched row by row)."""
        if self.gradient_bar:
            profile = np.array([c.bgr for c in self.bar_profile], np.uint8)[:, None, :]
            profile = cv2.resize(profile, (1, bgr.shape[0]), interpolation=cv2.INTER_LINEAR).reshape(-1, 1, 3)
            return np.abs(bgr.astype(np.int16) - profile.astype(np.int16)).max(axis=-1) <= self.bar_color.tolerance
        mask = self.bar_color.mask(bgr)
        for color in self.bar_colors[1:]:
            mask |= color.mask(bgr)
        return mask


@dataclass(frozen=True)
class Observation:
    timestamp: float
    fish_x: float | None             # fish center, 0-1 along the track; None if not reliably found
    fish_velocity: float | None      # track widths per second
    fish_confidence: float
    fish_box: tuple[int, int, int, int] | None  # client pixels (x, y, w, h), drawn even when below threshold
    bar_left: float | None
    bar_right: float | None
    bar_center: float | None
    bar_velocity: float | None
    bar_confidence: float
    bar_box: tuple[int, int, int, int] | None
    reason: str

    @property
    def valid(self) -> bool:
        return self.fish_x is not None and self.bar_center is not None

    @property
    def distance(self) -> float | None:
        """fish_x - bar_center: positive when the fish is right of the bar's center."""
        return self.fish_x - self.bar_center if self.valid else None


def clip_to_region(name: str, box, region: Region, fw: int, fh: int) -> tuple[int, int, int, int]:
    """Boxes may overhang the thin REEL strip a little; trim them instead of rejecting the selection."""
    rx, ry, rw, rh = region.to_pixels(fw, fh)
    x, y, w, h = box
    x0, y0, x1, y1 = max(x, rx), max(y, ry), min(x + w, rx + rw), min(y + h, ry + rh)
    if x1 - x0 < MIN_SIZE or y1 - y0 < MIN_SIZE:
        raise ValueError(f"{name} selection is too small or outside the REEL region")
    if (x1 - x0) * (y1 - y0) < 0.5 * w * h:
        raise ValueError(f"{name} selection is mostly outside the REEL region")
    return x0, y0, x1 - x0, y1 - y0


def extract_features(frame: Frame, region: Region, fish_box, bar_box, style: str = "solid") -> TrackFeatures:
    """Builds features from client-pixel boxes (x, y, w, h). Raises ValueError if the selection is unusable.
    Style "two_color": the bar's left and right halves each give one color; "gradient": each row does."""
    fh, fw = frame.image.shape[:2]
    rx, ry, rw, rh = region.to_pixels(fw, fh)
    fx, fy, fwid, fhgt = clip_to_region("fish", fish_box, region, fw, fh)
    bx, by, bw, bh = clip_to_region("control bar", bar_box, region, fw, fh)
    fish_pixels = frame.image[fy:fy + fhgt, fx:fx + fwid]
    fish_color = TrackColor.sample(fish_pixels, FISH_TOLERANCE)
    fill = fish_color.mask(fish_pixels).mean(axis=0)
    if fill.max() < MIN_FISH_FILL:
        raise ValueError("fish selection has no consistent color in its middle")
    fish_fill = round(min(1.0, float(fill.max())), 3)  # a patterned fish (e.g. chevrons) never fills a column
    columns = _fish_columns(fill, fish_fill)  # same cut as _find_fish, so widths agree
    keep = np.ones((bh, bw), bool)  # leave out the fish where it overlaps the bar
    ox0, oy0 = max(fx, bx) - bx, max(fy, by) - by
    ox1, oy1 = min(fx + fwid, bx + bw) - bx, min(fy + fhgt, by + bh) - by
    if ox1 > ox0 and oy1 > oy0:
        keep[oy0:oy1, ox0:ox1] = False
    if keep.sum() < 20:
        raise ValueError("control bar selection is mostly covered by the fish")
    bar_pixels = frame.image[by:by + bh, bx:bx + bw]
    bar_color2 = bar_profile = None
    if style == "two_color":
        half = bw // 2
        sides = [(bar_pixels[:, :half], keep[:, :half]), (bar_pixels[:, half:], keep[:, half:])]
        if min(k.sum() for _, k in sides) < 10:
            raise ValueError("one side of the control bar is covered by the fish; move the fish and try again")
        bar_color, bar_color2 = (TrackColor.median(px[k], BAR_TOLERANCE) for px, k in sides)
    else:
        bar_color = TrackColor.median(bar_pixels[keep], BAR_TOLERANCE)
    if style == "gradient":
        if not keep.any(axis=1).all():
            raise ValueError("the fish covers whole rows of the control bar; move the fish and try again")
        rows = np.array([np.median(bar_pixels[r][keep[r]], axis=0) for r in range(bh)], np.uint8)[:, None, :]
        rows = cv2.resize(rows, (1, PROFILE_ROWS), interpolation=cv2.INTER_AREA).reshape(-1, 3)
        bar_profile = tuple(TrackColor(tuple(int(v) for v in c), BAR_TOLERANCE) for c in rows)
    band = lambda y, h: ((y - ry) / rh, (y + h - ry) / rh)
    features = TrackFeatures(rw, band(fy, fhgt), band(by, bh), int(columns.sum()) / rw, bw / rw, fish_color,
                             bar_color, bar_color2, style, bar_profile, fish_fill=fish_fill)
    _warn_if_close(features)
    return features


def _warn_if_close(f: TrackFeatures) -> None:
    fish = f.fish_color
    for bar in f.bar_colors:
        if bar.mask(np.array([[fish.bgr]], np.uint8))[0, 0] or fish.mask(np.array([[bar.bgr]], np.uint8))[0, 0]:
            log.warning("Fish color %s and bar color %s are within tolerance of each other; tracking may confuse "
                        "them (lower the tolerances)", fish.hex, bar.hex)


def save_features(features: TrackFeatures, path: Path = CONFIG_PATH) -> None:
    write_section(SECTION, {
        "region_width": features.region_width, "fish_band": list(features.fish_band),
        "bar_band": list(features.bar_band), "fish_width": features.fish_width, "bar_width": features.bar_width,
        "fish_color": features.fish_color.to_config(), "bar_color": features.bar_color.to_config(),
        "bar_style": features.bar_style, "bar_shrinks": features.bar_shrinks,
        "bar_color2": features.bar_color2.to_config() if features.bar_color2 else None,
        "bar_profile": [c.hex for c in features.bar_profile] if features.bar_profile else None,
        "fish_fill": features.fish_fill,
    }, path)


def load_features(path: Path = CONFIG_PATH) -> TrackFeatures | None:
    try:
        raw = read_config(path).get(SECTION)
        if raw is None:
            return None
        pair = lambda v: (float(v[0]), float(v[1]))
        color = lambda c: TrackColor.from_hex(c["hex"], c["tolerance"])
        bar_color = color(raw["bar_color"])
        style = raw.get("bar_style") or ("two_color" if raw.get("two_color_bar") else "solid")  # older configs
        if style not in BAR_STYLES:
            raise ValueError(f"bar_style {style!r} is not one of {BAR_STYLES}")
        profile = raw.get("bar_profile")
        features = TrackFeatures(int(raw["region_width"]), pair(raw["fish_band"]), pair(raw["bar_band"]),
                                 float(raw["fish_width"]), float(raw["bar_width"]),
                                 color(raw["fish_color"]), bar_color,
                                 color(raw["bar_color2"]) if raw.get("bar_color2") else None, style,
                                 tuple(TrackColor.from_hex(h, bar_color.tolerance) for h in profile) if profile
                                 else None, bool(raw.get("bar_shrinks", False)), float(raw.get("fish_fill", 1.0)))
        if features.region_width <= 0 or not 0 < features.bar_width <= 1 or not 0 < features.fish_width <= 1 \
                or not 0 < features.fish_fill <= 1:
            raise ValueError("bad sizes")
        return features
    except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
        log.warning("Ignoring invalid reel tracking calibration (%s); press T to recalibrate", e)
        return None


class TrackingCalibration:
    """Holds the current features; shared by every tracker so recalibration applies everywhere at once."""

    def __init__(self, regions: dict[str, Region], path: Path = CONFIG_PATH):
        self.regions = regions
        self.path = path
        self.features = load_features(path)
        log.info("Reel tracking calibration: %s", self._describe() if self.features else "not set (press T)")

    def calibrate(self, frame: Frame, fish_box, bar_box) -> None:
        region = self.regions.get("REEL")
        if region is None:
            raise ValueError("no REEL region; calibrate it first")
        old = self.features
        features = extract_features(frame, region, fish_box, bar_box, old.bar_style if old else "solid")
        if old is not None:  # keep what this selection didn't measure
            features = replace(features, bar_shrinks=old.bar_shrinks, bar_color2=features.bar_color2 or old.bar_color2,
                               bar_profile=features.bar_profile or old.bar_profile)
        self._set(features, "calibrated")

    def set_color(self, part: str, hex_color: str | None = None, tolerance: int | None = None) -> None:
        """Sets the fish ("fish"), bar ("bar", the left side of a two-color bar) or right side ("bar2") color
        and/or tolerance; unset values and sizes are kept."""
        f = self.features
        if f is None:
            raise ValueError("calibrate tracking (T) first")
        attr = {"fish": "fish_color", "bar": "bar_color", "bar2": "bar_color2"}[part]
        old = getattr(f, attr) or f.bar_color
        color = TrackColor.from_hex(hex_color or old.hex, old.tolerance if tolerance is None else tolerance)
        f = replace(f, **{attr: color})
        _warn_if_close(f)
        self._set(f, f"{part} color set")

    def set_bar_style(self, style: str) -> None:
        """"solid", "two_color" (left / right; color 2 starts as a copy of color 1 until picked) or "gradient"
        (top to bottom; learned by boxing the bar in calibrate)."""
        f = self.features
        if f is None:
            raise ValueError("calibrate tracking (T) first")
        if style not in BAR_STYLES:
            raise ValueError(f"bar style {style!r} is not one of {BAR_STYLES}")
        if f.bar_style != style:
            self._set(replace(f, bar_style=style, bar_color2=f.bar_color2 or f.bar_color), f"bar style {style}")
            if style == "gradient" and not f.bar_profile:
                log.warning("Gradient bar: box the fish and bar (Calibrate fish & bar) to learn the bar's colors")

    def set_bar_shrinks(self, on: bool) -> None:
        f = self.features
        if f is None:
            raise ValueError("calibrate tracking (T) first")
        if f.bar_shrinks != on:
            self._set(replace(f, bar_shrinks=on), "shrinking bar " + ("on" if on else "off"))

    def reload(self) -> None:
        """Picks up hand edits to config.json."""
        features = load_features(self.path)
        if features is not None and features != self.features:
            self.features = features
            log.info("Reel tracking reloaded: %s", self._describe())

    def _set(self, features: TrackFeatures, what: str) -> None:
        save_features(features, self.path)
        self.features = features  # swapped whole, so trackers on other threads see old or new
        log.info("Reel tracking %s: %s", what, self._describe())

    def _describe(self) -> str:
        f = self.features
        bars = (f"gradient {f.bar_profile[0].hex}..{f.bar_profile[-1].hex} +/-{f.bar_color.tolerance}"
                if f.gradient_bar else " | ".join(f"{c.hex} +/-{c.tolerance}" for c in f.bar_colors))
        bars += ", shrinks" if f.bar_shrinks else ""
        return f"fish {f.fish_color.hex} +/-{f.fish_color.tolerance}, bar {bars}, bar width {f.bar_width * 100:.0f}% of track"


class _Axis:
    """Rejects one-frame jumps and estimates velocity from timestamped positions."""

    def __init__(self):
        self.value: float | None = None
        self.time = 0.0
        self.velocity: float | None = None
        self.pending: float | None = None

    def update(self, value: float | None, t: float, max_jump: float) -> tuple[float | None, float | None]:
        if value is None:
            return None, None
        recent = self.value is not None and 0 < t - self.time <= HISTORY_MAX_AGE
        if recent:
            dt = t - self.time
            predicted = self.value + (self.velocity or 0.0) * dt
            if abs(value - predicted) > max_jump:
                if self.pending is None or abs(value - self.pending) > max_jump:
                    self.pending = value  # wait for one more frame to agree before trusting a jump
                    return None, None
                self.velocity = None  # confirmed jump: old motion no longer applies
            else:
                v = (value - self.value) / dt
                self.velocity = v if self.velocity is None else 0.5 * v + 0.5 * self.velocity
        elif self.value is None or t - self.time > HISTORY_MAX_AGE:
            self.velocity = None
        self.pending = None
        self.value, self.time = value, t
        return value, self.velocity


class MinigameTracker:
    """One per thread (keeps motion history). Call observe() once per new frame while REEL_ACTIVE."""

    def __init__(self, calibration: TrackingCalibration, settings: TrackingSettings):
        self.calibration = calibration
        self.settings = settings
        self.reset()

    def reset(self) -> None:
        self._fish, self._bar = _Axis(), _Axis()
        self._track: TrackColor | None = None  # empty-track color, learned this minigame
        self._ends = (0, 0)  # columns of rim at the track's left / right end that aren't track color either
        self._snapshot_saved = False

    def observe(self, frame: Frame) -> Observation:
        f = self.calibration.features
        region = self.calibration.regions.get("REEL")
        if f is None:
            return self._invalid(frame.timestamp, "tracking not calibrated (press T)")
        if region is None:
            return self._invalid(frame.timestamp, "no REEL region")
        crop = crop_region(frame.image, region)
        if crop is None or crop.shape[1] < 16:
            return self._invalid(frame.timestamp, "REEL region too small")
        rx, ry, rw, _ = region.to_pixels(frame.image.shape[1], frame.image.shape[0])
        scale = crop.shape[1] / f.region_width
        s, t, reasons = self.settings, frame.timestamp, []

        fish = _find_fish(crop, f, scale)
        fish_conf = fish[4] if fish else 0.0
        fish_x = fish[0] / rw if fish and fish_conf >= s.fish_threshold else None
        if fish is None or fish_conf < s.fish_threshold:
            reasons.append("fish not found")
        fish_x, fish_v = self._fish.update(fish_x, t, s.max_jump)
        if fish and fish_conf >= s.fish_threshold and fish_x is None:
            reasons.append("fish jump unconfirmed")

        seen = fish if fish and fish_conf >= s.fish_threshold else None
        # By color, or by contrast: the bar-wide stretch that isn't empty track, for when the game recolors the
        # bar (e.g. while the fish is outside it). A shrinking bar also restyles, and a partial color match of
        # the new style would make its center jump, so for those rods contrast goes first once it is known.
        if self._track is None:
            modes = [None]
        else:
            modes = [self._track, None] if f.bar_shrinks else [None, self._track]
        for track in modes:
            if track is not None and modes[0] is None:
                self._save_snapshot(crop)
            bar = _find_bar(crop, f, scale, track, seen, self._ends)
            found = bar is not None and bar[4] >= s.bar_threshold
            if found:
                break
        if found and track is not None:
            reasons.append("bar found by contrast")
        if found and self._track is None:
            self._track = _sample_track(crop, bar, fish)
            if self._track is not None:
                self._ends = _track_ends(crop, f, self._track, bar)
        bar_conf = bar[4] if bar else 0.0
        if not found:
            reasons.append("bar not found")
        center, bar_v = self._bar.update((bar[0] + bar[1]) / 2 / rw if found else None, t, s.max_jump)
        if found and center is None:
            reasons.append("bar jump unconfirmed")
        left, right = (bar[0] / rw, bar[1] / rw) if center is not None else (None, None)

        fish_box = (rx + round(fish[0] - fish[2] / 2), ry + round(fish[1] - fish[3] / 2), fish[2], fish[3]) \
            if fish else None
        bar_box = (rx + bar[0], ry + bar[2], bar[1] - bar[0], bar[3] - bar[2]) if bar else None
        return Observation(t, fish_x, fish_v, fish_conf, fish_box, left, right, center, bar_v, bar_conf, bar_box,
                           ", ".join(reasons) or "ok")

    def _save_snapshot(self, crop: np.ndarray) -> None:
        """The REEL strip the first time the bar's color stops matching, to see what it turned into."""
        if self._snapshot_saved:
            return
        self._snapshot_saved = True
        try:
            BAR_LOST_PATH.parent.mkdir(exist_ok=True)
            if write_image(BAR_LOST_PATH, crop):
                log.info("Bar color stopped matching; saved the reel strip to %s", BAR_LOST_PATH)
        except OSError as e:
            log.warning("Could not save %s: %s", BAR_LOST_PATH.name, e)

    @staticmethod
    def _invalid(t: float, reason: str) -> Observation:
        return Observation(t, None, None, 0.0, None, None, None, None, None, 0.0, None, reason)


def _rows(band: tuple[float, float], height: int, pad: int = 0) -> tuple[int, int]:
    return max(0, int(band[0] * height) - pad), min(height, int(np.ceil(band[1] * height)) + pad)


def _runs(columns: np.ndarray):
    """(start, end) of each run of True columns."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], columns.astype(np.int8), [0]))))
    return zip(edges[::2], edges[1::2])


def _fish_columns(fill: np.ndarray, fish_fill: float) -> np.ndarray:
    columns = (fill >= min(FILL, FISH_FILL_SHARE * fish_fill)).astype(np.uint8)[None, :]
    return cv2.morphologyEx(columns, cv2.MORPH_CLOSE, np.ones((1, FISH_GAP), np.uint8))[0] > 0


def _find_fish(crop: np.ndarray, f: TrackFeatures, scale: float):
    """(center x, center y, w, h, confidence) in REEL-region pixels, or None: the run of fish-colored columns
    closest to the calibrated fish width. Works on any background, inside or outside the bar."""
    y0, y1 = _rows(f.fish_band, crop.shape[0])
    if y1 - y0 < 2:
        y0, y1 = 0, crop.shape[0]
    profile = f.fish_color.mask(crop[y0:y1]).mean(axis=0)
    expected = max(1.0, f.fish_width * f.region_width * scale)
    best = None
    for left, right in _runs(_fish_columns(profile, f.fish_fill)):
        ratio = min(right - left, expected) / max(right - left, expected)
        confidence = ratio * min(1.0, float(profile[left:right].mean()) / f.fish_fill)  # vs. as calibrated
        if best is None or confidence > best[4]:
            # Weighted by how much of each column matches: sub-pixel center, so less jitter in position and speed.
            center = float(np.average(np.arange(left, right) + 0.5, weights=profile[left:right]))
            best = (center, (y0 + y1) / 2, int(right - left), y1 - y0, confidence)
    return best


def _sample_track(crop: np.ndarray, bar, fish) -> TrackColor | None:
    """Color of the empty track: the bar's rows, minus the bar and the fish."""
    left, right, y0, y1, _ = bar
    columns = np.ones(crop.shape[1], bool)
    columns[left:right] = False
    if fish:
        columns[max(0, int(fish[0] - fish[2])):int(fish[0] + fish[2]) + 1] = False
    if columns.sum() < 10:
        return None  # the bar fills the track; try again next frame
    return TrackColor.median(crop[y0:y1][:, columns], TRACK_TOLERANCE)


def _track_ends(crop: np.ndarray, f: TrackFeatures, track: TrackColor, bar) -> tuple[int, int]:
    """Width of the rim at each end of the track that isn't track color, so a bar near an end isn't joined to it.
    Measured while the bar is away from that end; at most 3% of the track per end."""
    y0, y1 = _rows(f.bar_band, crop.shape[0])
    solid = (~track.mask(crop[y0:y1])).mean(axis=0) >= 0.5
    limit = max(1, int(0.03 * crop.shape[1]))
    left = int(np.argmin(solid[:limit])) if not solid[:limit].all() else limit
    right = int(np.argmin(solid[::-1][:limit])) if not solid[::-1][:limit].all() else limit
    return (left if bar[0] > left + 2 else 0), (right if bar[1] < crop.shape[1] - right - 2 else 0)


def _find_bar(crop: np.ndarray, f: TrackFeatures, scale: float, track: TrackColor | None = None, fish=None,
              ends: tuple[int, int] = (0, 0)):
    """(left, right, top, bottom, confidence) in REEL-region pixels, or None. By the bar's color, or with
    `track`, by everything that isn't the empty track's color. By contrast, columns of `fish` (from _find_fish)
    are not bar: the fish and its outline would otherwise widen the bar on its side. (By color the fish never
    matches, and blanking it would hide the bar's edge under it.)"""
    y0, y1 = _rows(f.bar_band, crop.shape[0])
    band = crop[y0:y1]
    matches = ~track.mask(band) if track else f.bar_mask(band)  # whole band, so a gradient lines up row by row
    inset = (y1 - y0) // 6  # skip the bar's top and bottom edge pixels, which blend with the track
    matches = matches[inset:max(inset + 1, y1 - y0 - inset)]
    y0, y1 = y0 + inset, max(y0 + inset + 1, y1 - inset)
    profile = matches.mean(axis=0)  # fraction of bar pixels per column
    if track is not None and fish is not None:  # core width again as outline: 2x its width, which the gap bridges
        profile[max(0, int(fish[0] - fish[2])):int(fish[0] + fish[2]) + 1] = 0.0
    if track is not None:  # the track's own end rims
        profile[:ends[0]] = 0.0
        profile[len(profile) - ends[1]:] = 0.0
    visible = profile >= 0.5
    gap = max(3, int(2 * f.fish_width * crop.shape[1]) + 4)  # bridge the fish (and any outline) covering the bar
    columns = cv2.morphologyEx(visible.astype(np.uint8)[None, :], cv2.MORPH_CLOSE, np.ones((1, gap), np.uint8))[0]
    expected = f.bar_width * crop.shape[1]
    best, best_score = None, -1.0
    for left, right in _runs(columns > 0):
        width = right - left
        if f.bar_shrinks:  # narrower than calibrated is normal; only wider counts against it
            if width < MIN_SHRINK * expected:
                continue
            ratio = min(1.0, expected / width)
        else:
            ratio = min(width, expected) / max(width, expected)
        shown = visible[left:right]  # columns hidden by the fish don't count against the fill
        confidence = ratio * float(profile[left:right][shown].mean()) if shown.any() else 0.0
        score = confidence * width if f.bar_shrinks else confidence  # a shrunk bar beats a small speck
        if score > best_score:
            best, best_score = (int(left), int(right), y0, y1, confidence), score
    return best
