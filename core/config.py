"""Reads and writes sections of config.json."""

import json
import logging
import math
import os
import sys
from dataclasses import asdict, fields
from pathlib import Path

log = logging.getLogger(__name__)

# Where settings, captured images, profiles and logs live: next to the .exe when packaged (PyInstaller's own
# folder is temporary), else the project folder. Read-only bundled files (the icon) are in BUNDLE_DIR.
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
CONFIG_PATH = APP_DIR / "config.json"


def read_config(path: Path = CONFIG_PATH) -> dict:
    """Whole config; {} if missing or empty. Raises ValueError if the file is not a JSON object."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("config root is not an object")
    return data


def write_section(name: str, value, path: Path = CONFIG_PATH) -> None:
    """Replaces one section, keeping the rest of the file."""
    try:
        data = read_config(path)
    except ValueError:
        backup = path.with_suffix(path.suffix + ".bak")
        os.replace(path, backup)
        log.warning("%s was not valid JSON; moved it to %s", path.name, backup.name)
        data = {}
    data[name] = value
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a crash never leaves a half-written config


def load_settings(section: str, cls, path: Path = CONFIG_PATH):
    """Loads a settings dataclass from a config section, writing defaults if the section is missing.
    Invalid values fall back to their defaults; numeric fields honor metadata "min" (default 0) and "max"."""
    defaults = cls()
    try:
        raw = read_config(path).get(section)
    except (OSError, ValueError) as e:
        log.warning("Could not read %s (%s); using default %s settings", path.name, e, section)
        return defaults
    if raw is None:
        try:
            write_section(section, asdict(defaults), path)
        except OSError as e:
            log.warning("Could not write default %s settings: %s", section, e)
        return defaults
    if not isinstance(raw, dict):
        log.warning("Invalid %s section in %s; using defaults", section, path.name)
        return defaults
    values = {}
    for f in fields(cls):
        default = getattr(defaults, f.name)
        value = raw.get(f.name, default)
        if isinstance(default, bool):
            valid = isinstance(value, bool)
        else:
            number_type = int if isinstance(default, int) else (int, float)
            valid = isinstance(value, number_type) and not isinstance(value, bool) and math.isfinite(value) \
                and f.metadata.get("min", 0) <= value <= f.metadata.get("max", math.inf)
        if not valid:
            log.warning("Invalid %s.%s=%r; using %r", section, f.name, value, default)
            value = default
        values[f.name] = value
    return cls(**values)
