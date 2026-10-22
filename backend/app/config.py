"""Where the app keeps its data and finds its front-end, in development and once packaged."""
import os
import sys
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
