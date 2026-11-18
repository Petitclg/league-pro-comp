"""Riot Games API client for the solo queue import: ladders (League-Exp-V4), summoners and matches
(Match-V5), with the client-side rate limiting a personal or development key needs.

The API key travels in the `X-Riot-Token` header only: it never appears in a URL, an exception
message or a log line.
"""
import threading
import time
from collections import deque
from collections.abc import Callable

import httpx

# Platform routing value -> (regional routing value used by Match-V5, short label shown in the app)
PLATFORMS: dict[str, tuple[str, str]] = {
    "euw1": ("europe", "EUW"),
    "eun1": ("europe", "EUNE"),
    "tr1": ("europe", "TR"),
    "ru": ("europe", "RU"),
    "me1": ("europe", "ME"),
    "kr": ("asia", "KR"),
    "jp1": ("asia", "JP"),
    "na1": ("americas", "NA"),
    "br1": ("americas", "BR"),
    "la1": ("americas", "LAN"),
    "la2": ("americas", "LAS"),
    "oc1": ("sea", "OCE"),
    "sg2": ("sea", "SG"),
    "tw2": ("sea", "TW"),
    "vn2": ("sea", "VN"),
}
# Ranked tiers the import can sample, lowest first. Apex tiers have a single division.
TIERS = ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}
DIVISIONS = ["I", "II", "III", "IV"]
QUEUE_SOLO = 420
RANKED_SOLO = "RANKED_SOLO_5x5"

# Personal / development keys: 20 requests every second and 100 every 2 minutes, per routing value.
DEFAULT_LIMITS = ((20, 1.0), (100, 120.0))
MAX_RETRIES = 5  # server errors and network failures, with exponential backoff
MAX_RATE_LIMITED = 30  # 429 answers in a row before giving up (each one waits as Riot asks)
USER_AGENT = "league-pro-comp (personal analysis project)"


def region_of(platform: str) -> str:
    """Regional routing value (europe, asia, americas, sea) of a platform (euw1, kr...)."""
    return PLATFORMS[platform][0]


def label_of(platform: str) -> str:
    return PLATFORMS.get(platform, (None, platform.upper()))[1]


def divisions_of(tier: str) -> list[str]:
    return ["I"] if tier in APEX_TIERS else DIVISIONS


class RiotError(Exception):
    """The API could not answer (outage, too many failures)."""


class KeyRejected(RiotError):
    """401/403: the key is missing, invalid or expired (development keys last 24 hours)."""


class Cancelled(Exception):
    """The caller asked to stop while the client was waiting."""


class RateLimiter:
    """Sliding-window limiter honouring several windows at once (e.g. 20/1 s and 100/120 s).

    Counting locally from the moment a request is sent is slightly more conservative than Riot's
    fixed windows, and `margin` absorbs the network delay between the two clocks."""

    def __init__(self, limits=DEFAULT_LIMITS, clock: Callable[[], float] = time.monotonic, margin: float = 0.1):
        self.limits = [(int(n), float(w)) for n, w in limits]
        self.clock = clock
        self.margin = margin
        self._calls: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def wait_time(self) -> float:
        """Seconds to wait before the next request is allowed (0 when it can go now)."""
        with self._lock:
            return self._wait_time(self.clock())

    def _wait_time(self, now: float) -> float:
        longest = max((w for _, w in self.limits), default=0.0) + self.margin
        while self._calls and self._calls[0] <= now - longest:
            self._calls.popleft()
        wait = max(0.0, self._blocked_until - now)
        for n, window in self.limits:
            recent = [t for t in self._calls if t > now - window - self.margin]
            if len(recent) >= n:
                wait = max(wait, recent[-n] + window + self.margin - now)
        return wait

    def try_acquire(self) -> float:
        """Record a request and return 0 when allowed now, else the seconds to wait (nothing recorded)."""
        with self._lock:
            now = self.clock()
            wait = self._wait_time(now)
            if wait <= 0:
                self._calls.append(now)
            return wait

    def acquire(self, sleep: Callable[[float], None]) -> float:
        """Block (through `sleep`) until a request is allowed, record it and return the time waited."""
        waited = 0.0
        while (wait := self.try_acquire()) > 0:
            sleep(wait)
            waited += wait
        return waited

    def block_for(self, seconds: float) -> None:
        """A 429 asked to wait: no request until then."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock() + seconds)

    def update_limits(self, header: str | None) -> None:
        """Follow the limits Riot reports (`X-App-Rate-Limit: 20:1,100:120`)."""
        if not header:
            return
        try:
            limits = [(int(n), float(w)) for n, w in (part.split(":") for part in header.split(",") if part)]
        except ValueError:
            return
        if limits:
            with self._lock:
                self.limits = limits


_shared_limiters: dict[str, RateLimiter] = {}
_shared_lock = threading.Lock()


def shared_limiter(routing: str) -> RateLimiter:
    """Riot counts requests per key and routing value: every client of the app shares these."""
    with _shared_lock:
        if routing not in _shared_limiters:
            _shared_limiters[routing] = RateLimiter()
        return _shared_limiters[routing]


class RiotClient:
    """Thin JSON client. `sleep` may raise `Cancelled` to abort a wait; `on_wait(seconds, reason)`
    reports long waits (rate limit) to the caller."""

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None,
                 limiter: Callable[[str], RateLimiter] = shared_limiter,
                 sleep: Callable[[float], None] = time.sleep,
                 on_wait: Callable[[float, str], None] | None = None, timeout: float = 20):
        self._client = httpx.Client(headers={"X-Riot-Token": api_key, "User-Agent": USER_AGENT},
                                    timeout=timeout, transport=transport)
        self._limiter = limiter
        self._sleep = sleep
        self._on_wait = on_wait

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _wait(self, seconds: float, reason: str) -> None:
        if self._on_wait and seconds >= 1:
            self._on_wait(seconds, reason)
        self._sleep(seconds)

    def get(self, routing: str, path: str, params: dict | None = None):
        """GET https://{routing}.api.riotgames.com{path}: decoded JSON, or None on 404."""
        url = f"https://{routing}.api.riotgames.com{path}"
        limiter = self._limiter(routing)
        failures = limited = 0
        while True:
            limiter.acquire(lambda s: self._wait(s, "limite de requêtes"))
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                failures += 1
                if failures > MAX_RETRIES:
                    raise RiotError(f"Riot API unreachable ({exc.__class__.__name__})") from None
                self._wait(2 ** failures, "réseau indisponible")
                continue
            limiter.update_limits(response.headers.get("X-App-Rate-Limit"))
            status = response.status_code
            if status == 200:
                return response.json()
            if status == 404:
                return None
            if status in (401, 403):
                raise KeyRejected("Riot API key rejected (expired or invalid)")
            if status == 429:
                limited += 1
                if limited > MAX_RATE_LIMITED:
                    raise RiotError("Riot API keeps answering 429 (rate limited)")
                retry_after = _retry_after(response.headers.get("Retry-After"))
                # No Retry-After: the underlying service is busy, not our key; back off on our own.
                wait = retry_after if retry_after is not None else min(60.0, 2.0 ** limited)
                limiter.block_for(wait)
                continue
            failures += 1
            if failures > MAX_RETRIES or status < 500:
                raise RiotError(f"Riot API error {status}")
            self._wait(2 ** failures, f"erreur {status} de l'API")

    # --- endpoints ------------------------------------------------------

    def platform_status(self, platform: str):
        """Cheapest call that needs a valid key: used to check one."""
        return self.get(platform, "/lol/status/v4/platform-data")

    def league_entries(self, platform: str, tier: str, division: str, page: int = 1) -> list[dict]:
        """One page (up to 205 players) of a ranked solo ladder tier/division."""
        path = f"/lol/league-exp/v4/entries/{RANKED_SOLO}/{tier}/{division}"
        return self.get(platform, path, {"page": page}) or []

    def puuid_of(self, platform: str, entry: dict) -> str | None:
        """League entries carry the puuid; older answers only had the summoner id."""
        if entry.get("puuid"):
            return entry["puuid"]
        if entry.get("summonerId"):
            summoner = self.get(platform, f"/lol/summoner/v4/summoners/{entry['summonerId']}")
            return (summoner or {}).get("puuid")
        return None

    def match_ids(self, platform: str, puuid: str, count: int = 20, start_time: int | None = None) -> list[str]:
        params = {"queue": QUEUE_SOLO, "type": "ranked", "start": 0, "count": count}
        if start_time:
            params["startTime"] = start_time
        return self.get(region_of(platform), f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params) or []

    def match(self, platform: str, match_id: str) -> dict | None:
        return self.get(region_of(platform), f"/lol/match/v5/matches/{match_id}")

    def timeline(self, platform: str, match_id: str) -> dict | None:
        return self.get(region_of(platform), f"/lol/match/v5/matches/{match_id}/timeline")


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) + 0.5 if value is not None else None
    except ValueError:
        return None


def mask_key(key: str | None) -> str | None:
    """What the app shows of a stored key: its prefix and last 4 characters."""
    if not key:
        return None
    return f"RGAPI-…{key[-4:]}" if len(key) > 10 else "…"
