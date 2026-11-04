import logging

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from core.macro_engine import MacroState
from core.runtime import HOTKEY_STOP, HOTKEY_TOGGLE
from detection.catch_detector import CatchResult
from ui.pages import ROBLOX, STATUS_COLORS, duration, stage, success_rate
from ui.widgets import HotkeyRow, LogView, Page, Panel, Stat, StatStrip, Switch, label


class ReelInputToggle(QWidget):
    """Debug / active switch for reel control (the preview's M key); starts active every launch."""

    def __init__(self, ctx):
        super().__init__()
        self.reeler = ctx.runtime.reeler
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label("Send mouse input while reeling", "fieldLabel"))
        text.addWidget(label("Off = debug: decisions are shown, nothing is clicked. On at every launch.",
                             "fieldHelp", wrap=True))
        layout.addLayout(text, 1)
        self.switch = Switch(self.reeler.active)
        self.switch.toggled.connect(self._toggled)
        layout.addWidget(self.switch)

    def _toggled(self, on: bool) -> None:
        self.reeler.active = on
        logging.getLogger(__name__).info("Reel control %s", "ACTIVE: holds / releases the mouse" if on
                                         else "DEBUG: decisions only, no input")

    def refresh(self) -> None:
        if self.switch.isChecked() != self.reeler.active:
            self.switch.set_silently(self.reeler.active)


class DashboardPage(Page):
    def __init__(self, ctx):
        super().__init__("Dashboard", "Live status of the macro. It is controlled entirely with global hotkeys.")
        self.ctx = ctx
        self.status, self.roblox = Stat("Status", dot=True), Stat("Roblox", dot=True)
        self.stage, self.elapsed = Stat("Stage"), Stat("Session runtime")
        self.catches, self.rate = Stat("Catches"), Stat("Success rate")
        self.body.addWidget(StatStrip([self.status, self.roblox, self.stage, self.elapsed, self.catches, self.rate]))

        row = QHBoxLayout()
        row.setSpacing(12)
        activity = Panel("Activity")
        self.log = LogView()
        for line in ctx.logs.recent:
            self.log.add(*line)
        ctx.logs.line.connect(self.log.add)
        activity.body.addWidget(self.log, 1)
        row.addWidget(activity, 1)

        side = QVBoxLayout()
        side.setSpacing(12)
        keys = Panel("Hotkeys", spacing=14)
        self.toggle_key, self.stop_key = HotkeyRow(HOTKEY_TOGGLE.upper()), HotkeyRow(HOTKEY_STOP.upper())
        keys.body.addWidget(self.toggle_key)
        keys.body.addWidget(self.stop_key)
        keys.body.addWidget(label("Global: they work while Roblox is focused or this window is hidden.",
                                  "fieldHelp", wrap=True))
        side.addWidget(keys)
        reel = Panel("Reeling")
        self.reel_input = ReelInputToggle(ctx)
        reel.body.addWidget(self.reel_input)
        side.addWidget(reel)
        side.addStretch()
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(310)
        side.setContentsMargins(0, 0, 0, 0)
        row.addWidget(holder)
        self.body.addLayout(row, 1)

    def refresh(self) -> None:
        rt, bridge = self.ctx.runtime, self.ctx.bridge
        status = bridge.status()
        self.status.set(status, STATUS_COLORS[status])
        self.roblox.set(*ROBLOX[rt.capture.window_manager.info.state])
        self.stage.set(stage(self.ctx))
        self.elapsed.set(duration(bridge.runtime_seconds()))
        self.catches.set(str(rt.cycle.counts[CatchResult.CATCH_SUCCESS]))
        self.rate.set(success_rate(rt.cycle.counts))
        state, hides = rt.engine.state, rt.settings["app"].auto_hide
        running, paused = state is MacroState.RUNNING, state is MacroState.PAUSED
        if running:
            self.toggle_key.set("Pause", "Pauses fishing and releases every input.", True)
        elif paused:
            self.toggle_key.set("Resume", "Continues fishing and hides this window." if hides
                                else "Continues where it paused.", True)
        else:
            self.toggle_key.set("Start", "Starts fishing and hides this window." if hides else "Starts fishing.", True)
        self.stop_key.set("Stop", "Stops the macro and releases every input.", running or paused)
        self.reel_input.refresh()
