"""Small building blocks shared by the pages."""

import html
import math
from dataclasses import fields, replace

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from core.runtime import SECTIONS, Runtime
from ui import theme
from ui.field_text import describe


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def line(vertical: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("vline" if vertical else "hline")
    return frame


class Page(QWidget):
    """Title, subtitle and a body layout. refresh() is called a few times a second while it's visible."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)
        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(label(title, "pageTitle"))
        header.addWidget(label(subtitle, "pageSubtitle", wrap=True))
        outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        outer.addLayout(self.body, 1)

    def refresh(self) -> None:
        pass


class Panel(QFrame):
    def __init__(self, title: str | None = None, action: QWidget | None = None, spacing: int = 10):
        super().__init__()
        self.setObjectName("panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 14)
        self.body.setSpacing(spacing)
        if title:
            header = QHBoxLayout()
            header.addWidget(label(title.upper(), "panelTitle"))
            header.addStretch()
            if action:
                header.addWidget(action)
            self.body.addLayout(header)


class Dot(QWidget):
    def __init__(self, color: str = theme.TEXT_FAINT, size: int = 8):
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        if QColor(color) != self._color:
            self._color = QColor(color)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(self.rect())


class Stat(QWidget):
    """Caption above a value, with an optional status dot."""

    def __init__(self, caption: str, dot: bool = False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(label(caption.upper(), "caption"))
        row = QHBoxLayout()
        row.setSpacing(7)
        self.dot = Dot() if dot else None
        if self.dot:
            row.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.value = label("—", "value")
        row.addWidget(self.value)
        row.addStretch()
        layout.addLayout(row)

    def set(self, text: str, color: str | None = None) -> None:
        if self.value.text() != text:
            self.value.setText(text)
        if self.dot and color:
            self.dot.set_color(color)


class StatStrip(Panel):
    """One row of stats separated by thin dividers, instead of a wall of cards."""

    def __init__(self, stats: list[Stat]):
        super().__init__()
        self.body.setContentsMargins(18, 14, 18, 14)
        row = QHBoxLayout()
        row.setSpacing(18)
        for i, stat in enumerate(stats):
            if i:
                row.addWidget(line(vertical=True))
            row.addWidget(stat, 1)
        self.body.addLayout(row)


class Keycap(QLabel):
    def __init__(self, key: str):
        super().__init__(key)
        self.setObjectName("keycap")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(42)

    def set_active(self, active: bool) -> None:
        if self.property("active") != active:
            self.setProperty("active", active)
            self.style().unpolish(self)
            self.style().polish(self)


class HotkeyRow(QWidget):
    def __init__(self, key: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.cap = Keycap(key)
        layout.addWidget(self.cap, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = label("", "fieldLabel")
        self.help = label("", "fieldHelp", wrap=True)
        text.addWidget(self.title)
        text.addWidget(self.help)
        layout.addLayout(text, 1)

    def set(self, title: str, help_text: str, active: bool) -> None:
        self.title.setText(title)
        self.help.setText(help_text)
        self.cap.set_active(active)
        self.title.setStyleSheet("" if active else f"color: {theme.TEXT_DIM};")


class Switch(QAbstractButton):
    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 20)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def set_silently(self, checked: bool) -> None:
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._set_knob(1.0 if checked else 0.0)

    def sizeHint(self) -> QSize:
        return QSize(36, 20)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(theme.BORDER_STRONG), QColor(theme.ACCENT)
        track = QColor(round(off.red() + (on.red() - off.red()) * self._pos),
                       round(off.green() + (on.green() - off.green()) * self._pos),
                       round(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.NoPen)
        p.setBrush(track if self.isEnabled() else QColor(theme.BORDER))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self.height() / 2, self.height() / 2)
        d = self.height() - 6
        p.setBrush(QColor("#F4F2F8"))
        p.drawEllipse(QRectF(3 + (self.width() - d - 6) * self._pos, 3, d, d))


class LogView(QPlainTextEdit):
    COLORS = {"WARNING": theme.AMBER, "ERROR": theme.RED, "CRITICAL": theme.RED, "DEBUG": theme.TEXT_FAINT}

    def __init__(self, max_lines: int = 1500):
        super().__init__()
        self.setObjectName("log")
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)

    def add(self, when: str, level: str, message: str) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        color = self.COLORS.get(level, theme.TEXT)
        self.appendHtml(f'<span style="color:{theme.TEXT_FAINT}">{when}</span>&nbsp;&nbsp;'
                        f'<span style="color:{color}">{html.escape(message)}</span>')
        if at_bottom:
            bar.setValue(bar.maximum())


class _NoWheel:
    """Spin boxes in a scrolling page shouldn't change when the page is scrolled past them."""

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FloatBox(_NoWheel, QDoubleSpinBox):
    pass


class IntBox(_NoWheel, QSpinBox):
    pass


class SettingsForm(QWidget):
    """Edits some or all fields of one config section. Changes are saved (and applied live by the
    runtime) shortly after the user stops typing."""

    def __init__(self, runtime: Runtime, section: str, only: tuple[str, ...] | None = None):
        super().__init__()
        self.runtime, self.section = runtime, section
        self.controls: dict[str, QWidget] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        current = runtime.settings[section]
        for row, f in enumerate(f for f in fields(SECTIONS[section]) if only is None or f.name in only):
            title, help_text = describe(section, f.name)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(title, "fieldLabel"))
            if help_text:
                text.addWidget(label(help_text, "fieldHelp", wrap=True))
            grid.addLayout(text, row, 0)
            control = self._control(f, getattr(current, f.name))
            self.controls[f.name] = control
            grid.addWidget(control, row, 1, Qt.AlignRight | Qt.AlignVCenter)

    def _control(self, f, value) -> QWidget:
        lo, hi = f.metadata.get("min", 0), f.metadata.get("max")
        if isinstance(value, bool):
            control = Switch(value)
            control.toggled.connect(self._changed)
            return control
        if isinstance(value, int):
            control = IntBox()
            control.setRange(int(lo), int(hi if hi is not None else 100000))
        else:
            control = FloatBox()
            hi = hi if hi is not None else 1000.0
            control.setDecimals(3)
            control.setRange(lo, hi)
            control.setSingleStep(0.01 if hi <= 1 else 0.05 if hi <= 10 else 10 ** math.floor(math.log10(hi) - 2))
        control.setFocusPolicy(Qt.StrongFocus)
        control.setFixedWidth(96)
        control.setAlignment(Qt.AlignRight)
        control.setValue(value)
        control.valueChanged.connect(self._changed)
        return control

    def _changed(self, *_):
        self._save_timer.start()

    def _save(self) -> None:
        values = {name: c.isChecked() if isinstance(c, Switch) else c.value() for name, c in self.controls.items()}
        new = replace(self.runtime.settings[self.section], **values)  # other fields: whatever is current
        if new != self.runtime.settings[self.section]:
            self.runtime.save(self.section, new)

    def refresh(self) -> None:
        """Shows values changed elsewhere (another form, a profile, config.json) unless one is being edited."""
        if self._save_timer.isActive():
            return
        current = self.runtime.settings[self.section]
        for name, c in self.controls.items():
            value = getattr(current, name)
            if isinstance(c, Switch):
                if c.isChecked() != value:
                    c.set_silently(value)
            elif not c.hasFocus() and c.value() != value:
                c.blockSignals(True)
                c.setValue(value)
                c.blockSignals(False)
