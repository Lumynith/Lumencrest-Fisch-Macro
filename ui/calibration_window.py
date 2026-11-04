"""Calibration window: draw, move, resize and delete detection regions on a Roblox screenshot."""

import logging
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from core.calibration import CONFIG_PATH, REGION_NAMES, Region, save_calibration
from core.screen_capture import ScreenCapture
from core.window_manager import WindowState

log = logging.getLogger(__name__)

COLORS = {"SHAKE": QColor(255, 200, 0), "REEL": QColor(255, 80, 200)}
EDGE_TOLERANCE = 6  # widget pixels around a region edge that count as grabbing it
CURSORS = {
    "": Qt.CursorShape.SizeAllCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
    "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor,
}


class RegionCanvas(QWidget):
    """Shows the screenshot scaled to fit and edits regions in normalized client coordinates."""
    changed = Signal()         # an edit finished; regions should be saved
    active_changed = Signal()

    def __init__(self, regions: dict[str, Region]):
        super().__init__()
        self.regions = regions
        self.image: QImage | None = None
        self.active = REGION_NAMES[0]  # region that is drawn, moved or deleted
        self._drag = None  # (name, edges, start point, region at drag start); edges "new" = drawing
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 270)

    def set_image(self, image: QImage | None) -> None:
        self.image = image
        self.update()

    def set_active(self, name: str) -> None:
        if name != self.active:
            self.active = name
            self.active_changed.emit()
            self.update()

    def delete_active(self) -> None:
        if self.regions.pop(self.active, None):
            self.changed.emit()
            self.update()

    def image_rect(self) -> QRectF | None:
        """Where the screenshot is drawn: scaled to fit, aspect kept, centered."""
        if self.image is None:
            return None
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        w, h = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def to_norm(self, pos: QPointF) -> tuple[float, float]:
        r = self.image_rect()
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def to_widget(self, region: Region) -> QRectF:
        r = self.image_rect()
        return QRectF(r.x() + region.x * r.width(), r.y() + region.y * r.height(),
                      region.w * r.width(), region.h * r.height())

    def _hit(self, pos: QPointF) -> tuple[str | None, str]:
        """Region under pos and which of its edges ("l", "rb", ...) are grabbed; "" means the body."""
        t = EDGE_TOLERANCE
        for name in sorted(self.regions, key=lambda n: n != self.active):  # active region wins overlaps
            rect = self.to_widget(self.regions[name])
            if not rect.adjusted(-t, -t, t, t).contains(pos):
                continue
            edges = ""
            if abs(pos.x() - rect.left()) <= t:
                edges += "l"
            elif abs(pos.x() - rect.right()) <= t:
                edges += "r"
            if abs(pos.y() - rect.top()) <= t:
                edges += "t"
            elif abs(pos.y() - rect.bottom()) <= t:
                edges += "b"
            return name, edges
        return None, ""

    def mousePressEvent(self, event):
        rect = self.image_rect()
        if event.button() != Qt.MouseButton.LeftButton or rect is None:
            return
        pos = event.position()
        name, edges = self._hit(pos)
        if name is None:
            if not rect.contains(pos):
                return
            name, edges = self.active, "new"
        self.set_active(name)
        self._drag = (name, edges, self.to_norm(pos), self.regions.get(name))

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag is None:
            name, edges = self._hit(pos) if self.image is not None else (None, "")
            rect = self.image_rect()
            if name:
                self.setCursor(CURSORS[edges])
            elif rect is not None and rect.contains(pos):
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
            return
        name, edges, (sx, sy), start = self._drag
        nx, ny = self.to_norm(pos)
        dx, dy = nx - sx, ny - sy
        if edges == "new":
            region = Region(min(sx, nx), min(sy, ny), abs(nx - sx), abs(ny - sy))
        elif edges == "":
            region = Region(min(max(start.x + dx, 0.0), 1.0 - start.w),
                            min(max(start.y + dy, 0.0), 1.0 - start.h), start.w, start.h)
        else:
            left, top, right, bottom = start.x, start.y, start.x + start.w, start.y + start.h
            left += dx if "l" in edges else 0
            right += dx if "r" in edges else 0
            top += dy if "t" in edges else 0
            bottom += dy if "b" in edges else 0
            region = Region(min(left, right), min(top, bottom), abs(right - left), abs(bottom - top))
        region = region.clamped()  # keeps every region inside the Roblox viewport
        if region:
            self.regions[name] = region
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.changed.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_active()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        image_rect = self.image_rect()
        if image_rect is None:
            p.setPen(Qt.GlobalColor.white)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No screenshot.\nOpen Roblox (not minimized) and press Refresh Screenshot.")
            return
        p.drawImage(image_rect, self.image)
        metrics = p.fontMetrics()
        for name, region in self.regions.items():
            color = COLORS[name]
            rect = self.to_widget(region)
            fill = QColor(color)
            fill.setAlpha(40)
            p.setPen(QPen(color, 3 if name == self.active else 2))
            p.setBrush(fill)
            p.drawRect(rect)
            label = QRectF(rect.left(), rect.top(), metrics.horizontalAdvance(name) + 8, metrics.height() + 2)
            p.fillRect(label, color)
            p.setPen(Qt.GlobalColor.black)
            p.drawText(label, Qt.AlignmentFlag.AlignCenter, name)
            if name == self.active:
                for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
                    p.fillRect(QRectF(corner.x() - 4, corner.y() - 4, 8, 8), color)


class CalibrationWindow(QWidget):
    def __init__(self, capture: ScreenCapture, regions: dict[str, Region], config_path=CONFIG_PATH):
        super().__init__()
        self.capture = capture
        self.config_path = config_path
        self.client_size: tuple[int, int] | None = None
        self.roblox_was_active = True
        self.setWindowTitle("FischMacro Calibration")
        self.resize(1100, 700)

        self.canvas = RegionCanvas(regions)
        self.canvas.changed.connect(self._save)
        self.canvas.active_changed.connect(self._sync_buttons)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Region:"))
        self.region_buttons = QButtonGroup(self)
        for name in REGION_NAMES:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setStyleSheet(f"QPushButton:checked {{ background: {COLORS[name].name()}; color: black; }}")
            button.clicked.connect(lambda _=False, n=name: self.canvas.set_active(n))
            self.region_buttons.addButton(button)
            toolbar.addWidget(button)
        toolbar.addStretch()
        for text, slot in (("Delete Region", self.canvas.delete_active),
                           ("Refresh Screenshot", self.refresh_screenshot),
                           ("Reset Calibration", self._reset)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status)
        self._sync_buttons()

    def open(self) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()
        else:
            self.refresh_screenshot()

    def refresh_screenshot(self) -> None:
        # Hide first, so the screenshot shows Roblox and not this window on top of it.
        self._hidden_at = time.perf_counter()
        self.hide()
        QTimer.singleShot(250, lambda: self._grab(attempts=10))

    def _grab(self, attempts: int) -> None:
        frame = self.capture.get_frame(max_age=1.0)
        if frame is None or frame.timestamp < self._hidden_at + 0.1:  # older frames may still show this window
            if attempts:
                QTimer.singleShot(100, lambda: self._grab(attempts - 1))
                return
            self.canvas.set_image(None)
            self.client_size = None
        else:
            h, w = frame.image.shape[:2]
            self.canvas.set_image(QImage(frame.image.tobytes(), w, h, w * 3, QImage.Format.Format_BGR888).copy())
            self.client_size = (frame.window.width, frame.window.height)
            self.roblox_was_active = frame.window.state is WindowState.ACTIVE
        self._update_status()
        self.show()
        self.raise_()
        self.activateWindow()

    def _save(self) -> None:
        try:
            save_calibration(self.canvas.regions, self.config_path)
            log.info("Calibration saved: %s", ", ".join(self.canvas.regions) or "no regions")
        except OSError as e:
            log.error("Saving calibration failed: %s", e)
            QMessageBox.warning(self, "Save failed", f"Could not save calibration:\n{e}")
        self._update_status()

    def _reset(self) -> None:
        answer = QMessageBox.question(self, "Reset calibration", "Delete all calibration regions?")
        if answer == QMessageBox.StandardButton.Yes:
            self.canvas.regions.clear()
            self.canvas.update()
            self._save()

    def _sync_buttons(self) -> None:
        for button in self.region_buttons.buttons():
            button.setChecked(button.text() == self.canvas.active)
        self._update_status()

    def _update_status(self) -> None:
        if self.client_size is None:
            size_text = "Roblox not captured"
        else:
            size_text = f"Roblox client {self.client_size[0]}x{self.client_size[1]}"
        parts = [size_text]
        for name in REGION_NAMES:
            region = self.canvas.regions.get(name)
            if region is None:
                parts.append(f"{name}: not set")
            elif self.client_size:
                parts.append(f"{name}: {region.to_pixels(*self.client_size)} px")
            else:
                parts.append(f"{name}: set")
        text = " | ".join(parts) + f"\nDrag on the screenshot to draw {self.canvas.active}. " \
            "Drag a region to move it, drag its edges or corners to resize, Delete to remove."
        if self.client_size and not self.roblox_was_active:
            text += "\nRoblox was not the active window; if other windows cover the screenshot, " \
                "click into Roblox, then press Refresh Screenshot."
        self.status.setText(text)
