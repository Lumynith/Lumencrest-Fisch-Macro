import logging

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QInputDialog, QListWidget, QMessageBox, QPushButton

from core.profiles import list_profiles, load_profile, read_profile, save_profile, delete_profile
from ui import theme
from ui.widgets import Page, Panel, label

log = logging.getLogger(__name__)

# (label, section, key) shown for the selected profile
DETAILS = [("Fish color", "reel_tracking", "fish_color"), ("Bar color", "reel_tracking", "bar_color"),
           ("Bar color 2", "reel_tracking", "bar_color2"), ("Bar type", "reel_tracking", "bar_style"),
           ("Bar shrinks", "reel_tracking", "bar_shrinks"),
           ("Bar width", "reel_tracking", "bar_width"), ("Kp / Kd", "reel_control", ("kp", "kd")),
           ("Sticky distance", "reel_control", "sticky_distance"), ("Spam click speed", "reel_control",
                                                                    "sticky_interval"),
           ("Fish / bar confidence", "tracking", ("fish_threshold", "bar_threshold"))]


def _fmt(sections: dict, section: str, key) -> str:
    data = sections.get(section) or {}
    if isinstance(key, tuple):
        values = [data.get(k) for k in key]
        return " / ".join("—" if v is None else f"{v:g}" for v in values)
    value = data.get(key)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):  # a color
        return f"{value.get('hex', '?')}  ±{value.get('tolerance', '?')}"
    if key == "bar_width" and isinstance(value, (int, float)):
        return f"{value:.0%} of track"
    return "—" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)


class ProfilesPage(Page):
    def __init__(self, ctx):
        super().__init__("Profiles", "Rod profiles store tracking colors and sizes, tracking thresholds and the "
                                     "reel controller, so switching rods is one click.")
        self.ctx = ctx
        row = QHBoxLayout()
        row.setSpacing(12)

        listing = Panel("Saved profiles")
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._show)
        listing.body.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        save = QPushButton("Save current…")
        save.setProperty("primary", True)
        save.clicked.connect(self._save)
        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(self._load)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self._delete)
        buttons.addWidget(save)
        buttons.addStretch()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.delete_button)
        listing.body.addLayout(buttons)
        row.addWidget(listing, 2)

        details = Panel("Details")
        self.saved = label("", "fieldHelp")
        details.body.addWidget(self.saved)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.values = {}
        for i, (title, section, key) in enumerate(DETAILS):
            grid.addWidget(label(title, "fieldHelp"), i, 0)
            self.values[title] = label("—", "mono")
            grid.addWidget(self.values[title], i, 1)
        grid.setColumnStretch(1, 1)
        details.body.addLayout(grid)
        details.body.addStretch()
        row.addWidget(details, 3)
        self.body.addLayout(row, 1)
        self._names: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        names = list_profiles()
        if names != self._names:
            current = self.list.currentItem().text() if self.list.currentItem() else None
            self._names = names
            self.list.clear()
            self.list.addItems(names)
            matches = self.list.findItems(current, 0) if current else []
            self.list.setCurrentItem(matches[0] if matches else self.list.item(0))
        has = self.list.currentItem() is not None
        self.load_button.setEnabled(has)
        self.delete_button.setEnabled(has)
        if not has:
            self._show("")

    def _show(self, name: str) -> None:
        sections = {}
        if name:
            try:
                data = read_profile(name)
                sections = data["sections"]
                self.saved.setText(f"Saved {data.get('saved', '?').replace('T', ' ')}")
            except (OSError, ValueError) as e:
                self.saved.setText(f"Unreadable profile: {e}")
        else:
            self.saved.setText("No profiles yet. Calibrate a rod, then Save current.")
        for title, section, key in DETAILS:
            self.values[title].setText(_fmt(sections, section, key) if sections else "—")
            color = (sections.get(section) or {}).get(key) if isinstance(key, str) else None
            self.values[title].setStyleSheet(f"color: {color['hex']};" if isinstance(color, dict) and
                                             isinstance(color.get("hex"), str) else f"color: {theme.TEXT};")

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name (e.g. the rod's name):")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._names and QMessageBox.question(
                self, "Overwrite profile", f"Replace the saved profile '{name}'?") != QMessageBox.Yes:
            return
        try:
            save_profile(name)
            log.info("Saved profile '%s'", name)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Could not save profile", str(e))
        self.refresh()
        matches = self.list.findItems(name, 0)
        if matches:
            self.list.setCurrentItem(matches[0])

    def _load(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        try:
            applied = load_profile(item.text())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Could not load profile", str(e))
            return
        self.ctx.runtime.reload()  # applies to the running macro now
        log.info("Loaded profile '%s' (%s)", item.text(), ", ".join(applied))

    def _delete(self) -> None:
        item = self.list.currentItem()
        if not item or QMessageBox.question(self, "Delete profile",
                                            f"Delete the profile '{item.text()}'?") != QMessageBox.Yes:
            return
        try:
            delete_profile(item.text())
            log.info("Deleted profile '%s'", item.text())
        except OSError as e:
            QMessageBox.warning(self, "Could not delete profile", str(e))
        self.refresh()
