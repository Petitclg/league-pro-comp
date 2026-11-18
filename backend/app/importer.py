"""Data import started from the app (the "Données" page): runs the Leaguepedia ingest, and the solo
queue sampling through the Riot API, in the background."""
import threading
import time
from datetime import date

from . import config, dataset, live, riot, soloq
from .db import INGEST_REVISION, connect, ingest_revision
from .ingest import FIRST_YEAR, ingest
from .leagues import INTERNATIONAL, LEAGUES
from .soloq_ingest import Sampler, normalize_settings


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

    def start(self, leagues: list[str], first_year: int, repair: bool = False) -> bool:
        years = list(range(first_year, date.today().year + 1))
        with self._lock:
            if self._state.get("state") == "running":
                return False
            self._cancel.clear()
            self._state = {
                "state": "running", "started": time.time(), "leagues": leagues, "years": [years[0], years[-1]],
                "done": 0, "total": len(leagues) * len(years), "games": 0, "current": None, "log": [], "error": None,
                "repair": repair,
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


def outdated_leagues() -> tuple[list[str], int]:
    """Leagues of the database imported under an older revision of the import rules, and the first
    year to re-import. International events count when any of their games is stored, even if those
    games were given to the league that organised them."""
    conn = connect()
    try:
        stored = {r[0] for r in conn.execute("SELECT DISTINCT league FROM games")}
        for key in INTERNATIONAL:
            if any(conn.execute("SELECT 1 FROM games WHERE tournament LIKE ? LIMIT 1", (f"%{name}%",)).fetchone()
                   for name in LEAGUES[key]["sources"]):
                stored.add(key)
        leagues = [k for k in LEAGUES if k in stored and ingest_revision(conn, k) < INGEST_REVISION]
        oldest = conn.execute("SELECT MIN(substr(date, 1, 4)) FROM games").fetchone()[0]
    finally:
        conn.close()
    return leagues, int(oldest) if oldest else FIRST_YEAR


def start_repair() -> bool:
    """Re-import, in the background, the data stored by an older version so that fixes to the import rules
    reach existing installs. Offline, the job simply fails and the next start tries again."""
    leagues, first_year = outdated_leagues()
    return bool(leagues) and job.start(leagues, first_year, repair=True)

# --- solo queue (Riot API) ----------------------------------------------------

KEY_SETTING = "riot_api_key"
KEY_STATE_SETTING = "riot_key_state"
SOLOQ_SETTING = "soloq"
MAX_FAILURES = 5  # Riot API errors in a row (after the client's own retries) before the import stops
riot_transport = None  # tests plug an httpx.MockTransport here
riot_limiter = riot.shared_limiter  # and a limiter that never waits


def riot_key() -> str | None:
    return config.load_settings().get(KEY_SETTING) or None


def key_status() -> dict:
    """What the app shows of the stored key: never the key itself."""
    settings = config.load_settings()
    key = settings.get(KEY_SETTING)
    checked = settings.get(KEY_STATE_SETTING) or {}
    state = checked.get("state", "unchecked") if key else "missing"
    return {"state": state, "masked": riot.mask_key(key), "checked_at": checked.get("checked_at") if key else None}


def mark_key(state: str) -> None:
    config.save_settings(**{KEY_STATE_SETTING: {"state": state, "checked_at": time.time()}})


def check_key(key: str, platform: str) -> bool:
    """Ask Riot whether `key` works (one cheap request). Raises riot.RiotError when Riot cannot be reached."""
    with riot.RiotClient(key, transport=riot_transport, limiter=riot_limiter) as client:
        try:
            client.platform_status(platform)
        except riot.KeyRejected:
            return False
    return True


def store_key(key: str | None) -> None:
    """Save a key that Riot just accepted (None removes it)."""
    config.save_settings(**{KEY_SETTING: key, KEY_STATE_SETTING: None})
    if key:
        mark_key("valid")


def soloq_settings() -> dict:
    return normalize_settings(config.load_settings().get(SOLOQ_SETTING))


def save_soloq_settings(raw: dict) -> dict:
    settings = normalize_settings(raw)
    config.save_settings(**{SOLOQ_SETTING: settings})
    return settings


def champion_names() -> dict[int, str]:
    """Champion id -> display name (Data Dragon), the naming of the pro data."""
    try:
        return live.champion_names()
    except Exception as exc:  # offline: the import cannot name champions consistently
        raise riot.RiotError(f"Data Dragon unreachable ({exc.__class__.__name__})") from None


class SoloqImportJob:
    """Continuous solo queue import until the volume target is reached. The `running` flag stored in
    soloq.db survives a restart: the desktop app resumes an import that was interrupted by closing it."""

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

    def start(self, api_key: str, settings: dict, resumed: bool = False) -> bool:
        settings = normalize_settings(settings)
        with self._lock:
            if self._state.get("state") == "running":
                return False
            self._cancel.clear()
            self._state = {
                "state": "running", "started": time.time(), "resumed": resumed, "target": settings["target"],
                "platforms": settings["platforms"], "tiers": settings["tiers"], "games": None, "added": 0,
                "current": None, "waiting": None, "log": [], "error": None,
            }
        self._thread = threading.Thread(target=self._run, args=(api_key, settings), daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _set(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _log(self, line: str) -> None:
        with self._lock:
            self._state["log"] = [*self._state.get("log", [])[-14:], line]

    def _sleep(self, seconds: float) -> None:
        """Waits of the Riot client: interrupted at once by a cancel."""
        try:
            if self._cancel.wait(seconds):
                raise riot.Cancelled()
        finally:
            self._set(waiting=None)

    def _on_wait(self, seconds: float, reason: str) -> None:
        self._set(waiting={"seconds": round(seconds), "reason": reason, "until": time.time() + seconds})

    def _on_game(self, game: dict, tier: str | None) -> None:
        with self._lock:
            self._state["added"] = self._state.get("added", 0) + 1
            self._state["games"] = (self._state.get("games") or 0) + 1
        self._log(f"{riot.label_of(game['platform'])} · {(tier or '?').title()} · {game['patch']} : {game['match_id']}")

    def _run(self, api_key: str, settings: dict) -> None:
        error = None
        finished = "done"
        keep_flag = False  # leave the import marked as running: it resumes at the next launch
        conn = soloq.connect(soloq.SOLOQ_DB_PATH)
        try:
            soloq.set_state(conn, "running", "1")
            names = champion_names()
            with riot.RiotClient(api_key, transport=riot_transport, limiter=riot_limiter, sleep=self._sleep,
                                 on_wait=self._on_wait) as client:
                sampler = Sampler(conn, client, settings, names, on_game=self._on_game,
                                  on_activity=lambda text: self._set(current=text))
                failures = 0
                while not self._cancel.is_set():
                    stored = sampler.stored()
                    self._set(games=stored)
                    if stored >= settings["target"]:
                        break
                    try:
                        if not sampler.step():
                            finished = "exhausted"  # every ladder page read, no seed to re-read yet
                            break
                        failures = 0
                    except riot.KeyRejected:
                        raise
                    except riot.RiotError as exc:
                        failures += 1
                        if failures >= MAX_FAILURES:
                            raise
                        self._log(f"{exc} : nouvel essai dans {30 * failures} s")
                        self._on_wait(30 * failures, "erreur de l'API Riot")
                        self._sleep(30 * failures)
        except riot.Cancelled:
            pass
        except riot.KeyRejected:
            error = "Clé API Riot refusée : expirée ou invalide (une clé de développement expire après 24 h)."
            self._set(key_rejected=True)
            mark_key("invalid")
        except riot.RiotError as exc:
            error = str(exc)
            keep_flag = True
        except Exception as exc:  # a bug or a full disk: stop, the data stored so far is kept
            error = str(exc) or exc.__class__.__name__
        finally:
            try:
                if not keep_flag:
                    soloq.set_state(conn, "running", None)
                stored = conn.execute("SELECT COUNT(*) FROM soloq_games").fetchone()[0]
            finally:
                conn.close()
        cancelled = self._cancel.is_set()
        with self._lock:
            self._state["state"] = "error" if error else "cancelled" if cancelled else finished
            self._state["error"] = error
            self._state["games"] = stored
            self._state["current"] = None
            self._state["waiting"] = None
            self._state["finished"] = time.time()


soloq_job = SoloqImportJob()


def resume_soloq() -> bool:
    """At startup, resume a solo queue import that the previous run left unfinished."""
    if not soloq.SOLOQ_DB_PATH.exists():
        return False
    key = riot_key()
    if not key or key_status()["state"] == "invalid":
        return False
    conn = soloq.connect(soloq.SOLOQ_DB_PATH)
    try:
        unfinished = soloq.get_state(conn, "running") == "1"
    finally:
        conn.close()
    return unfinished and soloq_job.start(key, soloq_settings(), resumed=True)
