import pytest

from app import importer, ingest
from app.db import INGEST_REVISION, connect, ingest_revision, mark_ingest_revision, save_game


def _game(game_id, league, tournament, date):
    picks = [{"side": s, "pick_order": o, "role": r, "champion": f"C{s}{o}"}
             for s in (1, 2) for o, r in enumerate(["top", "jungle", "mid", "bot", "support"], 1)]
    return {"game_id": game_id, "league": league, "tournament": tournament, "date": date,
            "team1": "A", "team2": "B", "picks": picks, "bans": []}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(importer, "connect", lambda: connect(path))
    monkeypatch.setattr(ingest, "connect", lambda: connect(path))
    return path


def test_fresh_install_has_nothing_to_repair(db_path):
    assert importer.outdated_leagues()[0] == []
    assert importer.start_repair() is False


def test_old_data_is_outdated_until_re_imported(db_path):
    conn = connect(db_path)
    save_game(conn, _game("G1", "LCK", "LCK/2024 Season", "2024-03-01T10:00:00"))
    save_game(conn, _game("G2", "LPL", "LPL/2023 Season", "2023-03-01T10:00:00"))
    conn.commit()
    assert importer.outdated_leagues() == (["LCK", "LPL"], 2023)
    mark_ingest_revision(conn, "LCK")
    assert importer.outdated_leagues()[0] == ["LPL"]
    assert ingest_revision(conn, "LCK") == INGEST_REVISION


def test_international_games_given_to_their_league_still_count(db_path):
    conn = connect(db_path)
    save_game(conn, _game("G1", "LCK", "2025 Season World Championship/Play-In", "2025-10-01T10:00:00"))
    conn.commit()
    assert importer.outdated_leagues()[0] == ["LCK", "WORLDS"]


def test_start_repair_runs_a_flagged_job(db_path, monkeypatch):
    conn = connect(db_path)
    save_game(conn, _game("G1", "LCK", "LCK/2024 Season", "2024-03-01T10:00:00"))
    conn.commit()
    started = {}
    monkeypatch.setattr(importer.job, "start",
                        lambda leagues, first_year, repair=False: started.update(leagues=leagues, year=first_year, repair=repair) or True)
    assert importer.start_repair()
    assert started == {"leagues": ["LCK"], "year": 2024, "repair": True}


def test_a_complete_import_marks_the_league_but_a_partial_one_does_not(db_path, monkeypatch):
    conn = connect(db_path)
    save_game(conn, _game("G1", "LCK", "LCK/2024 Season", "2024-03-01T10:00:00"))
    conn.commit()
    monkeypatch.setattr(ingest, "ingest_year", lambda client, conn, league, year: (1, 0))

    ingest.ingest(["LCK"], [2026])  # current year only: older games were not re-imported
    assert ingest_revision(conn, "LCK") == 0
    ingest.ingest(["LCK"], [2024, 2025, 2026])
    assert ingest_revision(conn, "LCK") == INGEST_REVISION


def test_a_failed_year_leaves_the_league_outdated(db_path, monkeypatch):
    conn = connect(db_path)
    save_game(conn, _game("G1", "LCK", "LCK/2024 Season", "2024-03-01T10:00:00"))
    conn.commit()

    def fail(client, conn, league, year):
        raise RuntimeError("Leaguepedia down")

    monkeypatch.setattr(ingest, "ingest_year", fail)
    ingest.ingest(["LCK"], [2024])
    assert ingest_revision(conn, "LCK") == 0
