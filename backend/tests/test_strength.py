import random

from app import stats
from app.dataset import Filters, load
from app.strength import champion_spots, global_spots, strength, wilson

from .synth import build_db


def _comp(side: int, game_number: int, win: bool, role: str = "mid", slot: int = 2, champ: str = "Azir"):
    player = {"player": "P", "slot": slot, "champion": champ}
    return {
        "side": side, "game_number": game_number, "win": win, "patch": "25.1", "team": "T1",
        "fearless": True, "picks": {role: champ}, "enemy": {role: "Ahri"},
        "players": {role: player},
    }, role


def test_wilson_interval_is_wider_for_small_samples():
    small, large = wilson(3, 3), wilson(60, 60)
    assert small[0] < large[0] < 1 and small[1] <= 1
    low, high = wilson(50, 100)
    assert low < 0.5 < high


def test_strength_rewards_presence_and_discounts_tiny_samples():
    contested = strength(picks=60, bans=100, wins=33, decided=60, n_games=300)
    perfect_but_rare = strength(picks=3, bans=0, wins=3, decided=3, n_games=300)
    assert contested["score"] > perfect_but_rare["score"]
    assert perfect_but_rare["wr_adjusted"] < 0.6  # 3-0 is shrunk toward 50%
    assert perfect_but_rare["tier"] is None and contested["tier"] in {"S", "A", "B", "C", "D"}
    solid = strength(picks=100, bans=0, wins=65, decided=100, n_games=300)
    assert solid["significant_edge"] and not perfect_but_rare["significant_edge"]


def test_champion_spots_finds_a_planted_edge():
    rng = random.Random(1)
    mine = []
    for i in range(80):
        side = 1 if i % 2 == 0 else 2
        win = rng.random() < (0.85 if side == 1 else 0.25)  # strong on blue only
        mine.append(_comp(side, 1 + i % 3, win))
    spots = champion_spots(mine)
    hot = {(s["dimension"], s["label"]) for s in spots["hot"]}
    cold = {(s["dimension"], s["label"]) for s in spots["cold"]}
    assert [d for d, _ in hot | cold if d == "Side"] == ["Side"]  # Blue up and Red down is one finding
    assert not [s for s in spots["hot"] + spots["cold"] if s["dimension"] in ("Face à", "Avec")]
    top = min(spots["hot"] + spots["cold"], key=lambda s: -abs(s["z"]))
    assert top["dimension"] == "Side" and abs(top["z"]) >= 1.28 and top["games"] >= 8


def test_champion_spots_ignores_noise_and_small_samples():
    rng = random.Random(2)
    mine = [_comp(1 + i % 2, 1, rng.random() < 0.5) for i in range(200)]
    assert not [s for s in champion_spots(mine)["hot"] if s["z"] >= 2.6]
    assert champion_spots(mine[:10]) == {"baseline": None, "hot": [], "cold": []}


def test_global_spots_and_api_fields():
    ds = load(build_db())
    comps = ds.filter_comps(Filters())
    spots = global_spots(comps, min_games=5, z_min=1.0)
    assert all(s["z"] >= 1.0 and s["games"] >= 5 for s in spots)
    assert spots == sorted(spots, key=lambda s: -s["z"])
    table = stats.champion_table(ds, Filters())
    assert all("strength" in r and 0 <= r["strength"]["score"] <= 100 for r in table)
    detail = stats.champion_detail(ds, "Azir", Filters())
    assert detail["strength"]["score"] >= 0 and set(detail["spots"]) == {"baseline", "hot", "cold"}
    ids = {s["id"] for s in stats.records(ds, Filters())}
    assert "strongest" in ids


def test_matchup_verdict_needs_a_real_edge_and_never_flags_both_ways():
    from app.strength import matchup_verdict

    assert matchup_verdict(3, 3)["verdict"] is None  # 3-0 happens 1 time in 8 by chance
    assert matchup_verdict(0, 3)["verdict"] is None
    assert matchup_verdict(8, 10)["verdict"] == "favorable" and matchup_verdict(2, 10)["verdict"] == "difficult"
    assert matchup_verdict(5, 10)["verdict"] is None
    assert matchup_verdict(10, 10)["level"] == "strong"
    for wins in range(0, 13):  # a record is favourable, difficult or inconclusive, and mirrored records mirror
        a, b = matchup_verdict(wins, 12), matchup_verdict(12 - wins, 12)
        assert a["z"] == -b["z"] and {a["verdict"], b["verdict"]} <= {None, "favorable", "difficult"}
        assert a["verdict"] != b["verdict"] or a["verdict"] is None
