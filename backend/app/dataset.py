"""In-memory dataset of every imported game, with series and Fearless handling.

Fearless (hard) draft, as observed in LCK data since the 2025 Cup: inside a best-of series a
champion picked by either team can no longer be picked by anyone in the following games.
A tournament is flagged Fearless when (almost) none of its multi-game series repeats a
champion, so the flag comes from the data rather than from hard-coded dates.
"""
import sqlite3
import sys
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .db import DB_PATH, connect
from .leagues import DEFAULT_LEAGUE
from .teams import canonical

# Draft order of the 10 picks / 10 bans (side 1 = blue, which has first pick).
PICK_SLOT = {1: [1, 4, 5, 8, 9], 2: [2, 3, 6, 7, 10]}
BAN_SLOT = {1: [1, 3, 5, 8, 10], 2: [2, 4, 6, 7, 9]}
FEARLESS_MIN_SERIES = 3
FEARLESS_MAX_REPEAT_RATIO = 0.2


@dataclass
class Filters:
    """Every list filter keeps a game when it matches ANY listed value (empty = no restriction)."""
    teams: tuple[str, ...] = ()  # the game involves one of these teams
    tournaments: tuple[str, ...] = ()  # prefix match, e.g. "LCK/2025 Season/Cup"
    years: tuple[str, ...] = ()  # calendar year of the game, e.g. "2025"
    patches: tuple[str, ...] = ()
    fearless: bool | None = None
    game_numbers: tuple[int, ...] = ()
    champion: str | None = None  # picked or banned in the game
    with_champions: tuple[str, ...] = ()  # all picked by the same side
    versus: tuple[str, ...] = ()  # picked by opposing sides (needs two champions)
    role: str | None = None  # restricts `champion`/`with_champions[0]` matches to this role

    def key(self) -> tuple:
        return tuple(getattr(self, f) for f in self.__dataclass_fields__)


def _empty_side() -> dict:
    return {"team": None, "win": None, "bans": [], "ban_order": [], "picks": [], "stats": {}}


def _names(value: str | None, sep: str) -> tuple[str, ...]:
    return tuple(sys.intern(v.strip()) for v in (value or "").split(sep) if v.strip())


def _loadout(r: sqlite3.Row) -> dict | None:
    """Items, runes and spells of a pick row (names), or None when the game was imported without them."""
    keys = r.keys()
    if "items" not in keys or not (r["items"] or r["runes"]):
        return None
    return {
        "items": _names(r["items"], ";"), "trinket": r["trinket"], "keystone": r["keystone"],
        "primary_tree": r["primary_tree"], "secondary_tree": r["secondary_tree"],
        "runes": _names(r["runes"], ","), "spells": _names(r["spells"], ","),
    }


class Dataset:
    def __init__(self, games: list[dict], matches: dict[str, dict]):
        self.games = games
        self.by_id = {g["game_id"]: g for g in games}
        self.matches = matches
        self._flag_fearless()
        self._link_series()
        self.comps_all = [c for g in games for c in _comps_of(g)]
        self.teams = sorted({g["team1"] for g in games} | {g["team2"] for g in games})
        self._cache: dict[tuple, list] = {}

    # --- construction -------------------------------------------------

    def _flag_fearless(self) -> None:
        series: dict[str, list[dict]] = defaultdict(list)
        for g in self.games:
            series[g["match_id"] or g["game_id"]].append(g)
        per_tournament: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # series, repeats
        for games in series.values():
            if len(games) < 2:
                continue
            seen: set[str] = set()
            repeat = False
            for g in sorted(games, key=lambda g: g["game_number"] or 0):
                champs = {p["champion"] for s in g["sides"].values() for p in s["picks"]}
                repeat = repeat or bool(champs & seen)
                seen |= champs
            entry = per_tournament[games[0]["tournament"]]
            entry[0] += 1
            entry[1] += repeat
        self.fearless_tournaments = {
            t for t, (n, r) in per_tournament.items()
            if n >= FEARLESS_MIN_SERIES and r / n <= FEARLESS_MAX_REPEAT_RATIO
        }
        for g in self.games:
            g["fearless"] = g["tournament"] in self.fearless_tournaments

    def _link_series(self) -> None:
        series: dict[str, list[dict]] = defaultdict(list)
        for g in self.games:
            series[g["match_id"] or g["game_id"]].append(g)
        self.series: dict[str, list[dict]] = {}
        for mid, games in series.items():
            games.sort(key=lambda g: (g["game_number"] or 0, g["date"] or ""))
            locked: list[str] = []
            for g in games:
                g["locked"] = list(locked) if g["fearless"] else []
                if g["fearless"]:
                    locked += [p["champion"] for s in g["sides"].values() for p in s["picks"]
                               if p["champion"] not in locked]
            self.series[mid] = games
        self.series_order = sorted(
            self.series, key=lambda m: self.series[m][0]["date"] or "", reverse=True
        )

    # --- filtering ----------------------------------------------------

    def match_game(self, g: dict, f: Filters) -> bool:
        if f.tournaments and not any((g["tournament"] or "").startswith(t) for t in f.tournaments):
            return False
        if f.years and (g["date"] or "")[:4] not in f.years:
            return False
        if f.patches and g["patch"] not in f.patches:
            return False
        if f.fearless is not None and g["fearless"] != f.fearless:
            return False
        if f.game_numbers and g["game_number"] not in f.game_numbers:
            return False
        if f.teams and g["team1"] not in f.teams and g["team2"] not in f.teams:
            return False
        if f.champion:
            in_picks = any(
                p["champion"] == f.champion and (not f.role or p["role"] == f.role)
                for s in g["sides"].values() for p in s["picks"]
            )
            in_bans = not f.role and any(f.champion in s["bans"] for s in g["sides"].values())
            if not (in_picks or in_bans):
                return False
        if f.with_champions:
            if not any(
                all(c in {p["champion"] for p in s["picks"]} for c in f.with_champions)
                for s in g["sides"].values()
            ):
                return False
        if f.versus and len(f.versus) == 2:
            a, b = f.versus
            s1 = {p["champion"] for p in g["sides"][1]["picks"]}
            s2 = {p["champion"] for p in g["sides"][2]["picks"]}
            if not ((a in s1 and b in s2) or (a in s2 and b in s1)):
                return False
        return True

    def filter_games(self, f: Filters) -> list[dict]:
        key = ("games", *f.key())
        if key not in self._cache:
            self._cache[key] = [g for g in self.games if self.match_game(g, f)]
        return self._cache[key]

    def filter_comps(self, f: Filters) -> list[dict]:
        """Comps (one per game side) of the games matching every filter but `teams`,
        keeping only the sides played by one of `teams` when it is set."""
        key = ("comps", *f.key())
        if key not in self._cache:
            base = Filters(**{**f.__dict__, "teams": ()})
            ids = {g["game_id"] for g in self.filter_games(base)}
            self._cache[key] = [
                c for c in self.comps_all
                if c["game_id"] in ids and (not f.teams or c["team"] in f.teams)
            ]
        return self._cache[key]


def _comps_of(g: dict) -> list[dict]:
    comps = []
    for side in (1, 2):
        s, o = g["sides"][side], g["sides"][3 - side]
        s_roles = [p["role"] for p in s["picks"]]
        comps.append({
            "game_id": g["game_id"],
            "side": side,
            "team": s["team"],
            "opponent": o["team"],
            "win": s["win"],
            "date": g["date"],
            "patch": g["patch"],
            "tournament": g["tournament"],
            "game_number": g["game_number"],
            "fearless": g["fearless"],
            "duration": g["duration"],
            "picks": {p["role"]: p["champion"] for p in s["picks"]},
            "enemy": {p["role"]: p["champion"] for p in o["picks"]},
            "players": {p["role"]: p for p in s["picks"]},
            "loadouts": {role: lo for role in s_roles if (lo := g["loadouts"].get((side, role)))},
        })
    return comps


def _league_tuple(leagues: "str | Iterable[str] | None") -> tuple[str, ...]:
    if leagues is None:
        return ()
    return (leagues,) if isinstance(leagues, str) else tuple(sorted(set(leagues)))


def load(conn: sqlite3.Connection, league: "str | Iterable[str] | None" = None) -> Dataset:
    """Load the games of one league, of several leagues, or of every league (`league` None)."""
    chosen = _league_tuple(league)
    marks = ",".join("?" for _ in chosen)
    where, args = (f"WHERE g.league IN ({marks})", chosen) if chosen else ("", ())

    def query(select: str, table: str, order: str = ""):
        """`select` columns of `table` (alias t), restricted to the chosen leagues' games."""
        join = "JOIN games g ON g.game_id = t.game_id" if where else ""
        return conn.execute(f"SELECT {select} FROM {table} t {join} {where} {order}", args)

    games: dict[str, dict] = {}
    for g in conn.execute(
        f"SELECT g.* FROM games g {where} ORDER BY g.date, g.game_id", args
    ):
        g = {**dict(g), "team1": canonical(g["team1"]), "team2": canonical(g["team2"])}
        sides = {}
        for side in (1, 2):
            s = _empty_side()
            s["team"] = g[f"team{side}"]
            s["win"] = None if g["winner"] is None else g["winner"] == side
            sides[side] = s
        games[g["game_id"]] = {**g, "sides": sides, "loadouts": {}}
    for r in query("t.*", "game_sides"):
        if r["game_id"] in games:
            games[r["game_id"]]["sides"][r["side"]]["stats"] = {
                k: r[k] for k in ("kills", "gold", "towers", "dragons", "barons")
            }
    for r in query("t.*", "picks", "ORDER BY t.game_id, t.side, t.pick_order"):
        g = games.get(r["game_id"])
        if g:
            g["sides"][r["side"]]["picks"].append({
                "order": r["pick_order"],
                "slot": PICK_SLOT[r["side"]][r["pick_order"] - 1],
                "role": r["role"],
                "champion": r["champion"],
                "player": r["player"],
                "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"],
                "gold": r["gold"], "cs": r["cs"], "damage": r["damage"], "vision": r["vision"],
            })
            if loadout := _loadout(r):
                g["loadouts"][(r["side"], r["role"])] = loadout
    for r in query("t.*", "bans", "ORDER BY t.game_id, t.side, t.slot"):
        g = games.get(r["game_id"])
        if g:
            g["sides"][r["side"]]["bans"].append(r["champion"])
            g["sides"][r["side"]]["ban_order"].append(BAN_SLOT[r["side"]][r["slot"] - 1])
    matches = {m["match_id"]: dict(m) for m in conn.execute(
        "SELECT * FROM matches" + (f" WHERE league IN ({marks})" if chosen else ""), chosen)}
    ordered = [g for g in games.values() if all(len(s["picks"]) == 5 for s in g["sides"].values())]
    return Dataset(ordered, matches)


MAX_CACHED_DATASETS = 3
_cache: "OrderedDict[tuple[str, ...], tuple[float, Dataset]]" = OrderedDict()
_cache_lock = threading.Lock()
_reload_paused = False


def set_reload_paused(paused: bool) -> None:
    """While an import rewrites the database at every commit, keep serving what is already loaded."""
    global _reload_paused
    _reload_paused = paused


def get_dataset(leagues: "str | Iterable[str]" = DEFAULT_LEAGUE) -> Dataset:
    """The dataset of one or several leagues, reloaded whenever the database file changes.

    Datasets are built on demand, keyed by their set of leagues; only the most recently used few
    stay in memory."""
    key = _league_tuple(leagues) or (DEFAULT_LEAGUE,)
    mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else 0
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (cached[0] == mtime or _reload_paused):
            _cache.move_to_end(key)
            return cached[1]
    conn = connect()
    try:
        ds = load(conn, key)
    finally:
        conn.close()
    with _cache_lock:
        _cache[key] = (mtime, ds)
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHED_DATASETS:
            _cache.popitem(last=False)
    return ds


def league_summary() -> list[dict]:
    """Leagues present in the database with their size and period."""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT league, COUNT(*) AS games, MIN(date) AS first, MAX(date) AS last"
            " FROM games GROUP BY league ORDER BY games DESC")]
    finally:
        conn.close()
