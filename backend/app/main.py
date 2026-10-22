import threading
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import gamedata, importer, live, runtime, stats
from .builds import champion_build
from .config import APP_NAME, STATIC_DIR, VERSION
from .dataset import Dataset, Filters, get_dataset, league_summary
from .db import ROLES
from .denial import pair_denial, ranking
from .leagues import DEFAULT_LEAGUE, LEAGUES
from .suggest import substitutes
from .suggest import suggest as run_suggest

def latest_year(ds: Dataset) -> str:
    """Most recent season in the data: the default scope of the app."""
    return max((g["date"][:4] for g in ds.games if g["date"]), default="")


def _warm_up() -> None:
    """Pre-compute the slow analyses of the default scope (LCK, latest year) so the first visit is fast."""
    try:
        ds = get_dataset((DEFAULT_LEAGUE,))
        year = latest_year(ds)
        f = Filters(years=(year,) if year else ())
        ranking(ds, f)
        stats.records(ds, f)
        stats.champion_table(ds, f)
    except Exception:  # best effort: pages compute on demand anyway
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


app = FastAPI(title="League Pro Comp", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def selected_leagues(league: list[str] = Query([DEFAULT_LEAGUE])) -> tuple[str, ...]:
    """Leagues of the scope (?league=LCK&league=LPL): their games are analysed together."""
    unknown = [lg for lg in league if lg not in LEAGUES]
    if unknown:
        raise HTTPException(404, f"Unknown league {', '.join(unknown)}")
    return tuple(sorted(set(league))) or (DEFAULT_LEAGUE,)


def dataset(leagues: Annotated[tuple[str, ...], Depends(selected_leagues)]) -> Dataset:
    return get_dataset(leagues)


DS = Annotated[Dataset, Depends(dataset)]


def filters(
    team: list[str] | None = Query(None),
    tournament: list[str] | None = Query(None),
    year: list[Annotated[str, Field(pattern=r"^\d{4}$")]] | None = Query(None),
    patch: list[str] | None = Query(None),
    fearless: bool | None = None,
    game_number: list[Annotated[int, Field(ge=1, le=7)]] | None = Query(None),
) -> Filters:
    """Scope filters. List filters can be repeated (?team=T1&team=Gen.G) and match ANY value."""
    return Filters(
        teams=tuple(team or ()), tournaments=tuple(tournament or ()), years=tuple(year or ()),
        patches=tuple(patch or ()), fearless=fearless, game_numbers=tuple(game_number or ()),
    )


F = Annotated[Filters, Depends(filters)]


def _check_role(role: str | None) -> None:
    if role and role not in ROLES:
        raise HTTPException(422, f"Unknown role {role}")


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


@app.get("/api/meta")
def meta(ds: DS, leagues: Annotated[tuple[str, ...], Depends(selected_leagues)]):
    patches: dict[str, str] = {}
    tournaments: dict[str, str] = {}
    for g in ds.games:
        patches.setdefault(g["patch"], g["date"] or "")
        tournaments.setdefault(g["tournament"], g["date"] or "")
    fearless_dates = [g["date"] for g in ds.games if g["fearless"] and g["date"]]
    years = sorted({g["date"][:4] for g in ds.games if g["date"]}, reverse=True)
    return {
        "selected": list(leagues),
        "leagues": [
            {"key": s["league"], "name": LEAGUES[s["league"]]["name"], "region": LEAGUES[s["league"]].get("region", "Autre"),
             "level": LEAGUES[s["league"]].get("level", "other"), "games": s["games"],
             "first": (s["first"] or "")[:4], "last": (s["last"] or "")[:4]}
            for s in league_summary() if s["league"] in LEAGUES
        ],
        "roles": ROLES,
        "games": len(ds.games),
        "series": len(ds.series),
        "teams": ds.teams,
        "years": years,
        "tournaments": [
            {"name": t, "fearless": t in ds.fearless_tournaments}
            for t in sorted(tournaments, key=lambda t: tournaments[t], reverse=True)
        ],
        "patches": [p for p in sorted(patches, key=lambda p: patches[p], reverse=True) if p],
        "fearless_since": min(fearless_dates)[:10] if fearless_dates else None,
        "version": VERSION,
        "desktop": runtime.desktop,
        "registry": [{"key": k, "name": v["name"], "region": v.get("region", "Autre")} for k, v in LEAGUES.items()],
    }


def _game_json(ds: Dataset, g: dict, matched: set[str] | None = None) -> dict:
    sides = {}
    for side, s in g["sides"].items():
        sides[str(side)] = {
            "team": s["team"],
            "win": s["win"],
            "stats": s["stats"],
            "bans": [{"champion": c, "order": o} for c, o in zip(s["bans"], s["ban_order"])],
            "picks": s["picks"],
        }
    return {
        "game_id": g["game_id"],
        "game_number": g["game_number"],
        "date": g["date"],
        "patch": g["patch"],
        "duration": g["duration"],
        "fearless": g["fearless"],
        "locked": g["locked"],
        "winner": g["winner"],
        "matched": matched is None or g["game_id"] in matched,
        "sides": sides,
        "highlights": stats.game_highlights(ds, g),
    }


def _series_json(ds: Dataset, mid: str, matched: set[str]) -> dict:
    games = ds.series[mid]
    first = games[0]
    wins: dict[str, int] = {}
    for g in games:
        if g["winner"]:
            winner = g[f"team{g['winner']}"]
            wins[winner] = wins.get(winner, 0) + 1
    match = ds.matches.get(mid) or {}
    teams = [first["team1"], first["team2"]]
    return {
        "match_id": mid,
        "league": first["league"],
        "tournament": first["tournament"],
        "stage": match.get("stage"),
        "date": first["date"],
        "best_of": match.get("best_of"),
        "fearless": first["fearless"],
        "teams": teams,
        "score": [wins.get(teams[0], 0), wins.get(teams[1], 0)],
        "games": [_game_json(ds, g, matched) for g in games],
    }


@app.get("/api/series")
def list_series(
    ds: DS,
    base: F,
    champion: str | None = None,
    role: str | None = None,
    with_champions: Annotated[str | None, Query(alias="with")] = None,
    versus: str | None = None,
    game: str | None = None,
    limit: int = Query(15, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    _check_role(role)
    f = Filters(**{**base.__dict__, "champion": champion, "role": role,
                   "with_champions": _split(with_champions), "versus": _split(versus)})
    matched = {g["game_id"] for g in ds.filter_games(f)}
    if game:
        matched &= {game}
    mids = [m for m in ds.series_order if any(g["game_id"] in matched for g in ds.series[m])]
    page = mids[offset:offset + limit]
    return {"total": len(mids), "series": [_series_json(ds, m, matched) for m in page]}


@app.get("/api/champions")
def champions(ds: DS, f: F):
    return stats.champion_table(ds, f)


@app.get("/api/champions/{name}")
def champion(name: str, ds: DS, f: F):
    detail = stats.champion_detail(ds, name, f)
    if detail is None:
        raise HTTPException(404, f"No data for {name}")
    return detail


@app.get("/api/pairs")
def pairs(
    ds: DS,
    f: F,
    min_games: int = Query(2, ge=1),
    min_lift: float = Query(0, ge=0),
    role_a: str | None = None,
    role_b: str | None = None,
    champion: str | None = None,
    sort: str = Query("games", pattern="^(games|winrate|lift|presence)$"),
    limit: int = Query(100, ge=1, le=500),
):
    _check_role(role_a)
    _check_role(role_b)
    comps = ds.filter_comps(f)
    if sort == "presence":
        # Presence needs a pass over the games: rank the most played pairs by it, not every pair.
        rows = stats.pair_stats(comps, min_games, role_a, role_b, champion, "games", 500, min_lift)
        stats.pair_presence(ds, f, rows)
        rows.sort(key=lambda r: (-r["presence"], -r["games"]))
        rows = rows[:limit]
    else:
        rows = stats.pair_stats(comps, min_games, role_a, role_b, champion, sort, limit, min_lift)
        stats.pair_presence(ds, f, rows)
    return {"comps": len(comps), "pairs": rows}


@app.get("/api/duo")
def duo(ds: DS, f: F, kind: str = Query(pattern="^(synergy|counter)$"), a: str = "", b: str = ""):
    return stats.duo_detail(ds, kind, a, b, f)


@app.get("/api/denial")
def denial_endpoint(ds: DS, f: F, a: str, b: str):
    return pair_denial(ds, f, a, b)


@app.get("/api/countered-synergies")
def countered(ds: DS, f: F, limit: int = Query(15, ge=1, le=50)):
    return ranking(ds, f, limit)


@app.get("/api/records")
def records(ds: DS, f: F):
    return stats.records(ds, f)


class SuggestRequest(BaseModel):
    picks: dict[str, str] = {}
    enemy: list[str] = []
    unavailable: list[str] = []
    teams: list[str] = []
    tournaments: list[str] = []
    years: list[str] = []
    patches: list[str] = []
    fearless: bool | None = None
    expand: bool = False  # substitute look-alikes for locked champions the pros never played

    @field_validator("picks")
    @classmethod
    def valid_roles(cls, picks):
        bad = [r for r in picks if r not in ROLES]
        if bad:
            raise ValueError(f"Unknown roles: {bad}")
        return {r: c for r, c in picks.items() if c}


def _suggest(req: "SuggestRequest", ds) -> dict:
    f = Filters(teams=tuple(req.teams), tournaments=tuple(req.tournaments), years=tuple(req.years),
                patches=tuple(req.patches), fearless=req.fearless)
    comps = ds.filter_comps(f)
    subs = substitutes(comps, req.picks, live.champion_profiles()) if req.expand and req.picks else None
    return run_suggest(comps, req.picks, set(req.unavailable), req.enemy, subs=subs)


@app.post("/api/suggest")
def suggest(req: SuggestRequest, ds: DS):
    return _suggest(req, ds)


class LobbyRecoRequest(SuggestRequest):
    my_role: str | None = None


@app.post("/api/live/send-recos")
def send_recos(req: LobbyRecoRequest, ds: DS):
    """Post the current top picks (English, one line) in the champ select chat."""
    result = _suggest(req, ds)
    lines = live.format_recommendations(result["suggestions"], req.my_role)
    if not lines:
        return {"sent": False, "messages": [], "reason": "Nothing to recommend."}
    try:
        posted = live.send_lobby_messages(lines)
    except Exception as exc:  # the client API is local and best-effort
        return {"sent": False, "messages": list(lines.values()), "reason": str(exc)}
    return {"sent": bool(posted), "messages": posted}


class BuildRequest(SuggestRequest):
    champion: str
    role: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, role):
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role}")
        return role


def _build(req: BuildRequest, ds) -> dict:
    f = Filters(teams=tuple(req.teams), tournaments=tuple(req.tournaments), years=tuple(req.years),
                patches=tuple(req.patches), fearless=req.fearless)
    return champion_build(ds.filter_comps(f), req.champion, req.role, req.picks, req.enemy,
                          gamedata.load_catalog())


@app.post("/api/build")
def build(req: BuildRequest, ds: DS):
    """Items, runes and spells the pros played with a champion, from games like the current draft."""
    return _build(req, ds)


@app.post("/api/live/import-build")
def import_build(req: BuildRequest, ds: DS):
    """Write that build into the League client (item set and rune page)."""
    built = _build(req, ds)
    try:
        return live.import_build(built)
    except Exception as exc:  # the client API is local and best-effort
        return {"sent": False, "reason": str(exc)}


@app.get("/api/live")
def live_state():
    try:
        return live.read_champ_select()
    except Exception as exc:  # the client API is local and best-effort
        return {"connected": False, "in_champ_select": False, "reason": str(exc)}


@app.get("/api/health")
def health():
    """Lets a second launch recognise that the app is already running."""
    return {"app": APP_NAME, "version": VERSION}


@app.post("/api/heartbeat")
def heartbeat():
    """The desktop window pings this; the launcher quits once it stops."""
    runtime.beat()
    return {"ok": True}


@app.post("/api/quit")
def quit_app(x_requested_with: Annotated[str | None, Header()] = None):
    """A newer launch asks this outdated instance to quit so the update can be installed."""
    if x_requested_with != APP_NAME:  # a custom header forces a CORS preflight: web pages cannot send it
        raise HTTPException(403, "Forbidden")
    if not runtime.desktop or importer.job.running:
        raise HTTPException(409, "Cannot quit now")
    runtime.quit_requested = True
    return {"ok": True}


class ImportRequest(BaseModel):
    leagues: list[str] = []  # empty: every league already in the database (the LCK on a fresh install)
    first_year: int = Field(2017, ge=2017, le=2100)


@app.get("/api/import")
def import_status():
    return importer.job.status()


@app.post("/api/import")
def import_start(req: ImportRequest):
    unknown = [k for k in req.leagues if k not in LEAGUES]
    if unknown:
        raise HTTPException(422, f"Unknown league {', '.join(unknown)}")
    leagues = req.leagues or [s["league"] for s in league_summary() if s["league"] in LEAGUES] or [DEFAULT_LEAGUE]
    if not importer.job.start(leagues, req.first_year):
        raise HTTPException(409, "An import is already running")
    return importer.job.status()


@app.post("/api/import/cancel")
def import_cancel():
    importer.job.cancel()
    return importer.job.status()


class FrontFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Hashed bundles never change; everything else (index.html) must be revalidated, or the
        # window keeps showing the previous release after an update.
        immutable = path.replace("\\", "/").startswith("assets/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return response


if STATIC_DIR is not None:
    # The built front-end, served by the API itself (desktop app). Must come after every /api route.
    app.mount("/", FrontFiles(directory=STATIC_DIR, html=True), name="front")
