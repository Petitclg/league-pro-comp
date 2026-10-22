"""Walk-forward backtest of the suggestions: hide one pick of a past draft, rebuild the suggestions from
the games played on earlier days only and check where the real pick comes out.

    python -m app.backtest [--leagues LCK LPL] [--last 400] [--seed 1]
"""
import argparse
import random
import time
from bisect import bisect_left

from .dataset import Filters, get_dataset
from .db import ROLES
from .suggest import Params, suggest

TOP = (1, 3, 5, 10)
MIN_HISTORY = 200  # comps played before a case, or it is skipped

VARIANTS = {
    "previous (no decay, win votes 1.25/0.75)": Params(half_life_days=None),
    "no win votes": Params(win_vote=0.0),
    "recency 365 d": Params(half_life_days=365),
    "recency 180 d": Params(half_life_days=180),
    "recency 120 d": Params(half_life_days=120),
    "recency 90 d": Params(half_life_days=90),
    "recency 60 d": Params(half_life_days=60),
    "recency 30 d": Params(half_life_days=30),
    "winrate factor 2": Params(win_vote=0.0, wr_strength=2.0),
    "winrate factor 4": Params(win_vote=0.0, wr_strength=4.0),
    "recency 90 d + winrate 2": Params(half_life_days=90, win_vote=0.0, wr_strength=2.0),
    "recency 90 d + win votes 0": Params(half_life_days=90, win_vote=0.0),
    "recency 90 d + winrate 1": Params(half_life_days=90, win_vote=0.0, wr_strength=1.0),
}


def build_cases(comps: list[dict], last: int, seed: int) -> tuple[list[dict], list[tuple]]:
    """Dated comps oldest first, and the cases: (history size, locked picks, known enemy picks, hidden role, answer)."""
    comps = sorted((c for c in comps if c["date"] and set(c["picks"]) == set(ROLES)), key=lambda c: c["date"])
    days = [c["date"][:10] for c in comps]
    rng = random.Random(seed)
    cases = []
    for i in range(max(0, len(comps) - last), len(comps)):
        history = bisect_left(days, days[i])  # earlier days only: the other side of the same game is no hint
        if history < MIN_HISTORY:
            continue
        c = comps[i]
        known = rng.randint(1, 4)
        roles = rng.sample(list(ROLES), known + 1)
        enemy = rng.sample(list(c["enemy"].values()), rng.randint(0, min(3, len(c["enemy"]))))
        cases.append((history, {r: c["picks"][r] for r in roles[:known]}, enemy, roles[known], c["picks"][roles[known]]))
    return comps, cases


def evaluate(comps: list[dict], cases: list[tuple], params: Params) -> dict:
    hits = dict.fromkeys(TOP, 0)
    reciprocal = 0.0
    for history, locked, enemy, role, answer in cases:
        result = suggest(comps[:history], locked, enemy=enemy, top_n=max(TOP), params=params, details=False)
        names = [s["champion"] for s in result["suggestions"][role]]
        if answer in names:
            rank = names.index(answer) + 1
            reciprocal += 1 / rank
            for k in TOP:
                hits[k] += rank <= k
    n = len(cases) or 1
    return {**{f"top{k}": hits[k] / n for k in TOP}, "mrr": reciprocal / n}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--leagues", nargs="+", default=["LCK"])
    parser.add_argument("--last", type=int, default=400, help="number of most recent comps used as test cases")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    comps, cases = build_cases(get_dataset(tuple(args.leagues)).filter_comps(Filters()), args.last, args.seed)
    print(f"{len(cases)} cases from {len(comps)} comps ({', '.join(args.leagues)})")
    print(f"{'variant':<30}" + "".join(f"{'top' + str(k):>8}" for k in TOP) + f"{'mrr':>8}")
    for name, params in VARIANTS.items():
        started = time.time()
        r = evaluate(comps, cases, params)
        print(f"{name:<30}" + "".join(f"{r[f'top{k}']:>8.1%}" for k in TOP) + f"{r['mrr']:>8.3f}  ({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
