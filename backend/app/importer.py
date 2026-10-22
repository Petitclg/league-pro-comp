"""Data import started from the app (the "Données" page): runs the Leaguepedia ingest in the background."""
import threading
import time
from datetime import date

from . import dataset
from .ingest import FIRST_YEAR, ingest
from .leagues import LEAGUES


class ImportJob:
    """One import at a time, with progress that the front-end polls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = {"state": "idle"}

    def status(self) -> dict:
        with self._lock:
            return dict(self._state, log=list(self._state.get("log", [])))

    @property
    def running(self) -> bool:
        return self._state.get("state") == "running"

    def start(self, leagues: list[str], first_year: int) -> bool:
        years = list(range(first_year, date.today().year + 1))
        with self._lock:
            if self._state.get("state") == "running":
                return False
            self._cancel.clear()
            self._state = {
                "state": "running", "started": time.time(), "leagues": leagues, "years": [years[0], years[-1]],
                "done": 0, "total": len(leagues) * len(years), "games": 0, "current": None, "log": [], "error": None,
            }
        dataset.set_reload_paused(True)  # the file changes at every commit: keep serving the loaded data
        self._thread = threading.Thread(target=self._run, args=(leagues, years), daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def _step(self, league: str, year: int, saved: int, skipped: int, done: int, total: int) -> None:
        with self._lock:
            s = self._state
            s["done"], s["total"], s["current"] = done, total, f"{league} {year}"
            s["games"] += saved
            if saved:
                s["log"] = [*s["log"][-14:], f"{league} {year} : {saved} parties"]

    def _run(self, leagues: list[str], years: list[int]) -> None:
        error = None
        try:
            ingest(leagues, years, on_step=self._step, cancelled=self._cancel.is_set)
        except Exception as exc:  # network down, Leaguepedia unavailable...
            error = str(exc) or exc.__class__.__name__
        finally:
            dataset.set_reload_paused(False)
        with self._lock:
            self._state["state"] = "error" if error else "cancelled" if self._cancel.is_set() else "done"
            self._state["error"] = error
            self._state["finished"] = time.time()


job = ImportJob()


def valid_leagues(keys: list[str]) -> list[str]:
    return [k for k in keys if k in LEAGUES]


def first_year_default() -> int:
    return FIRST_YEAR
