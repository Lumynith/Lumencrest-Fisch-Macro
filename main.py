"""FischMacro application entry point."""

import ctypes
import logging
import signal
import sys

import cv2
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.config import APP_DIR
from core.runtime import Runtime
from core.window_manager import enable_dpi_awareness
from ui.bridge import LogHub
from ui.main_window import MainWindow, app_icon
from ui.theme import STYLESHEET


def main() -> int:
    handlers = [logging.StreamHandler()] if sys.stderr else []  # the packaged .exe has no console
    try:  # this run's log, for bug reports
        (APP_DIR / "logs").mkdir(exist_ok=True)
        handlers.append(logging.FileHandler(APP_DIR / "logs" / "lumencrest.log", mode="w", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
                        handlers=handlers or None)
    logs = LogHub()  # before anything logs, so the UI shows startup messages too
    logging.getLogger().addHandler(logs)
    enable_dpi_awareness()
    try:  # its own taskbar entry and icon, instead of grouping under python.exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Lumencrest.FischMacro")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Lumencrest Fisch Macro")
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(False)  # the window hides while the macro runs; quitting is explicit
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer(interval=200, timeout=lambda: None)  # lets Python see Ctrl+C while Qt's loop runs
    wake.start()

    runtime = Runtime()
    try:
        runtime.start()
        window = MainWindow(runtime, logs)
        window.show()
        app.exec()
    finally:
        runtime.shutdown()  # stops the engine (releasing inputs), capture and hotkeys
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
