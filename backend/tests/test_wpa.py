import hashlib
import json
import math
import os
import random
import shutil
import threading
from collections import OrderedDict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dataset, main, soloq, wpa
from tools import train_wpa

from .riot_fake import CHAMPIONS, make_match, make_timeline

FIXTURE = Path(__file__).parent / "fixtures" / "wpa_model.json"  # train_wpa.py --synthetic 400 --trees 8 --depth 2 --seed 1


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def model_file(**changes) -> dict:
    """W = sigmoid(sum of leaves): kills_diff gives -1 / 0 / +1, towers_diff +0.5 when Blue leads."""
    return {"format": "lpc-wpa-gbt", "version": 1, "feature_version": 1, "features": ["kills_diff", "towers_diff"],
            "init": 0.0, "learning_rate": 1.0, "source": "synthetic",
            "trees": [{"f": 0, "t": -0.5, "l": {"v": -1.0}, "r": {"f": 0, "t": 0.5, "l": {"v": 0.0}, "r": {"v": 1.0}}},
                      {"f": 1, "t": 0.5, "l": {"v": 0.0}, "r": {"v": 0.5}}],
            **changes}


# The illustrative model of example A (docs/requirements.md): init 0, learning rate 0.1, 3 trees
EXAMPLE_MODEL = model_file(features=["gold_diff", "towers_diff", "dragons_diff", "minute", "kills_diff"],
                           learning_rate=0.1, trees=[
    {"f": 0, "t": 1000.5, "l": {"v": -0.2}, "r": {"v": 2.0}},
    {"f": 1, "t": 0.5, "l": {"v": -0.5}, "r": {"f": 2, "t": 0.5, "l": {"v": 0.5}, "r": {"v": 1.5}}},
    {"f": 3, "t": 9.5, "l": {"v": 0.0}, "r": {"f": 4, "t": -0.5, "l": {"v": -1.0}, "r": {"v": 1.0}}}])
EXAMPLE_VECTOR = [14, 1440, 1350, 2, 14, 2500, 3, -3, 5, 1, 0, 2, 1, 0, 3, 0, 0, 2, 1, 1]


_mtime = [1_700_000_000 * 10**9]


def touch(path: Path) -> None:
    """A distinct modification time: quick successive writes of the same size must look changed."""
    _mtime[0] += 10**9
    os.utime(path, ns=(_mtime[0], _mtime[0]))


def write_model(path: Path, data: dict) -> str:
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    touch(path)
    return hashlib.sha256(raw).hexdigest()


def install(path: Path, source: Path = FIXTURE) -> None:
    shutil.copy(source, path)
    touch(path)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """soloq.db, the model file, the background job and every cache in a temporary folder."""
    monkeypatch.setattr(soloq, "SOLOQ_DB_PATH", tmp_path / "soloq.db")
    monkeypatch.setattr(wpa, "MODEL_PATH", tmp_path / "wpa_model.json")
    monkeypatch.setattr(wpa, "_loaded", {"key": None, "id": None, "model": None, "error": None})
    monkeypatch.setattr(wpa, "_agg_cache", OrderedDict())
    monkeypatch.setattr(wpa, "job", wpa.CacheJob())
    monkeypatch.setattr(dataset, "_cache", OrderedDict())
    monkeypatch.setattr(dataset, "_loaded_at", {})
    monkeypatch.setattr(main, "league_summary", lambda: [])
    yield tmp_path
    wpa.job.stop()


def fill(path, n=12, start=0):
    """`n` fake games of 20:30 with a 20-minute timeline; game i is Emerald, Diamond or Master in turn."""
    conn = soloq.connect(path)
    tiers = ["EMERALD", "DIAMOND", "MASTER"]
    for i in range(start, start + n):
        match_id = f"EUW1_{i}"
        game = soloq.extract_match(make_match(match_id, duration=1230, winner=100 if i % 2 else 200), "euw1", CHAMPIONS)
        soloq.save_game(conn, game, tiers[i % 3], soloq.extract_timeline(make_timeline(match_id, 20)))
    conn.commit()
    return conn


def add_truncated(conn, match_id="EUW1_T"):
    """A 30-minute game whose timeline stops at 20 minutes: unusable."""
    game = soloq.extract_match(make_match(match_id, duration=1800), "euw1", CHAMPIONS)
    soloq.save_game(conn, game, "DIAMOND", soloq.extract_timeline(make_timeline(match_id, 20)))
    conn.commit()


def frame(blue: dict | None = None, red: dict | None = None) -> dict:
    """A minute of frames where participant 1 (Blue) and 6 (Red) hold the team totals."""
    out = {pid: dict.fromkeys(soloq.FRAME_FIELDS, 0) for pid in range(1, 11)}
    out[1].update(blue or {})
    out[6].update(red or {})
    return out


def kill(t, killer, victim, assists=0):
    team = range(1, 6) if killer <= 5 else range(6, 11)
    return {"t": t, "killer": killer, "victim": victim, "assists": [p for p in team if p != killer][:assists]}


def example_a_timeline() -> dict:
    """Example A: Diamond game at 14:05 (845 s), one Blue kill exactly at 845 s."""
    blue = {"gold": 24310, "xp": 26950, "level": 47, "cs": 512, "damage_dealt": 18400}
    red = {"gold": 22870, "xp": 25600, "level": 45, "cs": 498, "damage_dealt": 15900}
    kills = [kill(100 + 60 * i, 1 + i % 5, 6 + i % 5, a) for i, a in enumerate([2, 2, 2, 2, 1, 1])]
    kills += [kill(150 + 60 * i, 6 + i, 1 + i, a) for i, a in enumerate([2, 2, 1, 1])]
    kills.append(kill(845, 3, 8, 1))  # Blue: 7 kills, 11 assists; Red: 4 kills, 6 assists
    objectives = [{"t": 600, "kind": "tower", "side": 1, "detail": "top"},
                  *({"t": 300 + i, "kind": "plate", "side": 1, "detail": "mid"} for i in range(3)),
                  {"t": 320, "kind": "plate", "side": 2, "detail": "bot"},
                  {"t": 400, "kind": "dragon", "side": 1, "detail": "FIRE"},
                  {"t": 700, "kind": "dragon", "side": 1, "detail": "EARTH"},
                  {"t": 550, "kind": "dragon", "side": 2, "detail": "WATER"},
                  *({"t": 422, "kind": "grub", "side": 1, "detail": None} for _ in range(3)),
                  {"t": 500, "kind": "herald", "side": None, "detail": None}]  # no team: not counted
    frames = [frame() for _ in range(14)] + [frame(blue, red)]
    return {"frames": frames, "kills": kills, "objectives": objectives, "items": []}


# --- state and model (REQ-WPA-1, 4, 13) ----------------------------------------------------------


def test_state_features_follow_frames_and_objectives():
    timeline = example_a_timeline()
    assert wpa.features(wpa.state_at(timeline, 845, "DIAMOND")) == EXAMPLE_VECTOR
    before = dict(zip(wpa.FEATURES, wpa.features(wpa.state_at(timeline, 844, "DIAMOND"))))
    assert before["kills_diff"] == 2 and before["deaths_diff"] == -2 and before["assists_diff"] == 4
    late = dict(zip(wpa.FEATURES, wpa.features(wpa.state_at(timeline, 4000, "CHALLENGER"))))
    assert late["minute"] == 60 and late["gold_diff"] == 1440 and late["rank"] == 4  # last frame past the end
    executed = {**timeline, "kills": [kill(30, 0, 2)], "objectives": []}
    executed.pop("_events", None)
    x = dict(zip(wpa.FEATURES, wpa.features(wpa.state_at(executed, 31, None))))
    assert (x["kills_diff"], x["deaths_diff"], x["rank"]) == (0, 1, 0)
    assert len(wpa.FEATURES) == 20 and not {"item", "champion", "role", "player"} & set(wpa.FEATURES)


def test_evaluator_matches_hand_computed_model(tmp_path):
    model = wpa.parse_model(EXAMPLE_MODEL)
    assert model.predict(EXAMPLE_VECTOR) == pytest.approx(sigmoid(0.45), abs=1e-12)
    assert round(model.predict(EXAMPLE_VECTOR), 6) == 0.610639
    model = wpa.parse_model(model_file(init=0.25, learning_rate=0.5))
    x = dict.fromkeys(wpa.FEATURES, 0)
    x.update(kills_diff=-1)
    assert model.predict([x[f] for f in wpa.FEATURES]) == pytest.approx(sigmoid(0.25 - 0.5), abs=1e-15)

    path = tmp_path / "wpa_model.json"
    assert wpa.load_model(path) == (None, f"Aucun modèle de probabilité de victoire (fichier introuvable : {path}).")
    ident = write_model(path, model_file())
    loaded, error = wpa.load_model(path)
    assert error is None and loaded.id == ident and loaded.key == f"{ident}:{wpa.WPA_REVISION}"
    assert wpa.load_model(path)[0] is loaded
    ident2 = write_model(path, model_file(init=1.0, trained_at="2026-11-18"))
    assert wpa.load_model(path)[0].id == ident2 != ident  # reloaded without restart
    invalid = [
        ({**model_file(), "format": "onnx"}, "format inconnu (attendu : lpc-wpa-gbt)"),
        (model_file(version=2), "version 2 non prise en charge (attendue : 1)"),
        (model_file(feature_version=0),
         "modèle entraîné avec une autre définition des variables (v0, attendue v1) : réentraînez-le"),
        (model_file(features=["kills_diff", "item_bought", "hero"]), "variables inconnues : item_bought, hero"),
        (model_file(trees=[{"f": 5, "t": 0, "l": {"v": 0}, "r": {"v": 1}}]), "arbre n°1 mal formé : indice de variable invalide"),
        (model_file(trees=[{"v": 0}, {"f": 0, "t": 0, "l": {"v": 0}}]), "arbre n°2 mal formé : nœud sans ses deux branches"),
        (model_file(trees=[{"f": 0, "t": "x", "l": {"v": 0}, "r": {"v": 1}}]), "arbre n°1 mal formé : seuil invalide"),
        (model_file(source="pro"), "source inconnue"),
    ]
    for data, reason in invalid:
        write_model(path, data)
        model, error = wpa.load_model(path)
        assert model is None and error == f"Modèle de probabilité de victoire invalide ({reason}) : {path}.", error
    path.write_bytes(b'{"format": "lpc-wpa-gbt", "trees": [')  # truncated
    assert wpa.load_model(path) == (None, f"Modèle de probabilité de victoire illisible (JSON invalide) : {path}.")
    path.write_bytes(b" " * (wpa.MAX_MODEL_BYTES + 1))
    assert wpa.load_model(path)[1] == f"Modèle de probabilité de victoire invalide (fichier de plus de 5 Mo) : {path}."


def test_fixture_model_is_tiny_and_valid():
    assert FIXTURE.stat().st_size < 5000
    model = wpa.parse_model(json.loads(FIXTURE.read_text()))
    assert model.data["source"] == "synthetic" and model.data["metrics"]["export_gap"] < 1e-9


# --- training (REQ-WPA-2, 3) -------------------------------------------------------------------------


def test_sampling_draws_one_state_per_five_minute_bucket(tmp_path):
    rng = random.Random(3)
    times = train_wpa.sample_times(1830, rng)
    assert len(times) == 6 and all(300 * k <= t < 300 * k + 300 for k, t in enumerate(times))
    assert len(train_wpa.sample_times(1920, rng)) == 7 and train_wpa.sample_times(1920, rng)[6] >= 1800
    assert len(train_wpa.sample_times(1500, rng)) == 5
    assert len(train_wpa.sample_times(330, rng)) == 1 and train_wpa.sample_times(299, rng) == []
    assert len({tuple(train_wpa.sample_times(1830, random.Random(s))) for s in range(20)}) > 15

    games = [{"match_id": f"G{i}"} for i in range(100)]
    train, test = train_wpa.split(games, random.Random(0))
    assert len(train) == 80 and len(test) == 20 and not {g["match_id"] for g in train} & {g["match_id"] for g in test}
    assert train_wpa.split(games, random.Random(0)) == (train, test)

    conn = fill(tmp_path / "s.db", 10)
    add_truncated(conn)
    soloq.save_game(conn, soloq.extract_match(make_match("EUW1_NT"), "euw1", CHAMPIONS), "MASTER", None)
    conn.commit()
    loaded = train_wpa.load_games(conn)
    assert [g["match_id"] for g in loaded] == sorted(f"EUW1_{i}" for i in range(10))  # truncated / no timeline: out
    X, y = train_wpa.samples(conn, loaded, random.Random(0))
    assert len(X) == len(y) == 10 * 4  # 20:30: 4 full buckets, the last 30 s are not sampled
    assert all(len(x) == len(wpa.FEATURES) for x in X)
    assert y == [int(g["winner"] == 1) for g in loaded for _ in range(4)]


def test_training_script_smoke(tmp_path):
    pytest.importorskip("sklearn")
    out = tmp_path / "model.json"
    assert train_wpa.main(["--synthetic", "150", "--trees", "5", "--depth", "2", "--out", str(out)]) == 0
    model, error = wpa.load_model(out)
    assert error is None and model.data["source"] == "synthetic" and model.data["games"] == 150
    metrics = model.data["metrics"]
    assert {"accuracy", "log_loss", "brier", "ece", "reliability", "baseline_log_loss", "export_gap"} <= metrics.keys()
    assert metrics["export_gap"] < 1e-9 and sum(b["n"] for b in metrics["reliability"]) == metrics["n"]
    assert model.data["feature_version"] == 1 and 0 <= model.data["minute_coverage"] <= 60
    assert train_wpa.minute_coverage([[10]] * 100 + [[20]] * 99 + [[5]] * 150) == 10
    assert wpa.quality(model.data) == ("provisional", "entraîné sur des données synthétiques")
    again = tmp_path / "again.json"
    train_wpa.main(["--synthetic", "150", "--trees", "5", "--depth", "2", "--out", str(again)])
    assert json.loads(again.read_text())["trees"] == model.data["trees"]  # same seed, same trees

    db = tmp_path / "soloq.db"
    fill(db, 30).close()
    assert train_wpa.main(["--db", str(db), "--trees", "3", "--min-leaf", "5", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["source"] == "soloq"
    small = tmp_path / "small.db"
    fill(small, 5).close()
    with pytest.raises(SystemExit, match=r"^Not enough games with a timeline: 5 \(at least 20\)\.$"):
        train_wpa.main(["--db", str(small), "--out", str(tmp_path / "none.json")])
    assert not (tmp_path / "none.json").exists()


def test_calibration_metrics():
    m = wpa.calibration([0.9, 0.8, 0.3, 0.1], [1, 0, 0, 0])
    assert (m["accuracy"], m["brier"], m["log_loss"], m["ece"]) == (0.75, 0.1875, 0.5442, 0.325)
    assert [b["n"] for b in m["reliability"]] == [1, 1, 1, 1]
    assert m["reliability"][-1] == {"lo": 0.9, "hi": 1.0, "n": 1, "predicted": 0.9, "observed": 1.0}


# --- WPA of actions (REQ-WPA-7, 8) ---------------------------------------------------------------


def test_item_wpa_follows_purchase_to_sale_or_end():
    def ev(t, pid, action, item, after=None):
        return {"t": t, "participant": pid, "action": action, "item": item, **({"after": after} if after is not None else {})}

    items = [
        ev(10, 1, "P", 1055), ev(400, 1, "S", 1055),                            # sold
        ev(310, 1, "P", 1036), ev(312, 1, "U", 1036, 0), ev(340, 1, "P", 1036),  # bought, undone, bought again
        ev(20, 2, "P", 1055), ev(300, 2, "S", 1055), ev(302, 2, "U", 0, 1055),  # undone sale: open to the end
        ev(100, 3, "P", 1036), ev(110, 3, "P", 1036),
        ev(600, 3, "D", 1036), ev(600, 3, "D", 1036), ev(600, 3, "P", 3031), ev(605, 3, "U", 3031, 0),
        ev(900, 3, "D", 1036),                                                  # ends the oldest open one
        ev(50, 6, "P", 2003), ev(200, 6, "D", 2003), ev(250, 6, "S", 2003),     # selling nothing open: ignored
        ev(60, 0, "P", 2003),                                                   # no participant: ignored
    ]
    assert sorted(wpa.item_spans(items, 1800)) == [
        (10, 1, 1055, 400), (20, 2, 1055, 1800), (50, 6, 2003, 200), (100, 3, 1036, 900), (110, 3, 1036, 1800),
        (340, 1, 1036, 1800)]

    model = wpa.parse_model(model_file(features=["gold_diff"], trees=[
        {"f": 0, "t": 0, "l": {"v": -1.0}, "r": {"v": 1.0}}]))  # W = sigmoid(±1) whether Blue leads in gold
    frames = [frame({"gold": 10 * m}, {"gold": 0 if m < 5 else 1000}) for m in range(31)]
    result = wpa.game_wpa(model, {"frames": frames, "kills": [], "objectives": [], "items": items}, None, 1800)
    spans = {(t, pid, item): (end, w, dw) for t, pid, item, end, w, dw in result["items"]}
    up, down = sigmoid(1), sigmoid(-1)
    assert spans[(10, 1, 1055)] == (400, pytest.approx(down, abs=1e-6), pytest.approx(0, abs=1e-6))
    # Blue buyer at 1:40 (Blue leads: W 0.73) until 15:00 (Red leads: W 0.27): ΔW -0.46 for Blue
    assert spans[(100, 3, 1036)] == (900, pytest.approx(up, abs=1e-6), pytest.approx(down - up, abs=1e-6))
    # Red buyer at 0:50 (even gold: W Blue 0.27, its own W 0.73) until 3:20 (Blue leads: W Blue 0.73): ΔW -0.46
    assert spans[(50, 6, 2003)][1:] == (pytest.approx(1 - down, abs=1e-6), pytest.approx(down - up, abs=1e-6))
    # Red buyer while Blue's W drops from 0.73 to 0.27: ΔW +0.46 for Red
    red = wpa.game_wpa(model, {"frames": frames, "kills": [], "objectives": [], "items": [ev(70, 7, "P", 3190)]}, None, 1800)
    assert red["items"][0][4:] == [pytest.approx(1 - up, abs=1e-6), pytest.approx(up - down, abs=1e-6)]
    assert len(result["minutes"]) == 31 and result["minutes"][1] == pytest.approx(up, abs=1e-6)


def test_event_wpa_and_champion_aggregates():
    # example D: Red takes a tower in state A
    timeline = example_a_timeline()
    timeline["objectives"].append({"t": 845, "kind": "tower", "side": 2, "detail": "mid"})
    result = wpa.game_wpa(wpa.parse_model(EXAMPLE_MODEL), timeline, "DIAMOND", 870)
    tower = next(e for e in result["events"] if e[:2] == [845, "tower"])
    assert tower[2] == 2 and tower[6] == 0.562177 and tower[7] == 0.048463
    at_845 = [e for e in result["events"] if e[0] == 845]
    assert [e[1] for e in at_845] == ["kill", "tower"]  # kills before objectives at the same second
    model = wpa.parse_model(EXAMPLE_MODEL)
    start = model.win_probability(wpa.state_at(timeline, 845, "DIAMOND", before=(845, -1, 0)))
    end = model.win_probability(wpa.state_at(timeline, 845, "DIAMOND"))
    assert sum(e[7] if e[2] == 1 else -e[7] for e in at_845) == pytest.approx(end - start, abs=1e-5)  # additive
    assert [e[0] for e in result["events"]] == sorted(e[0] for e in result["events"])

    model = wpa.parse_model(model_file())
    timeline = {"frames": [frame() for _ in range(6)], "items": [],
                "kills": [kill(100, 1, 6, 1), kill(130, 7, 2), kill(150, 0, 3)],  # the last one is an execution
                "objectives": [{"t": 200, "kind": "tower", "side": 1, "detail": "top"},
                               {"t": 210, "kind": "dragon", "side": 2, "detail": "FIRE"}]}
    players = {pid: (wpa.side_of(pid), soloq.ROLES[(pid - 1) % 5], f"Champ{pid}", f"P{pid}#EUW") for pid in range(1, 11)}
    result = wpa.game_wpa(model, timeline, "EMERALD", 330, players)
    kill_dw = round(sigmoid(1) - 0.5, 6)
    events = {(e[0], e[1]): e for e in result["events"]}
    assert events[(100, "kill")][2:] == [1, None, 1, 6, round(sigmoid(1), 6), kill_dw]
    assert events[(130, "kill")][2] == 2 and events[(130, "kill")][7] == kill_dw  # Red's point of view
    assert events[(150, "kill")][2:6] == [2, None, 0, 3] and events[(150, "kill")][7] == 0
    assert events[(200, "tower")][7] == round(sigmoid(0.5) - 0.5, 6)
    assert events[(210, "dragon")][6:] == [round(sigmoid(0.5), 6), 0]

    games = {"G1": wpa.summarise(result), "G2": wpa.summarise(result)}
    table = wpa.events_table(games)
    assert table["kills"] == {"n": 6, "dw": round(2 * kill_dw / 3, 4)}
    assert table["objectives"] == [{"kind": "tower", "n": 2, "dw": round(sigmoid(0.5) - 0.5, 4)},
                                   {"kind": "dragon", "n": 2, "dw": 0.0}]
    rows = {r["champion"]: r for r in wpa.players_table(games, min_games=2)}
    assert rows["Champ1"] == {"champion": "Champ1", "games": 2, "wpa": round(kill_dw, 4),
                              "kills_wpa": round(kill_dw, 4), "deaths_wpa": 0.0}
    assert rows["Champ6"]["deaths_wpa"] == -round(kill_dw, 4) and rows["Champ3"]["wpa"] == 0
    assert len(rows) == 10 and next(iter(rows)) in ("Champ1", "Champ7")  # by WPA
    assert wpa.players_table(games, min_games=3) == []
    jungle = wpa.players_table(games, by="player", role="jungle", min_games=1)
    assert {r["player"] for r in jungle} == {"P2#EUW", "P7#EUW"}


# --- cache and routes (REQ-WPA-5, 6, 9, 10, 11, 12) -----------------------------------------------


def settled(client: TestClient, url: str) -> dict:
    """The answer of an aggregate route once the background job has computed every game."""
    client.get(url)
    wpa.job.join()
    return client.get(url).json()


def test_per_game_results_are_cached_per_model(isolated, monkeypatch):
    conn = fill(soloq.SOLOQ_DB_PATH, 6)
    model = wpa.parse_model(model_file(), "model-a")
    calls = []
    compute = wpa.compute_game
    monkeypatch.setattr(wpa, "compute_game", lambda c, m, mod: calls.append(m) or compute(c, m, mod))
    ids = [f"EUW1_{i}" for i in range(6)]
    first = wpa.results(conn, ids, model)
    assert len(first) == 6 and len(calls) == 6
    assert conn.execute("SELECT COUNT(*) FROM soloq_wpa WHERE model = ?", (model.key,)).fetchone()[0] == 6
    assert wpa.results(conn, ids, model) == first and len(calls) == 6  # read back, not recomputed
    other = wpa.parse_model(model_file(init=0.3), "model-b")
    assert len(wpa.results(conn, ids[:2], other)) == 2 and len(calls) == 8
    assert {r[0] for r in conn.execute("SELECT model FROM soloq_wpa")} == {"model-b:1"}  # old key dropped
    monkeypatch.setattr(wpa, "WPA_REVISION", 2)
    revised = wpa.parse_model(model_file(init=0.3), "model-b")
    assert revised.key == "model-b:2" and wpa.results(conn, ids[:1], revised) and len(calls) == 9
    conn.execute("DELETE FROM soloq_games WHERE match_id = 'EUW1_0'")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM soloq_wpa WHERE match_id = 'EUW1_0'").fetchone()[0] == 0
    conn.close()

    write_model(wpa.MODEL_PATH, model_file())
    client = TestClient(main.app)
    everything = settled(client, "/api/wpa/events")
    diamond = client.get("/api/wpa/events?rank=DIAMOND&league=LCK").json()  # `league` is ignored
    conn = soloq.connect()
    kills = {i: len(soloq.load_timeline(conn, f"EUW1_{i}")["kills"]) for i in range(1, 6)}
    conn.close()
    assert everything["coverage"] == {"computed": 5, "total": 5} and everything["kills"]["n"] == sum(kills.values())
    assert diamond["coverage"] == {"computed": 2, "total": 2} and diamond["kills"]["n"] == kills[1] + kills[4]
    assert client.get("/api/wpa/events?patch=99.1").json() == {
        "coverage": {"computed": 0, "total": 0}, "kills": {"n": 0, "dw": None}, "objectives": []}


def test_aggregates_report_coverage_while_computing(isolated, monkeypatch):
    conn = fill(soloq.SOLOQ_DB_PATH, 4)
    add_truncated(conn)
    conn.close()
    write_model(wpa.MODEL_PATH, model_file())
    gate = threading.Event()
    compute = wpa.compute_game
    monkeypatch.setattr(wpa, "compute_game", lambda *a: gate.wait(10) and compute(*a))
    client = TestClient(main.app)
    first = client.get("/api/wpa/champions?min_games=1").json()  # answers at once
    assert first == {"coverage": {"computed": 0, "total": 5}, "rows": []}
    assert wpa.job.running() and client.get("/api/wpa").json()["cache"]["running"]
    gate.set()
    wpa.job.join()
    done = client.get("/api/wpa/champions?min_games=1").json()
    assert done["coverage"] == {"computed": 4, "total": 4}  # the truncated game is not counted any more
    assert sum(r["games"] for r in done["rows"]) == 40
    assert client.get("/api/wpa").json()["cache"] == {"computed": 4, "total": 4, "running": False}


def test_no_model_disables_the_feature_gracefully(isolated):
    fill(soloq.SOLOQ_DB_PATH, 3).close()
    client = TestClient(main.app)
    status = client.get("/api/wpa").json()
    assert status["available"] is False and status["model"] is None and status["cache"] is None
    assert status["message"] == (f"Aucun modèle de probabilité de victoire (fichier introuvable : {wpa.MODEL_PATH})."
                                 " Pour en entraîner un : pip install -r backend/requirements-ml.txt"
                                 " puis python backend/tools/train_wpa.py")
    assert [w["key"] for w in status["warnings"]] == ["soloq", "selection"]
    for url in ("/api/wpa/game/EUW1_0", "/api/wpa/items", "/api/wpa/events", "/api/wpa/champions"):
        r = client.get(url)
        assert r.status_code == 503 and r.json()["detail"] == status["message"]
    without = [client.get(u).json() for u in ("/api/meta?league=SOLOQ", "/api/champions?league=SOLOQ")]
    wpa.MODEL_PATH.write_text('{"format": "lpc-wpa-gbt", "version": 99}')
    touch(wpa.MODEL_PATH)
    status = client.get("/api/wpa").json()
    assert status["message"].startswith(f"Modèle de probabilité de victoire invalide (version 99 non prise en charge"
                                        f" (attendue : 1)) : {wpa.MODEL_PATH}. Pour en entraîner un")
    assert client.get("/api/wpa/events").status_code == 503
    install(wpa.MODEL_PATH)
    assert client.get("/api/wpa").json()["available"]
    wpa.MODEL_PATH.write_text("{")  # replaced by a corrupt file: no fallback on the previous model
    touch(wpa.MODEL_PATH)
    assert "illisible (JSON invalide)" in client.get("/api/wpa/events").json()["detail"]
    install(wpa.MODEL_PATH)
    assert [client.get(u).json() for u in ("/api/meta?league=SOLOQ", "/api/champions?league=SOLOQ")] == without


def test_api_status_reports_model_and_warnings(isolated):
    install(wpa.MODEL_PATH)
    client = TestClient(main.app)
    status = client.get("/api/wpa").json()
    assert status["available"] and status["message"] is None
    assert (status["status"], status["status_reason"]) == ("provisional", "entraîné sur des données synthétiques")
    model = status["model"]
    assert model["id"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest() and model["source"] == "synthetic"
    assert model["games"] == 400 and model["samples"] > 400 and model["features"] == list(wpa.FEATURES)
    assert model["feature_version"] == 1 and model["minute_coverage"] is not None and model["params"]["trees"] == 8
    assert {"accuracy", "log_loss", "brier", "ece", "reliability", "baseline_log_loss"} <= model["metrics"].keys()
    assert [w["key"] for w in status["warnings"]] == ["soloq", "selection", "synthetic", "provisional"]
    assert status["warnings"][3]["text"] == ("Modèle non validé : entraîné sur des données synthétiques."
                                             " Les probabilités sont indicatives.")
    assert status["cache"] == {"computed": 0, "total": 0, "running": False}  # no soloq.db

    real = model_file(source="soloq", games=60000, metrics={"accuracy": 0.712, "ece": 0.021}, trained_at="2026-11-18")
    write_model(wpa.MODEL_PATH, real)
    status = client.get("/api/wpa").json()
    assert (status["status"], status["status_reason"]) == ("validated", None)
    assert [w["key"] for w in status["warnings"]] == ["soloq", "selection"]
    cases = [({"metrics": {"accuracy": 0.712, "ece": 0.041}}, "erreur de calibration de 4,1 % (3 % au plus)"),
             ({"metrics": {"accuracy": 0.6804, "ece": 0.01}}, "accuracy de 68,0 % (70 % au moins)"),
             ({"games": 12400}, "entraîné sur 12 400 parties (50 000 au moins)")]
    for change, reason in cases:
        write_model(wpa.MODEL_PATH, {**real, **change})
        status = client.get("/api/wpa").json()
        assert (status["status"], status["status_reason"]) == ("provisional", reason)
        assert status["warnings"][-1]["text"] == f"Modèle non validé : {reason}. Les probabilités sont indicatives."


def test_api_game_curve(isolated):
    conn = fill(soloq.SOLOQ_DB_PATH, 3)
    add_truncated(conn)
    soloq.save_game(conn, soloq.extract_match(make_match("EUW1_99"), "euw1", CHAMPIONS), "MASTER", None)
    conn.commit()
    conn.close()
    install(wpa.MODEL_PATH)
    client = TestClient(main.app)
    curve = client.get("/api/wpa/game/EUW1_1").json()
    assert (curve["winner"], curve["duration"], curve["tier"], curve["patch"]) == (1, 20.5, "DIAMOND", "15.22")
    assert len(curve["minutes"]) == 21 and all(0 < w < 1 for w in curve["minutes"])
    events = curve["events"]
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)
    assert "kill" in {e["kind"] for e in events} and {"dragon", "grub", "tower", "plate", "herald"} <= {e["kind"] for e in events}
    killed = next(e for e in events if e["kind"] == "kill")
    assert killed["champion"] in set(CHAMPIONS.values()) and killed["victim"] and killed["side"] in (1, 2)
    dragon = next(e for e in events if e["kind"] == "dragon")
    assert dragon == {**dragon, "side": 1, "detail": "FIRE", "champion": None, "victim": None}
    assert all(0 < e["w"] < 1 and -1 < e["dw"] < 1 for e in events)
    for game_id, detail in (("EUW1_99", "No usable timeline for EUW1_99"), ("EUW1_T", "No usable timeline for EUW1_T"),
                            ("NOPE_1", "Unknown solo queue game NOPE_1")):
        r = client.get(f"/api/wpa/game/{game_id}")
        assert r.status_code == 404 and r.json()["detail"] == detail


def test_api_items(isolated):
    fill(soloq.SOLOQ_DB_PATH, 9).close()
    install(wpa.MODEL_PATH)
    client = TestClient(main.app)
    body = settled(client, "/api/wpa/items?min_n=1&limit=1000")
    assert body["coverage"] == {"computed": 9, "total": 9}
    base = body["baseline"]
    assert base["n"] == sum(base[c]["n"] for c in wpa.CONTEXTS) >= sum(r["n"] for r in body["items"]) > 0
    assert len(body["items"]) == 1000  # random fake items: more distinct ones than the limit
    for row in body["items"]:
        assert row["n"] == sum(c["n"] for c in row["contexts"].values()) and 0 < row["w"] < 1
        for c, ctx in row["contexts"].items():
            if ctx["n"]:
                assert ctx["excess"] == pytest.approx(ctx["dw"] - base[c]["dw"], abs=2e-4)
    assert [r["n"] for r in body["items"]] == sorted((r["n"] for r in body["items"]), reverse=True)
    few = client.get("/api/wpa/items?min_n=3&limit=2").json()["items"]
    assert len(few) <= 2 and all(r["n"] >= 3 for r in few)
    assert client.get("/api/wpa/items?limit=5000").status_code == 422
    assert 0 < client.get("/api/wpa/items?min_n=1&rank=MASTER").json()["baseline"]["n"] < base["n"]

    def bought(item, w, dw, n):
        return [[0, 1, item, 60, w, dw]] * n

    games = {"G": wpa.summarise({"minutes": [], "events": [], "players": {}, "items": bought(1, 0.2, 0.05, 10) + bought(1, 0.8, -0.01, 10) + bought(2, 0.2, -0.02, 10)
                   + bought(2, 0.8, 0.04, 10) + bought(3, 0.5, 0.0, 9)})}
    table = wpa.items_table(games, min_n=1)
    assert table["baseline"]["behind"] == {"n": 20, "dw": 0.015} and table["baseline"]["ahead"] == {"n": 20, "dw": 0.015}
    rows = {r["item"]: r for r in table["items"]}
    assert rows[1]["profile"] == "comeback" and rows[2]["profile"] == "snowball"
    assert (rows[1]["contexts"]["behind"]["excess"], rows[1]["contexts"]["ahead"]["excess"]) == (0.035, -0.025)
    assert rows[3]["profile"] is None and rows[3]["contexts"]["ahead"] == {"n": 0, "dw": None, "excess": None}
    assert rows[1]["w"] == 0.5


def test_api_champions_routes(isolated):
    fill(soloq.SOLOQ_DB_PATH, 6).close()
    install(wpa.MODEL_PATH)
    client = TestClient(main.app)
    body = settled(client, "/api/wpa/champions?min_games=1")
    rows = body["rows"]
    assert body["coverage"] == {"computed": 6, "total": 6}
    assert all(r.keys() == {"champion", "games", "wpa", "kills_wpa", "deaths_wpa"} for r in rows)
    assert sum(r["games"] for r in rows) == 6 * 10
    assert [r["wpa"] for r in rows] == sorted((r["wpa"] for r in rows), reverse=True)
    players = client.get("/api/wpa/champions?by=player&role=mid&min_games=1&limit=3").json()["rows"]
    assert {r["player"] for r in players} == {"Player2#EUW", "Player7#EUW"}  # fake games reuse the Riot IDs
    assert all(r["games"] == 6 for r in players)
    assert len(client.get("/api/wpa/champions?min_games=1&limit=3").json()["rows"]) == 3
    assert client.get("/api/wpa/champions?by=team").json()["detail"] == "Unknown grouping team"
    assert client.get("/api/wpa/champions?role=adc").status_code == 422
    assert client.get("/api/wpa/champions?min_games=100").json()["rows"] == []
