import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QListView, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from core.calibration import REGION_NAMES
from detection.shake_detector import TEMPLATE_DIR
from ui import icons, theme
from ui.widgets import IntBox, Page, Panel, SettingsForm, Switch, label

log = logging.getLogger(__name__)

THRESHOLDS = [("Shake button", "shake", ("threshold",)), ("Reel minigame", "reel", ("active_threshold",
                                                                                    "inactive_threshold")),
              ("Fish and bar", "tracking", ("fish_threshold", "bar_threshold")), ("Catch result", "catch", ("threshold",))]
BAR_TYPES = [("solid", "One color"), ("two_color", "Two colors (left / right)"), ("gradient", "Gradient (top to bottom)")]


class TemplateGroup(QWidget):
    """Thumbnails of one kind of reference image, with capture and delete."""

    def __init__(self, title: str, help_text: str, pattern: str, capture, reload):
        super().__init__()
        self.pattern, self.reload = pattern, reload
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = label(title, "fieldLabel")
        text.addWidget(self.title)
        text.addWidget(label(help_text, "fieldHelp", wrap=True))
        header.addLayout(text, 1)
        self.delete = QPushButton(icons.icon("trash", theme.TEXT_DIM, 15), "")
        self.delete.setToolTip("Delete the selected image")
        self.delete.setProperty("danger", True)
        self.delete.clicked.connect(self._delete)
        add = QPushButton(icons.icon("camera", theme.TEXT, 15), " Capture")
        add.clicked.connect(capture)
        header.addWidget(self.delete)
        header.addWidget(add)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setFlow(QListView.LeftToRight)
        self.list.setWrapping(False)
        self.list.setIconSize(QSize(120, 56))
        self.list.setFixedHeight(92)
        self.list.setMovement(QListView.Static)
        self.list.itemSelectionChanged.connect(self._sync)
        layout.addWidget(self.list)
        self._names: list[str] = []
        self.load()

    def load(self) -> None:
        paths = sorted(TEMPLATE_DIR.glob(self.pattern))
        if [p.name for p in paths] == self._names:
            return
        self._names = [p.name for p in paths]
        self.list.clear()
        for path in paths:
            item = QListWidgetItem(QIcon(QPixmap(str(path))), "")
            item.setToolTip(path.name)
            item.setData(Qt.UserRole, str(path))
            self.list.addItem(item)
        self.title.setText(f"{self.title.text().split(' (')[0]} ({len(paths)})")
        self._sync()

    def _sync(self) -> None:
        self.delete.setEnabled(bool(self.list.selectedItems()))

    def _delete(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        path = Path(items[0].data(Qt.UserRole))
        if QMessageBox.question(self, "Delete reference image", f"Delete {path.name}?") != QMessageBox.Yes:
            return
        try:
            path.unlink()
            log.info("Deleted %s", path.name)
        except OSError as e:
            log.warning("Could not delete %s: %s", path.name, e)
        self.reload()
        self.load()


class ColorRow(QWidget):
    def __init__(self, ctx, part: str, title: str):
        super().__init__()
        self.ctx, self.part = ctx, part
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.title = label(title, "fieldLabel")
        layout.addWidget(self.title, 1)
        self.swatch = label()
        self.swatch.setFixedSize(22, 22)
        self.hex = label("—", "mono")
        self.hex.setFixedWidth(70)
        layout.addWidget(self.swatch)
        layout.addWidget(self.hex)
        layout.addWidget(label("±", "fieldHelp"))
        self.tolerance = IntBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setFixedWidth(56)
        self.tolerance.setAlignment(Qt.AlignRight)
        self.tolerance.setToolTip("How far each color channel may differ (0-255)")
        self.tolerance.editingFinished.connect(self._set_tolerance)
        layout.addWidget(self.tolerance)
        self.pick = QPushButton(icons.icon("pipette", theme.TEXT, 15), " Pick")
        self.pick.setToolTip("Eyedropper on a fresh screenshot")
        self.pick.clicked.connect(lambda: ctx.run_capture(lambda: ctx.preview.pick_track_color(part)))
        layout.addWidget(self.pick)

    def refresh(self) -> None:
        f = self.ctx.runtime.tracking.features
        color = None if f is None else {"fish": f.fish_color, "bar": f.bar_color, "bar2": f.bar_color2}[self.part]
        self.tolerance.setEnabled(color is not None)
        if color is None:
            self.hex.setText("—")
            self.swatch.setStyleSheet(f"border: 1px dashed {theme.BORDER_STRONG}; border-radius: 5px;")
            return
        gradient = self.part == "bar" and f.gradient_bar  # learned by boxing the bar, so no eyedropper
        self.pick.setVisible(not gradient)
        if gradient:
            n = len(f.bar_profile) - 1
            fill = "qlineargradient(x1:0, y1:0, x2:0, y2:1, " + ", ".join(
                f"stop:{i / n:.3f} {c.hex}" for i, c in enumerate(f.bar_profile)) + ")"
        else:
            fill = color.hex
        self.hex.setText("gradient" if gradient else color.hex)
        self.swatch.setStyleSheet(f"background: {fill}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 5px;")
        if not self.tolerance.hasFocus() and self.tolerance.value() != color.tolerance:
            self.tolerance.setValue(color.tolerance)

    def _set_tolerance(self) -> None:
        try:
            self.ctx.runtime.tracking.set_color(self.part, tolerance=self.tolerance.value())
        except (ValueError, OSError) as e:
            log.warning("Could not set the %s tolerance: %s", self.part, e)


def _option(title: str, help_text: str, control: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    text = QVBoxLayout()
    text.setSpacing(1)
    text.addWidget(label(title, "fieldLabel"))
    text.addWidget(label(help_text, "fieldHelp", wrap=True))
    layout.addLayout(text, 1)
    layout.addWidget(control)
    return row


class BarOptions(QWidget):
    """Control bar type and shrinking, for rods with a multicolored or shrinking bar."""

    def __init__(self, ctx):
        super().__init__()
        self.tracking = ctx.runtime.tracking
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.style = QComboBox()
        for key, text in BAR_TYPES:
            self.style.addItem(text, key)
        self.style.setMinimumWidth(200)
        self.style.currentIndexChanged.connect(self._style)
        layout.addWidget(_option("Control bar type", "Gradient is for bars whose colors change from top to bottom; "
                                 "it is learned with Calibrate fish & bar.", self.style))
        self.shrinks = Switch()
        self.shrinks.toggled.connect(self._shrinks)
        layout.addWidget(_option("Bar shrinks", "Some rods make the bar smaller, and change its look, while the fish is outside it.",
                                 self.shrinks))

    def _style(self, index: int) -> None:
        try:
            self.tracking.set_bar_style(self.style.itemData(index))
        except (ValueError, OSError) as e:
            log.warning("Could not change the bar type: %s", e)
            self.refresh()

    def _shrinks(self, on: bool) -> None:
        try:
            self.tracking.set_bar_shrinks(on)
        except (ValueError, OSError) as e:
            log.warning("Could not change the shrinking bar setting: %s", e)
            self.shrinks.set_silently(not on)

    def refresh(self) -> None:
        f = self.tracking.features
        self.style.setEnabled(f is not None)
        self.shrinks.setEnabled(f is not None)
        index = self.style.findData(f.bar_style if f else "solid")
        if self.style.currentIndex() != index:
            self.style.blockSignals(True)
            self.style.setCurrentIndex(index)
            self.style.blockSignals(False)
        if f is not None and self.shrinks.isChecked() != f.bar_shrinks:
            self.shrinks.set_silently(f.bar_shrinks)


class CalibrationPage(Page):
    def __init__(self, ctx):
        super().__init__("Calibration", "Screen regions, the colors used to track the reel minigame, reference "
                                        "images and detection thresholds.")
        self.ctx = ctx
        rt = ctx.runtime
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(12)

        edit = QPushButton("Edit regions")
        edit.clicked.connect(ctx.open_regions)
        regions = Panel("Screen regions", edit)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.region_labels = {}
        for row, name in enumerate(REGION_NAMES):
            grid.addWidget(label(name, "fieldLabel"), row, 0)
            self.region_labels[name] = label("", "mono")
            grid.addWidget(self.region_labels[name], row, 1)
        grid.setColumnStretch(1, 1)
        regions.body.addLayout(grid)
        column.addWidget(regions)

        recalibrate = QPushButton("Calibrate fish && bar")
        recalibrate.setToolTip("Box the fish and the whole control bar on a screenshot of the minigame")
        recalibrate.clicked.connect(lambda: ctx.run_capture(ctx.preview.calibrate_tracking))
        tracking = Panel("Reel tracking", recalibrate)
        self.bar_options = BarOptions(ctx)
        self.colors = [ColorRow(ctx, "fish", "Fish line color"), ColorRow(ctx, "bar", "Control bar color"),
                       ColorRow(ctx, "bar2", "Control bar color 2 (right side)")]
        for row in self.colors:
            tracking.body.addWidget(row)
        tracking.body.insertWidget(tracking.body.indexOf(self.colors[1]), self.bar_options)  # above the bar colors
        self.tracking_hint = label("", "fieldHelp", wrap=True)
        tracking.body.addWidget(self.tracking_hint)
        column.addWidget(tracking)

        images = Panel("Reference images", spacing=14)
        self.groups = [
            TemplateGroup("Shake button", "Box the SHAKE button.", "shake*.png",
                          lambda: ctx.run_capture(ctx.preview.capture_shake_template),
                          rt.shake.detector.reload_templates),
            TemplateGroup("Reel minigame", "Takes the REEL region while the minigame is on screen.", "reel*.png",
                          lambda: ctx.run_capture(ctx.preview.capture_reel_template), rt.reel_matcher.reload_templates),
            TemplateGroup("Catch success", "Box a part of the catch popup that never changes.",
                          "catch_success_*.png",
                          lambda: ctx.run_capture(lambda: ctx.preview.capture_catch_template("success")),
                          rt.catch.reload_templates),
            TemplateGroup("Catch failed", "Box a part of the 'fish lost' indicator that never changes.",
                          "catch_failed_*.png",
                          lambda: ctx.run_capture(lambda: ctx.preview.capture_catch_template("failed")),
                          rt.catch.reload_templates),
        ]
        for group in self.groups:
            images.body.addWidget(group)
        column.addWidget(images)

        thresholds = Panel("Detection thresholds", spacing=14)
        self.forms = [SettingsForm(rt, section, only) for _, section, only in THRESHOLDS]
        for form in self.forms:
            thresholds.body.addWidget(form)
        column.addWidget(thresholds)
        column.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.body.addWidget(scroll, 1)

    def refresh(self) -> None:
        for name, widget in self.region_labels.items():
            r = self.ctx.runtime.regions.get(name)
            widget.setText("not set" if r is None else
                           f"x {r.x:.1%}   y {r.y:.1%}   w {r.w:.1%}   h {r.h:.1%}")
        f = self.ctx.runtime.tracking.features
        two = f is not None and f.two_color_bar
        self.bar_options.refresh()
        self.colors[1].title.setText("Control bar color 1 (left side)" if two else "Control bar gradient"
                                     if f is not None and f.gradient_bar else "Control bar color")
        self.colors[2].setVisible(two)
        for row in self.colors:
            row.refresh()
        error = self.ctx.preview.tracking_error
        if error:
            hint = error
        elif f is None:
            hint = "Not calibrated: open the minigame in Roblox, then Calibrate fish & bar."
        elif f.bar_style == "gradient" and not f.bar_profile:
            hint = "Gradient not learned yet: open the minigame, then Calibrate fish & bar and box the whole bar."
        else:
            hint = (f"Control bar is {f.bar_width:.0%} of the track" + (" at full size" if f.bar_shrinks else "")
                    + ". Pick a color again, or calibrate, if a rod uses different colors.")
        self.tracking_hint.setText(hint)
        self.tracking_hint.setStyleSheet(f"color: {theme.AMBER};" if error else "")
        for group in self.groups:
            group.load()
        for form in self.forms:
            form.refresh()
