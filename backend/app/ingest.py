"""Import games (drafts, series, player stats) of every league from Leaguepedia.

Uses Special:CargoExport, which is not subject to the strict api.php rate limit.

Usage (from backend/):
    python -m app.ingest                                  # every league, every year since 2017
    python -m app.ingest --leagues LPL LEC --years 2025 2026
"""
import argparse
import csv
import io
import sys
import time
from collections import defaultdict
from datetime import date

import httpx

from .db import ROLES, connect, save_game, save_match
from .leagues import INTERNATIONAL, LEAGUES, owner_league

EXPORT_URL = "https://lol.fandom.com/wiki/Special:CargoExport"
USER_AGENT = "league-pro-comp/0.2 (personal analysis project)"
PAGE_SIZE = 5000  # CargoExport serves 5000 rows in under a second
FIRST_YEAR = 2017  # PicksAndBansS7 (the draft table) starts with season 7

ROLE_ALIASES = {
    "top": "top",
    "jungle": "jungle",
    "jungler": "jungle",
    "jng": "jungle",
    "mid": "mid",
    "middle": "mid",
    "bot": "bot",
    "adc": "bot",
    "bottom": "bot",
    "support": "support",
    "sup": "support",
}

GAME_FIELDS = (
    ["PB.GameId", "PB.MatchId", "PB.Team1", "PB.Team2", "PB.Winner", "PB.OverviewPage",
     "PB.N_GameInMatch", "SG.DateTime_UTC", "SG.Patch", "SG.Gamelength_Number"]
    + [f"PB.Team{t}{k}{i}" for t in (1, 2) for k in ("Ban", "Pick", "Role") for i in range(1, 6)]
    + [f"PB.Team{t}PicksByRoleOrder" for t in (1, 2)]
    + [f"SG.Team{t}{k}" for t in (1, 2) for k in ("Kills", "Gold", "Towers", "Dragons", "Barons")]
)
PLAYER_FIELDS = [f"SP.{f}" for f in ("GameId", "Link", "Team", "Champion", "Role", "Side", "Kills", "Deaths",
                                     "Assists", "Gold", "CS", "DamageToChampions", "VisionScore")]
MATCH_FIELDS = [f"MS.{f}" for f in ("MatchId", "BestOf", "Team1", "Team2", "Team1Score", "Team2Score",
                                    "Winner", "OverviewPage", "Tab", "DateTime_UTC")]


def normalize_role(value: str | None) -> str | None:
    return ROLE_ALIASES.get((value or "").strip().lower())


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_game(row: dict, players: dict[tuple[int, str], dict] | None = None) -> dict | None:
    """Convert one joined Cargo row into a game dict, or None if the draft is incomplete.

    `players` maps (side, role) to the ScoreboardPlayers row of this game.
    """
    players = players or {}
    picks: list[dict] = []
    bans: list[tuple[int, int, str]] = []
    for side in (1, 2):
        by_role_order = [c.strip() for c in (row.get(f"Team{side}PicksByRoleOrder") or "").split(",")]
        role_of = dict(zip(by_role_order, ROLES)) if len(by_role_order) == 5 else {}
        side_picks = []
        for order in range(1, 6):
            champion = (row.get(f"Team{side}Pick{order}") or "").strip()
            if not champion:
                return None
            role = normalize_role(row.get(f"Team{side}Role{order}")) or role_of.get(champion)
            if not role:
                return None
            stats = players.get((side, role), {})
            side_picks.append({
                "side": side, "pick_order": order, "role": role, "champion": champion,
                "player": stats.get("Link") or None,
                "kills": _int(stats.get("Kills")), "deaths": _int(stats.get("Deaths")),
                "assists": _int(stats.get("Assists")), "gold": _int(stats.get("Gold")),
                "cs": _int(stats.get("CS")), "damage": _int(stats.get("DamageToChampions")),
                "vision": _int(stats.get("VisionScore")),
            })
        if len({p["role"] for p in side_picks}) != 5:
            return None
        picks += side_picks
        for slot in range(1, 6):
            champion = (row.get(f"Team{side}Ban{slot}") or "").strip()
            if champion and champion.lower() != "none":
                bans.append((side, slot, champion))
    winner = _int(row.get("Winner"))
    return {
        "game_id": row["GameId"],
        "match_id": row.get("MatchId") or None,
        "game_number": _int(row.get("N GameInMatch")),
        "date": (row.get("DateTime UTC") or "").replace(" ", "T") or None,
        "patch": row.get("Patch") or None,
        "tournament": row.get("OverviewPage"),
        "stage": None,
        "team1": row["Team1"],
        "team2": row["Team2"],
        "winner": winner if winner in (1, 2) else None,
        "duration": _float(row.get("Gamelength Number")),
        "source": "leaguepedia",
        "sides": {
            side: {k.lower(): _int(row.get(f"Team{side}{k}"))
                   for k in ("Kills", "Gold", "Towers", "Dragons", "Barons")}
            for side in (1, 2)
        },
        "picks": picks,
        "bans": bans,
    }


def parse_match(row: dict) -> dict:
    winner = _int(row.get("Winner"))
    return {
        "match_id": row["MatchId"],
        "tournament": row.get("OverviewPage"),
        "stage": row.get("Tab") or None,
        "date": (row.get("DateTime UTC") or "").replace(" ", "T") or None,
        "best_of": _int(row.get("BestOf")),
        "team1": row.get("Team1"),
        "team2": row.get("Team2"),
        "score1": _int(row.get("Team1Score")),
        "score2": _int(row.get("Team2Score")),
        "winner": winner if winner in (1, 2) else None,
    }


def export(client: httpx.Client, tables: str, fields: list[str], where: str,
           order_by: str, join_on: str | None = None, retries: int = 5):
    """Yield every row of a Cargo query, paginating transparently."""
    offset = 0
    while True:
        params = {
            "tables": tables, "fields": ",".join(fields), "where": where,
            "order_by": order_by, "limit": PAGE_SIZE, "offset": offset, "format": "csv",
        }
        if join_on:
            params["join_on"] = join_on
        for attempt in range(retries):
            response = client.get(EXPORT_URL, params=params)
            if response.status_code == 200:
                break
            if response.status_code == 500 and attempt >= 1:
                # Cargo answers 500 when a query matches nothing (a league that did not exist that year)
                return
            wait = 3 if response.status_code == 500 else 10 * 2 ** attempt
            print(f"  HTTP {response.status_code}, retrying in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)
        else:
            raise RuntimeError(f"Leaguepedia export failed ({response.status_code})")
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        yield from rows
        if len(rows) < PAGE_SIZE:
            return
        offset += PAGE_SIZE
        time.sleep(0.3)


def ingest_year(client: httpx.Client, conn, league: str, year: int) -> tuple[int, int]:
    """Import one league-year. Tournaments are matched through Leaguepedia's Tournaments table."""
    names = ",".join(f'"{n}"' for n in LEAGUES[league]["sources"])
    where = f"T.League IN ({names}) AND T.Year = {year}"
    game_rows = list(export(
        client, "PicksAndBansS7=PB,ScoreboardGames=SG,Tournaments=T", GAME_FIELDS, where,
        "SG.DateTime_UTC,PB.GameId", join_on="PB.GameId=SG.GameId,PB.OverviewPage=T.OverviewPage",
    ))
    if not game_rows:  # the league did not exist (or had no draft data) that year
        return 0, 0
    players: dict[str, dict[tuple[int, str], dict]] = defaultdict(dict)
    for row in export(client, "ScoreboardPlayers=SP,Tournaments=T", PLAYER_FIELDS, where, "SP.GameId",
                      join_on="SP.OverviewPage=T.OverviewPage"):
        role = normalize_role(row.get("Role"))
        side = _int(row.get("Side"))
        if role and side:
            players[row["GameId"]][(side, role)] = row

    saved = skipped = 0
    for row in game_rows:
        game = parse_game(row, players.get(row["GameId"]))
        if game is None:
            skipped += 1
            continue
        game["league"] = owner_league(game["game_id"]) or league
        save_game(conn, game)
        saved += 1

    for row in export(client, "MatchSchedule=MS,Tournaments=T", MATCH_FIELDS, where,
                      "MS.DateTime_UTC,MS.MatchId", join_on="MS.OverviewPage=T.OverviewPage"):
        match = parse_match(row)
        match["league"] = owner_league(match["match_id"]) or league
        save_match(conn, match)
        conn.execute(
            "UPDATE games SET stage = ? WHERE match_id = ?", (match["stage"], match["match_id"])
        )
    conn.commit()
    return saved, skipped


def repair_leagues(conn) -> int:
    """Give games and matches imported under an international event back to the league that
    organised them (e.g. LCK Regional Finals tagged as World Championship). Returns rows fixed."""
    marks = ",".join("?" for _ in INTERNATIONAL)
    fixed = 0
    for table, key in (("games", "game_id"), ("matches", "match_id")):
        rows = conn.execute(f"SELECT {key}, league FROM {table} WHERE league IN ({marks})", tuple(INTERNATIONAL)).fetchall()
        for row in rows:
            owner = owner_league(row[0])
            if owner and owner != row[1]:
                conn.execute(f"UPDATE {table} SET league = ? WHERE {key} = ?", (owner, row[0]))
                fixed += 1
    conn.commit()
    return fixed


def ingest(leagues: list[str], years: list[int], on_step=None, cancelled=None) -> int:
    """Import every league-year. `on_step(league, year, saved, skipped, done, total)` reports progress
    and `cancelled()` lets a caller (the app's import button) stop between two league-years."""
    conn = connect()
    total = 0
    done = 0
    steps = len(leagues) * len(years)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True) as client:
        for league in leagues:
            league_total = 0
            for year in years:
                if cancelled and cancelled():
                    repair_leagues(conn)
                    conn.close()
                    return total
                saved = skipped = 0
                try:
                    saved, skipped = ingest_year(client, conn, league, year)
                except RuntimeError as exc:
                    print(f"{league} {year}: FAILED ({exc})", file=sys.stderr, flush=True)
                league_total += saved
                done += 1
                if saved or skipped:
                    print(f"{league} {year}: {saved} games saved, {skipped} skipped (incomplete drafts)", flush=True)
                if on_step:
                    on_step(league, year, saved, skipped, done, steps)
            total += league_total
            print(f"== {league}: {league_total} games", flush=True)
    fixed = repair_leagues(conn)
    if fixed:
        print(f"{fixed} qualifier games/matches given back to their organising league", flush=True)
    conn.close()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES), metavar="LEAGUE",
                        help=f"any of: {', '.join(LEAGUES)} (default: all)")
    parser.add_argument("--years", type=int, nargs="+",
                        default=list(range(FIRST_YEAR, date.today().year + 1)))
    args = parser.parse_args()
    unknown = [lg for lg in args.leagues if lg not in LEAGUES]
    if unknown:
        parser.error(f"unknown league(s): {', '.join(unknown)}")
    ingest(args.leagues, args.years)


if __name__ == "__main__":
    main()
