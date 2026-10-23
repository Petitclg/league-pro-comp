from fastapi.testclient import TestClient

from app import dataset, live, stats
from app.dataset import Filters, load
from app.db import ROLES
from app.ingest import parse_game
from app.suggest import Params, substitutes, suggest

from .synth import build_db

import pytest


@pytest.fixture(scope="module")
def ds():
    return load(build_db())


def test_parse_game_uses_roles_and_player_stats():
    row = {"GameId": "G1", "MatchId": "M1", "Team1": "A", "Team2": "B", "Winner": "2",
           "DateTime UTC": "2026-01-01 10:00:00", "N GameInMatch": "2", "Patch": "26.1",
           "Gamelength Number": "31.5", "Team1Kills": "12"}
    players = {}
    for side in (1, 2):
        names = [f"S{side}C{i}" for i in range(1, 6)]
        for i, n in enumerate(names, 1):
            row[f"Team{side}Pick{i}"] = n
            row[f"Team{side}Role{i}"] = "" if side == 2 else ["Top", "Jungle", "Mid", "Bot", "Support"][i - 1]
            row[f"Team{side}Ban{i}"] = f"B{side}{i}"
        row[f"Team{side}PicksByRoleOrder"] = ",".join(names)
        players[(side, "mid")] = {"Link": f"P{side}", "Kills": "3", "Deaths": "1", "Assists": "7"}
    game = parse_game(row, players)
    assert game["winner"] == 2 and game["game_number"] == 2 and game["duration"] == 31.5
    mid = next(p for p in game["picks"] if p["side"] == 2 and p["role"] == "mid")
    assert mid["champion"] == "S2C3" and mid["player"] == "P2" and mid["kills"] == 3
    assert len(game["bans"]) == 10 and game["sides"][1]["kills"] == 12


def test_parse_game_follows_champions_when_scoreboard_sides_are_swapped():
    row = {"GameId": "G1", "Team1": "A", "Team2": "B", "Winner": "1", "Team1Kills": "1", "Team2Kills": "20"}
    players = {}
    for side in (1, 2):
        for i, role in enumerate(["top", "jungle", "mid", "bot", "support"], 1):
            row[f"Team{side}Pick{i}"] = f"S{side}C{i}"
            row[f"Team{side}Role{i}"] = role
            # the scoreboard lists team B first
            players[(3 - side, role)] = {"Link": f"P{side}{i}", "Champion": f"S{side}C{i}", "Kills": str(side)}
    game = parse_game(row, players)
    top = next(p for p in game["picks"] if p["side"] == 1 and p["role"] == "top")
    assert top["player"] == "P11" and top["kills"] == 1
    assert game["sides"][1]["kills"] == 20 and game["sides"][2]["kills"] == 1


def test_parse_game_rejects_incomplete_draft():
    assert parse_game({"GameId": "G", "Team1": "A", "Team2": "B", "Team1Pick1": "Ahri"}) is None


def test_fearless_detected_from_data_and_locked_champions(ds):
    assert ds.fearless_tournaments == {"LCK/2025 Season/Cup"}
    for games in ds.series.values():
        seen = set()
        for g in games:
            picks = {p["champion"] for s in g["sides"].values() for p in s["picks"]}
            if g["fearless"]:
                assert set(g["locked"]) == seen and not (picks & set(g["locked"]))
            else:
                assert g["locked"] == []
            seen |= picks
    third = next(g for gs in ds.series.values() for g in gs if g["fearless"] and g["game_number"] == 3)
    assert len(third["locked"]) == 20


def test_filters(ds):
    fearless = ds.filter_games(Filters(fearless=True))
    assert fearless and all(g["fearless"] for g in fearless)
    first = ds.filter_games(Filters(game_numbers=(1,)))
    assert all(g["game_number"] == 1 for g in first)
    azir = ds.filter_games(Filters(champion="Azir", role="mid"))
    assert all(any(p["champion"] == "Azir" and p["role"] == "mid" for s in g["sides"].values() for p in s["picks"])
               for g in azir)
    team = ds.filter_comps(Filters(teams=("T1",)))
    assert team and all(c["team"] == "T1" for c in team)
    versus = ds.filter_games(Filters(versus=("Azir", "Ahri")))
    assert all({"Azir", "Ahri"} <= {p["champion"] for s in g["sides"].values() for p in s["picks"]} for g in versus)


def test_pairs_consistent_and_synergy_detected(ds):
    comps = ds.filter_comps(Filters())
    all_pairs = stats.pair_stats(comps, min_games=1, limit=10_000)
    assert sum(p["games"] for p in all_pairs) == len(comps) * 10
    pair = next(p for p in all_pairs if p["a"]["champion"] == "Sejuani" and p["b"]["champion"] == "Azir")
    assert pair["lift"] > 1.3
    azir = stats.pair_stats(comps, min_games=1, champion="Azir", role_a="jungle", limit=1000)
    assert azir and all("jungle" in (p["a"]["role"], p["b"]["role"]) for p in azir)


def test_champion_detail(ds):
    d = stats.champion_detail(ds, "Azir", Filters())
    assert d["picks"] == sum(r["games"] for r in d["roles"]) and d["roles"][0]["role"] == "mid"
    assert d["sides"]["blue"]["games"] + d["sides"]["red"]["games"] == d["picks"]
    assert sum(g["games"] for g in d["by_game_number"]) == d["picks"]
    assert d["partners"] and d["matchups"] and d["players"]
    assert d["stats"]["kda"] is not None
    assert stats.champion_detail(ds, "Nope", Filters()) is None


def test_champion_detail_follows_every_filter(ds):
    """Synergies, matchups and rates must all be computed inside the selected scope."""
    for f in (Filters(fearless=True), Filters(patches=("24.5",)), Filters(tournaments=("LCK/2025",)),
              Filters(teams=("T1",)), Filters(game_numbers=(2,))):
        d = stats.champion_detail(ds, "Azir", f)
        comps = ds.filter_comps(f)
        in_scope = sum(1 for c in comps if "Azir" in c["picks"].values())
        assert d["picks"] == in_scope
        assert d["games_in_scope"] == len(ds.filter_games(Filters(**{**f.__dict__, "champion": None})))
        assert all(p["games"] <= in_scope for p in d["partners"])
        assert all(m["games"] <= in_scope for m in d["matchups"])
        assert sum(m["games"] for m in d["matchups"]) <= in_scope
    everything = stats.champion_detail(ds, "Azir", Filters())
    scoped = stats.champion_detail(ds, "Azir", Filters(fearless=True))
    assert scoped["partners"][0]["games"] < everything["partners"][0]["games"]


def test_team_filter_rates_use_the_team_games(ds):
    t1_games = ds.filter_games(Filters(teams=("T1",)))
    rows = {r["champion"]: r for r in stats.champion_table(ds, Filters(teams=("T1",)))}
    top = max(rows.values(), key=lambda r: r["picks"])
    assert top["pick_rate"] == round(top["picks"] / len(t1_games), 4)


def test_records_follow_the_scope(ds):
    full = {s["id"]: s for s in stats.records(ds, Filters())}
    fearless = {s["id"]: s for s in stats.records(ds, Filters(fearless=True))}
    assert full["synergy"]["items"] != (fearless.get("synergy") or {}).get("items")


def test_duo_detail(ds):
    syn = stats.duo_detail(ds, "synergy", "Azir", "Sejuani", Filters())
    assert syn["games"] > 0
    counter = stats.duo_detail(ds, "counter", "Azir", "Ahri", Filters())
    reverse = stats.duo_detail(ds, "counter", "Ahri", "Azir", Filters())
    assert counter["games"] == reverse["games"] and counter["wins"] + reverse["wins"] == counter["games"]


def test_highlights_and_records(ds):
    g = next(iter(ds.games))
    assert isinstance(stats.game_highlights(ds, g), list)
    sections = {s["id"]: s for s in stats.records(ds, Filters())}
    assert "kills" in sections and "longest" in sections and "synergy" in sections


def test_pair_stats_min_lift():
    comps = load(build_db(60)).comps_all
    everything = stats.pair_stats(comps, min_games=1, limit=10_000)
    lifts = sorted({r["lift"] for r in everything})
    assert len(lifts) > 2
    threshold = lifts[len(lifts) // 2]
    kept = stats.pair_stats(comps, min_games=1, limit=10_000, min_lift=threshold)
    assert kept and len(kept) < len(everything)
    assert all(r["lift"] >= threshold for r in kept)
    assert {(r["a"]["champion"], r["b"]["champion"]) for r in kept} == {
        (r["a"]["champion"], r["b"]["champion"]) for r in everything if r["lift"] >= threshold
    }


def test_suggest_respects_unavailable_and_synergy(ds):
    comps = ds.filter_comps(Filters())
    result = suggest(comps, {"mid": "Azir"}, unavailable={"Sejuani"})
    assert set(result["suggestions"]) == set(ROLES) - {"mid"}
    assert all(s["champion"] != "Sejuani" for s in result["suggestions"]["jungle"])
    assert suggest(comps, {"mid": "Azir"})["suggestions"]["jungle"][0]["champion"] == "Sejuani"
    full = comps[0]["picks"]
    assert suggest(comps, full)["similar_comps"][0]["match"] == 5


def test_suggest_enemy_scales_scores(ds):
    comps = ds.filter_comps(Filters())
    result = suggest(comps, {"mid": "Azir"}, enemy=["Ornn"])
    first = result["suggestions"]["top"][0]
    assert first["champion"] != "Ornn" and first["counter_games"] >= 0
    assert any(s["counter_winrate"] is not None for r in result["suggestions"].values() for s in r)


def test_parse_lcu_session():
    names = {1: "Annie", 2: "Olaf", 3: "Galio", 4: "Ahri", 5: "Jinx", 6: "Lulu", 7: "Zed", 8: "Vi"}
    session = {
        "localPlayerCellId": 0,
        "myTeam": [
            {"cellId": 0, "assignedPosition": "middle"},
            {"cellId": 1, "assignedPosition": "utility"},
            {"cellId": 2, "assignedPosition": "bottom"},
        ],
        "actions": [
            [{"actorCellId": 0, "championId": 1, "completed": True, "type": "ban"},
             {"actorCellId": 5, "championId": 2, "completed": True, "type": "ban"}],
            [{"actorCellId": 0, "championId": 4, "completed": True, "type": "pick"},
             {"actorCellId": 1, "championId": 6, "completed": False, "type": "pick"},
             {"actorCellId": 2, "championId": 5, "completed": True, "type": "pick"},
             {"actorCellId": 6, "championId": 7, "completed": True, "type": "pick"},
             {"actorCellId": 7, "championId": 8, "completed": True, "type": "pick"}],
        ],
        "timer": {"phase": "BAN_PICK"},
    }
    state = live.parse_session(session, names)
    assert state["my_team"] == {"mid": "Ahri", "bot": "Jinx"}
    assert state["bans"] == ["Annie", "Olaf"] and sorted(state["enemy_picks"]) == ["Vi", "Zed"]


def test_api(ds):
    from app import main

    main.app.dependency_overrides[main.dataset] = lambda: ds
    client = TestClient(main.app)
    meta = client.get("/api/meta").json()
    assert meta["fearless_since"] and "2024" in meta["years"] and meta["series"] == 120

    series = client.get("/api/series?limit=5&fearless=true").json()
    assert series["total"] == 60 and len(series["series"]) == 5
    s = series["series"][0]
    assert s["fearless"] and s["best_of"] == 3 and sum(s["score"]) == len(s["games"])
    g2 = s["games"][-1]
    assert g2["sides"]["1"]["picks"][0]["slot"] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

    one = client.get("/api/series?champion=Azir&role=mid&limit=50").json()
    assert one["total"] > 0
    assert client.get("/api/series?role=nope").status_code == 422
    assert client.get("/api/champions/Azir").status_code == 200
    assert client.get("/api/champions/Nope").status_code == 404
    assert client.get("/api/pairs?min_games=3&sort=lift").json()["pairs"]
    assert client.get("/api/duo?kind=synergy&a=Azir&b=Sejuani").json()["games"] > 0
    assert client.get("/api/records").json()
    res = client.post("/api/suggest", json={"picks": {"mid": "Azir"}, "enemy": ["Ornn"], "unavailable": ["Vi"]})
    assert res.status_code == 200 and res.json()["suggestions"]
    assert client.post("/api/suggest", json={"picks": {"king": "Azir"}}).status_code == 422
    main.app.dependency_overrides.clear()


def test_recency_decay_favours_recent_comps():
    def comp(day, mid):
        return {"game_id": day, "team": "A", "opponent": "B", "win": True, "date": f"{day} 10:00:00",
                "game_number": 1, "enemy": {}, "picks": {"top": "Ornn", "mid": mid}}

    old = [comp("2024-01-01", "Azir") for _ in range(10)]
    recent = [comp("2025-06-01", "Ahri") for _ in range(6)]
    flat = suggest(old + recent, {"top": "Ornn"}, params=Params(half_life_days=None))["suggestions"]["mid"]
    decayed = suggest(old + recent, {"top": "Ornn"}, params=Params(half_life_days=90))["suggestions"]["mid"]
    assert flat[0]["champion"] == "Azir" and decayed[0]["champion"] == "Ahri"


def _pro_comps():
    def comp(day, top, mid, win=True):
        return {"game_id": day + top, "team": "A", "opponent": "B", "win": win, "date": f"{day} 10:00:00",
                "game_number": 1, "enemy": {}, "picks": {"top": top, "mid": mid}}

    return [comp("2025-06-01", "Ornn", "Azir"), comp("2025-06-02", "Ornn", "Azir"),
            comp("2025-06-03", "Gnar", "Ahri"), comp("2025-06-04", "Gnar", "Ahri")]


def test_suggest_unknown_locked_champion_falls_back_to_popularity():
    comps = _pro_comps()
    result = suggest(comps, {"top": "Singed"})
    assert result["matching_comps"] == 0 and result["unknown_picks"] == ["top"]
    assert {s["champion"] for s in result["suggestions"]["mid"]} == {"Azir", "Ahri"}
    assert result["similar_comps"] == [] and result["closest_teams"] == []


def test_suggest_substitutes_for_unknown_champion():
    comps = _pro_comps()
    profiles = {
        "Singed": {"tags": ["Tank", "Mage"], "info": {"attack": 4, "defense": 8, "magic": 7}},
        "Ornn": {"tags": ["Tank", "Fighter"], "info": {"attack": 5, "defense": 9, "magic": 3}},
        "Gnar": {"tags": ["Tank", "Fighter"], "info": {"attack": 6, "defense": 5, "magic": 5}},
    }
    subs = substitutes(comps, {"top": "Singed"}, profiles)
    assert set(subs["top"]) == {"Ornn", "Gnar"} and 0 < subs["top"]["Ornn"] < 0.5
    result = suggest(comps, {"top": "Singed"}, subs=subs)
    assert result["matching_comps"] == 4 and set(result["substitutes"]["top"]) == {"Ornn", "Gnar"}
    assert result["suggestions"]["mid"][0]["share"] > 0
    assert substitutes(comps, {"top": "Ornn"}, profiles) == {}


def test_cooc_model_ranks_by_popularity_times_lift():
    comps = _pro_comps() + [dict(_pro_comps()[0], game_id=f"x{i}") for i in range(3)]
    ranked = suggest(comps, {"top": "Gnar"}, params=Params(model="cooc"))["suggestions"]["mid"]
    assert ranked[0]["champion"] == "Ahri"


def test_lobby_messages_one_line_per_role_and_only_changes(monkeypatch):
    suggestions = {
        "top": [{"champion": c} for c in ("Ornn", "Jax", "Gnar", "Aatrox", "Fiora", "Camille")],
        "mid": [{"champion": c} for c in ("Azir", "Ahri")],
        "jungle": [],
    }
    lines = live.format_recommendations(suggestions, "mid")
    assert list(lines) == ["mid", "top"] and lines["mid"] == "[Pro] Mid: Azir, Ahri"
    assert lines["top"] == "[Pro] Top: Ornn, Jax, Gnar, Aatrox, Fiora"  # 5 champions

    posted = []

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: [{"type": "championSelect", "id": "c1"}]})()
        def post(self, url, json):
            posted.append(json["body"])
            return type("R", (), {"raise_for_status": lambda s: None})()

    monkeypatch.setattr(live, "find_credentials", lambda: (1234, "pw"))
    monkeypatch.setattr(live.httpx, "Client", FakeClient)
    live._sent.clear()
    assert live.send_lobby_messages(lines) == list(lines.values()) and posted == list(lines.values())
    assert live.send_lobby_messages(lines) == []  # nothing changed
    changed = {**lines, "top": "[Pro] Top: Jax, Ornn"}
    assert live.send_lobby_messages(changed) == ["[Pro] Top: Jax, Ornn"]
    live._sent.clear()
