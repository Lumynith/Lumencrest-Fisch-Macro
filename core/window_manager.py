"""Finds and tracks the Roblox client window."""

import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum

import pywintypes
import win32api
import win32con
import win32gui
import win32process

log = logging.getLogger(__name__)

ROBLOX_EXE = "RobloxPlayerBeta.exe"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_user32.GetDpiForWindow.argtypes = (wintypes.HWND,)


class WindowState(Enum):
    CLOSED = "closed"
    MINIMIZED = "minimized"
    OPEN = "open"      # visible but not focused
    ACTIVE = "active"  # foreground window


@dataclass(frozen=True)
class WindowInfo:
    """Client-area geometry in physical screen pixels (same space mss uses)."""
    state: WindowState
    hwnd: int = 0
    pid: int = 0
    title: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    dpi_scale: float = 1.0

    @property
    def is_usable(self) -> bool:
        return self.state in (WindowState.OPEN, WindowState.ACTIVE) and self.width > 0 and self.height > 0


CLOSED = WindowInfo(WindowState.CLOSED)


def enable_dpi_awareness() -> bool:
    """Call once at startup, before any window is created, so coordinates are not virtualized by display scaling."""
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return True
    except AttributeError:
        pass
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        return bool(_user32.SetProcessDPIAware())


def _process_exe(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def find_window(exe_name: str = ROBLOX_EXE) -> int:
    """Return the main top-level window of exe_name, or 0 if none."""
    matches = []
    exe_by_pid = {}

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in exe_by_pid:
            exe_by_pid[pid] = _process_exe(pid)
        if exe_by_pid[pid].lower() == exe_name.lower():
            matches.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except pywintypes.error as e:
        log.warning("EnumWindows failed: %s", e)
        return 0
    # Prefer a titled window (the game), fall back to any match.
    titled = [h for h in matches if win32gui.GetWindowText(h)]
    return (titled or matches or [0])[0]


def get_window_info(hwnd: int) -> WindowInfo:
    """Snapshot hwnd's state and client area. Returns CLOSED if hwnd is gone or invalid."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return CLOSED
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd)
        dpi_scale = (_user32.GetDpiForWindow(hwnd) or 96) / 96
        if win32gui.IsIconic(hwnd):
            return WindowInfo(WindowState.MINIMIZED, hwnd, pid, title, dpi_scale=dpi_scale)
        _, _, width, height = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        state = WindowState.ACTIVE if win32gui.GetForegroundWindow() == hwnd else WindowState.OPEN
        return WindowInfo(state, hwnd, pid, title, x, y, width, height, dpi_scale)
    except pywintypes.error as e:
        # Window closed between calls.
        log.debug("Window %s became invalid: %s", hwnd, e)
        return CLOSED


def focus_window(hwnd: int) -> bool:
    """Brings a window to the foreground without restoring it if minimized. Windows refuses
    SetForegroundWindow from background processes; attaching to the foreground thread's input is the
    usual way around that without sending fake keys. Returns whether it is now the foreground window."""
    try:
        if not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
            return False
        foreground = win32gui.GetForegroundWindow()
        if foreground == hwnd:
            return True
        current = win32api.GetCurrentThreadId()
        other = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        attached = bool(other and other != current and win32process.AttachThreadInput(current, other, True))
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current, other, False)
        return win32gui.GetForegroundWindow() == hwnd
    except pywintypes.error as e:
        log.debug("Could not focus window %s: %s", hwnd, e)
        return False


class WindowManager:
    """Call update() periodically; it re-finds the window when needed and logs changes."""

    def __init__(self, exe_name: str = ROBLOX_EXE):
        self.exe_name = exe_name
        self.info = CLOSED

    def update(self) -> WindowInfo:
        info = get_window_info(self.info.hwnd)
        if info.state is WindowState.CLOSED:
            info = get_window_info(find_window(self.exe_name))
        self._log_changes(self.info, info)
        self.info = info
        return info

    @staticmethod
    def _log_changes(old: WindowInfo, new: WindowInfo) -> None:
        if old.hwnd != new.hwnd:
            if new.state is WindowState.CLOSED:
                log.info("Roblox window lost")
            else:
                log.info("Roblox window found: hwnd=%s pid=%s title=%r", new.hwnd, new.pid, new.title)
        if old.state != new.state:
            log.info("State: %s -> %s", old.state.value, new.state.value)
        # Minimized windows report 0 size; only compare geometry between visible states.
        if old.is_usable and new.is_usable:
            if (old.x, old.y) != (new.x, new.y):
                log.info("Moved: (%d, %d) -> (%d, %d)", old.x, old.y, new.x, new.y)
            if (old.width, old.height) != (new.width, new.height):
                log.info("Resized: %dx%d -> %dx%d", old.width, old.height, new.width, new.height)
        if old.dpi_scale != new.dpi_scale and new.state is not WindowState.CLOSED:
            log.info("DPI scale: %.2f -> %.2f", old.dpi_scale, new.dpi_scale)
