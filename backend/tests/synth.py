import random
import sqlite3
from datetime import datetime, timedelta

from app.db import ROLES, init_schema, save_game, save_match

TEAMS = ["T1", "Gen.G", "HLE", "KT", "DK", "BRO"]
POOL = {
    "top": ["Aatrox", "Ornn", "K'Sante", "Jax", "Gnar", "Renekton", "Sion", "Camille"],
    "jungle": ["Sejuani", "Vi", "Lee Sin", "Viego", "Maokai", "Jarvan IV", "Nidalee", "Wukong"],
    "mid": ["Azir", "Orianna", "Ahri", "Syndra", "Taliyah", "Viktor", "Corki", "Hwei"],
    "bot": ["Kai'Sa", "Jinx", "Varus", "Ezreal", "Aphelios", "Lucian", "Ashe", "Xayah"],
    "support": ["Rakan", "Nautilus", "Alistar", "Renata Glasc", "Lulu", "Braum", "Leona", "Karma"],
}
SYNERGY = {("mid", "Azir"): ("jungle", "Sejuani"), ("bot", "Jinx"): ("support", "Lulu")}
for _role, _pool in POOL.items():
    _pool.extend(f"{_role.title()}Filler{i}" for i in range(6))
ALL = [c for pool in POOL.values() for c in pool]


def _synergy_weight(role: str, champ: str, comp: dict[str, str]) -> int:
    for (r1, c1), (r2, c2) in SYNERGY.items():
        if (role, champ) == (r1, c1) and comp.get(r2) == c2:
            return 6
        if (role, champ) == (r2, c2) and comp.get(r1) == c1:
            return 6
    return 1


def build_db(n_series: int = 120, seed: int = 3) -> sqlite3.Connection:
    """Bo3 series; the first half is classic draft, the second half hard Fearless."""
    rng = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    date = datetime(2024, 3, 1, 8)
    for s in range(n_series):
        fearless = s >= n_series // 2
        tournament = "LCK/2025 Season/Cup" if fearless else "LCK/2024 Season/Spring Season"
        t1, t2 = rng.sample(TEAMS, 2)
        match_id = f"{tournament}_M{s}"
        used: set[str] = set()
        n_games = rng.choice([2, 3])
        score = [0, 0]
        for n in range(1, n_games + 1):
            blue, red = (t1, t2) if n % 2 else (t2, t1)
            taken = set(used) if fearless else set()
            bans = rng.sample([c for c in ALL if c not in taken], 10)
            taken |= set(bans)
            picks = []
            for side in (1, 2):
                comp: dict[str, str] = {}
                for order, role in enumerate(rng.sample(ROLES, 5), 1):
                    options = [c for c in POOL[role] if c not in taken]
                    weights = [_synergy_weight(role, c, comp) for c in options]
                    champ = rng.choices(options, weights)[0]
                    comp[role] = champ
                    taken.add(champ)
                    picks.append({"side": side, "pick_order": order, "role": role, "champion": champ,
                                  "player": f"{(t1 if (side == 1) == (n % 2 == 1) else t2)}-{role}",
                                  "kills": rng.randint(0, 9), "deaths": rng.randint(0, 6),
                                  "assists": rng.randint(0, 14), "gold": 10000, "cs": 250,
                                  "damage": rng.randint(8000, 25000), "vision": 40})
            used |= {p["champion"] for p in picks}
            winner = rng.choice([1, 2])
            score[0 if (blue if winner == 1 else red) == t1 else 1] += 1
            save_game(conn, {
                "game_id": f"{match_id}_{n}", "match_id": match_id, "game_number": n,
                "date": (date + timedelta(hours=2 * n)).isoformat(timespec="seconds"),
                "patch": "24.5" if not fearless else "25.S1.1", "tournament": tournament,
                "team1": blue, "team2": red, "winner": winner, "duration": rng.uniform(25, 40),
                "sides": {1: {"kills": 15, "gold": 60000, "towers": 8, "dragons": 3, "barons": 1},
                          2: {"kills": 10, "gold": 55000, "towers": 4, "dragons": 1, "barons": 0}},
                "picks": picks,
                "bans": [(1 if i < 5 else 2, i % 5 + 1, c) for i, c in enumerate(bans)],
            })
        save_match(conn, {"match_id": match_id, "tournament": tournament, "stage": "Week 1",
                          "best_of": 3, "team1": t1, "team2": t2, "score1": score[0], "score2": score[1]})
        date += timedelta(days=1)
    conn.commit()
    return conn
