"""Champion strength and "spots" where a champion over- or under-performs."""
from math import sqrt

# Prior for the shrunk winrate: 10 virtual wins and 10 virtual losses.
PRIOR_GAMES = 20
MIN_TIER_GAMES = 10  # picks + bans needed before a champion gets a tier
GLOBAL_DIMENSIONS = ("Side", "Game de la série", "Moment de la draft", "Rôle")


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson confidence interval of a winrate."""
    if not n:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def shrunk_winrate(wins: int, decided: int) -> float:
    return (wins + PRIOR_GAMES / 2) / (decided + PRIOR_GAMES)


# Reliability of a head-to-head record: (level, minimum |z| against 50 %, minimum games).
# The games floor matters because the normal approximation is poor on tiny samples (3-0 is only 1 chance in 8).
MATCHUP_LEVELS = ("strong", 2.33, 8), ("likely", 1.64, 6), ("hint", 1.28, 5)


def matchup_verdict(wins: int, games: int) -> dict:
    """Is a head-to-head record clearly favourable or difficult, or just inconclusive?

    z is the binomial z-score of the winrate against 50 % (the two champions are in the same lane, so
    an even matchup wins half of the time). `verdict` is None while the record could be luck."""
    z = (2 * wins - games) / sqrt(games) if games else 0.0
    level = next((key for key, z_min, n_min in MATCHUP_LEVELS if abs(z) >= z_min and games >= n_min), None)
    return {"z": round(z, 2), "level": level, "verdict": None if level is None else "favorable" if z > 0 else "difficult"}


def _tier(score: float) -> str:
    return "S" if score >= 70 else "A" if score >= 55 else "B" if score >= 40 else "C" if score >= 25 else "D"


def strength(picks: int, bans: int, wins: int, decided: int, n_games: int) -> dict:
    """How strong a champion looks in pro play, from how contested it is and how it performs.

    - presence: share of games where it is picked or banned (teams fear it or want it);
    - wr_adjusted: winrate shrunk toward 50%, so 3-0 does not beat 60-40;
    - score: 60% presence (50% presence = max) + 40% adjusted winrate (40% -> 0, 60% -> max);
    - wr_low / wr_high: 95% interval; wr_low > 0.5 means the edge is statistically real.
    """
    presence = (picks + bans) / n_games if n_games else 0
    wr_adj = shrunk_winrate(wins, decided)
    score = 100 * (0.6 * min(presence / 0.5, 1) + 0.4 * min(max((wr_adj - 0.40) / 0.20, 0), 1))
    ci = wilson(wins, decided)
    return {
        "score": round(score),
        "tier": _tier(score) if picks + bans >= MIN_TIER_GAMES else None,
        "wr_adjusted": round(wr_adj, 4),
        "wr_low": round(ci[0], 4) if ci else None,
        "wr_high": round(ci[1], 4) if ci else None,
        "significant_edge": bool(ci and ci[0] > 0.5),
    }


def _slot_bucket(slot: int) -> str:
    return "Picks 1-3" if slot <= 3 else "Picks 4-6" if slot <= 6 else "Picks 7-10"


def _spot_keys(c: dict, role: str, dims: tuple[str, ...] | None):
    """(dimension, label, link) for every context a pick belongs to."""
    p = c["players"][role]
    keys = [("Side", "Blue" if c["side"] == 1 else "Red", None)]
    gn = c["game_number"]
    if gn:
        keys.append(("Game de la série", f"Game {gn}" if gn < 4 else "Game 4+", None))
    if p["slot"]:
        keys.append(("Moment de la draft", _slot_bucket(p["slot"]), None))
    keys.append(("Rôle", role, None))
    if dims is None:
        keys.append(("Format", "Fearless" if c["fearless"] else "Draft classique", None))
        keys.append(("Patch", c["patch"], None))
        keys.append(("Équipe", c["team"], None))
        if p["player"]:
            keys.append(("Joueur", p["player"], None))
    return [k for k in keys if dims is None or k[0] in dims]


def _z(p: float, p0: float, n: int) -> float:
    p0 = min(max(p0, 0.25), 0.75)
    return (p - p0) / sqrt(p0 * (1 - p0) / n)


def _segment(dim, label, link, games, wins, baseline, z):
    return {
        "dimension": dim, "label": label, "games": games, "wins": wins,
        "winrate": round(wins / games, 4), "baseline": round(baseline, 4),
        "delta": round(wins / games - baseline, 4), "z": round(z, 2), "link": link,
    }


def champion_spots(mine: list[tuple[dict, str]], min_games: int = 8, z_min: float = 1.28, limit: int = 6) -> dict:
    """Contexts where a champion's winrate departs from its own overall winrate.

    `mine` = [(comp, role)] for every pick of the champion. z is a one-sided binomial z-score
    against the overall winrate (1.28 ~ 90%, 1.64 ~ 95%, 2.33 ~ 99%). With dozens of contexts
    tested, a few mild hits are expected by chance: trust high z and larger samples.
    """
    decided = [(c, r) for c, r in mine if c["win"] is not None]
    total = len(decided)
    if total < 2 * min_games:
        return {"baseline": None, "hot": [], "cold": []}
    baseline = sum(bool(c["win"]) for c, _ in decided) / total
    tallies: dict[tuple, list] = {}
    for c, role in decided:
        for dim, label, link in _spot_keys(c, role, None):
            t = tallies.setdefault((dim, label), [0, 0, link])
            t[0] += 1
            t[1] += bool(c["win"])
    segments = []
    for (dim, label), (games, wins, link) in tallies.items():
        if games < min_games or games == total:
            continue
        segments.append(_segment(dim, label, link, games, wins, baseline, _z(wins / games, baseline, games)))
    sides = [s for s in segments if s["dimension"] == "Side"]
    if len(sides) == 2:  # Blue above average means Red below it: one finding, not two
        segments.remove(min(sides, key=lambda s: abs(s["z"])))
    hot = sorted((s for s in segments if s["z"] >= z_min), key=lambda s: -s["z"])[:limit]
    cold = sorted((s for s in segments if s["z"] <= -z_min), key=lambda s: s["z"])[:limit]
    return {"baseline": round(baseline, 4), "hot": hot, "cold": cold}


def global_spots(comps: list[dict], min_games: int = 12, z_min: float = 1.64) -> list[dict]:
    """Most striking over-performance of any champion in a simple context (side, game, role...)."""
    base: dict[str, list[int]] = {}
    seg: dict[tuple, list[int]] = {}
    for c in comps:
        if c["win"] is None:
            continue
        for role, champ in c["picks"].items():
            b = base.setdefault(champ, [0, 0])
            b[0] += 1
            b[1] += bool(c["win"])
            for dim, label, _ in _spot_keys(c, role, GLOBAL_DIMENSIONS):
                s = seg.setdefault((champ, dim, label), [0, 0])
                s[0] += 1
                s[1] += bool(c["win"])
    out = []
    for (champ, dim, label), (games, wins) in seg.items():
        total, total_wins = base[champ]
        if games < min_games or games == total or total < 2 * min_games:
            continue
        baseline = total_wins / total
        z = _z(wins / games, baseline, games)
        if z >= z_min:
            out.append({"champion": champ, **_segment(dim, label, None, games, wins, baseline, z)})
    out.sort(key=lambda s: -s["z"])
    return out
