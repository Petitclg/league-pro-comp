"""Comp suggestions: complete a partial draft so it resembles winning pro comps."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Callable, Literal

from .db import ROLES
from .stats import COMBO_PRIOR, Tally
from .strength import shrunk_winrate

ComboStats = Callable[..., dict]  # (role, champion) x 2 [, decay=...] -> combos.ComboIndex.stats


@dataclass(frozen=True)
class Params:
    """Knobs of the ranking; `python -m app.backtest` compares settings on past drafts."""
    half_life_days: float | None = 90  # a comp this much older than the newest votes half as much; None: no decay
    win_vote: float = 0.25  # a winning comp votes (1 + x) times, a losing one (1 - x) times
    wr_strength: float = 0.0  # score *= 1 + x * (shrunk winrate of the candidate in similar comps - 0.5)
    patch_decay: float | None = None  # a comp from the patch n before the newest weighs x^n; None: patches are ignored
    sim_power: float = 2.0  # a comp sharing m locked slots votes with weight m^x
    min_match: int = 1  # comps sharing fewer locked slots do not vote (capped by the number of locked slots)
    counter_strength: float = 1.0  # score *= 1 + x * (smoothed winrate against the enemy picks - 0.5); 0: ignore enemies
    model: Literal["votes", "cooc"] = "votes"  # cooc: popularity x lift with each locked champion (naive Bayes)
    cooc_smoothing: float = 2.0  # pseudo-weight pulling a champion's rate with a locked one towards its overall rate
    cooc_power: float = 1.0  # each locked champion's lift is raised to x (< 1 tempers correlated evidence)
    combo_strength: float = 1.0  # a duo with a locked champion that pros take whenever it is open: score *= 1 + x * rate * (0.5 + 2 * (winrate - 0.5))
    pair_wr_strength: float = 0.5  # a duo with a locked champion that wins a lot: score *= 1 + x * (shrunk duo winrate - 0.5)
    pair_patch_decay: float | None = None  # duo stats of the bonuses: a comp from the patch n before the newest weighs x^n; None: all comps equal


DEFAULT = Params()


@lru_cache(maxsize=None)
def _ordinal(day: str) -> int | None:
    try:
        return date.fromisoformat(day).toordinal()
    except ValueError:
        return None


def _base_weights(comps: list[dict], params: Params) -> list[float]:
    """Per comp: win/loss vote x recency (by days and/or by patch)."""
    newest = None
    if params.half_life_days:
        newest = max((o for c in comps if (o := _ordinal((c["date"] or "")[:10]))), default=None)
    patch_rank: dict[str, int] = {}
    if params.patch_decay is not None:
        first: dict[str, str] = {}
        for c in comps:
            day = (c["date"] or "")[:10]
            if c.get("patch") and day and day < first.get(c["patch"], "9"):
                first[c["patch"]] = day
        patch_rank = {p: i for i, p in enumerate(sorted(first, key=first.__getitem__, reverse=True))}

    weights = []
    for c in comps:
        w = 1.0
        if c["win"] is True:
            w *= 1 + params.win_vote
        elif c["win"] is False:
            w *= 1 - params.win_vote
        if newest is not None:
            age = newest - (_ordinal((c["date"] or "")[:10]) or newest - params.half_life_days)  # undated: one half-life old
            w *= 0.5 ** (age / params.half_life_days)
        if params.patch_decay is not None:
            w *= params.patch_decay ** patch_rank.get(c.get("patch"), len(patch_rank))  # unknown patch: oldest
        weights.append(w)
    return weights


def _similarity(a: dict, b: dict) -> float:
    """0..1 resemblance of two Data Dragon champion profiles: shared tags and attack/defense/magic ratings."""
    ta, tb = set(a.get("tags", ())), set(b.get("tags", ()))
    tags = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    ia, ib = a.get("info", {}), b.get("info", {})
    keys = [k for k in ("attack", "defense", "magic") if k in ia and k in ib]
    info = 1 - sum(abs(ia[k] - ib[k]) for k in keys) / (10 * len(keys)) if keys else 0.0
    return 0.6 * tags + 0.4 * info


def substitutes(
    comps: list[dict],
    locked: dict[str, str],
    profiles: dict[str, dict],
    k: int = 5,
    discount: float = 0.5,
) -> dict[str, dict[str, float]]:
    """For each locked champion the pros never played in its role: the k most alike champions they did
    play there (role -> {champion: weight}), weighted by `discount` x resemblance."""
    played = Counter((r, ch) for c in comps for r, ch in c["picks"].items())
    out: dict[str, dict[str, float]] = {}
    for role, champ in locked.items():
        if played[(role, champ)] or champ not in profiles:
            continue
        ranked = sorted(
            ((_similarity(profiles[champ], profiles[ch]), ch) for (r, ch) in played if r == role and ch in profiles),
            reverse=True,
        )[:k]
        if ranked:
            out[role] = {ch: round(discount * s, 3) for s, ch in ranked}
    return out


def _synergies(
    matches: list[dict], role: str, champ: str, locked_items: list[tuple[str, str]], combos: ComboStats | None = None,
) -> list[dict]:
    """Winrate of `champ` together with each locked champion (`matches` already share a locked slot),
    and how often the pros played the duo when it was open (`combos`)."""
    out = []
    for lr, lc in locked_items:
        t = Tally()
        for c in matches:
            picks = c["picks"]
            if picks.get(role) == champ and picks.get(lr) == lc:
                t.add(c["win"])
        info = combos((lr, lc), (role, champ)) if combos else None
        if t.games or (info and info["open"]):
            entry = {"role": lr, "champion": lc, "games": t.games, "winrate": t.wr}
            if info and info["open"]:
                entry["open"] = {k: info[k] for k in ("open", "realised", "taken", "banned", "unused", "rate")}
            out.append(entry)
    out.sort(key=lambda s: -s["games"])
    return out


def _pair_boost(combos: ComboStats, locked_items: list[tuple[str, str]], role: str, champ: str, params: Params) -> float:
    """Bonus of a candidate for the duos it forms with the locked champions (see `Params`)."""
    boost = 0.0
    for item in locked_items:
        info = combos(item, (role, champ), decay=params.pair_patch_decay)
        if not info["open"]:
            continue
        counts = info.get("weighted") or info
        rate = counts["realised"] / (counts["open"] + COMBO_PRIOR)
        edge = shrunk_winrate(counts["wins"], counts["decided"]) - 0.5
        boost += params.pair_wr_strength * edge + params.combo_strength * rate * (0.5 + 2 * edge)
    return max(-0.3, min(boost, 0.75))


def _counter_scores(comps: list[dict], enemy: list[str]) -> dict[tuple[str, str], tuple[float, int]]:
    """(role, champion) -> (smoothed winrate against the enemy champions, games)."""
    if not enemy:
        return {}
    wanted = set(enemy)
    per: dict[tuple[str, str], dict[str, Tally]] = defaultdict(lambda: defaultdict(Tally))
    for c in comps:
        present = wanted & set(c["enemy"].values())
        if not present:
            continue
        for role, champ in c["picks"].items():
            for e in present:
                per[(role, champ)][e].add(c["win"])
    result = {}
    for key, by_enemy in per.items():
        rates = [((t.wins + 2) / (t.decided + 4), t.games) for t in by_enemy.values() if t.decided]
        if rates:
            result[key] = (sum(r for r, _ in rates) / len(rates), sum(n for _, n in rates))
    return result


def suggest(
    comps: list[dict],
    locked: dict[str, str],
    unavailable: set[str] | None = None,
    enemy: list[str] | None = None,
    top_n: int = 5,
    similar_n: int = 8,
    params: Params = DEFAULT,
    details: bool = True,
    subs: dict[str, dict[str, float]] | None = None,
    combos: ComboStats | None = None,
) -> dict:
    """Every pro comp sharing m >= 1 (role, champion) slots with `locked` votes for the
    champions it played in the open roles, with weight m^2 (wins count a bit more than
    losses, recent comps more than old ones, see `Params`). With nothing locked, every comp votes
    equally, and so do they when no locked champion was ever played by the pros. With
    `Params.model == "cooc"` the candidates are ranked by recent popularity in their
    role times their lift with each locked champion instead. When enemy picks are known, each
    candidate is then scaled by its smoothed winrate against them (0.5x to 1.5x).
    `subs` (see `substitutes`) lets a comp playing a look-alike of an unplayed locked champion
    count as a partial match. `combos` (see `combos.ComboIndex.stats`) feeds the duo bonuses of `Params`:
    duos the pros play whenever they are open, and duos that win a lot, are favoured.
    """
    enemy = [e for e in (enemy or []) if e]
    unavailable = set(unavailable or ()) | set(locked.values()) | set(enemy)
    open_roles = [r for r in ROLES if r not in locked]
    locked_items = list(locked.items())

    subs = subs or {}

    def shared_slots(picks: dict[str, str]) -> list[tuple[tuple[str, str], float]]:
        out = []
        for item in locked_items:
            role, champ = item
            s = 1.0 if picks.get(role) == champ else subs.get(role, {}).get(picks.get(role), 0.0)
            if s:
                out.append((item, s))
        return out

    shared_all = [shared_slots(c["picks"]) for c in comps]
    exact = {item for slots in shared_all for item, s in slots if s == 1.0}
    fallback = bool(locked_items) and not any(shared_all)  # nothing known: plain recent popularity
    cooc = params.model == "cooc" and bool(locked_items)
    min_match = min(params.min_match, len(locked_items)) if params.min_match > 1 else 0  # 1 would drop partial matches
    votes: dict[str, Counter] = {r: Counter() for r in open_roles}
    support: dict[tuple[str, str], Tally] = defaultdict(Tally)
    total_weight = 0.0
    matches = []
    all_weight = 0.0  # cooc: every comp counts, similar or not
    popularity: Counter = Counter()  # (role, champion) -> weight
    slot_weight: Counter = Counter()  # locked (role, champion) -> weight
    joint: Counter = Counter()  # (locked slot, role, champion) -> weight
    for c, base, shared in zip(comps, _base_weights(comps, params), shared_all):
        picks = c["picks"]
        m = sum(s for _, s in shared)
        if cooc:
            all_weight += base
            for item, s in shared:
                slot_weight[item] += base * s
            for r in open_roles:
                if champ := picks.get(r):
                    popularity[(r, champ)] += base
                    for item, s in shared:
                        joint[(item, r, champ)] += base * s
        if locked_items and not fallback and (m <= 0 or m < min_match):
            continue
        weight = base * (m ** params.sim_power if locked_items and not fallback else 1.0)
        total_weight += weight
        matches.append((m, c))
        for r in open_roles:
            champ = picks.get(r)
            if champ:
                votes[r][champ] += weight
                support[(r, champ)].add(c["win"])

    counters = _counter_scores(comps, enemy)
    pair_bonus = combos is not None and bool(params.combo_strength or params.pair_wr_strength)
    matched = [c for _, c in matches]
    suggestions: dict[str, list[dict]] = {}
    for r in open_roles:
        ranked = []
        for champ, score in votes[r].items():
            if champ in unavailable:
                continue
            share = score / total_weight if total_weight else 0
            cwr, cgames = counters.get((r, champ), (None, 0))
            tally = support[(r, champ)]
            factor = 1 + params.wr_strength * (shrunk_winrate(tally.wins, tally.decided) - 0.5)
            if cwr is not None:
                factor *= 1 + params.counter_strength * (cwr - 0.5)
            if pair_bonus and locked_items:
                factor *= 1 + _pair_boost(combos, locked_items, r, champ, params)
            base_score = share
            if cooc:
                pop = popularity[(r, champ)] / all_weight
                lift = 1.0
                for item in locked_items:
                    if slot_weight[item]:
                        rate = (joint[(item, r, champ)] + params.cooc_smoothing * pop) / (slot_weight[item] + params.cooc_smoothing)
                        lift *= (rate / pop) ** params.cooc_power
                base_score = pop * lift
            ranked.append({
                "champion": champ,
                "score": round(base_score * factor, 6),
                "share": round(share, 4),
                "games": support[(r, champ)].games,
                "winrate": support[(r, champ)].wr,
                "counter_winrate": None if cwr is None else round(cwr, 3),
                "counter_games": cgames,
            })
        ranked.sort(key=lambda s: -s["score"])
        suggestions[r] = ranked[:top_n]
        # Synergies are only worth computing for the candidates actually returned.
        for s in suggestions[r] if details else ():
            s["synergies"] = _synergies(matched, r, s["champion"], locked_items, combos)

    coverage = {
        "matching_comps": 0 if fallback else len(matches),
        "unknown_picks": [r for r, ch in locked_items if (r, ch) not in exact],
        "substitutes": {r: list(s) for r, s in subs.items()},
    }
    if not details:  # ranking only (backtests)
        return {"locked": locked, **coverage, "suggestions": suggestions, "similar_comps": [], "closest_teams": []}

    if fallback:
        matches = []
    matches.sort(key=lambda mc: mc[1]["date"] or "", reverse=True)
    matches.sort(key=lambda mc: (-mc[0], mc[1]["win"] is not True))
    banned = unavailable - set(locked.values())
    similar = [
        {
            "match": round(m, 2), "game_id": c["game_id"], "team": c["team"], "opponent": c["opponent"],
            "win": c["win"], "date": c["date"], "game_number": c["game_number"], "picks": c["picks"],
        }
        for m, c in matches
        if c["picks"].keys() == set(ROLES) and not (set(c["picks"].values()) & banned)
    ][:similar_n]

    teams: dict[str, dict] = {}
    for m, c in matches:
        t = teams.setdefault(c["team"], {"team": c["team"], "similarity": 0.0, "best_match": 0, "games": 0})
        t["similarity"] += m
        t["best_match"] = round(max(t["best_match"], m), 2)
        t["games"] += 1
    closest_teams = sorted(
        ({**t, "similarity": round(t["similarity"] / t["games"], 2)} for t in teams.values()),
        key=lambda t: (-t["best_match"], -t["similarity"], -t["games"]),
    )[:5]

    return {
        "locked": locked,
        **coverage,
        "suggestions": suggestions,
        "similar_comps": similar,
        "closest_teams": closest_teams if locked_items else [],
    }
