import sqlite3
from pathlib import Path

from .config import DATA_DIR

ROLES = ["top", "jungle", "mid", "bot", "support"]

DB_PATH = DATA_DIR / "lck.db"

SCHEMA_VERSION = 3

# Bump when the import rules change: leagues imported under an older revision are re-imported on the next start.
# 2: scoreboard players and side stats follow the champions of each draft side (wrong when Leaguepedia lists the teams in opposite order).
INGEST_REVISION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_revisions (
    league TEXT PRIMARY KEY,
    revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'LCK',
    tournament TEXT,
    stage TEXT,
    date TEXT,
    best_of INTEGER,
    team1 TEXT,
    team2 TEXT,
    score1 INTEGER,
    score2 INTEGER,
    winner INTEGER
);
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'LCK',
    match_id TEXT,
    game_number INTEGER,
    date TEXT,
    patch TEXT,
    tournament TEXT,
    stage TEXT,
    team1 TEXT NOT NULL,
    team2 TEXT NOT NULL,
    winner INTEGER,
    duration REAL,
    source TEXT NOT NULL DEFAULT 'leaguepedia'
);
CREATE TABLE IF NOT EXISTS game_sides (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    kills INTEGER,
    gold INTEGER,
    towers INTEGER,
    dragons INTEGER,
    barons INTEGER,
    PRIMARY KEY (game_id, side)
);
CREATE TABLE IF NOT EXISTS picks (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    pick_order INTEGER NOT NULL,
    role TEXT NOT NULL,
    champion TEXT NOT NULL,
    player TEXT,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    gold INTEGER,
    cs INTEGER,
    damage INTEGER,
    vision INTEGER,
    PRIMARY KEY (game_id, side, pick_order)
);
CREATE TABLE IF NOT EXISTS bans (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    side INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    champion TEXT NOT NULL,
    PRIMARY KEY (game_id, side, slot)
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);
CREATE INDEX IF NOT EXISTS idx_games_match ON games(match_id);
CREATE INDEX IF NOT EXISTS idx_games_league ON games(league);
CREATE INDEX IF NOT EXISTS idx_picks_champion ON picks(champion);
"""

PICK_STAT_FIELDS = ["player", "kills", "deaths", "assists", "gold", "cs", "damage", "vision"]


def _migrate(conn: sqlite3.Connection, version: int) -> None:
    """Bring an existing database up to SCHEMA_VERSION."""
    if version == 2:
        # v2 held LCK only: add the league column, existing rows are LCK
        for table in ("games", "matches"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN league TEXT NOT NULL DEFAULT 'LCK'")
    elif version != 0:
        # Unknown older schema: it only held disposable imported data, rebuild it.
        for table in ("bans", "picks", "game_sides", "games", "matches"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:  # a newer database (v0.2.6 wrote v4, with extra picks columns) is used as it is
        _migrate(conn, version)
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    # Nothing is written for an up-to-date database: a changed file mtime reloads the in-memory datasets.
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def ingest_revision(conn: sqlite3.Connection, league: str) -> int:
    """Import revision of a league (0 for data imported before revisions existed)."""
    conn.executescript("CREATE TABLE IF NOT EXISTS ingest_revisions (league TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
    row = conn.execute("SELECT revision FROM ingest_revisions WHERE league = ?", (league,)).fetchone()
    return row[0] if row else 0


def mark_ingest_revision(conn: sqlite3.Connection, league: str) -> None:
    ingest_revision(conn, league)  # makes sure the table exists in a database created by an older version
    conn.execute("INSERT OR REPLACE INTO ingest_revisions (league, revision) VALUES (?, ?)", (league, INGEST_REVISION))
    conn.commit()


def save_match(conn: sqlite3.Connection, match: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO matches (match_id, league, tournament, stage, date, best_of,"
        " team1, team2, score1, score2, winner)"
        " VALUES (:match_id, :league, :tournament, :stage, :date, :best_of, :team1, :team2,"
        " :score1, :score2, :winner)",
        {"league": "LCK", **{k: match.get(k) for k in (
            "match_id", "tournament", "stage", "date", "best_of",
            "team1", "team2", "score1", "score2", "winner")},
         **({"league": match["league"]} if match.get("league") else {})},
    )


def save_game(conn: sqlite3.Connection, game: dict) -> None:
    """Insert or replace a game with its picks, bans and side stats.

    game = {game_id, league, match_id, game_number, date, patch, tournament, stage,
            team1, team2, winner, duration, source,
            sides: {1: {kills, gold, towers, dragons, barons}, 2: ...},
            picks: [{side, pick_order, role, champion, player?, kills?, ...}],
            bans: [(side, slot, champion)]}
    """
    conn.execute("DELETE FROM games WHERE game_id = ?", (game["game_id"],))
    conn.execute(
        "INSERT INTO games (game_id, league, match_id, game_number, date, patch, tournament,"
        " stage, team1, team2, winner, duration, source)"
        " VALUES (:game_id, :league, :match_id, :game_number, :date, :patch, :tournament,"
        " :stage, :team1, :team2, :winner, :duration, :source)",
        {
            "league": "LCK", "match_id": None, "game_number": None, "date": None, "patch": None,
            "tournament": None, "stage": None, "winner": None, "duration": None,
            "source": "leaguepedia",
            **{k: v for k, v in game.items() if k not in ("picks", "bans", "sides")},
        },
    )
    for side, stats in (game.get("sides") or {}).items():
        conn.execute(
            "INSERT INTO game_sides (game_id, side, kills, gold, towers, dragons, barons)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game["game_id"], side, *(stats.get(k) for k in ("kills", "gold", "towers", "dragons", "barons"))),
        )
    for p in game["picks"]:
        conn.execute(
            "INSERT INTO picks (game_id, side, pick_order, role, champion, player, kills,"
            " deaths, assists, gold, cs, damage, vision)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (game["game_id"], p["side"], p["pick_order"], p["role"], p["champion"],
             *(p.get(k) for k in PICK_STAT_FIELDS)),
        )
    conn.executemany(
        "INSERT INTO bans (game_id, side, slot, champion) VALUES (?, ?, ?, ?)",
        [(game["game_id"], *b) for b in game["bans"]],
    )
