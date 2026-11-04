from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from detection.catch_detector import CatchResult
from ui import theme
from ui.pages import duration, success_rate
from ui.widgets import Page, Panel, Stat, StatStrip, label

RESULTS = {CatchResult.CATCH_SUCCESS: ("Caught", theme.GREEN), CatchResult.CATCH_FAILED: ("Failed", theme.RED),
           CatchResult.UNKNOWN: ("Unknown", theme.TEXT_DIM)}


class StatisticsPage(Page):
    def __init__(self, ctx):
        super().__init__("Statistics", "This session's results, counted from real catch checks after each "
                                       "reel minigame.")
        self.ctx = ctx
        self.caught, self.failed, self.unknown = Stat("Total catches"), Stat("Failed"), Stat("Unknown")
        self.rate, self.elapsed = Stat("Success rate"), Stat("Session duration")
        self.body.addWidget(StatStrip([self.caught, self.failed, self.unknown, self.rate, self.elapsed]))
        history = Panel("Catch history")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "TIME", "RESULT", "DETAILS"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                       QHeaderView.ResizeToContents, QHeaderView.Stretch)):
            header.setSectionResizeMode(column, mode)
        self.empty = label("No attempts yet. Each reel minigame adds a row once its result is checked.",
                           "fieldHelp", wrap=True)
        history.body.addWidget(self.empty)
        history.body.addWidget(self.table, 1)
        self.body.addWidget(history, 1)
        self._rows = 0

    def refresh(self) -> None:
        cycle = self.ctx.runtime.cycle
        self.caught.set(str(cycle.counts[CatchResult.CATCH_SUCCESS]))
        self.failed.set(str(cycle.counts[CatchResult.CATCH_FAILED]))
        self.unknown.set(str(cycle.counts[CatchResult.UNKNOWN]))
        self.rate.set(success_rate(cycle.counts))
        self.elapsed.set(duration(self.ctx.bridge.runtime_seconds()))
        history = list(cycle.history)
        self.empty.setVisible(not history)
        for when, outcome in history[self._rows:]:  # newest first
            self.table.insertRow(0)
            text, color = RESULTS[outcome.result]
            for column, value in enumerate((str(outcome.attempt), when.strftime("%H:%M:%S"), text, outcome.reason)):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(QColor(color))
                self.table.setItem(0, column, item)
        self._rows = len(history)
