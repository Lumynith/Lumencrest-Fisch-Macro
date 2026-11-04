"""Detection regions, stored normalized (0.0-1.0) to the Roblox client area."""

import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import CONFIG_PATH, read_config, write_section

log = logging.getLogger(__name__)

REGION_NAMES = ("SHAKE", "REEL")
MIN_SIZE = 0.005  # smallest region side, as a fraction of the client area


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    w: float
    h: float

    def clamped(self) -> "Region | None":
        """Clipped to the client area; None if nothing usable is left."""
        if not all(math.isfinite(v) for v in (self.x, self.y, self.w, self.h)):
            return None
        left, right = (min(max(v, 0.0), 1.0) for v in (self.x, self.x + self.w))
        top, bottom = (min(max(v, 0.0), 1.0) for v in (self.y, self.y + self.h))
        if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
            return None
        # Rounding stops float drift (0.3 -> 0.30000000000000004) building up across edits and saves.
        return Region(*(round(v, 6) for v in (left, top, right - left, bottom - top)))

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """(left, top, width, height) in pixels for a client area of the given size."""
        left, top = round(self.x * width), round(self.y * height)
        right, bottom = round((self.x + self.w) * width), round((self.y + self.h) * height)
        return left, top, max(1, right - left), max(1, bottom - top)


def load_calibration(path: Path = CONFIG_PATH) -> dict[str, Region]:
    try:
        raw = read_config(path).get("calibration", {})
    except (OSError, ValueError) as e:
        log.warning("Could not read %s (%s); starting without calibration", path.name, e)
        return {}
    if not isinstance(raw, dict):
        log.warning("Invalid calibration section in %s; ignoring it", path.name)
        return {}
    regions = {}
    for name in REGION_NAMES:
        item = raw.get(name)
        if item is None:
            continue
        try:
            region = Region(*(float(item[k]) for k in ("x", "y", "w", "h"))).clamped()
        except (TypeError, KeyError, ValueError):
            region = None
        if region is None:
            log.warning("Ignoring invalid %s region in %s", name, path.name)
        else:
            regions[name] = region
    return regions


def save_calibration(regions: dict[str, Region], path: Path = CONFIG_PATH) -> None:
    """Writes the calibration section, keeping any other settings in the file."""
    write_section("calibration", {name: asdict(regions[name]) for name in REGION_NAMES if name in regions}, path)
