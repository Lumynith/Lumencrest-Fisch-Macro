"""Colors and the application stylesheet."""

BG = "#101014"
PANEL = "#1B1A22"
RAISED = "#24232C"      # buttons, keycaps
INPUT = "#15141A"
HOVER = "#2B2A35"
BORDER = "#2A2833"
BORDER_STRONG = "#363341"
TEXT = "#E6E4EC"
TEXT_DIM = "#9A97A6"
TEXT_FAINT = "#6B6878"
ACCENT = "#8B6BC2"
ACCENT_HOVER = "#9A7DCC"
ACCENT_SOFT = "rgba(139, 107, 194, 0.18)"
GREEN = "#5FB98A"
AMBER = "#D9A55B"
RED = "#D46A6A"
MONO = '"Cascadia Mono", "Consolas"'

STYLESHEET = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 13px;
    color: {TEXT};
}}
QWidget#root {{ background: {BG}; border: 1px solid {BORDER}; }}
QWidget#titleBar, QWidget#sidebar, QStackedWidget, QWidget#page {{ background: {BG}; }}
QWidget#sidebar {{ border-right: 1px solid {BORDER}; }}
QWidget#titleBar {{ border-bottom: 1px solid {BORDER}; }}
QLabel#brand {{ font-weight: 600; }}
QLabel#brandSub {{ color: {TEXT_DIM}; }}

QToolButton#winButton {{ border: none; background: transparent; border-radius: 0; }}
QToolButton#winButton:hover {{ background: {RAISED}; }}
QToolButton#closeButton {{ border: none; background: transparent; border-radius: 0; }}
QToolButton#closeButton:hover {{ background: #C4434F; }}

QToolButton#navButton {{ border: none; border-radius: 8px; background: transparent; }}
QToolButton#navButton:hover {{ background: #1F1E27; }}
QToolButton#navButton:checked {{ background: {ACCENT_SOFT}; }}

QLabel#pageTitle {{ font-size: 18px; font-weight: 600; }}
QLabel#pageSubtitle, QLabel#hint {{ color: {TEXT_DIM}; }}
QLabel#panelTitle {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#caption {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#value {{ font-size: 15px; font-weight: 600; }}
QLabel#fieldLabel {{ font-weight: 500; }}
QLabel#fieldHelp {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#mono {{ font-family: {MONO}; font-size: 12px; }}

QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#vline {{ background: {BORDER}; min-width: 1px; max-width: 1px; border: none; }}
QFrame#hline {{ background: {BORDER}; min-height: 1px; max-height: 1px; border: none; }}

QLabel#keycap {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-bottom: 3px solid {BORDER_STRONG};
    border-radius: 6px; padding: 3px 0px; font-family: {MONO}; font-size: 12px; font-weight: 600;
    color: {TEXT_DIM};
}}
QLabel#keycap[active="true"] {{ background: #2E2640; border-color: {ACCENT}; color: #EEE7FA; }}

QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 5px 12px;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: #1F1E27; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #FFFFFF; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[danger="true"]:hover {{ background: #3A2228; border-color: #6B3440; }}

QAbstractSpinBox, QLineEdit {{
    background: {INPUT}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {ACCENT};
}}
QAbstractSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0 6px; }}
/* hidden this way, not with NoButtons: Qt then collapses the text field of a styled spin box to 1px */
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QComboBox {{
    background: {INPUT}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 4px 8px;
}}
QComboBox:hover, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER_STRONG}; outline: 0; padding: 4px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}

QPlainTextEdit#log {{
    background: transparent; border: none; font-family: {MONO}; font-size: 12px;
    selection-background-color: {ACCENT};
}}
QListWidget, QTableWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget::item:hover:!selected {{ background: #22212A; }}
QListWidget#sectionNav::item {{ padding: 7px 10px; }}
QTableWidget {{ gridline-color: transparent; selection-background-color: {ACCENT_SOFT}; }}
QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid #22212A; }}
QHeaderView::section {{
    background: {PANEL}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-size: 11px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: #2E2C38; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #3A3846; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 4px 8px; }}
QDialog, QMessageBox, QInputDialog {{ background: {PANEL}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
"""
