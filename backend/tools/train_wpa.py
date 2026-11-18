"""Train the win probability model of the WPA feature and export it to wpa_model.json (dev tool).

    pip install -r backend/requirements-ml.txt
    python backend/tools/train_wpa.py                    # soloq.db of the data folder -> data folder
    python backend/tools/train_wpa.py --synthetic 2000   # generated games (no Riot data needed)

Games are split 80 / 20 by game (train / test). One state is drawn at random in each 5-minute
bucket of each game (correlated samples of the same game would overstate the data), labelled by
Blue's win, described by `app.wpa.state_at` / `app.wpa.features` exactly as the app does at
inference. A scikit-learn gradient boosting classifier is trained, evaluated on the test games
(accuracy, log loss, Brier, ECE, reliability curve, against a logistic regression baseline),
exported to JSON and checked: the JSON model evaluated by the app must reproduce the library's
probabilities (max gap < 1e-9), else nothing is written. See REQ-WPA-2, 3 and 13.

scikit-learn is imported only to train: this module never ships with the app.
"""
import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import soloq, wpa  # noqa: E402
from app.riot import TIERS  # noqa: E402

BUCKET = 300  # seconds
MIN_LAST_BUCKET = 60  # a shorter incomplete last bucket is not sampled
MIN_DURATION = 300  # remakes are not stored, but never sample a game under 5 minutes
TEST_SHARE = 0.2
MIN_GAMES = 20
MAX_EXPORT_GAP = 1e-9
COVERAGE_STATES = 100  # minute_coverage: the largest minute with at least this many training states


# --- samples --------------------------------------------------------------------------


def sample_times(duration: float, rng: random.Random) -> list[int]:
    """One random second in each 5-minute bucket of a game lasting `duration` seconds; the incomplete
    last bucket only when it lasts at least a minute; nothing for a game under 5 minutes."""
    duration = int(duration)
    if duration < MIN_DURATION:
        return []
    times = []
    for start in range(0, duration, BUCKET):
        end = min(start + BUCKET, duration)
        if end - start >= MIN_LAST_BUCKET:
            times.append(rng.randrange(start, end))
    return times


def load_games(conn: sqlite3.Connection) -> list[dict]:
    """Games of at least 5 minutes with a winner and a usable timeline (headers only)."""
    games = []
    for r in conn.execute(
            "SELECT g.match_id, g.tier, g.duration, g.winner, t.frames FROM soloq_games g"
            " JOIN soloq_timelines t ON t.match_id = g.match_id WHERE g.winner IN (1, 2) ORDER BY g.match_id"):
        duration = round((r["duration"] or 0) * 60)
        if duration >= MIN_DURATION and wpa.usable({"frames": soloq._unpack(r["frames"]) or []}, duration):
            games.append({"match_id": r["match_id"], "tier": r["tier"], "duration": duration, "winner": r["winner"]})
    return games


def split(games: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """80 / 20 train / test split by game."""
    shuffled = list(games)
    rng.shuffle(shuffled)
    n_test = max(1, round(len(shuffled) * TEST_SHARE))
    return shuffled[n_test:], shuffled[:n_test]


def samples(conn: sqlite3.Connection, games: list[dict], rng: random.Random,
            log=lambda _: None) -> tuple[list[list[float]], list[int]]:
    X, y = [], []
    for i, g in enumerate(games, 1):
        timeline = soloq.load_timeline(conn, g["match_id"])
        for t in sample_times(g["duration"], rng):
            X.append(wpa.features(wpa.state_at(timeline, t, g["tier"])))
            y.append(int(g["winner"] == 1))
        if i % 5000 == 0:
            log(f"  {i} / {len(games)} games sampled")
    return X, y


# --- synthetic games ---------------------------------------------------------------------


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def synthetic_game(i: int, rng: random.Random) -> tuple[dict, dict, str]:
    """A Match-V5 match and timeline whose winner depends on a latent edge that also drives gold,
    XP, kills and objectives: a model can learn it, as from real games."""
    match_id = f"SYN1_{i}"
    duration = rng.randint(20, 38) * 60 + rng.randint(0, 59)
    edge = rng.gauss(0, 0.8)  # Blue's advantage, drifting over the game
    totals = {pid: {"gold": 500, "xp": 0, "cs": 0, "dmg": 0, "taken": 0} for pid in range(1, 11)}
    frames = []
    for m in range(duration // 60 + 2):
        ts = min(m * 60, duration) * 1000
        events = []
        if m:
            edge += rng.gauss(0, 0.15)
            for pid, tot in totals.items():
                sign = 1 if pid <= 5 else -1
                tot["gold"] += max(0, int(rng.gauss(380 + 45 * sign * edge, 60)))
                tot["xp"] += max(0, int(rng.gauss(420 + 35 * sign * edge, 50)))
                tot["cs"] += max(0, int(rng.gauss(6.5 + 0.4 * sign * edge, 1.5)))
                tot["dmg"] += max(0, int(rng.gauss(700 + 90 * sign * edge, 200)))
                tot["taken"] += max(0, int(rng.gauss(700 - 60 * sign * edge, 200)))
            for _ in range(rng.choice([0, 0, 1, 1, 2, 3]) if m >= 2 else 0):
                blue = rng.random() < _sigmoid(0.9 * edge)
                team, other = (range(1, 6), range(6, 11)) if blue else (range(6, 11), range(1, 6))
                killer = rng.choice(team)
                events.append({"type": "CHAMPION_KILL", "timestamp": ts - rng.randint(1, 59000), "killerId": killer,
                               "victimId": rng.choice(other),
                               "assistingParticipantIds": rng.sample([p for p in team if p != killer], rng.randint(0, 3))})

            def take(kind: dict, prob: float = 1.0) -> None:
                if rng.random() < prob:
                    blue = rng.random() < _sigmoid(0.8 * edge)
                    events.append({**kind, "timestamp": ts - rng.randint(1, 59000),
                                   "killerTeamId": 100 if blue else 200, "teamId": 200 if blue else 100})

            if m in (6, 12, 18, 24, 30, 36):
                take({"type": "ELITE_MONSTER_KILL", "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON"})
            if m == 7:
                for _ in range(3):
                    take({"type": "ELITE_MONSTER_KILL", "monsterType": "HORDE"})
            if m == 15:
                take({"type": "ELITE_MONSTER_KILL", "monsterType": "RIFTHERALD"})
            if m >= 21 and m % 7 == 0:
                take({"type": "ELITE_MONSTER_KILL", "monsterType": "BARON_NASHOR"}, 0.6)
            if 5 <= m <= 13:
                take({"type": "TURRET_PLATE_DESTROYED", "laneType": "MID_LANE"}, 0.5)
            if m >= 12:
                take({"type": "BUILDING_KILL", "buildingType": "TOWER_BUILDING", "laneType": "TOP_LANE"}, 0.35)
            if m >= 25:
                take({"type": "BUILDING_KILL", "buildingType": "INHIBITOR_BUILDING", "laneType": "BOT_LANE"}, 0.12)
            for pid in range(1, 11):
                if m == 1:
                    events.append({"type": "ITEM_PURCHASED", "timestamp": 1000, "participantId": pid, "itemId": 1055})
                if m == 9:
                    events.append({"type": "ITEM_PURCHASED", "timestamp": ts - 2000, "participantId": pid, "itemId": 1036})
                if m == 15:
                    events += [{"type": "ITEM_DESTROYED", "timestamp": ts - 3000, "participantId": pid, "itemId": 1036},
                               {"type": "ITEM_PURCHASED", "timestamp": ts - 3000, "participantId": pid,
                                "itemId": 3031 if pid % 2 else 3089}]
                if m == 22:
                    events.append({"type": "ITEM_SOLD", "timestamp": ts - 4000, "participantId": pid, "itemId": 1055})
        frames.append({"timestamp": ts, "events": events, "participantFrames": {str(pid): {
            "participantId": pid, "totalGold": t["gold"], "xp": t["xp"], "level": min(18, 1 + t["xp"] // 1000),
            "minionsKilled": t["cs"], "jungleMinionsKilled": 0,
            "damageStats": {"totalDamageDoneToChampions": t["dmg"], "totalDamageTaken": t["taken"]},
        } for pid, t in totals.items()}})
    winner = 100 if rng.random() < _sigmoid(2.0 * edge) else 200
    roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    participants = [{
        "participantId": pid, "teamId": 100 if pid <= 5 else 200, "puuid": f"syn-{i}-{pid}",
        "riotIdGameName": f"Synth{rng.randint(1, 300)}", "riotIdTagline": "SYN",
        "championId": 1 + (pid - 1) % 5 * 10 + rng.randint(0, 9), "teamPosition": roles[(pid - 1) % 5],
        "win": (100 if pid <= 5 else 200) == winner, "goldEarned": totals[pid]["gold"],
    } for pid in range(1, 11)]
    match = {"metadata": {"matchId": match_id}, "info": {
        "queueId": 420, "gameVersion": "15.22.1.1", "gameDuration": duration, "platformId": "SYN1",
        "gameStartTimestamp": 1_763_400_000_000 + i * 3_600_000, "gameEndTimestamp": 0,
        "participants": participants,
        "teams": [{"teamId": t, "win": t == winner, "bans": [], "objectives": {}} for t in (100, 200)]}}
    return match, {"metadata": {"matchId": match_id}, "info": {"frames": frames}}, rng.choice(TIERS)


def synthetic_db(n: int, seed: int = 0) -> sqlite3.Connection:
    """An in-memory soloq.db holding `n` synthetic games."""
    rng = random.Random(seed)
    conn = soloq.connect(":memory:")
    names = {cid: f"Champion{cid}" for cid in range(1, 60)}
    for i in range(n):
        match, timeline, tier = synthetic_game(i, rng)
        soloq.save_game(conn, soloq.extract_match(match, "euw1", names), tier, soloq.extract_timeline(timeline))
    conn.commit()
    return conn


# --- export --------------------------------------------------------------------------------


def _node(tree, i: int) -> dict:
    if tree.children_left[i] == -1:
        return {"v": float(tree.value[i][0][0])}
    return {"f": int(tree.feature[i]), "t": float(tree.threshold[i]),
            "l": _node(tree, int(tree.children_left[i])), "r": _node(tree, int(tree.children_right[i]))}


def _init_raw(clf, X) -> float:
    try:
        return float(clf._raw_predict_init(X[:1])[0, 0])
    except AttributeError:  # private API: fall back to the logit of the class prior, as scikit-learn does
        p = float(clf.init_.class_prior_[1])
        return math.log(p / (1 - p))


def export(clf, X) -> dict:
    return {"format": wpa.MODEL_FORMAT, "version": wpa.MODEL_VERSION, "feature_version": wpa.FEATURE_VERSION,
            "features": list(wpa.FEATURES), "init": _init_raw(clf, X), "learning_rate": float(clf.learning_rate),
            "trees": [_node(stage[0].tree_, 0) for stage in clf.estimators_]}


def minute_coverage(X: list[list[float]]) -> int:
    """Largest minute with at least COVERAGE_STATES training states."""
    counts = Counter(int(x[0]) for x in X)
    return max((m for m, n in counts.items() if n >= COVERAGE_STATES), default=0)


# --- main ------------------------------------------------------------------------------------


def train(conn: sqlite3.Connection, *, source: str, trees: int = 150, depth: int = 3, learning_rate: float = 0.1,
          min_leaf: int = 20, seed: int = 0, min_games: int = MIN_GAMES, log=print) -> dict:
    """Train, evaluate and export a model from the games of `conn`; returns the model file content."""
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rng = random.Random(seed)
    games = load_games(conn)
    if len(games) < min_games:
        raise SystemExit(f"Not enough games with a timeline: {len(games)} (at least {min_games}).")
    train_games, test_games = split(games, rng)
    log(f"Sampling {len(games)} games...")
    X_train, y_train = samples(conn, train_games, rng, log)
    X_test, y_test = samples(conn, test_games, rng, log)
    if len(set(y_train)) < 2:
        raise SystemExit("The training games all have the same winner.")
    log(f"{len(train_games)} train games ({len(X_train)} states), {len(test_games)} test games ({len(X_test)} states)")
    train_array, test_array = np.asarray(X_train, dtype=float), np.asarray(X_test, dtype=float)
    log(f"Training {trees} trees of depth {depth}...")
    clf = GradientBoostingClassifier(n_estimators=trees, max_depth=depth, learning_rate=learning_rate,
                                     min_samples_leaf=min_leaf, random_state=seed)
    clf.fit(train_array, y_train)
    baseline = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    baseline.fit(train_array, y_train)

    model = export(clf, train_array)
    model["source"] = source
    text = json.dumps(model, separators=(",", ":"))
    check = wpa.parse_model(json.loads(text), hashlib.sha256(text.encode()).hexdigest())
    X_all = X_train + X_test
    library = clf.predict_proba(np.asarray(X_all, dtype=float))[:, 1]
    gap = max(abs(check.predict(x) - float(p)) for x, p in zip(X_all, library))
    if gap >= MAX_EXPORT_GAP:
        raise SystemExit(f"The JSON export does not reproduce scikit-learn (max gap {gap:.3g}): nothing written.")

    metrics = wpa.calibration([float(p) for p in library[len(X_train):]], y_test)
    baseline_log_loss = wpa.calibration([float(p) for p in baseline.predict_proba(test_array)[:, 1]], y_test)["log_loss"]
    metrics.update(baseline_log_loss=baseline_log_loss, export_gap=gap, test_games=len(test_games))
    model.update(trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 games=len(train_games) + len(test_games), samples=len(X_all),
                 minute_coverage=minute_coverage(X_train),
                 params={"trees": trees, "depth": depth, "learning_rate": learning_rate, "min_leaf": min_leaf,
                         "seed": seed},
                 metrics=metrics)
    log(f"accuracy {metrics['accuracy']} | log loss {metrics['log_loss']} (logistic baseline {baseline_log_loss})"
        f" | Brier {metrics['brier']} | ECE {metrics['ece']} | export gap {gap:.2g}")
    if metrics["log_loss"] >= baseline_log_loss:
        log("WARNING: gradient boosting does not beat the logistic regression baseline on log loss.")
    status, reason = wpa.quality(model)
    log(f"Quality: {status}" + (f" ({reason})" if reason else ""))
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=soloq.SOLOQ_DB_PATH, help="soloq.db to read")
    parser.add_argument("--out", type=Path, default=wpa.MODEL_PATH, help="model file to write")
    parser.add_argument("--synthetic", type=int, metavar="N", help="train on N generated games instead of --db")
    parser.add_argument("--trees", type=int, default=150)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--min-leaf", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.synthetic:
        conn, source = synthetic_db(args.synthetic, args.seed), "synthetic"
    else:
        if not args.db.exists():
            raise SystemExit(f"No database at {args.db}: import solo queue games first, or use --synthetic N.")
        conn, source = soloq.connect(args.db), "soloq"
    try:
        model = train(conn, source=source, trees=args.trees, depth=args.depth, learning_rate=args.learning_rate,
                      min_leaf=args.min_leaf, seed=args.seed)
    finally:
        conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    tmp.replace(args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
