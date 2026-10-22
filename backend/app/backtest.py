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
    "current defaults": Params(),
    "similarity m^1": Params(sim_power=1),
    "similarity m^3": Params(sim_power=3),
    "similarity m^4": Params(sim_power=4),
    "min 2 shared picks": Params(min_match=2),
    "counters x0": Params(counter_strength=0),
    "counters x0.5": Params(counter_strength=0.5),
    "counters x1.5": Params(counter_strength=1.5),
    "counters x2": Params(counter_strength=2),
    "no day decay": Params(half_life_days=None),
    "patch decay 0.7 (no day decay)": Params(half_life_days=None, patch_decay=0.7),
    "patch decay 0.5 (no day decay)": Params(half_life_days=None, patch_decay=0.5),
    "patch decay 0.3 (no day decay)": Params(half_life_days=None, patch_decay=0.3),
    "patch decay 0.5 + days 90": Params(patch_decay=0.5),
    "cooc": Params(model="cooc"),
    "cooc power 0.5": Params(model="cooc", cooc_power=0.5),
    "cooc power 0.7": Params(model="cooc", cooc_power=0.7),
    "cooc smoothing 5": Params(model="cooc", cooc_smoothing=5),
    "cooc smoothing 10": Params(model="cooc", cooc_smoothing=10),
    "cooc patch 0.5": Params(model="cooc", half_life_days=None, patch_decay=0.5),
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
