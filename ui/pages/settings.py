from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from core.runtime import HOTKEY_STOP, HOTKEY_TOGGLE
from ui.pages.dashboard import ReelInputToggle
from ui.widgets import HotkeyRow, Page, Panel, SettingsForm, label

# (nav name, [(panel title, config section, only these fields or None)])
SECTIONS = [
    ("Casting", [("Cast", "cast", None)]),
    ("Shaking", [("Shake buttons", "shake", None)]),
    ("Reeling", [("Reel controller", "reel_control", None)]),
    ("Detection", [("Reel minigame", "reel", None), ("Fish and bar tracking", "tracking", None),
                   ("Catch result", "catch", None)]),
    ("Hotkeys", []),
    ("General", [("Window", "app", None)]),
]


class SettingsPage(Page):
    def __init__(self, ctx):
        super().__init__("Settings", "Changes save automatically and apply to the running macro straight away.")
        self.forms: list[SettingsForm] = []
        self.toggles: list[ReelInputToggle] = []
        row = QHBoxLayout()
        row.setSpacing(16)
        nav = QListWidget()
        nav.setObjectName("sectionNav")
        nav.setFixedWidth(170)
        stack = QStackedWidget()
        for name, panels in SECTIONS:
            nav.addItem(name)
            content = QWidget()
            column = QVBoxLayout(content)
            column.setContentsMargins(0, 0, 8, 0)
            column.setSpacing(12)
            for title, section, only in panels:
                panel = Panel(title)
                form = SettingsForm(ctx.runtime, section, only)
                self.forms.append(form)
                panel.body.addWidget(form)
                column.addWidget(panel)
            if name == "Reeling":
                column.insertWidget(0, self._reel_mode(ctx))
            elif name == "Hotkeys":
                column.addWidget(self._hotkeys())
            column.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            stack.addWidget(scroll)
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)
        row.addWidget(nav, 0)
        row.addWidget(stack, 1)
        self.body.addLayout(row, 1)

    def _reel_mode(self, ctx) -> Panel:
        panel = Panel("Reel input", spacing=14)
        toggle = ReelInputToggle(ctx)
        self.toggles.append(toggle)
        panel.body.addWidget(toggle)
        direction = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label("Hold direction", "fieldLabel"))
        text.addWidget(label("During the next minigame (macro running), holds the mouse for 0.3 s and saves "
                             "which way the bar moved.", "fieldHelp", wrap=True))
        direction.addLayout(text, 1)
        button = QPushButton("Calibrate")
        button.clicked.connect(ctx.runtime.reeler.request_direction_calibration)
        direction.addWidget(button)
        panel.body.addLayout(direction)
        return panel

    @staticmethod
    def _hotkeys() -> Panel:
        panel = Panel("Global hotkeys", spacing=14)
        for key, title, text in ((HOTKEY_TOGGLE, "Start / Pause / Resume", "Starts the macro, or pauses / resumes it."),
                                 (HOTKEY_STOP, "Stop", "Stops at once and releases every held input.")):
            row = HotkeyRow(key.upper())
            row.set(title, text, True)
            panel.body.addWidget(row)
        panel.body.addWidget(label("Hotkeys are system-wide, so they work while Roblox is focused and while this "
                                   "window is hidden.", "fieldHelp", wrap=True))
        return panel

    def refresh(self) -> None:
        for form in self.forms:
            form.refresh()
        for toggle in self.toggles:
            toggle.refresh()
