"""Window manager self-checks that do not need Roblox running."""

import os
import unittest

import win32gui

from core.window_manager import CLOSED, WindowManager, WindowState, find_window, get_window_info


class TestWindowManager(unittest.TestCase):
    def test_missing_process_is_closed(self):
        self.assertEqual(WindowManager("definitely_not_running.exe").update(), CLOSED)

    def test_invalid_hwnd_is_closed(self):
        self.assertEqual(get_window_info(0), CLOSED)
        self.assertEqual(get_window_info(0x7FFFFFF0), CLOSED)

    def test_finds_explorer_desktop_window(self):
        # explorer.exe always owns a visible top-level window (taskbar).
        hwnd = find_window("explorer.exe")
        self.assertTrue(win32gui.IsWindow(hwnd))
        self.assertNotEqual(get_window_info(hwnd).state, WindowState.CLOSED)


if __name__ == "__main__":
    unittest.main()
