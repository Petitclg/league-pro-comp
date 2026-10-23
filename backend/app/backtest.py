"""Walk-forward backtest of the suggestions: hide one pick of a past draft, rebuild the suggestions from
the games played on earlier days only and check where the real pick comes out.

    python -m app.backtest [--leagues LCK LPL] [--last 400] [--seed 1]
"""
import argparse
import random
import time
from bisect import bisect_left

from .combos import ComboIndex
from .dataset import Filters, get_dataset
from .db import ROLES
from .suggest import Params, suggest

TOP = (1, 3, 5, 10)
MIN_HISTORY = 200  # comps played before a case, or it is skipped

VARIANTS = {
    "current defaults": Params(),
    "no duo bonus": Params(combo_strength=0, pair_wr_strength=0),
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
    "duo: open rate x0.5": Params(combo_strength=0.5),
    "duo: open rate x1": Params(combo_strength=1),
    "duo: open rate x2": Params(combo_strength=2),
    "duo: winrate x1": Params(pair_wr_strength=1),
    "duo: winrate x2": Params(pair_wr_strength=2),
    "duo: open rate x1 + winrate x1": Params(combo_strength=1, pair_wr_strength=1),
    "duo: open rate x2 + winrate x2": Params(combo_strength=2, pair_wr_strength=2),
    "duo: open rate x1 + winrate x2": Params(combo_strength=1, pair_wr_strength=2),
    "duo: open rate x2 + winrate x1": Params(combo_strength=2, pair_wr_strength=1),
    "duo: open rate x0.5 + winrate x1": Params(combo_strength=0.5, pair_wr_strength=1),
    "duo: open rate x4 + winrate x4": Params(combo_strength=4, pair_wr_strength=4),
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


def evaluate(comps: list[dict], cases: list[tuple], params: Params, index: ComboIndex | None = None) -> dict:
    hits = dict.fromkeys(TOP, 0)
    reciprocal = 0.0
    for history, locked, enemy, role, answer in cases:
        combos = (lambda a, b, n=history: index.stats(a, b, n)) if index else None  # the duo stats of earlier comps only
        result = suggest(comps[:history], locked, enemy=enemy, top_n=max(TOP), params=params, details=False, combos=combos)
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
    parser.add_argument("--only", nargs="+", metavar="TEXT", help="run the variants whose name contains one of these")
    args = parser.parse_args()

    ds = get_dataset(tuple(args.leagues))
    comps, cases = build_cases(ds.filter_comps(Filters()), args.last, args.seed)
    index = ComboIndex(ds.by_id, comps)
    print(f"{len(cases)} cases from {len(comps)} comps ({', '.join(args.leagues)})")
    print(f"{'variant':<30}" + "".join(f"{'top' + str(k):>8}" for k in TOP) + f"{'mrr':>8}")
    for name, params in VARIANTS.items():
        if args.only and name != "current defaults" and not any(t in name for t in args.only):
            continue
        started = time.time()
        r = evaluate(comps, cases, params, index)
        print(f"{name:<30}" + "".join(f"{r[f'top{k}']:>8.1%}" for k in TOP) + f"{r['mrr']:>8.3f}  ({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
