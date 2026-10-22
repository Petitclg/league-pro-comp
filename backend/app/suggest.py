"""Comp suggestions: complete a partial draft so it resembles winning pro comps."""
from collections import Counter, defaultdict

from .db import ROLES
from .stats import Tally


def _synergies(matches: list[dict], role: str, champ: str, locked_items: list[tuple[str, str]]) -> list[dict]:
    """Winrate of `champ` together with each locked champion (`matches` already share a locked slot)."""
    out = []
    for lr, lc in locked_items:
        t = Tally()
        for c in matches:
            picks = c["picks"]
            if picks.get(role) == champ and picks.get(lr) == lc:
                t.add(c["win"])
        if t.games:
            out.append({"role": lr, "champion": lc, "games": t.games, "winrate": t.wr})
    out.sort(key=lambda s: -s["games"])
    return out


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
) -> dict:
    """Every pro comp sharing m >= 1 (role, champion) slots with `locked` votes for the
    champions it played in the open roles, with weight m^2 (wins count a bit more than
    losses). With nothing locked, every comp votes equally. When enemy picks are known,
    each candidate is then scaled by its smoothed winrate against them (0.5x to 1.5x).
    """
    enemy = [e for e in (enemy or []) if e]
    unavailable = set(unavailable or ()) | set(locked.values()) | set(enemy)
    open_roles = [r for r in ROLES if r not in locked]
    locked_items = list(locked.items())

    votes: dict[str, Counter] = {r: Counter() for r in open_roles}
    support: dict[tuple[str, str], Tally] = defaultdict(Tally)
    total_weight = 0.0
    matches = []
    for c in comps:
        m = sum(1 for r, ch in locked_items if c["picks"].get(r) == ch)
        if locked_items and m == 0:
            continue
        weight = float(m * m) if locked_items else 1.0
        if c["win"] is True:
            weight *= 1.25
        elif c["win"] is False:
            weight *= 0.75
        total_weight += weight
        matches.append((m, c))
        for r in open_roles:
            champ = c["picks"].get(r)
            if champ:
                votes[r][champ] += weight
                support[(r, champ)].add(c["win"])

    counters = _counter_scores(comps, enemy)
    matched = [c for _, c in matches]
    suggestions: dict[str, list[dict]] = {}
    for r in open_roles:
        ranked = []
        for champ, score in votes[r].items():
            if champ in unavailable:
                continue
            share = score / total_weight if total_weight else 0
            cwr, cgames = counters.get((r, champ), (None, 0))
            ranked.append({
                "champion": champ,
                "score": round(share * (0.5 + cwr) if cwr is not None else share, 4),
                "share": round(share, 4),
                "games": support[(r, champ)].games,
                "winrate": support[(r, champ)].wr,
                "counter_winrate": None if cwr is None else round(cwr, 3),
                "counter_games": cgames,
            })
        ranked.sort(key=lambda s: -s["score"])
        suggestions[r] = ranked[:top_n]
        # Synergies are only worth computing for the candidates actually returned.
        for s in suggestions[r]:
            s["synergies"] = _synergies(matched, r, s["champion"], locked_items)

    matches.sort(key=lambda mc: mc[1]["date"] or "", reverse=True)
    matches.sort(key=lambda mc: (-mc[0], mc[1]["win"] is not True))
    banned = unavailable - set(locked.values())
    similar = [
        {
            "match": m, "game_id": c["game_id"], "team": c["team"], "opponent": c["opponent"],
            "win": c["win"], "date": c["date"], "game_number": c["game_number"], "picks": c["picks"],
        }
        for m, c in matches
        if c["picks"].keys() == set(ROLES) and not (set(c["picks"].values()) & banned)
    ][:similar_n]

    teams: dict[str, dict] = {}
    for m, c in matches:
        t = teams.setdefault(c["team"], {"team": c["team"], "similarity": 0.0, "best_match": 0, "games": 0})
        t["similarity"] += m
        t["best_match"] = max(t["best_match"], m)
        t["games"] += 1
    closest_teams = sorted(
        ({**t, "similarity": round(t["similarity"] / t["games"], 2)} for t in teams.values()),
        key=lambda t: (-t["best_match"], -t["similarity"], -t["games"]),
    )[:5]

    return {
        "locked": locked,
        "matching_comps": len(matches),
        "suggestions": suggestions,
        "similar_comps": similar,
        "closest_teams": closest_teams if locked_items else [],
    }
