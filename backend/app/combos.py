"""How often a duo is played when it can be played.

A duo (A in role ra, B in role rb) is looked at for a team that picks one member. The decision
that matters is the team's pick for the partner's role: if the partner is still available then
(not banned, not taken by the opponent, not locked by Fearless), the duo is **open** and the team
either takes it (realised) or picks another champion there (unused). If the partner is gone before
the team fills that role, the opponent countered the duo, which is no choice of the team:
  - taken:  the opponent picked the partner before the team filled its role;
  - banned: the partner was banned before the team filled its role.
open = realised + unused; rate = realised / open. A partner already unavailable when the team picks
the first member, one the team plays in another role, or a role already filled, are left out.
"""
from bisect import bisect_left
from collections import defaultdict

from .denial import _pick_time, timeline

Slot = tuple[str, str]  # (role, champion)


class ComboIndex:
    """Open / realised counts of duos over `comps` (a list of team comps in a fixed order)."""

    def __init__(self, by_id: dict[str, dict], comps: list[dict]):
        self.by_id = by_id
        self.comps = comps
        self.by_slot: dict[Slot, list[int]] = defaultdict(list)
        for i, c in enumerate(comps):
            for role, champ in c["picks"].items():
                self.by_slot[(role, champ)].append(i)
        self._memo: dict[tuple, dict] = {}

    def stats(self, a: Slot, b: Slot, limit: int | None = None) -> dict:
        """Counts for the duo over the first `limit` comps (all of them by default)."""
        a, b = sorted((a, b))
        key = (a, b, limit)
        if key not in self._memo:
            self._memo[key] = self._compute(a, b, limit)
        return self._memo[key]

    def _slice(self, slot: Slot, limit: int | None) -> list[int]:
        ids = self.by_slot.get(slot, [])
        return ids if limit is None else ids[:bisect_left(ids, limit)]

    def _compute(self, a: Slot, b: Slot, limit: int | None) -> dict:
        out = {"open": 0, "realised": 0, "taken": 0, "banned": 0, "unused": 0, "wins": 0, "decided": 0}
        for i in sorted(set(self._slice(a, limit)) | set(self._slice(b, limit))):
            c = self.comps[i]
            g = self.by_id[c["game_id"]]
            if a[1] in g["locked"] or b[1] in g["locked"]:
                continue
            picks, players = c["picks"], c["players"]
            has_a, has_b = picks.get(a[0]) == a[1], picks.get(b[0]) == b[1]
            if has_a and has_b:
                out["open"] += 1
                out["realised"] += 1
                if c["win"] is not None:
                    out["decided"] += 1
                    out["wins"] += bool(c["win"])
                continue
            mine, other = (a, b) if has_a else (b, a)
            t = _pick_time(players[mine[0]]["slot"])
            decision = _pick_time(players[other[0]]["slot"])  # when the team fills the partner's role
            if decision < t:
                continue  # the partner's role was already filled
            partner = timeline(g).get(other[1], {})
            pick, ban = partner.get("pick"), partner.get("ban")
            if (ban and ban[1] < t) or (pick and (pick[0] == c["side"] or pick[1] < t)):
                continue  # not available when the first member was picked, or played in another role
            if pick and pick[1] < decision:
                out["taken"] += 1
            elif ban and ban[1] < decision:
                out["banned"] += 1
            else:
                out["open"] += 1
                out["unused"] += 1
        out["rate"] = round(out["realised"] / out["open"], 4) if out["open"] else None
        out["winrate"] = round(out["wins"] / out["decided"], 4) if out["decided"] else None
        return out


def combo_index(ds, key: tuple, comps: list[dict]) -> ComboIndex:
    """The index of `comps`, kept on the dataset under `key` (the scope they come from)."""
    cache_key = ("combo-index", *key)
    if cache_key not in ds._cache:
        ds._cache[cache_key] = ComboIndex(ds.by_id, comps)
    return ds._cache[cache_key]
