"""Rod profiles: named snapshots of the rod-specific config sections (tracking colors and sizes, tracking
thresholds, reel controller), saved as profiles/<name>.json. Loading one writes those sections back."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.config import APP_DIR, CONFIG_PATH, read_config, write_section

PROFILE_DIR = APP_DIR / "profiles"
PROFILE_SECTIONS = ("reel_tracking", "tracking", "reel_control")
NAME = re.compile(r"^[\w\- ]{1,40}$")


def _path(name: str, directory: Path) -> Path:
    name = name.strip()
    if not NAME.match(name):
        raise ValueError("use 1-40 letters, numbers, spaces, - or _")
    return directory / f"{name}.json"


def list_profiles(directory: Path = PROFILE_DIR) -> list[str]:
    return sorted((p.stem for p in directory.glob("*.json")), key=str.lower) if directory.is_dir() else []


def read_profile(name: str, directory: Path = PROFILE_DIR) -> dict:
    data = json.loads(_path(name, directory).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        raise ValueError("not a profile file")
    return data


def save_profile(name: str, config_path: Path = CONFIG_PATH, directory: Path = PROFILE_DIR) -> Path:
    path = _path(name, directory)
    config = read_config(config_path)
    sections = {s: config[s] for s in PROFILE_SECTIONS if s in config}
    if not sections:
        raise ValueError("nothing to save yet; calibrate tracking first")
    directory.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved": datetime.now().isoformat(timespec="seconds"), "sections": sections},
                              indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_profile(name: str, config_path: Path = CONFIG_PATH, directory: Path = PROFILE_DIR) -> list[str]:
    """Writes the profile's sections into config.json; returns the section names applied."""
    sections = read_profile(name, directory)["sections"]
    applied = [s for s in PROFILE_SECTIONS if isinstance(sections.get(s), dict)]
    for s in applied:
        write_section(s, sections[s], config_path)
    return applied


def delete_profile(name: str, directory: Path = PROFILE_DIR) -> None:
    _path(name, directory).unlink()
