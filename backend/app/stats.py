"""Statistics on top of the in-memory dataset."""
from collections import Counter, defaultdict
from itertools import combinations

from .dataset import Dataset, Filters
from .db import ROLES
from .strength import champion_spots, global_spots, strength

ROLE_INDEX = {r: i for i, r in enumerate(ROLES)}

MIN_SYNERGY_GAMES = 8
MIN_SYNERGY_SINGLE = 15
MIN_SYNERGY_LIFT = 2.0
MIN_COUNTER_GAMES = 5
MIN_COUNTER_WR = 0.68
MIN_SIGNATURE_GAMES = 15
DURATION_RANGE = (12, 65)  # minutes; Leaguepedia has a few bogus lengths


def winrate(wins: int, decided: int) -> float | None:
    return round(wins / decided, 4) if decided else None


def _div(a, b, digits=2):
    return round(a / b, digits) if b else None


class Tally:
    """Wins / games accumulator."""
    __slots__ = ("games", "wins", "decided")

    def __init__(self):
        self.games = self.wins = self.decided = 0

    def add(self, win):
        self.games += 1
        if win is not None:
            self.decided += 1
            self.wins += bool(win)

    @property
    def wr(self):
        return winrate(self.wins, self.decided)


# --- champions --------------------------------------------------------------

def champion_table(ds: Dataset, f: Filters) -> list[dict]:
    games = ds.filter_games(Filters(**{**f.__dict__, "teams": (), "champion": None}))
    comps = ds.filter_comps(f)
    n_games = len(ds.filter_games(Filters(**{**f.__dict__, "champion": None})))
    game_ids = {g["game_id"] for g in games}
    picks: dict[str, Tally] = defaultdict(Tally)
    roles: dict[str, Counter] = defaultdict(Counter)
    for c in comps:
        for role, champ in c["picks"].items():
            picks[champ].add(c["win"])
            roles[champ][role] += 1
    bans: Counter = Counter()
    for g in ds.games:
        if g["game_id"] in game_ids:
            for s in g["sides"].values():
                if not f.teams or s["team"] in f.teams:
                    bans.update(s["bans"])
    out = []
    for champ in set(picks) | set(bans):
        t = picks.get(champ) or Tally()
        power = strength(t.games, bans[champ], t.wins, t.decided, n_games)
        out.append({
            "champion": champ,
            "picks": t.games,
            "bans": bans[champ],
            "winrate": t.wr,
            "pick_rate": _div(t.games, n_games, 4),
            "ban_rate": _div(bans[champ], n_games, 4),
            "presence": _div(t.games + bans[champ], n_games, 4),
            "roles": dict(roles[champ].most_common()),
            "strength": power,
        })
    out.sort(key=lambda r: (-(r["picks"] + r["bans"]), r["champion"]))
    return out


def _player_agg(comps_roles: list[tuple[dict, str]]) -> dict:
    k = d = a = cs = gold = dmg = 0
    minutes = share_sum = kp_sum = 0.0
    n_stats = n_share = n_kp = 0
    for c, role in comps_roles:
        p = c["players"][role]
        if p["kills"] is None or p["deaths"] is None or p["assists"] is None:
            continue
        k += p["kills"]; d += p["deaths"]; a += p["assists"]
        n_stats += 1
        if c["duration"] and p["cs"] is not None:
            cs += p["cs"]; minutes += c["duration"]
            gold += p["gold"] or 0
            dmg += p["damage"] or 0
        total_dmg = sum((q["damage"] or 0) for q in c["players"].values())
        if total_dmg and p["damage"] is not None:
            share_sum += p["damage"] / total_dmg
            n_share += 1
        team_kills = sum((q["kills"] or 0) for q in c["players"].values())
        if team_kills:
            kp_sum += (p["kills"] + p["assists"]) / team_kills
            n_kp += 1
    return {
        "kda": _div(k + a, d) if d else (None if not n_stats else float(k + a)),
        "kills": _div(k, n_stats), "deaths": _div(d, n_stats), "assists": _div(a, n_stats),
        "cs_min": _div(cs, minutes), "gpm": _div(gold, minutes, 0), "dpm": _div(dmg, minutes, 0),
        "damage_share": _div(share_sum, n_share, 3), "kill_participation": _div(kp_sum, n_kp, 3),
    }


def champion_detail(ds: Dataset, champion: str, f: Filters) -> dict | None:
    base = Filters(**{**f.__dict__, "champion": None, "role": None})
    games = ds.filter_games(Filters(**{**base.__dict__, "teams": ()}))
    comps = ds.filter_comps(base)
    mine = [(c, r) for c in comps for r, ch in c["picks"].items() if ch == champion]
    bans_total = bans_p1 = 0
    ban_by_team: Counter = Counter()
    banned_vs: Counter = Counter()
    ban_orders = []
    game_ids = {g["game_id"] for g in games}
    for g in ds.games:
        if g["game_id"] not in game_ids:
            continue
        for side, s in g["sides"].items():
            for order, b in zip(s["ban_order"], s["bans"]):
                if b == champion and (not f.teams or s["team"] in f.teams):
                    bans_total += 1
                    bans_p1 += order <= 6
                    ban_by_team[s["team"]] += 1
                    banned_vs[g["sides"][3 - side]["team"]] += 1
                    ban_orders.append(order)
    if not mine and not bans_total:
        return None

    overall, by_role, by_side = Tally(), defaultdict(Tally), defaultdict(Tally)
    by_game_no, by_patch, by_team = defaultdict(Tally), defaultdict(Tally), defaultdict(Tally)
    players: dict[str, Tally] = defaultdict(Tally)
    player_rows: dict[str, list] = defaultdict(list)
    slots = []
    durations = []
    for c, role in mine:
        overall.add(c["win"])
        by_role[role].add(c["win"])
        by_side[c["side"]].add(c["win"])
        by_game_no[c["game_number"] or 0].add(c["win"])
        by_patch[c["patch"]].add(c["win"])
        by_team[c["team"]].add(c["win"])
        p = c["players"][role]
        players[p["player"] or "?"].add(c["win"])
        player_rows[p["player"] or "?"].append((c, role))
        slots.append(p["slot"])
        if c["duration"] and DURATION_RANGE[0] <= c["duration"] <= DURATION_RANGE[1]:
            durations.append(c["duration"])

    patch_first = {}
    for g in ds.games:
        patch_first.setdefault(g["patch"], g["date"] or "")
    patch_bans: Counter = Counter()
    patch_games: Counter = Counter()
    for g in ds.filter_games(base):
        patch_games[g["patch"]] += 1
        for s in g["sides"].values():
            if not f.teams or s["team"] in f.teams:
                patch_bans[g["patch"]] += s["bans"].count(champion)

    idx = scoped_indexes(ds, base)
    partners = []
    for (ra, ca, rb, cb), t in idx["pairs"].items():
        if champion in (ca, cb) and t.games >= 2:
            own, other = ((ra, ca), (rb, cb)) if ca == champion else ((rb, cb), (ra, ca))
            partners.append({
                "role": other[0], "champion": other[1], "own_role": own[0], "games": t.games,
                "winrate": t.wr, "lift": pair_lift(idx, (ra, ca), (rb, cb), t.games),
            })
    partners.sort(key=lambda p: (-p["games"], -p["lift"]))

    matchups = []
    for (role, ch), opp in idx["lane"].items():
        if ch != champion:
            continue
        for oc, t in opp.items():
            if t.games >= 2:
                matchups.append({"role": role, "champion": oc, "games": t.games,
                                 "wins": t.wins, "winrate": t.wr})
    matchups.sort(key=lambda m: (-m["games"], m["champion"]))

    n_games = len(ds.filter_games(base))
    return {
        "champion": champion,
        "games_in_scope": n_games,
        "picks": overall.games,
        "bans": bans_total,
        "winrate": overall.wr,
        "pick_rate": _div(overall.games, n_games, 4),
        "ban_rate": _div(bans_total, n_games, 4),
        "presence": _div(overall.games + bans_total, n_games, 4),
        "strength": strength(overall.games, bans_total, overall.wins, overall.decided, n_games),
        "spots": champion_spots(mine),
        "avg_duration": _div(sum(durations), len(durations), 1),
        "avg_pick_slot": _div(sum(slots), len(slots), 1),
        "first_rotation_rate": _div(sum(1 for s in slots if s <= 3), len(slots), 3),
        "ban_phase1_rate": _div(bans_p1, bans_total, 3),
        "avg_ban_order": _div(sum(ban_orders), len(ban_orders), 1),
        "stats": _player_agg(mine),
        "roles": [{"role": r, "games": t.games, "winrate": t.wr}
                  for r, t in sorted(by_role.items(), key=lambda kv: -kv[1].games)],
        "sides": {("blue" if s == 1 else "red"): {"games": t.games, "winrate": t.wr}
                  for s, t in sorted(by_side.items())},
        "by_game_number": [{"game_number": n, "games": t.games, "winrate": t.wr}
                           for n, t in sorted(by_game_no.items()) if n],
        "by_patch": [
            {"patch": p, "games": t.games, "bans": patch_bans[p], "pick_rate": _div(t.games, patch_games[p], 4),
             "ban_rate": _div(patch_bans[p], patch_games[p], 4), "winrate": t.wr}
            for p, t in sorted(by_patch.items(), key=lambda kv: patch_first.get(kv[0], ""))
        ],
        "players": [
            {"player": p, "games": t.games, "winrate": t.wr, **_player_agg(player_rows[p])}
            for p, t in sorted(players.items(), key=lambda kv: -kv[1].games)[:10]
        ],
        "teams": [{"team": tm, "games": t.games, "winrate": t.wr}
                  for tm, t in sorted(by_team.items(), key=lambda kv: -kv[1].games)[:8]],
        "banned_by": [{"team": tm, "bans": n} for tm, n in ban_by_team.most_common(5)],
        "banned_against": [{"team": tm, "bans": n} for tm, n in banned_vs.most_common(5)],
        "partners": partners[:20],
        "matchups": matchups,
    }


# --- pairs / matchups indexes (whole dataset, for highlights and detail pages) -------

def build_indexes(comps: list[dict]) -> dict:
    pairs: dict[tuple, Tally] = defaultdict(Tally)
    singles: Counter = Counter()
    lane: dict[tuple, dict[str, Tally]] = defaultdict(lambda: defaultdict(Tally))
    signature: dict[tuple, Tally] = defaultdict(Tally)
    champ_picks: Counter = Counter()
    for c in comps:
        items = sorted(c["picks"].items(), key=lambda kv: ROLE_INDEX[kv[0]])
        for role, champ in items:
            singles[(role, champ)] += 1
            champ_picks[champ] += 1
            p = c["players"][role]
            if p["player"]:
                signature[(p["player"], champ)].add(c["win"])
            opp = c["enemy"].get(role)
            if opp:
                lane[(role, champ)][opp].add(c["win"])
        for (ra, ca), (rb, cb) in combinations(items, 2):
            pairs[(ra, ca, rb, cb)].add(c["win"])
    return {"pairs": pairs, "singles": singles, "lane": lane, "total": len(comps),
            "signature": signature, "champ_picks": champ_picks}


def indexes(ds: Dataset) -> dict:
    """Indexes over the whole database (used by draft highlights)."""
    if "indexes" not in ds._cache:
        ds._cache["indexes"] = build_indexes(ds.comps_all)
    return ds._cache["indexes"]


def scoped_indexes(ds: Dataset, f: Filters) -> dict:
    """Indexes over the comps matching the filters."""
    key = ("indexes", *f.key())
    if key not in ds._cache:
        ds._cache[key] = build_indexes(ds.filter_comps(f))
    return ds._cache[key]


def pair_lift(idx: dict, a: tuple, b: tuple, games: int) -> float:
    return round(games * idx["total"] / (idx["singles"][a] * idx["singles"][b]), 2)


def pair_stats(
    comps: list[dict], min_games: int = 2, role_a: str | None = None, role_b: str | None = None,
    champion: str | None = None, sort: str = "games", limit: int = 100, min_lift: float = 0.0,
) -> list[dict]:
    """Pairs of champions picked by the same team, keyed by the roles they played.

    lift = P(A and B) / (P(A) * P(B)) over team comps; > 1 means picked together
    more often than chance would predict.
    """
    total = len(comps)
    singles: Counter = Counter()
    pairs: dict[tuple, Tally] = defaultdict(Tally)
    for c in comps:
        items = sorted(c["picks"].items(), key=lambda kv: ROLE_INDEX[kv[0]])
        singles.update(items)
        for (ra, ca), (rb, cb) in combinations(items, 2):
            pairs[(ra, ca, rb, cb)].add(c["win"])
    result = []
    for (ra, ca, rb, cb), t in pairs.items():
        if t.games < min_games:
            continue
        if role_a and role_b:
            if {ra, rb} != {role_a, role_b}:
                continue
        elif (role_a and role_a not in (ra, rb)) or (role_b and role_b not in (ra, rb)):
            continue
        if champion and champion not in (ca, cb):
            continue
        lift = round(t.games * total / (singles[(ra, ca)] * singles[(rb, cb)]), 2)
        if lift < min_lift:
            continue
        result.append({
            "a": {"champion": ca, "role": ra},
            "b": {"champion": cb, "role": rb},
            "games": t.games,
            "pick_rate": _div(t.games, total, 4),
            "winrate": t.wr,
            "lift": lift,
        })
    keys = {
        "games": lambda r: (-r["games"], -r["lift"]),
        "winrate": lambda r: (-(r["winrate"] or 0), -r["games"]),
        "lift": lambda r: (-r["lift"], -r["games"]),
    }
    result.sort(key=keys.get(sort, keys["games"]))
    return result[:limit]


def pair_presence(ds: Dataset, f: Filters, pairs: list[dict]) -> None:
    """Adds a presence score to each pair row (in place), like a champion's pick + ban presence.

    presence = (played + excess bans) / games of the scope, where
      - played: the team picks both champions in the row's roles (the row's `games`);
      - excess bans: games where A or B is banned (so the duo cannot be played as is), beyond what
        the two champions' own ban rates would give if banned independently.
    Bans are compared with chance patch by patch and for Game 1 vs later games (two champions that
    are strong on the same patch are banned together anyway, which is not a counter to the duo).
    So a duo is "present" when it is played, or when teams systematically ban one of its members
    (the "A or B" neutralisation), but not just because both are strong. Games where a member is
    locked by Fearless are left out. With a team filter, only bans made against that team count."""
    if not pairs:
        return
    from .denial import _group, timeline

    games = ds.filter_games(Filters(**{**f.__dict__, "champion": None}))
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    adjacent: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pairs:
        a, b = p["a"]["champion"], p["b"]["champion"]
        key = (min(a, b), max(a, b))
        if not rows[key]:
            adjacent[key[0]].append(key)
            adjacent[key[1]].append(key)
        rows[key].append(p)

    def counts(g: dict, champ: str) -> bool:
        """Is the ban of `champ` in game g one that counts (made against the filtered teams)?"""
        ban = timeline(g)[champ].get("ban")
        return bool(ban) and (not f.teams or g["sides"][ban[0]]["team"] not in f.teams)

    n_cell: Counter = Counter()
    bans: Counter = Counter()  # (champion, cell) -> games where it is banned
    locks: Counter = Counter()  # (champion, cell) -> games where it is locked by Fearless
    observed: Counter = Counter()  # (duo, cell) -> games with a member banned
    excluded: Counter = Counter()  # (duo, cell) -> games left out (a member is locked)
    for g in games:
        cell = (g.get("patch"), _group(g["game_number"]))
        n_cell[cell] += 1
        tl = timeline(g)
        banned = {c for c in tl if counts(g, c)}
        for c in banned:
            bans[(c, cell)] += 1
        for c in g["locked"]:
            locks[(c, cell)] += 1
        locked_duos = {k for c in g["locked"] for k in adjacent.get(c, ())}
        for k in locked_duos:
            excluded[(k, cell)] += 1
        for k in {k for c in banned for k in adjacent.get(c, ())} - locked_duos:
            observed[(k, cell)] += 1

    cells = list(n_cell)
    n_games = len(games)
    for key, duo_rows in rows.items():
        a, b = key
        seen = expected = 0.0
        for cell in cells:
            eligible = n_cell[cell] - excluded[(key, cell)]
            if eligible <= 0:
                continue
            seen += observed[(key, cell)]
            rate_a = bans[(a, cell)] / max(1, n_cell[cell] - locks[(a, cell)])
            rate_b = bans[(b, cell)] / max(1, n_cell[cell] - locks[(b, cell)])
            expected += eligible * (1 - (1 - rate_a) * (1 - rate_b))
        excess = max(0.0, seen - expected)
        for p in duo_rows:
            p["bans"] = {"either": int(seen), "expected": round(expected, 1)}
            p["countered"] = round(excess, 1)
            p["presence"] = _div(p["games"] + excess, n_games, 4)
            p["presence_played"] = _div(p["games"], n_games, 4)
            p["presence_countered"] = _div(excess, n_games, 4)
            p["realised_share"] = _div(p["games"], p["games"] + excess, 4)


def duo_detail(ds: Dataset, kind: str, a: str, b: str, f: Filters) -> dict:
    """Stats of a synergy pair (same side) or of a head-to-head matchup."""
    comps = ds.filter_comps(Filters(**{**f.__dict__, "champion": None, "role": None}))
    t = Tally()
    roles: Counter = Counter()
    for c in comps:
        picks = c["picks"]
        if kind == "synergy":
            ra = next((r for r, ch in picks.items() if ch == a), None)
            rb = next((r for r, ch in picks.items() if ch == b), None)
            if ra and rb:
                t.add(c["win"])
                roles[(ra, rb)] += 1
        else:
            ra = next((r for r, ch in picks.items() if ch == a), None)
            rb = next((r for r, ch in c["enemy"].items() if ch == b), None)
            if ra and rb:
                t.add(c["win"])
                roles[(ra, rb)] += 1
    return {
        "kind": kind, "a": a, "b": b, "games": t.games, "wins": t.wins, "winrate": t.wr,
        "roles": [{"a_role": ra, "b_role": rb, "games": n} for (ra, rb), n in roles.most_common()],
    }


# --- draft highlights -----------------------------------------------------------

def game_highlights(ds: Dataset, g: dict, per_side: int = 4) -> list[dict]:
    """Notable facts about a draft, strongest first: proven synergies, lane counters,
    signature picks and very rare picks (at most `per_side` per side)."""
    idx = indexes(ds)
    out: list[dict] = []
    for side in (1, 2):
        s, o = g["sides"][side], g["sides"][3 - side]
        found: list[tuple[float, dict]] = []
        picks = sorted(s["picks"], key=lambda p: ROLE_INDEX[p["role"]])
        for pa, pb in combinations(picks, 2):
            a, b = (pa["role"], pa["champion"]), (pb["role"], pb["champion"])
            t = idx["pairs"].get((*a, *b))
            if (not t or t.games < MIN_SYNERGY_GAMES
                    or min(idx["singles"][a], idx["singles"][b]) < MIN_SYNERGY_SINGLE):
                continue
            lift = pair_lift(idx, a, b, t.games)
            if lift >= MIN_SYNERGY_LIFT:
                found.append((min(lift, 6) / 6 + t.games / 40, {
                    "type": "synergy", "side": side, "champions": [pa["champion"], pb["champion"]],
                    "text": f"Synergie {pa['champion']} + {pb['champion']}",
                    "detail": f"{t.games} parties ensemble, {round((t.wr or 0) * 100)}% WR, lift {lift}",
                }))
        enemy_by_role = {p["role"]: p["champion"] for p in o["picks"]}
        for p in picks:
            opp = enemy_by_role[p["role"]]
            t = idx["lane"].get((p["role"], p["champion"]), {}).get(opp)
            if t and t.games >= MIN_COUNTER_GAMES and (t.wr or 0) >= MIN_COUNTER_WR:
                found.append(((t.wr or 0) + t.games / 30, {
                    "type": "counter", "side": side, "champions": [p["champion"], opp],
                    "text": f"{p['champion']} counter {opp}",
                    "detail": f"{t.wins}-{t.games - t.wins} en lane {p['role']} ({round((t.wr or 0) * 100)}% WR)",
                }))
            sig = idx["signature"].get((p["player"], p["champion"])) if p["player"] else None
            if sig and sig.games >= MIN_SIGNATURE_GAMES:
                found.append((sig.games / 40, {
                    "type": "signature", "side": side, "champions": [p["champion"]],
                    "text": f"Signature de {p['player']}",
                    "detail": f"{sig.games} parties, {round((sig.wr or 0) * 100)}% WR",
                }))
            if idx["champ_picks"][p["champion"]] <= 3:
                found.append((0.3, {
                    "type": "rare", "side": side, "champions": [p["champion"]],
                    "text": f"Pick rare : {p['champion']}",
                    "detail": f"{idx['champ_picks'][p['champion']]} pick(s) en tout dans la base",
                }))
        found.sort(key=lambda x: -x[0])
        out += [h for _, h in found[:per_side]]
    return out


# --- records ----------------------------------------------------------------------

def records(ds: Dataset, f: Filters, limit: int = 8) -> list[dict]:
    comps = ds.filter_comps(f)
    games = ds.filter_games(f)
    sections = []

    def section(sid, title, description, items):
        if items:
            sections.append({"id": sid, "title": title, "description": description, "items": items[:limit]})

    pick_t: dict[str, Tally] = defaultdict(Tally)
    side_t: dict[tuple, Tally] = defaultdict(Tally)
    role_t: dict[tuple, Tally] = defaultdict(Tally)
    for c in comps:
        for role, champ in c["picks"].items():
            pick_t[champ].add(c["win"])
            side_t[(champ, c["side"])].add(c["win"])
            role_t[(champ, role)].add(c["win"])

    def pct(v):
        return f"{round((v or 0) * 100)}%"

    table = [r for r in champion_table(ds, f) if r["picks"] + r["bans"] >= 20]
    section("strongest", "Champions les plus forts",
            "Indice de force : présence (picks + bans) et winrate ajusté, 20 picks/bans minimum.", [
        {"champions": [r["champion"]], "label": r["champion"],
         "value": f"{r['strength']['tier']} · {r['strength']['score']}",
         "sub": f"présence {pct(r['presence'])} · WR ajusté {pct(r['strength']['wr_adjusted'])}"}
        for r in sorted(table, key=lambda r: -r["strength"]["score"])
    ])
    section("spots", "Spots où un champion surperforme",
            "Contexte (side, game de la série, moment de la draft, rôle) où le winrate dépasse nettement "
            "celui habituel du champion (z ≥ 1,64, 12 parties minimum).", [
        {"champions": [s["champion"]], "label": f"{s['champion']} — {s['label']}",
         "value": pct(s["winrate"]),
         "sub": f"{s['games']} parties · {pct(s['baseline'])} d'habitude (+{round(s['delta'] * 100)} pts) · z {s['z']}"}
        for s in global_spots(comps)
    ])

    solid = [(ch, t) for ch, t in pick_t.items() if t.decided >= 15]
    section("best_wr", "Meilleurs winrates", "Champions avec au moins 15 parties.", [
        {"champions": [ch], "label": ch, "value": pct(t.wr), "sub": f"{t.games} parties"}
        for ch, t in sorted(solid, key=lambda kv: (-(kv[1].wr or 0), -kv[1].games))
    ])
    section("worst_wr", "Pires winrates", "Champions avec au moins 15 parties.", [
        {"champions": [ch], "label": ch, "value": pct(t.wr), "sub": f"{t.games} parties"}
        for ch, t in sorted(solid, key=lambda kv: ((kv[1].wr or 0), -kv[1].games))
    ])
    section("perfect", "Séries sans défaite", "Champions invaincus (au moins 5 parties).", [
        {"champions": [ch], "label": ch, "value": f"{t.wins}-0", "sub": "100% WR"}
        for ch, t in sorted(pick_t.items(), key=lambda kv: -kv[1].wins)
        if t.decided >= 5 and t.wins == t.decided
    ])
    imbalance = []
    for ch in pick_t:
        b, r = side_t.get((ch, 1)), side_t.get((ch, 2))
        if b and r and b.decided >= 8 and r.decided >= 8:
            imbalance.append((ch, b, r, (b.wr or 0) - (r.wr or 0)))
    section("side", "Dépendance au side", "Plus grand écart de winrate Blue / Red (8 parties minimum par side).", [
        {"champions": [ch], "label": ch, "value": f"{'+' if d > 0 else ''}{round(d * 100)} pts",
         "sub": f"Blue {pct(b.wr)} ({b.games}) · Red {pct(r.wr)} ({r.games})"}
        for ch, b, r, d in sorted(imbalance, key=lambda x: -abs(x[3]))
    ])
    flex = []
    for ch in pick_t:
        played = [(r, role_t[(ch, r)]) for r in ROLES if role_t.get((ch, r)) and role_t[(ch, r)].games >= 4]
        if len(played) >= 2:
            flex.append((ch, played))
    section("flex", "Picks flex", "Champions joués dans plusieurs rôles (4 parties minimum dans chacun).", [
        {"champions": [ch], "label": ch, "value": f"{len(pl)} rôles",
         "sub": " · ".join(f"{r} {t.games}" for r, t in pl)}
        for ch, pl in sorted(flex, key=lambda x: -sum(t.games for _, t in x[1]))
    ])

    # player specialists
    pc: dict[tuple, Tally] = defaultdict(Tally)
    pgames: Counter = Counter()
    for c in comps:
        for role, p in c["players"].items():
            if p["player"]:
                pc[(p["player"], p["champion"], role)].add(c["win"])
                pgames[p["player"]] += 1
    section("signature", "Picks signature", "Joueur + champion les plus joués ensemble.", [
        {"champions": [ch], "label": f"{pl} — {ch}", "value": f"{t.games} parties",
         "sub": f"{pct(t.wr)} WR · {pct(t.games / pgames[pl])} de ses games"}
        for (pl, ch, _), t in sorted(pc.items(), key=lambda kv: -kv[1].games)
    ])
    section("onetrick", "One-tricks", "Part la plus élevée des games d'un joueur sur un même champion (8 parties minimum).", [
        {"champions": [ch], "label": f"{pl} — {ch}", "value": pct(t.games / pgames[pl]),
         "sub": f"{t.games}/{pgames[pl]} parties · {pct(t.wr)} WR"}
        for (pl, ch, _), t in sorted(pc.items(), key=lambda kv: -(kv[1].games / pgames[kv[0][0]]))
        if t.games >= 8
    ])

    # single-game performances
    perf = []
    for c in comps:
        for role, p in c["players"].items():
            if p["kills"] is not None:
                perf.append((c, role, p))
    section("kills", "Plus de kills sur une partie", "Meilleures performances individuelles.", [
        {"champions": [p["champion"]], "label": f"{p['player']} — {p['champion']}",
         "value": f"{p['kills']}/{p['deaths']}/{p['assists']}", "sub": f"vs {c['opponent']} · {(c['date'] or '')[:10]}",
         "game_id": c["game_id"]}
        for c, _, p in sorted(perf, key=lambda x: -(x[2]["kills"] or 0))
    ])
    section("kda", "Meilleur KDA (0 mort)", "Parties sans mort avec le plus d'assistances + kills.", [
        {"champions": [p["champion"]], "label": f"{p['player']} — {p['champion']}",
         "value": f"{p['kills']}/0/{p['assists']}", "sub": f"vs {c['opponent']} · {(c['date'] or '')[:10]}",
         "game_id": c["game_id"]}
        for c, _, p in sorted((x for x in perf if x[2]["deaths"] == 0), key=lambda x: -((x[2]["kills"] or 0) + (x[2]["assists"] or 0)))
    ])
    timed = [g for g in games if g["duration"] and DURATION_RANGE[0] <= g["duration"] <= DURATION_RANGE[1]]
    def game_item(g, value):
        return {"champions": [], "label": f"{g['team1']} vs {g['team2']}", "value": value,
                "sub": f"{(g['date'] or '')[:10]} · {g['tournament']}", "game_id": g["game_id"]}
    section("longest", "Parties les plus longues", "", [
        game_item(g, f"{g['duration']:.0f} min") for g in sorted(timed, key=lambda g: -g["duration"])])
    section("shortest", "Parties les plus courtes", "", [
        game_item(g, f"{g['duration']:.0f} min") for g in sorted(timed, key=lambda g: g["duration"])])

    # strongest synergy / counters within the selected scope
    idx = scoped_indexes(ds, f)
    syn = [(k, t, pair_lift(idx, (k[0], k[1]), (k[2], k[3]), t.games)) for k, t in idx["pairs"].items()
           if t.games >= 8 and min(idx["singles"][(k[0], k[1])], idx["singles"][(k[2], k[3])]) >= MIN_SYNERGY_SINGLE]
    section("synergy", "Synergies les plus marquées",
            "Paires picks ensemble bien plus que le hasard (lift) — 8 parties minimum, chaque champion ≥ 15 picks.", [
        {"champions": [k[1], k[3]], "label": f"{k[1]} + {k[3]}", "value": f"lift {lift}",
         "sub": f"{t.games} parties · {pct(t.wr)} WR", "link": "synergy"}
        for k, t, lift in sorted(syn, key=lambda x: -x[2])
    ])
    counters = []
    for (role, ch), opp in idx["lane"].items():
        for oc, t in opp.items():
            if t.decided >= 8:
                counters.append((role, ch, oc, t))
    section("counters", "Counters de lane", "Meilleurs winrates en face-à-face direct (8 parties minimum).", [
        {"champions": [ch, oc], "label": f"{ch} vs {oc}", "value": f"{t.wins}-{t.games - t.wins}",
         "sub": f"{role} · {pct(t.wr)} WR", "link": "counter"}
        for role, ch, oc, t in sorted(counters, key=lambda x: (-(x[3].wr or 0), -x[3].games))
    ])
    return sections
