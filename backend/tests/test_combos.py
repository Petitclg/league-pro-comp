from app import stats
from app.combos import ComboIndex
from app.dataset import Filters, _comps_of, load
from app.suggest import Params, suggest

from .synth import build_db

ROLES = ["top", "jungle", "mid", "bot", "support"]


def _game(gid, side1, side2, bans1=(), bans2=(), locked=(), winner=1):
    """side1 / side2: {role: (champion, slot)}; bans: [(champion, order)]."""
    def side(picks, bans, win):
        return {"team": "T", "win": win, "stats": {}, "bans": [c for c, _ in bans], "ban_order": [o for _, o in bans],
                "picks": [{"role": r, "champion": c, "slot": s, "order": 1, "player": None} for r, (c, s) in picks.items()]}
    return {"game_id": gid, "game_number": 1, "date": "2025-01-01", "patch": "25.1", "tournament": "X", "duration": 30,
            "fearless": bool(locked), "locked": list(locked), "winner": winner,
            "sides": {1: side(side1, bans1, winner == 1), 2: side(side2, bans2, winner == 2)}}


def _fill(picks, first_free_slot):
    """Complete a side with filler champions picked after the duo."""
    out = dict(picks)
    slot = first_free_slot
    for r in ROLES:
        if r not in out:
            out[r] = (f"F{r}{len(picks)}{slot}", slot)
            slot += 1
    return out


def _index(games):
    comps = [c for g in games for c in _comps_of(g)]
    return ComboIndex({g["game_id"]: g for g in games}, comps)


A, B = ("top", "Aatrox"), ("mid", "Azir")


def _scenario():
    # side 1 picks Aatrox first (slot 1 = time 7); where is Azir when side 1 fills its mid role?
    return [
        _game("realised", _fill({"top": ("Aatrox", 1), "mid": ("Azir", 4)}, 5), _fill({}, 2)),
        _game("taken", _fill({"top": ("Aatrox", 1)}, 4), _fill({"mid": ("Azir", 2)}, 3)),  # countered before mid is picked
        _game("banned", _fill({"top": ("Aatrox", 1)}, 7), _fill({}, 2), bans2=[("Azir", 8)]),  # banned before mid is picked
        _game("unused", _fill({"top": ("Aatrox", 1)}, 4), _fill({}, 2)),
        _game("banned first", _fill({"top": ("Aatrox", 1)}, 4), _fill({}, 2), bans2=[("Azir", 1)]),
        _game("taken after", _fill({"top": ("Aatrox", 4)}, 5), _fill({"mid": ("Azir", 2)}, 3)),
        _game("locked", _fill({"top": ("Aatrox", 1)}, 4), _fill({}, 2), locked=["Azir"]),
        _game("role filled", _fill({"top": ("Aatrox", 3), "mid": ("Zed", 1)}, 4), _fill({}, 2)),
        _game("banned after", _fill({"top": ("Aatrox", 1)}, 4), _fill({}, 2), bans2=[("Azir", 10)]),
    ]


def test_open_duo_excludes_what_the_opponent_countered_before_the_team_could_pick():
    s = _index(_scenario()).stats(A, B)
    # open = realised + unused. "taken after" counts for side 2: it filled top (slot 3) while Aatrox was
    # still there. "banned after": Azir was banned once side 1 had picked something else for mid.
    assert (s["open"], s["realised"], s["unused"]) == (4, 1, 3)
    assert (s["taken"], s["banned"]) == (1, 1)  # countered: not part of the open cases
    assert s["rate"] == 0.25 and s["wins"] == 1 and s["decided"] == 1
    assert _index(_scenario()).stats(B, A) == s  # the order of the two slots does not matter


def test_opportunities_are_symmetric_when_the_other_member_comes_first():
    games = [_game("azir first", _fill({"mid": ("Azir", 1)}, 3), _fill({}, 2))]
    s = _index(games).stats(A, B)
    assert (s["open"], s["realised"], s["unused"]) == (1, 0, 1)


def test_limit_keeps_the_earliest_comps_only():
    idx = _index(_scenario())
    first_two = idx.stats(A, B, limit=4)  # comps are two per game: the first two games
    assert (first_two["open"], first_two["realised"], first_two["taken"]) == (1, 1, 1)


def test_duo_bonus_favours_a_duo_taken_whenever_open_and_winning():
    games = []
    for i in range(12):  # Azir always follows Aatrox, and wins
        games.append(_game(f"good{i}", _fill({"top": ("Aatrox", 1), "mid": ("Azir", 4)}, 5), _fill({}, 2)))
    for i in range(12):  # Ahri is open next to Aatrox and never taken
        games.append(_game(f"idle{i}", _fill({"top": ("Aatrox", 1), "mid": ("Ahri", 4)}, 5), _fill({}, 2), winner=2))
    idx = _index(games)
    comps = idx.comps
    plain = suggest(comps, {"top": "Aatrox"}, params=Params(), details=False)
    boosted = suggest(comps, {"top": "Aatrox"}, params=Params(combo_strength=2, pair_wr_strength=2), details=False,
                      combos=idx.stats)
    order = lambda r: [s["champion"] for s in r["suggestions"]["mid"]]
    assert set(order(plain)) >= {"Azir", "Ahri"}
    assert order(boosted)[0] == "Azir"
    scores = {s["champion"]: s["score"] for s in boosted["suggestions"]["mid"]}
    base = {s["champion"]: s["score"] for s in plain["suggestions"]["mid"]}
    assert scores["Azir"] / base["Azir"] > 1 > scores["Ahri"] / base["Ahri"] or scores["Azir"] / base["Azir"] > scores["Ahri"] / base["Ahri"]


def test_suggestions_report_how_often_a_duo_is_played_when_open():
    games = [_game(f"g{i}", _fill({"top": ("Aatrox", 1), "mid": ("Azir", 4)}, 5), _fill({}, 2)) for i in range(6)]
    idx = _index(games)
    result = suggest(idx.comps, {"top": "Aatrox"}, params=Params(), combos=idx.stats)
    azir = next(s for s in result["suggestions"]["mid"] if s["champion"] == "Azir")
    assert azir["synergies"][0]["open"] == {"open": 6, "realised": 6, "taken": 0, "banned": 0, "unused": 0, "rate": 1.0}


def test_pair_rows_carry_open_stats_consistent_with_games_played():
    ds = load(build_db(60))
    f = Filters()
    rows = stats.pair_stats(ds.filter_comps(f), 3, limit=40)
    stats.pair_openness(ds, f, rows)
    assert rows
    for r in rows:
        o = r["open"]
        assert o["realised"] == r["games"] or o["open"] < r["games"]  # games locked by Fearless are left out
        assert o["realised"] <= o["open"] and o["open"] == o["realised"] + o["unused"]
