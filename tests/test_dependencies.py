"""Verifies every library in requirements.txt imports."""

import importlib
import unittest

# pip package -> import name
MODULES = {
    "opencv-python": "cv2",
    "numpy": "numpy",
    "mss": "mss",
    "PySide6": "PySide6.QtWidgets",
    "pyautogui": "pyautogui",
    "pywin32": "win32gui",
    "keyboard": "keyboard",
}


class TestDependencies(unittest.TestCase):
    def test_imports(self):
        for package, module in MODULES.items():
            with self.subTest(package=package):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
