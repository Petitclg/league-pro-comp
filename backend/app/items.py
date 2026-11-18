"""Item analyses of solo queue games (REQ-ITEM-1 to 9): which completed items a champion buys, in
which order and when, its build paths, and how the games went compared with what was expected.

Builds are rebuilt at each request from the item events stored in soloq.db, and only for the games
of the chosen champions. The Data Dragon item catalogue of each patch is the only thing kept between
requests: in memory, and on disk (`ddragon/` in the data folder) so that it works offline once loaded.

A purchase is caused by the state of the game (a team ahead buys earlier and more), so a winrate is
never the effect of an item: each one is compared with the winrate expected for the same champion,
slot and purchase time, and its reliability is given (see `_contrast`).
"""
import json
import sqlite3
import threading
import zlib
from collections import Counter, defaultdict
from typing import NamedTuple

import httpx

from . import soloq
from .config import DATA_DIR
from .dataset import Dataset, Filters
from .strength import PRIOR_GAMES, _z, wilson

DDRAGON = "https://ddragon.leagueoflegends.com"
DDRAGON_DIR = DATA_DIR / "ddragon"
ddragon_transport: httpx.BaseTransport | None = None  # tests serve Data Dragon from memory
SUMMONERS_RIFT = "11"
KEPT_FIELDS = ("name", "gold", "maps", "inStore", "consumed", "requiredChampion", "requiredAlly", "into", "from", "tags")

MAX_CHAMPIONS = 10
MAX_SLOTS = 6
MIN_COMPLETED_GOLD = 1500  # keeps Mejai's Soulstealer, the cheapest finished item (see docs/architecture.md)
CHUNK_GAMES = 500  # games read and decoded together
HISTOGRAM_STEP = 2  # minutes per histogram bar
HISTOGRAM_LAST = 40  # the last bar holds every purchase from 40 minutes on
WINDOW = 5  # minutes: purchase time cells of the expected winrate
WINDOW_LAST = 35  # the last cell holds every purchase from 35 minutes on
PATH_LENGTHS = (2, 3)
PATH_LIMIT = 10
COMPARABLE_BUILDS = 10
# Reliability of a row: (level, minimum |z|, minimum decided games, two-sided chance of a false hit).
LEVELS = (("strong", 2.33, 30, 0.02), ("likely", 1.64, 20, 0.10), ("hint", 1.28, 12, 0.20))


class ItemDataUnavailable(Exception):
    """Data Dragon cannot be reached and no copy of the item catalogue is cached."""


# --- Data Dragon catalogue ---------------------------------------------------------------------------


class Catalogue:
    """The `item.json` (fr_FR) of one Data Dragon version."""

    def __init__(self, version: str, items: dict[str, dict]):
        self.version = version
        self.items = items
        self._kinds: dict[int, str | None] = {}

    def name(self, item: int) -> str | None:
        return (self.items.get(str(item)) or {}).get("name")

    def _in_store(self, item: str) -> bool:
        d = self.items.get(item)
        return bool(d and (d.get("maps") or {}).get(SUMMONERS_RIFT) and (d.get("gold") or {}).get("purchasable")
                    and d.get("inStore") is not False)

    def kind(self, item: int) -> str | None:
        """"completed", "boots" or None (component, starter, consumable, champion-specific...), REQ-ITEM-3."""
        if item not in self._kinds:
            key = str(item)
            d = self.items.get(key)
            kind = None
            if d and "Boots" in (d.get("tags") or []):
                kind = "boots" if d.get("from") else None
            elif (d and self._in_store(key) and not d.get("consumed") and "Consumable" not in (d.get("tags") or [])
                  and not d.get("requiredChampion") and not d.get("requiredAlly")
                  and ((d.get("gold") or {}).get("total") or 0) >= MIN_COMPLETED_GOLD
                  and not any(self._in_store(i) for i in d.get("into") or [])):
                kind = "completed"
            self._kinds[item] = kind
        return self._kinds[item]


_catalogues: dict[str, Catalogue] = {}
_catalogue_lock = threading.Lock()


def _get_json(url: str):
    with httpx.Client(transport=ddragon_transport, timeout=10) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def _matches(version: str, patch: str | None) -> bool:
    return bool(patch) and version.startswith(f"{patch}.")


def _read_cache(patch: str | None) -> Catalogue | None:
    if not patch:
        return None
    try:
        data = json.loads((DDRAGON_DIR / f"items-{patch}.json").read_text(encoding="utf-8"))
        return Catalogue(data["version"], data["items"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _download(patch: str | None) -> Catalogue:
    try:
        versions = _get_json(f"{DDRAGON}/api/versions.json")
        version = next((v for v in versions if _matches(v, patch)), versions[0])
        data = _get_json(f"{DDRAGON}/cdn/{version}/data/fr_FR/item.json")["data"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ItemDataUnavailable(f"Data Dragon unreachable ({exc.__class__.__name__})") from None
    items = {k: {f: v[f] for f in KEPT_FIELDS if f in v} for k, v in data.items()}
    if _matches(version, patch):  # the most recent version stands in for a patch not out yet: not cached
        try:
            DDRAGON_DIR.mkdir(parents=True, exist_ok=True)
            tmp = DDRAGON_DIR / f"items-{patch}.tmp"
            tmp.write_text(json.dumps({"version": version, "items": items}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(DDRAGON_DIR / f"items-{patch}.json")
        except OSError:  # read-only data folder: kept in memory only
            pass
    return Catalogue(version, items)


def catalogue(patch: str | None) -> Catalogue:
    """Item catalogue of a game's patch ("15.22" -> the first 15.22.* Data Dragon version, else the most
    recent one): memory, then disk, then network. Raises ItemDataUnavailable when none works."""
    key = patch or ""
    with _catalogue_lock:
        cached = _catalogues.get(key)
    if cached:
        return cached
    cat = _read_cache(patch) or _download(patch)
    with _catalogue_lock:
        _catalogues[key] = cat
    return cat


# --- build reconstruction ----------------------------------------------------------------------------


def _take(held: list[list], item: int) -> list | None:
    """Remove and return the most recent copy of `item` still held."""
    for i in range(len(held) - 1, -1, -1):
        if held[i][0] == item:
            return held.pop(i)
    return None


def reconstruct(events: list[list]) -> list[dict]:
    """Valid purchases of one player, in order (REQ-ITEM-2): [{"t": second, "item": id, "sold": bool}].

    `events` are the player's stored item events [second, participant, P|S|D|U, item(, after)], in order."""
    log: list[tuple[str, int, dict]] = []  # (action, second, state) of every purchase, sale and destruction
    held: list[list] = []  # inventory copies: [item, purchase or None]
    for e in events:
        t, action, item = e[0], e[2], e[3]
        if action == "P":
            purchase = {"t": t, "item": item, "valid": True, "sold": False}
            log.append(("P", t, purchase))
            held.append([item, purchase])
        elif action in ("S", "D"):
            copy = _take(held, item)
            if action == "S" and copy and copy[1]:
                copy[1]["sold"] = True
            log.append((action, t, {"item": item, "copy": copy, "valid": True}))
        elif action == "U":
            after = e[4] if len(e) > 4 else 0
            if item:
                _undo_purchase(log, held, item)
            elif after:
                _undo_sale(log, held, after)
    return [{"t": p["t"], "item": p["item"], "sold": p["sold"]} for a, _, p in log if a == "P" and p["valid"]]


def _last_valid(log: list[tuple], action: str, item: int) -> int | None:
    return next((i for i in range(len(log) - 1, -1, -1)
                 if log[i][0] == action and log[i][2]["valid"] and log[i][2]["item"] == item), None)


def _undo_purchase(log: list[tuple], held: list[list], item: int) -> None:
    """Cancel the last valid purchase of `item` and give back the components destroyed by it."""
    i = _last_valid(log, "P", item)
    if i is None:
        return
    _, t, purchase = log[i]
    purchase["valid"] = False
    for k in range(len(held) - 1, -1, -1):
        if held[k][1] is purchase:
            del held[k]
            break
    j = i - 1
    while j >= 0 and log[j][0] == "D" and log[j][1] == t:  # the components it consumed, at the same second
        destroyed = log[j][2]
        if destroyed["valid"]:
            destroyed["valid"] = False
            if destroyed["copy"]:
                held.append(destroyed["copy"])
        j -= 1


def _undo_sale(log: list[tuple], held: list[list], item: int) -> None:
    i = _last_valid(log, "S", item)
    if i is None:
        return
    sale = log[i][2]
    sale["valid"] = False
    if sale["copy"]:
        held.append(sale["copy"])
        if sale["copy"][1]:
            sale["copy"][1]["sold"] = False


def build_of(purchases: list[dict], cat: Catalogue) -> dict:
    """Distinct completed items in the order of their first valid purchase (6 at most) and the first
    boots, as (item, minute, sold) (REQ-ITEM-3)."""
    items, seen, boots = [], set(), None
    for p in purchases:
        kind = cat.kind(p["item"])
        entry = (p["item"], p["t"] / 60, p["sold"])
        if kind == "completed" and p["item"] not in seen and len(items) < MAX_SLOTS:
            seen.add(p["item"])
            items.append(entry)
        elif kind == "boots" and boots is None:
            boots = entry
    return {"items": items, "boots": boots}


# --- picks and builds ----------------------------------------------------------------------------------


def _picks(ds: Dataset, f: Filters, champions: list[str], role: str | None) -> list[dict]:
    """Every pick of the chosen champions (at the chosen role) in the games of the scope."""
    wanted = set(champions)
    picks = []
    for g in ds.filter_games(f):
        for side in (1, 2):
            s, o = g["sides"][side], g["sides"][3 - side]
            for p in s["picks"]:
                if p["champion"] in wanted and (not role or p["role"] == role):
                    picks.append({
                        "game": g["game_id"], "patch": g["patch"], "side": side, "role": p["role"],
                        "champion": p["champion"], "player": p["player"], "win": s["win"],
                        "opponent": next((q["champion"] for q in o["picks"] if q["role"] == p["role"]), None),
                    })
    return picks


def _chunks(values: list, size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _load_builds(picks: list[dict], conn: sqlite3.Connection) -> tuple[list[dict | None], dict[str, Catalogue]]:
    """The build of every pick (None when its game has no stored timeline) and the catalogues used.

    Games are read CHUNK_GAMES at a time and each blob is decoded once, for every chosen pick of its
    game; its events are dropped as soon as those builds are made (REQ-ITEM-10)."""
    by_game: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(picks):
        by_game[p["game"]].append(i)
    builds: list[dict | None] = [None] * len(picks)
    catalogues: dict[str, Catalogue] = {}
    for chunk in _chunks(sorted(by_game), CHUNK_GAMES):
        marks = ",".join("?" for _ in chunk)
        participants = {(r[0], r[1], r[2]): r[3] for r in conn.execute(
            f"SELECT match_id, side, role, participant FROM soloq_players WHERE match_id IN ({marks})", chunk)}
        for match_id, blob in conn.execute(
                f"SELECT match_id, items FROM soloq_timelines WHERE match_id IN ({marks}) AND items IS NOT NULL", chunk):
            events = json.loads(zlib.decompress(blob))
            for i in by_game[match_id]:
                p = picks[i]
                pid = participants.get((match_id, p["side"], p["role"]))
                if pid is None:
                    continue
                patch = p["patch"] or ""
                if patch not in catalogues:
                    catalogues[patch] = catalogue(p["patch"])
                build = build_of(reconstruct([e for e in events if e[1] == pid]), catalogues[patch])
                builds[i] = {**build, "champion": p["champion"], "win": p["win"], "patch": patch}
    return builds, catalogues


# --- aggregation and statistics ------------------------------------------------------------------------


class Purchase(NamedTuple):
    champion: str
    minute: float
    win: bool | None
    sold: bool
    slot: int  # 1-6, 0 for boots, the length for a path


def _collect(builds: list[dict]) -> tuple[dict[tuple, dict], Counter]:
    """Purchases of every row of every list, and the number of builds each list is a share of.

    Lists: ("slot", k), ("item",) (every slot), ("boots",) and ("path", length); a path counts the
    purchase of its last item."""
    rows: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    totals: Counter = Counter()
    for b in builds:
        items, champion, win = b["items"], b["champion"], b["win"]
        totals[("item",)] += 1
        totals[("boots",)] += 1
        for k, (item, minute, sold) in enumerate(items, 1):
            purchase = Purchase(champion, minute, win, sold, k)
            rows[("slot", k)][item].append(purchase)
            rows[("item",)][item].append(purchase)
            totals[("slot", k)] += 1
        if b["boots"]:
            item, minute, sold = b["boots"]
            rows[("boots",)][item].append(Purchase(champion, minute, win, sold, 0))
        for length in PATH_LENGTHS:
            if len(items) >= length:
                _, minute, sold = items[length - 1]
                rows[("path", length)][tuple(i[0] for i in items[:length])].append(
                    Purchase(champion, minute, win, sold, length))
                totals[("path", length)] += 1
    return rows, totals


def _quantile(values: list[float], q: float) -> float:
    pos = q * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 1)


def _timing(minutes: list[float]) -> dict:
    """Quartiles (minutes) and 2-minute histogram of purchase times, the last bar = 40 minutes and more."""
    histogram = [0] * (HISTOGRAM_LAST // HISTOGRAM_STEP + 1)
    for m in minutes:
        histogram[min(int(m // HISTOGRAM_STEP), len(histogram) - 1)] += 1
    if not minutes:
        return {"p25": None, "median": None, "p75": None, "histogram": histogram}
    s = sorted(minutes)
    return {"p25": _quantile(s, 0.25), "median": _quantile(s, 0.5), "p75": _quantile(s, 0.75), "histogram": histogram}


def _window(minute: float) -> int:
    return min(int(minute // WINDOW), WINDOW_LAST // WINDOW)


def _stats(wins: int, decided: int, expected: float | None) -> dict:
    if not decided or expected is None:
        return {"wins": wins, "decided": decided, "winrate": None, "wr_low": None, "wr_high": None, "expected": None,
                "wr_adjusted": None, "delta": None, "z": None, "level": None, "verdict": None}
    rate = wins / decided
    low, high = wilson(wins, decided)
    z = _z(rate, expected, decided)
    level = next((key for key, z_min, n_min, _ in LEVELS if abs(z) >= z_min and decided >= n_min), None)
    return {
        "wins": wins, "decided": decided, "winrate": round(rate, 4), "wr_low": round(low, 4), "wr_high": round(high, 4),
        "expected": round(expected, 4),
        "wr_adjusted": round((wins + PRIOR_GAMES * expected) / (decided + PRIOR_GAMES), 4),
        "delta": round(rate - expected, 4), "z": round(z, 2), "level": level,
        "verdict": None if level is None else "better" if z > 0 else "worse",
    }


def _contrast(rows: dict) -> dict:
    """Winrate of each row of a list against its expected winrate (REQ-ITEM-6).

    A purchase is expected to win as often as the other purchases of its cell: same champion, same slot
    (or boots, or path length) and same 5-minute purchase window, the row itself being left out. A cell
    without other purchases falls back on the other purchases of every cell, and then on 50 %. Games
    without a winner are left out."""
    cells: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    mine: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    total = [0, 0]
    for key, purchases in rows.items():
        for p in purchases:
            if p.win is None:
                continue
            cell = (p.champion, p.slot, _window(p.minute))
            for tally in (cells[cell], mine[key][cell], total):
                tally[0] += 1
                tally[1] += p.win
    out = {}
    for key in rows:
        own = mine.get(key, {})
        decided = sum(n for n, _ in own.values())
        wins = sum(w for _, w in own.values())
        others = total[0] - decided
        fallback = (total[1] - wins) / others if others else 0.5
        expected = 0.0
        for cell, (n, w) in own.items():
            n_other = cells[cell][0] - n
            expected += n * ((cells[cell][1] - w) / n_other if n_other else fallback)
        out[key] = _stats(wins, decided, expected / decided if decided else None)
    return out


def _reference(builds: list[dict]) -> dict[tuple, dict]:
    """Share and median purchase time of every row in the average build (REQ-ITEM-8)."""
    rows, totals = _collect(builds)
    return {(list_key, key): {"share": round(len(purchases) / totals[list_key], 4),
                              "median": _timing([p.minute for p in purchases])["median"]}
            for list_key, by_key in rows.items() for key, purchases in by_key.items()}


def analyse(ds: Dataset, f: Filters, champions: list[str], role: str | None = None, player: str | None = None,
            vs: str | None = None, min_games: int = 5, conn: sqlite3.Connection | None = None) -> dict:
    """The `/api/items` answer (contract in docs/api.md)."""
    picks = _picks(ds, f, champions, role)
    own = conn is None and bool(picks)
    if own:
        conn = soloq.connect()
    try:
        all_builds, catalogues = _load_builds(picks, conn) if picks else ([], {})
    finally:
        if own:
            conn.close()
    filtered = bool(player or vs)

    def kept(p: dict) -> bool:
        return ((not player or (p["player"] or "").casefold() == player.casefold())
                and (not vs or p["opponent"] == vs))

    chosen = [(p, b) for p, b in zip(picks, all_builds) if kept(p)]
    builds = [b for _, b in chosen if b is not None]
    missing = len({p["game"] for p, b in chosen if b is None})
    ref_builds = [b for b in all_builds if b is not None]  # the average build (REQ-ITEM-8)
    ref = _reference(ref_builds) if filtered else None

    rows, totals = _collect(builds)
    tested = 0
    cited: set[int] = set()

    def table(list_key: tuple, limit: int | None = None) -> list[dict]:
        nonlocal tested
        by_key = rows.get(list_key, {})
        contrast = _contrast(by_key)
        listed = sorted(((k, v) for k, v in by_key.items() if len(v) >= min_games), key=lambda kv: (-len(kv[1]), kv[0]))
        out = []
        for key, purchases in listed[:limit]:
            stats = contrast[key]
            tested += stats["decided"] >= LEVELS[-1][2]
            row = {"items": list(key)} if list_key[0] == "path" else {"item": key}
            cited.update(key if list_key[0] == "path" else (key,))
            row.update({
                "games": len(purchases), "share": round(len(purchases) / totals[list_key], 4), **stats,
                "timing": _timing([p.minute for p in purchases]),
                "sold": round(sum(p.sold for p in purchases) / len(purchases), 4),
                "ref": None if ref is None else ref.get((list_key, key), {"share": 0.0, "median": None}),
            })
            if list_key == ("item",):
                row["avg_slot"] = round(sum(p.slot for p in purchases) / len(purchases), 2)
            out.append(row)
        return out

    slots = [{"slot": k, "builds": totals[("slot", k)],
              "timing": _timing([p.minute for ps in rows.get(("slot", k), {}).values() for p in ps]),
              "items": table(("slot", k))}
             for k in range(1, MAX_SLOTS + 1) if totals[("slot", k)]]
    by_item = table(("item",))
    boots = {"builds": len(builds), "none": sum(b["boots"] is None for b in builds), "items": table(("boots",))}
    paths = []
    for n in PATH_LENGTHS:
        path_rows = table(("path", n), PATH_LIMIT)
        total = totals[("path", n)]
        paths.append({"length": n, "builds": total,
                      "covered": round(sum(r["games"] for r in path_rows) / total, 4) if total else None,
                      "rows": path_rows})

    newest = sorted(catalogues, key=soloq.patch_key, reverse=True)
    names = {}
    for item in sorted(cited):
        name = next((catalogues[p].name(item) for p in newest if catalogues[p].name(item)), None)
        if name:
            names[str(item)] = name
    own_patches = sorted({b["patch"] for b in builds}, key=soloq.patch_key, reverse=True)
    decided = [b for b in builds if b["win"] is not None]
    wins = sum(bool(b["win"]) for b in decided)
    ci = wilson(wins, len(decided))
    ref_decided = [b for b in ref_builds if b["win"] is not None]
    players = Counter(p["player"] for p in picks if p["player"])
    opponents = Counter(p["opponent"] for p in picks if p["opponent"])
    return {
        "champions": champions, "role": role, "player": player, "vs": vs, "min_games": min_games,
        "version": catalogues[own_patches[0]].version if own_patches else None,
        "names": names,
        "builds": len(builds), "decided": len(decided), "missing_timeline": missing,
        "baseline": {"wins": wins, "decided": len(decided),
                     "winrate": round(wins / len(decided), 4) if decided else None,
                     "wr_low": round(ci[0], 4) if ci else None, "wr_high": round(ci[1], 4) if ci else None},
        "slots": slots, "by_item": by_item, "boots": boots, "paths": paths,
        "tested": tested,
        "levels": [{"key": key, "z": z_min, "min_games": n_min, "by_chance": round(tested * chance, 1)}
                   for key, z_min, n_min, chance in LEVELS],
        "reference": None if not filtered else {
            "builds": len(ref_builds), "decided": len(ref_decided),
            "winrate": round(sum(bool(b["win"]) for b in ref_decided) / len(ref_decided), 4) if ref_decided else None,
            "comparable": len(builds) >= COMPARABLE_BUILDS,
        },
        "options": {
            "players": [{"player": k, "games": n} for k, n in sorted(players.items(), key=lambda kv: (-kv[1], kv[0].casefold()))],
            "opponents": [{"champion": k, "games": n} for k, n in sorted(opponents.items(), key=lambda kv: (-kv[1], kv[0]))],
        },
    }
