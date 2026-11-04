"""Captures the Roblox client area on a background thread."""

import logging
import threading
import time
from dataclasses import dataclass

import cv2
import mss
import mss.exception
import numpy as np

from core.window_manager import WindowInfo, WindowManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    image: np.ndarray   # BGR, read-only; copy before drawing on it
    timestamp: float    # time.perf_counter() when captured
    frame_id: int
    window: WindowInfo  # window geometry the frame was captured with


class ScreenCapture:
    def __init__(self, window_manager: WindowManager | None = None, target_fps: int = 60,
                 max_frame_age: float = 0.25):
        self.window_manager = window_manager or WindowManager()
        self.target_fps = target_fps
        self.max_frame_age = max_frame_age
        self.fps = 0.0
        self._frame: Frame | None = None  # swapped whole, so readers never see a half-written frame
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ScreenCapture", daemon=True)
        self._thread.start()
        log.info("Capture started (target %d FPS)", self.target_fps)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("Capture thread did not stop within %.1fs", timeout)
            else:
                log.info("Capture stopped")
        self._thread = None
        self._frame = None
        self.fps = 0.0

    def get_frame(self, max_age: float | None = None) -> Frame | None:
        """Latest frame, or None if there is none or it is older than max_age seconds."""
        frame = self._frame
        limit = self.max_frame_age if max_age is None else max_age
        if frame is None or time.perf_counter() - frame.timestamp > limit:
            return None
        return frame

    def _run(self) -> None:
        interval = 1 / self.target_fps
        frame_id = 0
        count, fps_start = 0, time.perf_counter()
        # mss >= 10.2 renamed mss.mss to mss.MSS and deprecated the old name.
        with getattr(mss, "MSS", mss.mss)() as sct:
            while not self._stop.is_set():
                started = time.perf_counter()
                info = self.window_manager.update()
                if not info.is_usable:
                    self._frame, self.fps = None, 0.0
                    count, fps_start = 0, time.perf_counter()
                    self._stop.wait(0.2)
                    continue
                try:
                    shot = sct.grab({"left": info.x, "top": info.y, "width": info.width, "height": info.height})
                except mss.exception.ScreenShotError as e:
                    log.warning("Grab failed: %s", e)
                    self._frame = None
                    self._stop.wait(0.2)
                    continue
                image = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
                image.flags.writeable = False
                frame_id += 1
                self._frame = Frame(image, time.perf_counter(), frame_id, info)

                count += 1
                now = time.perf_counter()
                if now - fps_start >= 1:
                    self.fps = count / (now - fps_start)
                    count, fps_start = 0, now
                # time.sleep is high-resolution on Windows (3.11+); Event.wait is ~15ms granular.
                time.sleep(max(0.0, interval - (now - started)))
