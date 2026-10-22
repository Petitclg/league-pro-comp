import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import ingest, main
from app.dataset import Filters, load
from app.db import SCHEMA_VERSION, connect, save_game
from app.leagues import LEAGUES

from .synth import build_db

V2_GAMES = """
CREATE TABLE games (
    game_id TEXT PRIMARY KEY, match_id TEXT, game_number INTEGER, date TEXT, patch TEXT,
    tournament TEXT, stage TEXT, team1 TEXT NOT NULL, team2 TEXT NOT NULL, winner INTEGER,
    duration REAL, source TEXT NOT NULL DEFAULT 'leaguepedia');
CREATE TABLE matches (
    match_id TEXT PRIMARY KEY, tournament TEXT, stage TEXT, date TEXT, best_of INTEGER,
    team1 TEXT, team2 TEXT, score1 INTEGER, score2 INTEGER, winner INTEGER);
"""


def test_v2_database_is_migrated_and_keeps_its_lck_games(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(V2_GAMES)
    old.execute("INSERT INTO games (game_id, team1, team2) VALUES ('G1', 'A', 'B')")
    old.execute("INSERT INTO matches (match_id) VALUES ('M1')")
    old.execute("PRAGMA user_version = 2")
    old.commit()
    old.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT league FROM games").fetchone()[0] == "LCK"
    assert conn.execute("SELECT league FROM matches").fetchone()[0] == "LCK"
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1  # nothing was dropped
    conn.close()
    assert connect(path).execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1  # idempotent


def test_database_of_a_newer_version_is_left_untouched(tmp_path):
    path = tmp_path / "newer.db"
    conn = connect(path)
    conn.execute("INSERT INTO games (game_id, team1, team2) VALUES ('G1', 'A', 'B')")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    reopened = connect(path)
    assert reopened.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    assert reopened.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1


def _two_league_db():
    """A third of the series (whole series, never split) belongs to the LPL, the rest to the LCK."""
    conn = build_db(60)
    matches = [r[0] for r in conn.execute("SELECT match_id FROM matches ORDER BY match_id")]
    lpl_matches = matches[: len(matches) // 3]
    conn.executemany("UPDATE games SET league = 'LPL' WHERE match_id = ?", [(m,) for m in lpl_matches])
    conn.executemany("UPDATE matches SET league = 'LPL' WHERE match_id = ?", [(m,) for m in lpl_matches])
    lpl = {r[0] for r in conn.execute("SELECT game_id FROM games WHERE league = 'LPL'")}
    return conn, lpl


def test_load_returns_only_the_requested_league():
    conn, lpl_ids = _two_league_db()
    lpl, lck, everything = load(conn, "LPL"), load(conn, "LCK"), load(conn)
    assert {g["game_id"] for g in lpl.games} == lpl_ids
    assert not ({g["game_id"] for g in lck.games} & lpl_ids)
    assert len(lpl.games) + len(lck.games) == len(everything.games)
    assert all(m["league"] == "LPL" for m in lpl.matches.values())
    assert load(conn, "LEC").games == []


def test_load_accepts_several_leagues_at_once():
    conn, lpl_ids = _two_league_db()
    both = load(conn, ("LCK", "LPL"))
    assert len(both.games) == len(load(conn).games)
    assert {g["league"] for g in both.games} == {"LCK", "LPL"}
    assert {g["game_id"] for g in load(conn, ["LPL"]).games} == lpl_ids
    assert load(conn, ("LPL", "LEC")).games == load(conn, ("LEC", "LPL")).games  # order does not matter
    assert len(load(conn, ("LPL", "LEC")).games) == len(lpl_ids)
    assert len(both.matches) == len(load(conn).matches)


def test_combined_leagues_keep_series_and_fearless_detection_per_tournament():
    conn, _ = _two_league_db()
    both = load(conn, ("LCK", "LPL"))
    one = load(conn, "LCK")
    assert both.fearless_tournaments >= one.fearless_tournaments
    assert set(both.series) >= set(one.series)
    assert all(len({g["league"] for g in games}) == 1 for games in both.series.values())  # a series never mixes leagues


def test_year_filter_uses_the_game_date():
    ds = load(build_db(60))
    years = {g["date"][:4] for g in ds.games}
    assert len(years) == 1
    year = years.pop()
    assert len(ds.filter_games(Filters(years=(year,)))) == len(ds.games)
    assert ds.filter_games(Filters(years=("1999",))) == []


def test_registry_has_no_source_shared_between_leagues():
    sources = [s for league in LEAGUES.values() for s in league["sources"]]
    assert len(sources) == len(set(sources))
    assert {"LCK", "LPL", "LEC", "LCS", "WORLDS"} <= set(LEAGUES)
    assert all(league["region"] and league["name"] for league in LEAGUES.values())


def test_api_league_selection(monkeypatch):
    conn, lpl_ids = _two_league_db()
    datasets = {("LCK",): load(conn, "LCK"), ("LPL",): load(conn, "LPL"), ("LCK", "LPL"): load(conn, ("LCK", "LPL"))}
    requested = []

    def fake_get_dataset(leagues="LCK"):
        leagues = (leagues,) if isinstance(leagues, str) else tuple(leagues)
        requested.append(leagues)
        return datasets[leagues]

    monkeypatch.setattr(main, "get_dataset", fake_get_dataset)
    client = TestClient(main.app)
    assert client.get("/api/meta?league=NOPE").status_code == 404
    assert client.get("/api/meta?league=LPL&league=NOPE").status_code == 404
    lck = client.get("/api/meta").json()
    lpl = client.get("/api/meta?league=LPL").json()
    both = client.get("/api/meta?league=LPL&league=LCK").json()  # order and repetition do not matter
    assert lck["selected"] == ["LCK"] and lpl["selected"] == ["LPL"] and both["selected"] == ["LCK", "LPL"]
    total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    assert lpl["games"] == len(lpl_ids) and lck["games"] + lpl["games"] == total == both["games"]
    assert ("LCK", "LPL") in requested
    assert "2024" in lck["years"] and "2024" in lpl["years"]
    assert client.get("/api/champions?league=LPL&year=2024").status_code == 200

    series = client.get("/api/series?league=LCK&league=LPL&limit=100").json()
    assert series["total"] == both["series"] and {s["league"] for s in series["series"]} == {"LCK", "LPL"}
    stats_both = client.get("/api/champions?league=LCK&league=LPL").json()
    stats_lck = client.get("/api/champions?league=LCK").json()
    assert sum(r["picks"] for r in stats_both) > sum(r["picks"] for r in stats_lck)
    suggest = client.post("/api/suggest?league=LCK&league=LPL", json={"picks": {"mid": "Azir"}})
    assert suggest.status_code == 200


class _Response:
    def __init__(self, text):
        self.status_code, self.content = 200, text.encode()


class _FakeClient:
    """Serves a CSV of `total` rows, PAGE_SIZE at a time, and records the query parameters."""

    def __init__(self, total):
        self.total, self.calls = total, []

    def get(self, _url, params):
        self.calls.append(params)
        start = params["offset"]
        n = max(0, min(ingest.PAGE_SIZE, self.total - start))
        return _Response("GameId\n" + "".join(f"G{start + i}\n" for i in range(n)))


def test_export_paginates_and_sends_the_join(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda _: None)
    client = _FakeClient(ingest.PAGE_SIZE + 7)
    rows = list(ingest.export(client, "A=A,Tournaments=T", ["A.GameId"], 'T.League IN ("x")', "A.GameId",
                              join_on="A.OverviewPage=T.OverviewPage"))
    assert len(rows) == ingest.PAGE_SIZE + 7 and rows[-1]["GameId"] == f"G{ingest.PAGE_SIZE + 6}"
    assert [c["offset"] for c in client.calls] == [0, ingest.PAGE_SIZE]
    assert client.calls[0]["join_on"] == "A.OverviewPage=T.OverviewPage"


def test_ingest_year_selects_the_league_by_its_leaguepedia_names(monkeypatch):
    seen = []

    def fake_export(client, tables, fields, where, *args, **kwargs):
        seen.append((tables, where))
        return iter(())

    monkeypatch.setattr(ingest, "export", fake_export)
    ingest.ingest_year(None, build_db(2), "LEC", 2025)
    assert len(seen) == 1  # no games found: players and matches are not even requested

    def fake_export_with_game(client, tables, fields, where, *args, **kwargs):
        seen.append((tables, where))
        return iter([{"GameId": "G", "Team1": "A", "Team2": "B"}]) if "PicksAndBans" in tables else iter(())

    seen.clear()
    monkeypatch.setattr(ingest, "export", fake_export_with_game)
    ingest.ingest_year(None, build_db(2), "LEC", 2025)
    assert len(seen) == 3  # games, players, matches
    expected = 'T.League IN ("LoL EMEA Championship","Europe League Championship Series") AND T.Year = 2025'
    assert all(where == expected for _, where in seen)
    assert all("Tournaments=T" in tables for tables, _ in seen)


def test_parse_game_keeps_league_after_save():
    conn = build_db(2)
    game = {"game_id": "LPL_X", "league": "LPL", "team1": "A", "team2": "B",
            "picks": [{"side": s, "pick_order": o, "role": r, "champion": f"C{s}{o}"}
                      for s in (1, 2) for o, r in enumerate(["top", "jungle", "mid", "bot", "support"], 1)],
            "bans": []}
    save_game(conn, game)
    assert conn.execute("SELECT league FROM games WHERE game_id = 'LPL_X'").fetchone()[0] == "LPL"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO picks (game_id, side, pick_order, role, champion) VALUES ('LPL_X', 1, 1, 'top', 'Dup')")
