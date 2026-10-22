"""Items, runes and summoner spells a champion was played with by the pros, favouring games that
resemble the current draft (same allies, same enemies, recent patches)."""
from collections import Counter

from .suggest import Params, _base_weights

# Items and runes change with the patch: weigh by patch distance rather than by days.
WEIGHTS = Params(half_life_days=None, patch_decay=0.6)
CORE_SIZE = 3
OPTION_SIZE = 6
BOOTS_SIZE = 2
MIN_PAGE_SHARE = 0.15  # a second rune page must come from this share of the games
RUNES_PER_PAGE = 9  # keystone, 3 primary, 2 secondary, 3 shards (Leaguepedia order)


def _top(group: list[tuple[float, dict]], key) -> tuple[object, list[tuple[float, dict]]]:
    """The heaviest value of `key` in `group` and the members that have it."""
    tally: Counter = Counter()
    for w, lo in group:
        tally[key(lo)] += w
    best = tally.most_common(1)[0][0]
    return best, [(w, lo) for w, lo in group if key(lo) == best]


def _rune_pages(pool: list[tuple[float, dict]], perks: dict, styles: dict) -> list[dict]:
    """Up to two coherent pages: for the most played keystones, the most played primary runes with it,
    then secondary runes and shards given those (each page therefore exists in a real game)."""
    pool = [(w, lo) for w, lo in pool if lo["keystone"] and len(lo["runes"]) == RUNES_PER_PAGE]
    if perks:  # a rune the game no longer has cannot be imported
        pool = [(w, lo) for w, lo in pool if all(n in perks for n in lo["runes"])]
    total = sum(w for w, _ in pool)
    by_keystone: Counter = Counter()
    for w, lo in pool:
        by_keystone[lo["keystone"]] += w
    pages = []
    for keystone, weight in by_keystone.most_common(2):
        if pages and weight / total < MIN_PAGE_SHARE:
            break
        group = [(w, lo) for w, lo in pool if lo["keystone"] == keystone]
        (primary_tree, primary), group = _top(group, lambda lo: (lo["primary_tree"], lo["runes"][1:4]))
        (secondary_tree, secondary), group = _top(group, lambda lo: (lo["secondary_tree"], lo["runes"][4:6]))
        shards, group = _top(group, lambda lo: lo["runes"][6:9])
        names = [keystone, *primary, *secondary, *shards]
        ids = [perks.get(n) for n in names]
        pages.append({
            "keystone": keystone, "primary_tree": primary_tree, "secondary_tree": secondary_tree,
            "runes": names, "ids": ids if all(ids) else None,
            "primary_style_id": styles.get(primary_tree), "sub_style_id": styles.get(secondary_tree),
            "share": round(weight / total, 3),
        })
    return pages


def champion_build(
    comps: list[dict],
    champion: str,
    role: str,
    locked: dict[str, str] | None = None,
    enemy: list[str] | None = None,
    catalog: dict | None = None,
    params: Params = WEIGHTS,
) -> dict:
    """The pros' loadout for `champion` in `role`. Each game they played it counts (1 + number of
    `locked` allies and `enemy` champions it shares with the current draft) times its recency weight.
    `catalog` (see `gamedata`) gives item ids and keeps only items that still exist."""
    locked = {r: c for r, c in (locked or {}).items() if r != role}
    foes = set(enemy or ())
    catalog = catalog or {}
    items, perks, styles = catalog.get("items") or {}, catalog.get("perks") or {}, catalog.get("styles") or {}

    rows: list[tuple[float, int, dict]] = []  # weight, similarity, loadout
    for c, base in zip(comps, _base_weights(comps, params)):
        lo = c.get("loadouts", {}).get(role)
        if lo is None or c["picks"].get(role) != champion:
            continue
        similar = sum(c["picks"].get(r) == ch for r, ch in locked.items()) + len(foes & set(c["enemy"].values()))
        rows.append((base * (1 + similar), similar, lo))
    result = {"champion": champion, "role": role, "games": len(rows),
              "similar_games": sum(1 for _, s, _ in rows if s), "item_blocks": [], "runes": [], "spells": []}
    if not rows:
        return result

    final: Counter = Counter()
    boots: Counter = Counter()
    trinkets: Counter = Counter()
    spells: Counter = Counter()
    total = sum(w for w, _, _ in rows)
    for w, _, lo in rows:
        for name in set(lo["items"]):
            entry = items.get(name.casefold())
            if entry and entry["final"]:
                final[entry["name"]] += w
            elif entry and entry["boots"]:
                boots[entry["name"]] += w
        if lo["trinket"] and lo["trinket"].casefold() in items:
            trinkets[items[lo["trinket"].casefold()]["name"]] += w
        if len(lo["spells"]) == 2:
            spells[tuple(sorted(lo["spells"]))] += w

    def block(label: str, counter: Counter, start: int, size: int) -> dict | None:
        picked = counter.most_common(start + size)[start:]
        if not picked:
            return None
        return {"type": label, "items": [
            {"name": n, "id": items[n.casefold()]["id"], "rate": round(w / total, 3)} for n, w in picked
        ]}

    blocks = [
        block("Core build", final, 0, CORE_SIZE),
        block("Boots", boots, 0, BOOTS_SIZE),
        block("Situational", final, CORE_SIZE, OPTION_SIZE),
        block("Trinket", trinkets, 0, 1),
    ]
    result["item_blocks"] = [b for b in blocks if b]
    result["runes"] = _rune_pages([(w, lo) for w, _, lo in rows], perks, styles)
    result["spells"] = list(spells.most_common(1)[0][0]) if spells else []
    return result
