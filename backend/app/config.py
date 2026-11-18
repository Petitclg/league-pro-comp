"""Where the app keeps its data and finds its front-end, in development and once packaged."""
import json
import os
import sys
import threading
from pathlib import Path

from ._version import __version__

APP_NAME = "LoL Pro Comp"
FROZEN = bool(getattr(sys, "frozen", False))  # running from the PyInstaller executable
VERSION = __version__


def _data_dir() -> Path:
    override = os.environ.get("LOLPRO_DATA_DIR")
    if override:
        return Path(override)
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "LoLProComp"
    return Path(__file__).resolve().parents[2] / "data"


def _static_dir() -> Path | None:
    candidates = []
    if FROZEN:
        candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "static")
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")
    return next((p for p in candidates if (p / "index.html").exists()), None)


DATA_DIR = _data_dir()
STATIC_DIR = _static_dir()

# User settings (Riot API key, solo queue import options). Local to the machine, never committed:
# the data folder is outside the repository once packaged and git-ignored in development.
SETTINGS_PATH = DATA_DIR / "settings.json"
_settings_lock = threading.Lock()


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(**updates) -> dict:
    """Merge `updates` into the settings file (a None value removes the key) and return the result."""
    with _settings_lock:
        data = load_settings()
        for key, value in updates.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
        return data
