from fastapi.testclient import TestClient

from app import main, stats
from app.dataset import Filters, load

from .synth import TEAMS, build_db


def _ds():
    return load(build_db())


def test_several_teams_match_any_of_them():
    ds = _ds()
    one = ds.filter_games(Filters(teams=("T1",)))
    two = ds.filter_games(Filters(teams=("T1", "Gen.G")))
    gen = ds.filter_games(Filters(teams=("Gen.G",)))
    assert two and len(two) >= max(len(one), len(gen))
    assert {g["game_id"] for g in two} == {g["game_id"] for g in one} | {g["game_id"] for g in gen}
    assert all({"T1", "Gen.G"} & {g["team1"], g["team2"]} for g in two)


def test_team_comps_are_the_sides_played_by_any_selected_team():
    ds = _ds()
    comps = ds.filter_comps(Filters(teams=("T1", "Gen.G")))
    assert comps and {c["team"] for c in comps} <= {"T1", "Gen.G"}
    assert len(comps) == len(ds.filter_comps(Filters(teams=("T1",)))) + len(ds.filter_comps(Filters(teams=("Gen.G",))))


def test_values_of_one_filter_are_ored_and_different_filters_are_anded():
    ds = _ds()
    patches = sorted({g["patch"] for g in ds.games})
    assert len(patches) == 2
    both = ds.filter_games(Filters(patches=tuple(patches)))
    assert len(both) == len(ds.games)
    only = ds.filter_games(Filters(patches=(patches[0],), teams=("T1", "Gen.G")))
    assert only and all(g["patch"] == patches[0] and ({"T1", "Gen.G"} & {g["team1"], g["team2"]}) for g in only)
    assert len(only) < len(both)


def test_several_tournaments_years_and_game_numbers():
    ds = _ds()
    names = sorted({g["tournament"] for g in ds.games})
    assert len(ds.filter_games(Filters(tournaments=tuple(names)))) == len(ds.games)
    prefix = ds.filter_games(Filters(tournaments=("LCK/2025", "LCK/2024")))
    assert len(prefix) == len(ds.games)  # prefix match, any of them
    g12 = ds.filter_games(Filters(game_numbers=(1, 2)))
    assert {g["game_number"] for g in g12} == {1, 2}
    assert ds.filter_games(Filters(years=("1999", "2024"))) == ds.filter_games(Filters(years=("2024",)))


def test_cache_distinguishes_different_selections():
    ds = _ds()
    a = ds.filter_games(Filters(teams=("T1",)))
    b = ds.filter_games(Filters(teams=("T1", "Gen.G")))
    assert a is not b and len(a) < len(b)
    assert ds.filter_games(Filters(teams=("T1",))) is a


def test_stats_follow_a_team_selection():
    ds = _ds()
    f = Filters(teams=("T1", "Gen.G"))
    games = ds.filter_games(f)
    rows = {r["champion"]: r for r in stats.champion_table(ds, f)}
    top = max(rows.values(), key=lambda r: r["picks"])
    assert top["pick_rate"] == round(top["picks"] / len(games), 4)
    detail = stats.champion_detail(ds, top["champion"], f)
    assert detail["picks"] == top["picks"] and detail["games_in_scope"] == len(games)


def test_api_accepts_repeated_parameters():
    ds = _ds()
    main.app.dependency_overrides[main.dataset] = lambda: ds
    try:
        client = TestClient(main.app)
        single = client.get("/api/series?team=T1&limit=100").json()["total"]
        double = client.get("/api/series?team=T1&team=Gen.G&limit=100").json()["total"]
        assert 0 < single < double
        patches = sorted({g["patch"] for g in ds.games})
        res = client.get(f"/api/champions?patch={patches[0]}&patch={patches[1]}&game_number=1&game_number=2")
        assert res.status_code == 200 and res.json()
        assert client.get("/api/champions?year=24").status_code == 422
        body = {"picks": {"mid": "Azir"}, "teams": ["T1", "Gen.G"], "patches": patches, "years": ["2024"]}
        assert client.post("/api/suggest", json=body).status_code == 200
        assert set(TEAMS) >= {"T1", "Gen.G"}
    finally:
        main.app.dependency_overrides.clear()
