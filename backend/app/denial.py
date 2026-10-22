"""Do opponents break a synergy? Measure what happens to a partner around its trigger's pick.

For a pair (A, P) we look at every game where team X picks A and P was not already picked.
P then ends up as:
  - banned_before: banned before A was picked (the opponent may have anticipated the synergy);
  - combo:         X picks P too (the synergy is realised);
  - deny_pick:     the opponent picks P after A;
  - ban_after:     P is banned after A (phase-2 bans);
  - untouched:     nobody takes P.
Two ways to break the synergy are measured against a baseline:
  - anticipation = banned_before;
  - reaction     = deny_pick + ban_after;
  - removed      = both together.
Strong champions get banned or picked anyway, and two champions that are strong on the same patch
are banned together. So each rate is compared to what happens to P in the same context (same
patch, same game group, same pick slot bucket) with any other champion as trigger.
lift = observed / expected, z = binomial z-score of the difference.
Games where A or P are locked by Fearless are skipped.
"""
from collections import defaultdict
from math import sqrt

from .dataset import Dataset, Filters

OUTCOMES = ("banned_before", "combo", "deny_pick", "ban_after", "untouched")
MEASURES = {
    "anticipation": ("banned_before",),
    "reaction": ("deny_pick", "ban_after"),
    "removed": ("banned_before", "deny_pick", "ban_after"),
    "combo": ("combo",),
}
GROUPS = (("Game 1", lambda n: n == 1), ("Games 2+", lambda n: n is not None and n >= 2))

# Candidate synergies for the global ranking
MIN_CO_PICKS = 8
MIN_SINGLE_PICKS = 20
MIN_LIFT = 1.5
MIN_EXPOSURE = 40
# Reliability levels of a hit: (key, minimum z, minimum observed count, chance of a false hit).
# The count floor matters because the normal approximation of z is unreliable for rare events.
LEVELS = (
    ("strong", 3.3, 8, 0.0005),
    ("likely", 2.33, 6, 0.01),
    ("hint", 1.64, 5, 0.05),
)
SIGNIFICANT_LIFT = 1.3
MIN_NEUTRAL_EXCESS = 8


def _pick_time(slot: int) -> int:
    return 6 + slot if slot <= 6 else 10 + slot


def _ban_time(order: int) -> int:
    return order if order <= 6 else 6 + order


def _bucket(slot: int) -> str:
    return "1-3" if slot <= 3 else "4-6" if slot <= 6 else "7-10"


def _group(game_number: int | None) -> str:
    return "g1" if game_number == 1 else "g2+"


def _ctx(g: dict, role: str, slot: int) -> tuple:
    """Context a trigger is compared within.

    The patch matters most: two champions that are strong on the same patch are banned together,
    which would otherwise look like a broken synergy. The pick slot bucket (draft timing) and the
    game group (Game 1 vs later) complete it. The trigger's role is left out on purpose: split by
    patch too, it would leave too few comparison games.
    """
    return (g.get("patch"), _group(g["game_number"]), _bucket(slot))


def timeline(g: dict) -> dict:
    """champion -> {"pick": (side, time, slot, role), "ban": (side, time)} in draft order."""
    if "_timeline" not in g:
        tl: dict[str, dict] = defaultdict(dict)
        for side, s in g["sides"].items():
            for p in s["picks"]:
                tl[p["champion"]]["pick"] = (side, _pick_time(p["slot"]), p["slot"], p["role"])
            for champ, order in zip(s["bans"], s["ban_order"]):
                tl[champ]["ban"] = (side, _ban_time(order))
        g["_timeline"] = dict(tl)
    return g["_timeline"]


def _outcome(side: int, t_a: int, pick: tuple | None, ban: tuple | None) -> str:
    """Outcome of a partner (its pick / ban in the game) relative to a trigger picked at `t_a`."""
    if pick and pick[1] < t_a:
        return "prior_combo" if pick[0] == side else "taken_before"
    if ban and ban[1] < t_a:
        return "banned_before"
    if pick:
        return "combo" if pick[0] == side else "deny_pick"
    return "ban_after" if ban else "untouched"


def fate(g: dict, trigger: str, partner: str) -> dict | None:
    """What became of `partner` around the pick of `trigger` in game `g` (None if n/a)."""
    if trigger in g["locked"] or partner in g["locked"]:
        return None
    tl = timeline(g)
    a = tl.get(trigger, {}).get("pick")
    if not a:
        return None
    side, t_a, slot, role = a
    p = tl.get(partner, {})
    return {"side": side, "role": role, "ctx": _ctx(g, role, slot), "group": _group(g["game_number"]),
            "outcome": _outcome(side, t_a, p.get("pick"), p.get("ban"))}


class _Scope:
    """Index of a set of games: every pick as a possible trigger, and where each champion appears."""

    def __init__(self, games: list[dict]):
        self.games = games
        self.entries: dict[str, list[tuple]] = {}  # game_id -> [(trigger, context)]
        self.triggers: dict[str, list[tuple]] = {}  # game_id -> [(trigger, context, pick)]
        self.ctx_of: dict[tuple, tuple] = {}  # (game_id, trigger) -> context
        self.all_counts: dict[tuple, int] = defaultdict(int)  # (trigger, context) -> picks
        self.ctx_totals: dict[tuple, int] = defaultdict(int)  # context -> picks, all triggers
        self.present: dict[str, dict[str, dict]] = defaultdict(dict)  # champion -> game_id -> game
        self.picked: dict[str, list[dict]] = defaultdict(list)
        self.baselines: dict[str, tuple] = {}
        self.cell_totals: dict[tuple, int] = defaultdict(int)  # (patch, group) -> games
        for g in games:
            self.cell_totals[(g.get("patch"), _group(g["game_number"]))] += 1
            entries = []
            for champ, e in timeline(g).items():
                self.present[champ][g["game_id"]] = g
                if "pick" in e and champ not in g["locked"]:
                    _, _, slot, role = e["pick"]
                    ctx = _ctx(g, role, slot)
                    entries.append((champ, ctx))
                    self.ctx_of[(g["game_id"], champ)] = ctx
                    self.all_counts[(champ, ctx)] += 1
                    self.ctx_totals[ctx] += 1
                    self.picked[champ].append(g)
            for champ in g["locked"]:
                self.present[champ][g["game_id"]] = g
            self.entries[g["game_id"]] = entries
            self.triggers[g["game_id"]] = [(c, ctx, timeline(g)[c]["pick"]) for c, ctx in entries]

    def baseline(self, partner: str) -> tuple[dict, dict]:
        """How `partner` fared with each other trigger, as corrections to an "all untouched" default.

        Returns (per, tot): outcome counts by (trigger, ctx) and by ctx, where "untouched" holds
        only the correction (negative for the games where the partner shows up). Games where the
        partner is absent are all "untouched"; counting them once per scope keeps this cheap.
        """
        if partner not in self.baselines:
            per: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            tot: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for g in self.present.get(partner, {}).values():
                locked = partner in g["locked"]
                p = timeline(g).get(partner, {})
                pick, ban = p.get("pick"), p.get("ban")
                for trigger, ctx, a in self.triggers[g["game_id"]]:
                    per[(trigger, ctx)]["untouched"] -= 1
                    tot[ctx]["untouched"] -= 1
                    if not locked and trigger != partner:
                        outcome = _outcome(a[0], a[1], pick, ban)
                        per[(trigger, ctx)][outcome] += 1
                        tot[ctx][outcome] += 1
            self.baselines[partner] = (per, tot)
        return self.baselines[partner]


def _scope(ds: Dataset, f: Filters) -> _Scope:
    scope = Filters(**{**f.__dict__, "teams": (), "champion": None, "role": None})
    key = ("denial-scope", *scope.key())
    if key not in ds._cache:
        ds._cache[key] = _Scope(ds.filter_games(scope))
    return ds._cache[key]


def direction(ds: Dataset, f: Filters, trigger: str, partner: str, with_splits: bool = True) -> dict:
    scope = _scope(ds, f)
    per, tot_all = scope.baseline(partner)

    rows: list[tuple] = []  # (outcome, context, game_number)
    for g in scope.picked.get(trigger, []):
        if partner in g["locked"]:
            continue
        tl = timeline(g)
        side, t_a, _, _ = tl[trigger]["pick"]
        if f.teams and g["sides"][side]["team"] not in f.teams:
            continue
        p = tl.get(partner, {})
        rows.append((_outcome(side, t_a, p.get("pick"), p.get("ban")),
                     scope.ctx_of[(g["game_id"], trigger)], g["game_number"]))

    probs_cache: dict[tuple, dict[str, float] | None] = {}

    def ctx_probs(ctx: tuple) -> dict[str, float] | None:
        """Outcome probabilities of the partner in this context, with other champions as trigger."""
        if ctx not in probs_cache:
            own, tot = per.get((trigger, ctx), {}), tot_all.get(ctx, {})
            counts = {o: tot.get(o, 0) - own.get(o, 0) for o in OUTCOMES}
            counts["untouched"] += scope.ctx_totals.get(ctx, 0) - scope.all_counts.get((trigger, ctx), 0)
            n = sum(counts.values())
            probs_cache[ctx] = {o: c / n for o, c in counts.items()} if n > 0 else None
        return probs_cache[ctx]

    def summarise(subset: list[tuple]) -> dict:
        counts: dict[str, int] = defaultdict(int)
        by_ctx: dict[tuple, int] = defaultdict(int)
        for outcome, ctx, _ in subset:
            counts[outcome] += 1
            if outcome in OUTCOMES:
                by_ctx[ctx] += 1
        avail = sum(counts[o] for o in OUTCOMES)
        probs = {ctx: p for ctx in by_ctx if (p := ctx_probs(ctx)) is not None}
        measures = {}
        for name, outcomes in MEASURES.items():
            observed = sum(counts[o] for o in outcomes)
            expected = variance = 0.0
            for ctx, n in by_ctx.items():
                if ctx in probs:
                    p = sum(probs[ctx][o] for o in outcomes)
                    expected += n * p
                    variance += n * p * (1 - p)
            measures[name] = {
                "count": observed,
                "rate": round(observed / avail, 4) if avail else None,
                "expected_rate": round(expected / avail, 4) if avail else None,
                "lift": round(observed / expected, 2) if expected else None,
                "z": round((observed - expected) / sqrt(variance), 2) if variance > 0 else 0.0,
                "excess": round(observed - expected, 1),
            }
        return {
            "available": avail,
            "outcomes": {o: counts[o] for o in (*OUTCOMES, "prior_combo", "taken_before")},
            "measures": measures,
        }

    return {
        "trigger": trigger, "partner": partner, "picked": len(rows), **summarise(rows),
        "splits": [{"label": label, **summarise([r for r in rows if pred(r[2])])} for label, pred in GROUPS]
        if with_splits else [],
    }


def pair_denial(ds: Dataset, f: Filters, a: str, b: str) -> dict:
    return {"a_to_b": direction(ds, f, a, b), "b_to_a": direction(ds, f, b, a),
            "neutralisation": neutralisation(ds, f, a, b)}


def neutralisation(ds: Dataset, f: Filters, a: str, b: str) -> dict:
    """Is the duo neutralised by banning one of the two ("l'un ou l'autre")?

    A synergy can be broken before anyone picks: ban A, or ban B, and the combo is gone. Looking
    at games where A or B is banned (whatever happens next), compared patch by patch with what
    independent bans would give:
      - either: at least one of the two is banned;
      - both banned: fewer than chance means teams ban one OR the other, not both.
    z measures that deficit of double bans (the hypergeometric test of independence, summed over
    patch x game-group cells). Games where A or B is locked by Fearless are left out.
    """
    scope = _scope(ds, f)
    cells: dict[tuple, dict] = {}
    for key, n in scope.cell_totals.items():
        cells[key] = {"n": n, "na": 0, "nb": 0, "both": 0, "together": 0, "split": 0, "one": 0}
    seen = {**scope.present.get(a, {}), **scope.present.get(b, {})}
    for g in seen.values():
        cell = cells[(g.get("patch"), _group(g["game_number"]))]
        if a in g["locked"] or b in g["locked"]:
            cell["n"] -= 1
            continue
        tl = timeline(g)
        ea, eb = tl.get(a, {}), tl.get(b, {})
        ban_a, ban_b = "ban" in ea, "ban" in eb
        cell["na"] += ban_a
        cell["nb"] += ban_b
        cell["both"] += ban_a and ban_b
        if not ban_a and not ban_b:
            pa, pb = ea.get("pick"), eb.get("pick")
            if pa and pb:
                cell["together" if pa[0] == pb[0] else "split"] += 1
            elif pa or pb:
                cell["one"] += 1

    def summarise(label: str, keep) -> dict:
        tot = defaultdict(float)
        exp_both = var = 0.0
        for (patch, group), c in cells.items():
            if not keep(group) or c["n"] <= 0:
                continue
            for k in ("n", "na", "nb", "both", "together", "split", "one"):
                tot[k] += c[k]
            if c["n"] > 1:
                exp_both += c["na"] * c["nb"] / c["n"]
                var += c["na"] * c["nb"] * (c["n"] - c["na"]) * (c["n"] - c["nb"]) / (c["n"] ** 2 * (c["n"] - 1))
        n = int(tot["n"])
        either = int(tot["na"] + tot["nb"] - tot["both"])
        exp_either = tot["na"] + tot["nb"] - exp_both
        free = n - either
        return {
            "label": label, "n": n,
            "a_banned": int(tot["na"]), "b_banned": int(tot["nb"]), "both_banned": int(tot["both"]),
            "either_banned": either,
            "either_rate": round(either / n, 4) if n else None,
            "expected_either_rate": round(exp_either / n, 4) if n else None,
            "expected_both_banned": round(exp_both, 1),
            "z": round(-(tot["both"] - exp_both) / sqrt(var), 2) if var > 0 else 0.0,
            "free": free, "together": int(tot["together"]), "split": int(tot["split"]),
            "one_picked": int(tot["one"]), "none_picked": int(free - tot["together"] - tot["split"] - tot["one"]),
        }

    return {"a": a, "b": b, "groups": [
        summarise("Game 1", lambda g: g == "g1"),
        summarise("Games 2+", lambda g: g == "g2+"),
        summarise("Toutes les games", lambda g: True),
    ]}


def _level(m: dict) -> str | None:
    """Reliability of one measure: the strictest level whose z, sample and size of effect it meets."""
    if (m["lift"] or 0) < SIGNIFICANT_LIFT:
        return None
    for key, z_min, min_count, _ in LEVELS:
        if m["z"] >= z_min and m["count"] >= min_count:
            return key
    return None


def neutralised_pairs(ds: Dataset, f: Filters) -> list[dict]:
    """Known synergies (picked together more than chance over the whole history) whose duo is
    neutralised by bans in the selected scope, each with its Game 1 / Games 2+ split."""
    from .stats import pair_lift, indexes

    key = ("neutralised", *f.key())
    if key in ds._cache:
        return ds._cache[key]
    idx = indexes(ds)  # synergy is judged on the whole history, bans on the selected scope
    seen: set[frozenset] = set()
    results = []
    for (ra, ca, rb, cb), t in idx["pairs"].items():
        pair = frozenset((ca, cb))
        if pair in seen or t.games < MIN_CO_PICKS:
            continue
        if min(idx["singles"][(ra, ca)], idx["singles"][(rb, cb)]) < MIN_SINGLE_PICKS:
            continue
        if pair_lift(idx, (ra, ca), (rb, cb), t.games) < MIN_LIFT:
            continue
        seen.add(pair)
        n = neutralisation(ds, f, ca, cb)
        total = n["groups"][2]
        if total["n"] < 30 or total["expected_both_banned"] < MIN_NEUTRAL_EXCESS:
            continue
        results.append({"a": ca, "b": cb, "co_picks": t.games, **{"groups": n["groups"]}})
    ds._cache[key] = results
    return results


def _neutral_level(g: dict) -> str | None:
    """Reliability of a neutralisation: z of the double-ban deficit, and a minimum size of the
    effect in games (at least 8 fewer double bans than independent bans would give)."""
    if g["expected_both_banned"] - g["both_banned"] < MIN_NEUTRAL_EXCESS:
        return None
    for key, z_min, _, _ in LEVELS:
        if g["z"] >= z_min:
            return key
    return None


def ranking(ds: Dataset, f: Filters, limit: int = 30) -> dict:
    """Synergies whose partner is removed (anticipation / reaction) far more than in the same
    context with other triggers, strongest first, each with a reliability level.

    `levels` also gives how many hits pure chance would produce at each level among the tested
    directions, so a short list on a small scope can still be read honestly."""
    results = countered_synergies(ds, f)
    tested = len(results)
    out: dict = {
        "tested": tested, "lift_min": SIGNIFICANT_LIFT,
        "levels": [{"key": key, "z": z_min, "min_count": min_count, "by_chance": round(tested * p, 1)}
                   for key, z_min, min_count, p in LEVELS],
    }
    neutral = []
    pairs = neutralised_pairs(ds, f)
    for r in pairs:
        level = _neutral_level(r["groups"][2])
        if level:
            neutral.append((level, r))
    neutral.sort(key=lambda lr: -lr[1]["groups"][2]["z"])
    out["neutralised"] = {
        "tested": len(pairs),
        "by_level": {key: sum(1 for lv, _ in neutral if lv == key) for key, *_ in LEVELS},
        "chance": {key: round(len(pairs) * p, 1) for key, _, _, p in LEVELS},
        "items": [{"level": level, "a": r["a"], "b": r["b"], "co_picks": r["co_picks"], "groups": r["groups"]}
                  for level, r in neutral[:limit]],
    }
    for name in ("anticipation", "reaction"):
        hits = []
        for r in results:
            level = _level(r["measures"][name])
            if level:
                hits.append((level, r))
        hits.sort(key=lambda lr: -lr[1]["measures"][name]["z"])
        for _, r in hits[:limit]:
            if "splits" not in r:
                r["splits"] = direction(ds, f, r["trigger"], r["partner"])["splits"]
        out[name] = {
            "by_level": {key: sum(1 for lv, _ in hits if lv == key) for key, *_ in LEVELS},
            "items": [{
                "level": level, "trigger": r["trigger"], "partner": r["partner"],
                "co_picks": r["co_picks"], "available": r["available"], **r["measures"][name],
                "splits": [{"label": s["label"], "available": s["available"],
                            "rate": s["measures"][name]["rate"],
                            "expected_rate": s["measures"][name]["expected_rate"],
                            "lift": s["measures"][name]["lift"], "z": s["measures"][name]["z"]}
                           for s in r["splits"]],
            } for level, r in hits[:limit]],
        }
    return out


def countered_synergies(ds: Dataset, f: Filters) -> list[dict]:
    """Every plausible synergy (picked together noticeably more than chance), in both directions,
    with how much opponents break it. A champion pair counts once per direction even if the two
    can play different roles."""
    from .stats import pair_lift, scoped_indexes

    key = ("countered", *f.key())
    if key in ds._cache:
        return ds._cache[key]
    idx = scoped_indexes(ds, f)
    found: dict[tuple[str, str], dict] = {}
    for (ra, ca, rb, cb), t in idx["pairs"].items():
        if t.games < MIN_CO_PICKS:
            continue
        if min(idx["singles"][(ra, ca)], idx["singles"][(rb, cb)]) < MIN_SINGLE_PICKS:
            continue
        if pair_lift(idx, (ra, ca), (rb, cb), t.games) < MIN_LIFT:
            continue
        for trigger, partner in ((ca, cb), (cb, ca)):
            if (trigger, partner) in found and found[(trigger, partner)]["co_picks"] >= t.games:
                continue
            # Game 1 / games 2+ splits are only computed (in `ranking`) for the few hits shown.
            d = direction(ds, f, trigger, partner, with_splits=False)
            if d["available"] < MIN_EXPOSURE:
                continue
            found[(trigger, partner)] = {
                "trigger": trigger, "partner": partner, "co_picks": t.games, "available": d["available"],
                "measures": d["measures"],
            }
    ds._cache[key] = list(found.values())
    return ds._cache[key]
