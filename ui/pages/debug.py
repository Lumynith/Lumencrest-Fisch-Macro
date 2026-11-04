from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton

from core.macro_engine import MacroState
from core.state_machine import Phase
from ui.pages import stage
from ui.widgets import LogView, Page, Panel, label

ROWS = ["Macro", "Reel minigame", "Capture", "Shake match", "Fish", "Control bar", "Controller", "Reel input"]


def _num(v, fmt=".3f") -> str:
    return "—" if v is None else format(v, fmt)


class DebugPage(Page):
    def __init__(self, ctx):
        super().__init__("Debug", "Live detection and controller readings from the running macro.")
        self.ctx = ctx
        self.preview_button = QPushButton("Open live preview")
        self.preview_button.setToolTip("The OpenCV preview with every detector's overlay and the capture keys")
        self.preview_button.clicked.connect(self._toggle_preview)
        readings = Panel("Live readings", self.preview_button)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.values = {}
        for i, name in enumerate(ROWS):
            grid.addWidget(label(name, "fieldHelp"), i, 0)
            self.values[name] = label("—", "mono")
            grid.addWidget(self.values[name], i, 1)
        grid.setColumnStretch(1, 1)
        readings.body.addLayout(grid)
        self.hint = label("", "fieldHelp", wrap=True)
        readings.body.addWidget(self.hint)
        logs = Panel("Log")
        self.log = LogView()
        for line in ctx.logs.recent:
            self.log.add(*line)
        ctx.logs.line.connect(self.log.add)
        logs.body.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(readings, 2)
        row.addWidget(logs, 3)
        self.body.addLayout(row, 1)

    def _toggle_preview(self) -> None:
        preview = self.ctx.preview
        preview.close() if preview.is_open else preview.open()

    def refresh(self) -> None:
        rt = self.ctx.runtime
        cycle, running = rt.cycle, rt.engine.state is MacroState.RUNNING
        self.preview_button.setText("Close live preview" if self.ctx.preview.is_open else "Open live preview")
        v = self.values
        v["Macro"].setText(f"{self.ctx.bridge.status()}  ·  {stage(self.ctx)}")
        reel = cycle.reel_detection if running else None
        v["Reel minigame"].setText("—" if reel is None else
                                   f"{reel.state.name}  conf {reel.confidence:.2f}  score {_num(reel.score, '.2f')}")
        v["Capture"].setText(f"{rt.capture.fps:.1f} FPS")
        shake = rt.shake.last_confidence if running and cycle.phase is Phase.SHAKING else None
        v["Shake match"].setText("—" if shake is None else
                                 f"{shake:.2f}  (need {rt.shake.settings.threshold:.2f})")
        obs = cycle.observation if running else None
        v["Fish"].setText("—" if obs is None else
                          f"x {_num(obs.fish_x)}  v {_num(obs.fish_velocity, '+.2f')}/s  conf {obs.fish_confidence:.2f}")
        v["Control bar"].setText("—" if obs is None else
                                 f"x {_num(obs.bar_center)}  v {_num(obs.bar_velocity, '+.2f')}/s  "
                                 f"conf {obs.bar_confidence:.2f}")
        d = rt.reeler.last if running and cycle.phase is Phase.REELING else None
        if d is None:
            v["Controller"].setText("—")
        else:
            action = f"SPAM {d.duty:.0%}" if d.sticky else "HOLD" if d.hold else "RELEASE"
            v["Controller"].setText(f"{action}  error {_num(d.error, '+.3f')}  output {_num(d.output, '+.3f')}  "
                                    f"·  {d.reason}")
        v["Reel input"].setText("ACTIVE (sends input)" if rt.reeler.active else "DEBUG (no input)")
        self.hint.setText("" if running else "Readings appear while the macro runs. The live preview shows "
                                             "detection at any time.")
