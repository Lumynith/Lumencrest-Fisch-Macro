"""The frameless main window: title bar, icon sidebar, pages, tray icon and auto-hide wiring."""

import ctypes
import logging
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QMenu, QStackedWidget, QSystemTrayIcon,
                               QToolButton, QVBoxLayout, QWidget)

from core.config import BUNDLE_DIR
from core.macro_engine import MacroState
from core.runtime import Runtime
from core.window_manager import WindowState, focus_window, get_window_info
from ui import icons, theme
from ui.bridge import LogHub, MacroBridge, VisibilityController
from ui.calibration_window import CalibrationWindow
from ui.pages import STATUS_COLORS
from ui.pages.calibration import CalibrationPage
from ui.pages.dashboard import DashboardPage
from ui.pages.debug import DebugPage
from ui.pages.profiles import ProfilesPage
from ui.pages.settings import SettingsPage
from ui.pages.statistics import StatisticsPage
from ui.preview import Preview
from ui.widgets import Dot, label

log = logging.getLogger(__name__)

TITLE = "Lumencrest — Fisch Macro"
NAV = [("dashboard", "Dashboard", DashboardPage), ("settings", "Settings", SettingsPage),
       ("calibration", "Calibration", CalibrationPage), ("profiles", "Profiles", ProfilesPage),
       ("statistics", "Statistics", StatisticsPage), ("debug", "Debug", DebugPage)]
GRIP = 5  # px of window edge that resizes
ICON_PATH = BUNDLE_DIR / "icon.ico"


@dataclass
class AppContext:
    """What the pages share."""
    runtime: Runtime
    bridge: MacroBridge
    logs: LogHub
    preview: Preview
    open_regions: object  # () -> None
    run_capture: object   # (action) -> None


def app_icon() -> QIcon:
    icon = QIcon(str(ICON_PATH))
    if not icon.isNull():
        return icon
    image = QPixmap(64, 64)  # icon.ico missing: a drawn stand-in
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    p.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI", 30)
    font.setWeight(QFont.DemiBold)
    p.setFont(font)
    p.drawText(QRectF(4, 2, 56, 56), Qt.AlignCenter, "L")
    p.end()
    return QIcon(image)


class TitleBar(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.window_ = window
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(8)
        mark = label()
        mark.setPixmap(app_icon().pixmap(18, 18))
        layout.addWidget(mark)
        layout.addWidget(label("Lumencrest", "brand"))
        layout.addWidget(label("—  Fisch Macro", "brandSub"))
        layout.addStretch()
        self.dot = Dot()
        self.status = label("Idle", "brandSub")
        layout.addWidget(self.dot)
        layout.addWidget(self.status)
        layout.addSpacing(14)
        self.buttons = {}
        for name, slot in (("minimize", window.showMinimized), ("maximize", window.toggle_maximized),
                           ("close", window.close)):
            button = QToolButton()
            button.setObjectName("closeButton" if name == "close" else "winButton")
            button.setIcon(icons.icon(name, theme.TEXT_DIM, 16))
            button.setFixedSize(46, 36)
            button.clicked.connect(slot)
            layout.addWidget(button)
            self.buttons[name] = button

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.dot.set_color(STATUS_COLORS[text])

    def set_maximized(self, maximized: bool) -> None:
        self.buttons["maximize"].setIcon(icons.icon("restore" if maximized else "maximize", theme.TEXT_DIM, 16))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window_.windowHandle().startSystemMove()  # native drag, including Aero snap

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window_.toggle_maximized()


class Grip(QWidget):
    """Invisible strip along an edge or corner that starts a native resize."""

    CURSORS = {Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor, Qt.TopEdge: Qt.SizeVerCursor,
               Qt.BottomEdge: Qt.SizeVerCursor}

    def __init__(self, parent: QWidget, edges):
        super().__init__(parent)
        self.edges = edges
        corner_main = Qt.SizeFDiagCursor if edges in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge) \
            else Qt.SizeBDiagCursor
        self.setCursor(self.CURSORS.get(edges, corner_main))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemResize(self.edges)


class MainWindow(QWidget):
    def __init__(self, runtime: Runtime, logs: LogHub):
        super().__init__()
        self.runtime = runtime
        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle(TITLE)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 580)
        self.resize(1120, 720)

        self.bridge = MacroBridge(runtime.engine)
        self._regions: CalibrationWindow | None = None
        self.preview = Preview(runtime, self.open_regions)
        self.ctx = AppContext(runtime, self.bridge, logs, self.preview, self.open_regions, self.run_capture)

        self.title_bar = TitleBar(self)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WA_StyledBackground)
        sidebar.setFixedWidth(56)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(10, 12, 10, 12)
        nav.setSpacing(6)
        self.stack = QStackedWidget()
        self.pages = []
        group = QButtonGroup(self)
        for i, (icon_name, name, page_cls) in enumerate(NAV):
            page = page_cls(self.ctx)
            self.pages.append(page)
            self.stack.addWidget(page)
            button = QToolButton()
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolTip(name)
            button.setIcon(icons.icon(icon_name, theme.TEXT_FAINT, 20, checked_color=theme.ACCENT_HOVER))
            button.setFixedSize(36, 36)
            button.setIconSize(button.size() * 0.55)
            button.clicked.connect(lambda _=False, i=i: self._show_page(i))
            group.addButton(button)
            nav.addWidget(button)
            if i == 0:
                button.setChecked(True)
        nav.addStretch()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(sidebar)
        body.addWidget(self.stack, 1)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)  # the 1px window border
        root.setSpacing(0)
        root.addWidget(self.title_bar)
        root.addLayout(body, 1)

        self.grips = [Grip(self, edges) for edges in (
            Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge, Qt.TopEdge | Qt.LeftEdge,
            Qt.TopEdge | Qt.RightEdge, Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge)]

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(TITLE)
        menu = QMenu()
        menu.addAction("Show window", self.bring_to_front)
        menu.addSeparator()
        menu.addAction("Quit", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.DoubleClick and self.bring_to_front())
        self.tray.show()  # a way back in even if the hotkeys stopped working while the window was hidden

        self.visibility = VisibilityController(
            runtime.engine, self.hide_for_run, self.restore_after_run,
            enabled=lambda: runtime.settings["app"].auto_hide,
            focus_roblox=lambda: focus_window(runtime.capture.window_manager.info.hwnd),
            roblox_active=lambda: get_window_info(runtime.capture.window_manager.info.hwnd).state
            is WindowState.ACTIVE)
        self.bridge.state_changed.connect(self.visibility.on_state_changed)
        self.bridge.state_changed.connect(lambda _: self._refresh())

        self._ui_timer = QTimer(self, interval=250, timeout=self._refresh)
        self._ui_timer.start()
        self._preview_timer = QTimer(self, interval=15, timeout=self.preview.tick)
        self._preview_timer.start()
        self._config_timer = QTimer(self, interval=1000, timeout=runtime.poll)
        self._config_timer.start()
        self._refresh()

    # --- navigation and refresh

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.pages[index].refresh()

    def _refresh(self) -> None:
        status = self.bridge.status()
        self.title_bar.set_status(status)
        self.tray.setToolTip(f"{TITLE}  ·  {status}")
        if self.isVisible():
            self.stack.currentWidget().refresh()

    # --- window chrome

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())
            for grip in self.grips:
                grip.setVisible(not self.isMaximized())
        super().changeEvent(event)

    def resizeEvent(self, event):
        w, h, g = self.width(), self.height(), GRIP
        rects = [(0, g, g, h - 2 * g), (w - g, g, g, h - 2 * g), (g, 0, w - 2 * g, g), (g, h - g, w - 2 * g, g),
                 (0, 0, g, g), (w - g, 0, g, g), (0, h - g, g, g), (w - g, h - g, g, g)]
        for grip, rect in zip(self.grips, rects):
            grip.setGeometry(*rect)
            grip.raise_()
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        try:  # Windows 11 rounded corners and shadow for a frameless window; ignored elsewhere
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 33, ctypes.byref(ctypes.c_int(2)), 4)
        except (AttributeError, OSError):
            pass

    def closeEvent(self, event):
        self.preview.close()
        if self._regions:
            self._regions.close()
        self.tray.hide()
        QApplication.quit()  # main() then stops the engine, capture and hotkeys
        event.accept()

    # --- visibility

    def hide_for_run(self) -> None:
        self.preview.suspend()
        if self._regions and self._regions.isVisible():
            self._regions.hide()
        self.hide()

    def restore_after_run(self) -> None:
        self.bring_to_front()
        self.preview.resume()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        focus_window(int(self.winId()))  # activateWindow alone only flashes the taskbar from the background
        self._refresh()

    # --- tools the pages use

    def open_regions(self) -> None:
        if self._regions is None:
            self._regions = CalibrationWindow(self.runtime.capture, self.runtime.regions)
        self._regions.open()

    def run_capture(self, action) -> None:
        """Hides this window so the screenshot shows Roblox, runs a preview capture tool, then comes back."""
        if self.runtime.engine.state is MacroState.RUNNING:
            log.warning("Pause the macro (F6) before capturing")
            return
        hidden_at = time.perf_counter()
        self.hide()

        def run():
            self.preview.not_before = hidden_at + 0.15  # frames older than this may still show this window
            try:
                action()
            finally:
                self.bring_to_front()
        QTimer.singleShot(350, run)
