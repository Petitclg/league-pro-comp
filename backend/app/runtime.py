"""State shared between the API and the desktop launcher."""
import time

desktop = False  # set by the desktop launcher
_last_beat: float | None = None


def beat() -> None:
    global _last_beat
    _last_beat = time.monotonic()


def seconds_since_beat() -> float | None:
    """Seconds since the window last pinged the server (None if it never did)."""
    return None if _last_beat is None else time.monotonic() - _last_beat
