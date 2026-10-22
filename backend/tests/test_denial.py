from app import denial
from app.dataset import Filters, load

from .synth import build_db


def _game(picks1, picks2, bans1=(), bans2=(), locked=(), number=1):
    """picks: [(champion, role)] in pick order; bans: [(champion, ban_order)]."""
    from app.dataset import PICK_SLOT

    def side(team, picks, bans, n):
        return {
            "team": team, "win": None, "stats": {},
            "picks": [{"slot": PICK_SLOT[n][i], "role": r, "champion": c, "order": i + 1, "player": None}
                      for i, (c, r) in enumerate(picks)],
            "bans": [c for c, _ in bans], "ban_order": [o for _, o in bans],
        }

    return {"game_id": "g", "game_number": number, "locked": list(locked),
            "sides": {1: side("A", picks1, bans1, 1), 2: side("B", picks2, bans2, 2)}}


BLUE = [("Nocturne", "jungle"), ("X1", "top"), ("X2", "mid"), ("X3", "bot"), ("X4", "support")]  # slots 1,4,5,8,9
RED = [("R1", "top"), ("R2", "jungle"), ("R3", "mid"), ("R4", "bot"), ("R5", "support")]  # slots 2,3,6,7,10


def test_fate_classifies_every_outcome():
    f = denial.fate
    # Nocturne is blue's pick 1 (slot 1). Anything blue does not touch is "untouched"
    assert f(_game(BLUE, RED), "Nocturne", "Orianna")["outcome"] == "untouched"
    # banned in phase 1 (before slot 1 is picked)
    assert f(_game(BLUE, RED, bans2=[("Orianna", 2)]), "Nocturne", "Orianna")["outcome"] == "banned_before"
    # banned in phase 2 (order 7+) after the first picks
    assert f(_game(BLUE, RED, bans2=[("Orianna", 7)]), "Nocturne", "Orianna")["outcome"] == "ban_after"
    # picked by the opponent after Nocturne
    red = RED[:2] + [("Orianna", "mid")] + RED[3:]
    assert f(_game(BLUE, red), "Nocturne", "Orianna")["outcome"] == "deny_pick"
    # picked by blue itself later
    blue = BLUE[:2] + [("Orianna", "mid")] + BLUE[3:]
    assert f(_game(blue, RED), "Nocturne", "Orianna")["outcome"] == "combo"
    # Orianna picked by blue before Nocturne (slot 1 vs slot 4): reverse order, not a reaction
    ordered = [("Orianna", "mid"), ("Nocturne", "jungle")] + BLUE[2:]
    assert f(_game(ordered, RED), "Nocturne", "Orianna")["outcome"] == "prior_combo"


def test_fate_ordering_and_exclusions():
    blue = [("Orianna", "mid")] + BLUE[1:]
    g = _game(blue, [("Nocturne", "jungle")] + RED[1:])
    assert denial.fate(g, "Nocturne", "Orianna")["outcome"] == "taken_before"  # Orianna (slot 1) came first
    assert denial.fate(g, "Orianna", "Nocturne")["outcome"] == "deny_pick"
    assert denial.fate(_game(BLUE, RED, locked=["Orianna"]), "Nocturne", "Orianna") is None
    assert denial.fate(_game(BLUE, RED), "Zed", "Orianna") is None  # trigger not picked
    # bans after pick slot 6 do not precede slot 1
    assert denial._ban_time(7) > denial._pick_time(6) and denial._ban_time(6) < denial._pick_time(1)


def test_direction_baseline_and_counts_are_consistent():
    ds = load(build_db())
    res = denial.pair_denial(ds, Filters(), "Azir", "Sejuani")
    for r in (res["a_to_b"], res["b_to_a"]):
        assert sum(r["outcomes"][o] for o in denial.OUTCOMES) == r["available"]
        m = r["measures"]
        assert m["removed"]["count"] == m["anticipation"]["count"] + m["reaction"]["count"]
        assert m["combo"]["count"] == r["outcomes"]["combo"]
        assert sum(s["available"] for s in r["splits"]) == r["available"]
    # in the synthetic data Azir / Sejuani are deliberately paired more often than chance
    assert res["a_to_b"]["measures"]["combo"]["lift"] > 1


def test_team_filter_restricts_to_the_trigger_team():
    ds = load(build_db())
    everyone = denial.direction(ds, Filters(), "Azir", "Sejuani")
    one_team = denial.direction(ds, Filters(teams=("T1",)), "Azir", "Sejuani")
    assert one_team["picked"] < everyone["picked"]


class _FakeDataset:
    def __init__(self, games):
        self.games, self._cache = games, {}

    def filter_games(self, _):
        return self.games


def _patch_game(i, patch, orianna_banned, nocturne_picked):
    blue = ([("Nocturne", "jungle")] if nocturne_picked else [("N0", "jungle")]) + BLUE[1:]
    g = _game(blue, RED, bans2=[("Orianna", 2)] if orianna_banned else [])
    return {**g, "game_id": f"{patch}-{i}", "patch": patch}


def test_baseline_is_matched_by_patch():
    """Orianna is banned all the time on patch P1 and Nocturne is only played on P1: no synergy denial."""
    games = [_patch_game(i, "P1", True, i < 18) for i in range(20)]
    games += [_patch_game(i, "P2", False, False) for i in range(20)]
    ds = _FakeDataset(games)
    m = denial.direction(ds, Filters(), "Nocturne", "Orianna")["measures"]["anticipation"]
    assert m["rate"] == 1.0
    assert 0.8 <= m["lift"] <= 1.25  # same as every other trigger of that patch
    assert abs(m["z"]) < 2


def test_baseline_detects_a_real_ban_reaction():
    """On the same patch, Orianna is banned only when Nocturne is picked."""
    games = [_patch_game(i, "P1", i < 20, i < 20) for i in range(40)]
    ds = _FakeDataset(games)
    m = denial.direction(ds, Filters(), "Nocturne", "Orianna")["measures"]["anticipation"]
    assert m["rate"] == 1.0 and m["lift"] > 1.5 and m["z"] > 3


def test_ranking_and_endpoint():
    ds = load(build_db())
    ranked = denial.ranking(ds, Filters())
    assert ranked["tested"] >= 0 and {"anticipation", "reaction"} <= set(ranked)
    assert [lv["key"] for lv in ranked["levels"]] == ["strong", "likely", "hint"]
    assert ranked["levels"][0]["by_chance"] <= ranked["levels"][2]["by_chance"]
    z_floor = {lv["key"]: lv["z"] for lv in ranked["levels"]}
    for name in ("anticipation", "reaction"):
        zs = [it["z"] for it in ranked[name]["items"]]
        assert zs == sorted(zs, reverse=True)
        for it in ranked[name]["items"]:
            assert it["z"] >= z_floor[it["level"]] and it["lift"] >= denial.SIGNIFICANT_LIFT
            assert it["count"] >= 5

    from fastapi.testclient import TestClient

    from app import main

    main.app.dependency_overrides[main.dataset] = lambda: ds
    try:
        res = TestClient(main.app).get("/api/countered-synergies?limit=3")
        assert res.status_code == 200 and res.json()["tested"] == ranked["tested"]
    finally:
        main.app.dependency_overrides.clear()


def _ban_game(i, patch, ban_n, ban_o, group_number=1):
    bans1 = [("Nocturne", 1)] if ban_n else []
    bans2 = [("Orianna", 2)] if ban_o else []
    g = _game([("N0", "jungle")] + BLUE[1:], RED, bans1=bans1, bans2=bans2, number=group_number)
    return {**g, "game_id": f"{patch}-{i}-{ban_n}{ban_o}", "patch": patch}


def test_neutralisation_detects_one_or_the_other_bans():
    # Each game bans Nocturne XOR Orianna: never both, and "either" is 100 %
    games = [_ban_game(i, "P1", i % 2 == 0, i % 2 == 1) for i in range(40)]
    n = denial.neutralisation(_FakeDataset(games), Filters(), "Nocturne", "Orianna")
    g1, g2, total = n["groups"]
    assert g1["n"] == 40 and g1["either_banned"] == 40 and g1["both_banned"] == 0
    assert g1["expected_both_banned"] == 10.0 and g1["z"] > 4
    assert g2["n"] == 0 and total["n"] == 40


def test_neutralisation_is_neutral_for_independent_bans():
    combos = [(True, True)] * 10 + [(True, False)] * 10 + [(False, True)] * 10 + [(False, False)] * 10
    games = [_ban_game(i, "P1", a, b) for i, (a, b) in enumerate(combos)]
    g1 = denial.neutralisation(_FakeDataset(games), Filters(), "Nocturne", "Orianna")["groups"][0]
    assert g1["both_banned"] == 10 and g1["expected_both_banned"] == 10.0 and abs(g1["z"]) < 0.1
    assert g1["free"] == 10 and g1["none_picked"] == 10


def test_neutralisation_is_matched_by_patch():
    # Popular on P1 (banned together often), unbanned on P2: no deficit once patches are separated
    games = [_ban_game(i, "P1", True, True) for i in range(18)] + [_ban_game(i, "P1", False, False) for i in range(2)]
    games += [_ban_game(i, "P2", False, False) for i in range(20)]
    g1 = denial.neutralisation(_FakeDataset(games), Filters(), "Nocturne", "Orianna")["groups"][0]
    assert g1["z"] <= 0.5  # pooled over patches this would look like a strong positive dependence


def test_neutralisation_skips_fearless_locked_games():
    games = [_ban_game(i, "P1", i % 2 == 0, i % 2 == 1) for i in range(10)]
    games.append({**_ban_game(99, "P1", False, False), "locked": ["Orianna"]})
    g1 = denial.neutralisation(_FakeDataset(games), Filters(), "Nocturne", "Orianna")["groups"][0]
    assert g1["n"] == 10


def test_neutralised_ranking_levels():
    assert denial._neutral_level({"expected_both_banned": 30, "both_banned": 10, "z": 5.0}) == "strong"
    assert denial._neutral_level({"expected_both_banned": 30, "both_banned": 10, "z": 2.5}) == "likely"
    # a deficit of less than 8 games is not worth reporting, however significant
    assert denial._neutral_level({"expected_both_banned": 6, "both_banned": 0, "z": 4.0}) is None
    assert denial._neutral_level({"expected_both_banned": 30, "both_banned": 25, "z": 5.0}) is None
    ranked = denial.ranking(load(build_db()), Filters())
    assert {"tested", "by_level", "chance", "items"} <= set(ranked["neutralised"])


def _presence(games, played=0, teams=()):
    from app import stats

    row = {"a": {"champion": "Nocturne", "role": "jungle"}, "b": {"champion": "Orianna", "role": "mid"}, "games": played}
    stats.pair_presence(_FakeDataset(games), Filters(teams=teams), [row])
    return row


def test_pair_presence_adds_bans_beyond_independent_bans():
    # Each game bans Nocturne XOR Orianna: "either" is 100 % where independent bans give 75 %
    games = [_ban_game(i, "P1", i % 2 == 0, i % 2 == 1) for i in range(40)]
    row = _presence(games, played=4)
    assert row["bans"] == {"either": 40, "expected": 30.0} and row["countered"] == 10.0
    assert row["presence"] == round((4 + 10) / 40, 4) and row["presence_played"] == 0.1
    assert row["presence_countered"] == 0.25 and row["realised_share"] == round(4 / 14, 4)


def test_pair_presence_ignores_independent_or_patch_correlated_bans():
    combos = [(True, True)] * 10 + [(True, False)] * 10 + [(False, True)] * 10 + [(False, False)] * 10
    independent = [_ban_game(i, "P1", a, b) for i, (a, b) in enumerate(combos)]
    row = _presence(independent, played=5)
    assert row["countered"] == 0.0 and row["presence"] == round(5 / 40, 4) == row["presence_played"]
    # Both banned together on a patch where they are strong, never on another: nothing to do with the duo
    games = [_ban_game(i, "P1", True, True) for i in range(18)] + [_ban_game(i, "P1", False, False) for i in range(2)]
    games += [_ban_game(i, "P2", False, False) for i in range(20)]
    assert _presence(games)["countered"] == 0.0


def test_pair_presence_leaves_out_fearless_locked_games_and_other_roles():
    games = [_ban_game(i, "P1", i % 2 == 0, i % 2 == 1) for i in range(10)]
    games.append({**_ban_game(99, "P1", False, True), "locked": ["Nocturne"]})
    row = _presence(games)
    assert row["bans"]["either"] == 10  # the locked game is not counted
    # Bans do not depend on roles, but `games` (played together) does: rows of one duo keep their own
    from app import stats

    top = {"a": {"champion": "Nocturne", "role": "top"}, "b": {"champion": "Orianna", "role": "mid"}, "games": 2}
    jungle = {"a": {"champion": "Nocturne", "role": "jungle"}, "b": {"champion": "Orianna", "role": "mid"}, "games": 6}
    stats.pair_presence(_FakeDataset(games), Filters(), [top, jungle])
    assert top["countered"] == jungle["countered"] and top["presence"] < jungle["presence"]


def test_pair_presence_with_a_team_filter_only_counts_bans_against_that_team():
    # Blue (team A) bans Nocturne, red (team B) bans Orianna: against team A only the Orianna bans count
    games = [_ban_game(i, "P1", i % 2 == 0, i % 2 == 1) for i in range(40)]
    row = _presence(games, teams=("A",))
    assert row["bans"]["either"] == 20  # games where red banned Orianna
    assert row["countered"] == 0.0  # 20 observed, 20 expected from Orianna's own ban rate


def test_level_picks_the_strictest_matching_level():
    base = {"lift": 1.5, "count": 10}
    assert denial._level({**base, "z": 4.0}) == "strong"
    assert denial._level({**base, "z": 2.5}) == "likely"
    assert denial._level({**base, "z": 1.7}) == "hint"
    assert denial._level({**base, "z": 1.0}) is None
    assert denial._level({"lift": 1.5, "count": 6, "z": 4.0}) == "likely"  # too few events for "strong"
    assert denial._level({"lift": 1.1, "count": 50, "z": 5.0}) is None  # effect too small
