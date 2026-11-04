"""Small OpenCV helpers shared by detectors."""

from pathlib import Path

import cv2
import numpy as np

from core.calibration import Region


def crop_region(image: np.ndarray, region: Region) -> np.ndarray | None:
    """Region of a client-area image, scaled to that image's resolution; None if empty."""
    h, w = image.shape[:2]
    left, top, cw, ch = region.to_pixels(w, h)
    crop = image[top:top + ch, left:left + cw]
    return crop if crop.size else None


# cv2.imread/imwrite fail on non-ASCII Windows paths; going through numpy avoids that.
def read_image(path: Path) -> np.ndarray | None:
    try:
        return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def write_image(path: Path, image: np.ndarray) -> bool:
    ok, data = cv2.imencode(path.suffix, image)
    if ok:
        data.tofile(path)
    return ok
