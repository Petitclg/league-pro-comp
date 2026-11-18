"""Solo queue games (Riot API) in their own compact database, `soloq.db`, next to the pro one.

Raw Match-V5 JSON (≈ 1 MB per game with its timeline) is never kept. Each game keeps:
- its header (region, rank tier, patch, date, duration, winner) and per-side totals,
- per-participant end-of-game stats (champion, role, K/D/A, gold, CS, damage dealt/taken, vision, level),
- the bans,
- zlib-compressed JSON blobs: per-minute frames for the 10 participants, champion kills, team
  objectives (towers, plates, inhibitors, dragons, heralds, grubs, barons, Atakhan) and item events.
That is a few KB per game. See docs/data-model.md.

The games load into the same `Dataset` as pro games: a solo queue game is a one-game "series" of
the teams "Blue" and "Red", its "tournament" is the region label (EUW, KR...) and it is never
Fearless. The draft order is unknown in solo queue (only bans carry a pick turn): picks get the
slots of a role-ordered draft, so draft-timing analyses are meaningless in this scope.
"""
import json
import sqlite3
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR
from .dataset import BAN_SLOT, PICK_SLOT, Dataset, _empty_side
from .db import ROLES
from .leagues import SOLOQ
from .riot import PLATFORMS, QUEUE_SOLO, TIERS, label_of, region_of

SOLOQ_DB_PATH = DATA_DIR / "soloq.db"
SCHEMA_VERSION = 1
LEAGUE_KEY = SOLOQ
TEAMS = {1: "Blue", 2: "Red"}
SIDE_OF_TEAM = {100: 1, 200: 2}
POSITION_ROLE = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "support"}
MIN_DURATION = 300  # seconds: shorter games are remakes
FRAME_FIELDS = ("gold", "xp", "level", "cs", "kills", "deaths", "assists", "damage_dealt", "damage_taken")
ITEM_EVENTS = {"ITEM_PURCHASED": "P", "ITEM_SOLD": "S", "ITEM_DESTROYED": "D", "ITEM_UNDO": "U"}
MONSTERS = {"DRAGON": "dragon", "BARON_NASHOR": "baron", "RIFTHERALD": "herald", "HORDE": "grub", "ATAKHAN": "atakhan"}
BUILDINGS = {"TOWER_BUILDING": "tower", "INHIBITOR_BUILDING": "inhibitor"}
LANES = {"TOP_LANE": "top", "MID_LANE": "mid", "BOT_LANE": "bot"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS soloq_games (
    match_id TEXT PRIMARY KEY,          -- e.g. EUW1_7212345678
    platform TEXT NOT NULL,             -- euw1, kr...
    region TEXT NOT NULL,               -- europe, asia, americas, sea
    tier TEXT,                          -- rank tier of the seed player(s) that found the game
    patch TEXT,                         -- "15.22" (major.minor of gameVersion)
    date TEXT,                          -- game start, ISO UTC
    duration REAL,                      -- minutes
    winner INTEGER,                     -- 1 (Blue, team 100) or 2 (Red, team 200)
    imported_at REAL
);
CREATE TABLE IF NOT EXISTS soloq_sides (
    match_id TEXT NOT NULL REFERENCES soloq_games(match_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    kills INTEGER, gold INTEGER, towers INTEGER, inhibitors INTEGER,
    dragons INTEGER, heralds INTEGER, grubs INTEGER, barons INTEGER,
    PRIMARY KEY (match_id, side)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS soloq_players (
    match_id TEXT NOT NULL REFERENCES soloq_games(match_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    role TEXT NOT NULL,
    participant INTEGER NOT NULL,       -- participantId (1-10), the index used by the timeline blobs
    champion TEXT NOT NULL,
    player TEXT,                        -- Riot ID (name#tag)
    kills INTEGER, deaths INTEGER, assists INTEGER, gold INTEGER, cs INTEGER,
    damage INTEGER, damage_taken INTEGER, vision INTEGER, level INTEGER,
    PRIMARY KEY (match_id, side, role)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS soloq_bans (
    match_id TEXT NOT NULL REFERENCES soloq_games(match_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    champion TEXT NOT NULL,
    PRIMARY KEY (match_id, side, slot)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS soloq_timelines (
    match_id TEXT PRIMARY KEY REFERENCES soloq_games(match_id) ON DELETE CASCADE,
    frames BLOB,                        -- zlib(JSON) [minute][participant 1-10][FRAME_FIELDS]
    kills BLOB,                         -- zlib(JSON) [[second, killer, victim, [assists]]]
    objectives BLOB,                    -- zlib(JSON) [[second, kind, side, detail]]
    items BLOB                          -- zlib(JSON) [[second, participant, P|S|D|U, item(, after)]]
);
-- Import state, so that an import resumes where it stopped after a restart.
CREATE TABLE IF NOT EXISTS soloq_ladder (
    platform TEXT NOT NULL, tier TEXT NOT NULL, division TEXT NOT NULL,
    next_page INTEGER NOT NULL DEFAULT 1, exhausted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform, tier, division)
);
CREATE TABLE IF NOT EXISTS soloq_seeds (
    platform TEXT NOT NULL, puuid TEXT NOT NULL, tier TEXT NOT NULL, division TEXT,
    crawled_at REAL,                    -- NULL until its match history was read
    PRIMARY KEY (platform, puuid)
);
CREATE TABLE IF NOT EXISTS soloq_queue (
    match_id TEXT PRIMARY KEY, platform TEXT NOT NULL,
    tiers TEXT NOT NULL,                -- tiers of every seed that found it, comma separated
    discovered_at REAL
);
CREATE TABLE IF NOT EXISTS soloq_skipped (
    match_id TEXT PRIMARY KEY, reason TEXT
);
CREATE TABLE IF NOT EXISTS soloq_state (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_soloq_games_patch ON soloq_games(patch);
CREATE INDEX IF NOT EXISTS idx_soloq_games_tier ON soloq_games(tier);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else SOLOQ_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn


# --- compact extraction -------------------------------------------------------


class Skip(Exception):
    """A match that is not stored (remake, other queue, unknown roles, old patch...)."""


def patch_of(version: str | None) -> str | None:
    """"15.22.712.1234" -> "15.22"."""
    parts = (version or "").split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}.{int(parts[1])}"
    return None


def patch_key(patch: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in (patch or "").split("."))
    except ValueError:
        return ()


def _pack(value) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":")).encode(), 9)


def _unpack(blob: bytes | None):
    return json.loads(zlib.decompress(blob)) if blob else None


def _roles(participants: list[dict], key: str) -> dict[int, str] | None:
    """participantId -> role when each side has its 5 roles exactly once."""
    roles = {p["participantId"]: POSITION_ROLE.get(p.get(key) or "") for p in participants}
    for team in (100, 200):
        side_roles = [roles[p["participantId"]] for p in participants if p.get("teamId") == team]
        if sorted(r for r in side_roles if r) != sorted(ROLES) or len(side_roles) != 5:
            return None
    return roles


def extract_match(match: dict, platform: str, names: dict[int, str] | None = None) -> dict:
    """The stored part of a Match-V5 match (without its timeline). Raises Skip when not stored."""
    names = names or {}
    info = match.get("info") or {}
    if info.get("queueId") != QUEUE_SOLO:
        raise Skip("queue")
    participants = info.get("participants") or []
    duration = info.get("gameDuration") or 0
    if "gameEndTimestamp" not in info:  # before patch 11.20 the duration was in milliseconds
        duration /= 1000
    if len(participants) != 10 or duration < MIN_DURATION or any(p.get("gameEndedInEarlySurrender") for p in participants):
        raise Skip("remake")
    roles = _roles(participants, "teamPosition") or _roles(participants, "individualPosition")
    if not roles:
        raise Skip("roles")
    teams = {SIDE_OF_TEAM.get(t.get("teamId")): t for t in info.get("teams") or []}
    winner = next((s for s, t in teams.items() if s and t.get("win")), None)

    def name(champion_id, fallback=None):
        return names.get(champion_id) or fallback or str(champion_id)

    players = []
    for p in participants:
        riot_id = p.get("riotIdGameName")
        players.append({
            "side": SIDE_OF_TEAM[p["teamId"]], "role": roles[p["participantId"]], "participant": p["participantId"],
            "champion": name(p.get("championId"), p.get("championName")),
            "player": f"{riot_id}#{p.get('riotIdTagline')}" if riot_id else None,
            "kills": p.get("kills"), "deaths": p.get("deaths"), "assists": p.get("assists"),
            "gold": p.get("goldEarned"),
            "cs": (p.get("totalMinionsKilled") or 0) + (p.get("neutralMinionsKilled") or 0),
            "damage": p.get("totalDamageDealtToChampions"), "damage_taken": p.get("totalDamageTaken"),
            "vision": p.get("visionScore"), "level": p.get("champLevel"),
        })
    sides, bans = {}, []
    for side in (1, 2):
        objectives = (teams.get(side) or {}).get("objectives") or {}

        def kills(key):
            return (objectives.get(key) or {}).get("kills")

        sides[side] = {
            "kills": kills("champion"), "gold": sum(p["gold"] or 0 for p in players if p["side"] == side),
            "towers": kills("tower"), "inhibitors": kills("inhibitor"), "dragons": kills("dragon"),
            "heralds": kills("riftHerald"), "grubs": kills("horde"), "barons": kills("baron"),
        }
        side_bans = sorted((teams.get(side) or {}).get("bans") or [], key=lambda b: b.get("pickTurn") or 0)
        for slot, b in enumerate(side_bans[:5], 1):
            if (b.get("championId") or -1) > 0:
                bans.append((side, slot, name(b["championId"])))
    start = info.get("gameStartTimestamp") or info.get("gameCreation") or 0
    return {
        "match_id": (match.get("metadata") or {}).get("matchId") or f"{platform.upper()}_{info.get('gameId')}",
        "platform": platform, "region": region_of(platform),
        "patch": patch_of(info.get("gameVersion")),
        "date": datetime.fromtimestamp(start / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if start else None,
        "duration": round(duration / 60, 2), "winner": winner,
        "sides": sides, "players": players, "bans": bans,
    }


def extract_timeline(timeline: dict) -> dict:
    """Per-minute frames and events of a Match-V5 timeline, as compressed blobs."""
    frames = (timeline.get("info") or {}).get("frames") or []
    events = sorted((e for f in frames for e in f.get("events") or []), key=lambda e: e.get("timestamp") or 0)
    kda = {pid: [0, 0, 0] for pid in range(1, 11)}
    kills, objectives, items = [], [], []
    rows = []
    i = 0
    for frame in frames:
        # K/D/A of a snapshot: every kill up to the frame's timestamp
        while i < len(events) and (events[i].get("timestamp") or 0) <= (frame.get("timestamp") or 0):
            _count_kill(events[i], kda)
            i += 1
        row = []
        for pid in range(1, 11):
            pf = (frame.get("participantFrames") or {}).get(str(pid)) or {}
            dmg = pf.get("damageStats") or {}
            row.append([pf.get("totalGold", 0), pf.get("xp", 0), pf.get("level", 0),
                        pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0), *kda[pid],
                        dmg.get("totalDamageDoneToChampions", 0), dmg.get("totalDamageTaken", 0)])
        rows.append(row)
    for e in events:
        t = (e.get("timestamp") or 0) // 1000
        kind = e.get("type")
        if kind == "CHAMPION_KILL":
            kills.append([t, e.get("killerId", 0), e.get("victimId", 0), e.get("assistingParticipantIds") or []])
        elif kind == "ELITE_MONSTER_KILL" and e.get("monsterType") in MONSTERS:
            sub = (e.get("monsterSubType") or "").replace("_DRAGON", "") or None
            objectives.append([t, MONSTERS[e["monsterType"]], SIDE_OF_TEAM.get(e.get("killerTeamId")), sub])
        elif kind == "BUILDING_KILL" and e.get("buildingType") in BUILDINGS:
            # teamId is the team that lost the building
            lost = SIDE_OF_TEAM.get(e.get("teamId"))
            objectives.append([t, BUILDINGS[e["buildingType"]], 3 - lost if lost else None, LANES.get(e.get("laneType"))])
        elif kind == "TURRET_PLATE_DESTROYED":
            lost = SIDE_OF_TEAM.get(e.get("teamId"))
            objectives.append([t, "plate", 3 - lost if lost else None, LANES.get(e.get("laneType"))])
        elif kind in ITEM_EVENTS:
            if kind == "ITEM_UNDO":
                items.append([t, e.get("participantId", 0), "U", e.get("beforeId", 0), e.get("afterId", 0)])
            else:
                items.append([t, e.get("participantId", 0), ITEM_EVENTS[kind], e.get("itemId", 0)])
    return {"frames": _pack(rows), "kills": _pack(kills), "objectives": _pack(objectives), "items": _pack(items)}


def _count_kill(e: dict, kda: dict[int, list[int]]) -> None:
    if e.get("type") != "CHAMPION_KILL":
        return
    if e.get("killerId") in kda:
        kda[e["killerId"]][0] += 1
    if e.get("victimId") in kda:
        kda[e["victimId"]][1] += 1
    for pid in e.get("assistingParticipantIds") or []:
        if pid in kda:
            kda[pid][2] += 1


# --- storage --------------------------------------------------------------


def save_game(conn: sqlite3.Connection, game: dict, tier: str | None, blobs: dict | None) -> None:
    """Insert or replace a game (from `extract_match`) with its timeline blobs."""
    conn.execute("DELETE FROM soloq_games WHERE match_id = ?", (game["match_id"],))
    conn.execute(
        "INSERT INTO soloq_games (match_id, platform, region, tier, patch, date, duration, winner, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (game["match_id"], game["platform"], game["region"], tier, game["patch"], game["date"],
         game["duration"], game["winner"], time.time()),
    )
    for side, s in game["sides"].items():
        conn.execute(
            "INSERT INTO soloq_sides (match_id, side, kills, gold, towers, inhibitors, dragons, heralds, grubs, barons)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (game["match_id"], side, *(s.get(k) for k in ("kills", "gold", "towers", "inhibitors", "dragons",
                                                         "heralds", "grubs", "barons"))),
        )
    conn.executemany(
        "INSERT INTO soloq_players (match_id, side, role, participant, champion, player, kills, deaths, assists,"
        " gold, cs, damage, damage_taken, vision, level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(game["match_id"], p["side"], p["role"], p["participant"], p["champion"], p["player"], p["kills"],
          p["deaths"], p["assists"], p["gold"], p["cs"], p["damage"], p["damage_taken"], p["vision"], p["level"])
         for p in game["players"]],
    )
    conn.executemany("INSERT INTO soloq_bans (match_id, side, slot, champion) VALUES (?, ?, ?, ?)",
                     [(game["match_id"], *b) for b in game["bans"]])
    if blobs:
        conn.execute("INSERT INTO soloq_timelines (match_id, frames, kills, objectives, items) VALUES (?, ?, ?, ?, ?)",
                     (game["match_id"], blobs["frames"], blobs["kills"], blobs["objectives"], blobs["items"]))


def load_timeline(conn: sqlite3.Connection, match_id: str) -> dict | None:
    """Decoded timeline of a stored game: frames as {participant: [{field: value}]} per minute."""
    row = conn.execute("SELECT * FROM soloq_timelines WHERE match_id = ?", (match_id,)).fetchone()
    if not row:
        return None
    frames = _unpack(row["frames"]) or []
    return {
        "frames": [{pid: dict(zip(FRAME_FIELDS, values)) for pid, values in enumerate(minute, 1)} for minute in frames],
        "kills": [{"t": t, "killer": k, "victim": v, "assists": a} for t, k, v, a in _unpack(row["kills"]) or []],
        "objectives": [{"t": t, "kind": k, "side": s, "detail": d} for t, k, s, d in _unpack(row["objectives"]) or []],
        "items": [{"t": e[0], "participant": e[1], "action": e[2], "item": e[3], **({"after": e[4]} if len(e) > 4 else {})}
                  for e in _unpack(row["items"]) or []],
    }


def purge_old_patches(conn: sqlite3.Connection, keep: int) -> int:
    """Keep the games of the `keep` most recent patches only (0 keeps everything). Returns games deleted."""
    if keep <= 0:
        return 0
    patches = sorted((r[0] for r in conn.execute("SELECT DISTINCT patch FROM soloq_games WHERE patch IS NOT NULL")),
                     key=patch_key, reverse=True)
    old = patches[keep:]
    if not old:
        return 0
    marks = ",".join("?" for _ in old)
    deleted = conn.execute(f"DELETE FROM soloq_games WHERE patch IN ({marks})", old).rowcount
    conn.commit()
    return deleted


def kept_patches(conn: sqlite3.Connection, keep: int) -> list[str]:
    patches = sorted((r[0] for r in conn.execute("SELECT DISTINCT patch FROM soloq_games WHERE patch IS NOT NULL")),
                     key=patch_key, reverse=True)
    return patches[:keep] if keep > 0 else patches


def summary(conn: sqlite3.Connection | None = None) -> dict:
    """Size and spread of the stored games."""
    own = conn is None
    if own:
        if not SOLOQ_DB_PATH.exists():
            return {"games": 0, "first": None, "last": None, "by_tier": {}, "by_platform": {}, "patches": [],
                    "queued": 0, "seeds": 0}
        conn = connect()
    try:
        games, first, last = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM soloq_games").fetchone()
        by_tier = dict(conn.execute("SELECT tier, COUNT(*) FROM soloq_games GROUP BY tier").fetchall())
        by_platform = {label_of(p): n for p, n in conn.execute(
            "SELECT platform, COUNT(*) FROM soloq_games GROUP BY platform ORDER BY COUNT(*) DESC")}
        patches = sorted((r[0] for r in conn.execute("SELECT DISTINCT patch FROM soloq_games WHERE patch IS NOT NULL")),
                         key=patch_key, reverse=True)
        return {
            "games": games, "first": first, "last": last,
            "by_tier": {t: by_tier[t] for t in TIERS if t in by_tier},
            "by_platform": by_platform, "patches": patches,
            "queued": conn.execute("SELECT COUNT(*) FROM soloq_queue").fetchone()[0],
            "seeds": conn.execute("SELECT COUNT(*) FROM soloq_seeds").fetchone()[0],
        }
    finally:
        if own:
            conn.close()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM soloq_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM soloq_state WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO soloq_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


# --- dataset ----------------------------------------------------------------


def load(conn: sqlite3.Connection) -> Dataset:
    """Every stored solo queue game as a Dataset (see the module docstring for the mapping)."""
    games: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM soloq_games ORDER BY date, match_id"):
        sides = {}
        for side in (1, 2):
            s = _empty_side()
            s["team"] = TEAMS[side]
            s["win"] = None if r["winner"] is None else r["winner"] == side
            sides[side] = s
        games[r["match_id"]] = {
            "game_id": r["match_id"], "league": LEAGUE_KEY, "match_id": None, "game_number": 1,
            "date": r["date"], "patch": r["patch"], "tournament": label_of(r["platform"]), "stage": None,
            "team1": TEAMS[1], "team2": TEAMS[2], "winner": r["winner"], "duration": r["duration"],
            "source": "riot", "rank": r["tier"], "platform": r["platform"], "sides": sides,
        }
    for r in conn.execute("SELECT * FROM soloq_sides"):
        g = games.get(r["match_id"])
        if g:
            g["sides"][r["side"]]["stats"] = {k: r[k] for k in ("kills", "gold", "towers", "dragons", "barons")}
    order = {role: i for i, role in enumerate(ROLES, 1)}
    for r in conn.execute("SELECT * FROM soloq_players"):
        g = games.get(r["match_id"])
        if g:
            pick_order = order[r["role"]]
            g["sides"][r["side"]]["picks"].append({
                "order": pick_order, "slot": PICK_SLOT[r["side"]][pick_order - 1], "role": r["role"],
                "champion": r["champion"], "player": r["player"],
                "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"],
                "gold": r["gold"], "cs": r["cs"], "damage": r["damage"], "vision": r["vision"],
            })
    for r in conn.execute("SELECT * FROM soloq_bans ORDER BY match_id, side, slot"):
        g = games.get(r["match_id"])
        if g:
            g["sides"][r["side"]]["bans"].append(r["champion"])
            g["sides"][r["side"]]["ban_order"].append(BAN_SLOT[r["side"]][r["slot"] - 1])
    for g in games.values():
        for s in g["sides"].values():
            s["picks"].sort(key=lambda p: p["order"])
    ordered = [g for g in games.values() if all(len(s["picks"]) == 5 for s in g["sides"].values())]
    return Dataset(ordered, {})


def majority_tier(tiers: str | list[str]) -> str | None:
    """Tier tag of a game found by several seeds: the most frequent one, the higher on a tie."""
    values = [t for t in (tiers.split(",") if isinstance(tiers, str) else tiers) if t in TIERS]
    if not values:
        return None
    counts = Counter(values)
    return max(counts, key=lambda t: (counts[t], TIERS.index(t)))


def known_platform(platform: str) -> bool:
    return platform in PLATFORMS
