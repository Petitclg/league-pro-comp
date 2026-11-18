"""Solo queue sampling through the Riot API: ladder -> seed players -> their recent ranked games.

One `Sampler.step()` does one small unit of work (fetch a queued game, read a seed's match history
or enumerate a ladder page), so an import can stop at any time. Everything it learns is written to
`soloq.db` (ladder cursors, seeds, queued match ids, skipped ids): a stopped or interrupted import
resumes where it was, and a game is never fetched twice.

Rank tagging: a game is tagged with the tier of the seed player(s) whose history contained it (the
usual approximation, since Match-V5 gives no rank). Seeds are picked in the tier that has the
fewest games so far, so every selected tier fills up evenly.
"""
import random
import sqlite3
import time
from collections.abc import Callable

from . import soloq
from .riot import PLATFORMS, TIERS, RiotClient, divisions_of, label_of

SEEDS_PER_PAGE = 20  # players sampled from each ladder page (205 players)
LADDER_PAGE_SIZE = 205
MAX_LADDER_PAGES = 20  # per tier/division; Emerald IV alone has hundreds of pages
MATCHES_PER_SEED = 20
RECRAWL_AFTER = 12 * 3600  # a seed's history is read again after this long once the ladder is exhausted

DEFAULT_SETTINGS = {
    "platforms": ["euw1"],
    "tiers": list(TIERS),
    "target": 5000,  # games to keep in soloq.db
    "min_patch": "",  # e.g. "15.22": older games are not imported
    "patches_kept": 3,  # only the games of the N most recent patches are kept (0: all)
    "max_age_days": 30,  # match histories are read back this far
}


def normalize_settings(raw: dict | None) -> dict:
    """Stored or submitted settings, completed with defaults and stripped of unknown values."""
    s = {**DEFAULT_SETTINGS, **{k: v for k, v in (raw or {}).items() if k in DEFAULT_SETTINGS}}
    s["platforms"] = [p for p in dict.fromkeys(s["platforms"] or []) if p in PLATFORMS] or list(DEFAULT_SETTINGS["platforms"])
    s["tiers"] = [t for t in TIERS if t in (s["tiers"] or [])] or list(TIERS)
    s["target"] = max(1, int(s["target"]))
    s["patches_kept"] = max(0, int(s["patches_kept"]))
    s["max_age_days"] = max(1, int(s["max_age_days"]))
    s["min_patch"] = (s["min_patch"] or "").strip()
    return s


class Sampler:
    def __init__(self, conn: sqlite3.Connection, client: RiotClient, settings: dict, names: dict[int, str] | None = None,
                 rng: random.Random | None = None, now: Callable[[], float] = time.time,
                 on_game: Callable[[dict, str | None], None] | None = None,
                 on_activity: Callable[[str], None] | None = None):
        self.conn = conn
        self.client = client
        self.settings = normalize_settings(settings)
        self.names = names or {}
        self.rng = rng or random.Random()
        self.now = now
        self.on_game = on_game
        self.on_activity = on_activity
        self._turn = 0

    # --- bookkeeping ------------------------------------------------------

    def stored(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM soloq_games").fetchone()[0]

    def _activity(self, text: str) -> None:
        if self.on_activity:
            self.on_activity(text)

    def _tier_load(self, platform: str) -> dict[str, int]:
        """Games stored or queued per selected tier: the emptiest tier gets the next seed."""
        load = dict.fromkeys(self.settings["tiers"], 0)
        for tier, n in self.conn.execute("SELECT tier, COUNT(*) FROM soloq_games WHERE platform = ? GROUP BY tier",
                                         (platform,)):
            if tier in load:
                load[tier] += n
        for (tiers,) in self.conn.execute("SELECT tiers FROM soloq_queue WHERE platform = ?", (platform,)):
            tier = soloq.majority_tier(tiers)
            if tier in load:
                load[tier] += 1
        return load

    # --- one step ----------------------------------------------------------

    def step(self) -> bool:
        """Do one unit of work on the next platform. False when no platform has anything left."""
        platforms = self.settings["platforms"]
        for i in range(len(platforms)):
            platform = platforms[(self._turn + i) % len(platforms)]
            if self._step_platform(platform):
                self._turn = (self._turn + i + 1) % len(platforms)
                return True
        return False

    def _step_platform(self, platform: str) -> bool:
        if self.fetch_next(platform):
            return True
        load = self._tier_load(platform)
        for tier in sorted(load, key=lambda t: (load[t], TIERS.index(t))):
            if self.crawl_next_seed(platform, tier) or self.enumerate_ladder(platform, tier):
                return True
        return self.recycle_seeds(platform)

    def fetch_next(self, platform: str) -> bool:
        """Fetch the oldest queued game of the platform (match, then timeline): 2 requests."""
        row = self.conn.execute(
            "SELECT match_id, tiers FROM soloq_queue WHERE platform = ? ORDER BY discovered_at, match_id LIMIT 1",
            (platform,)).fetchone()
        if not row:
            return False
        match_id, tiers = row
        self._activity(f"{label_of(platform)} · partie {match_id}")
        reason = None
        try:
            match = self.client.match(platform, match_id)
            if match is None:
                raise soloq.Skip("missing")
            game = soloq.extract_match(match, platform, self.names)
            reason = self._patch_refused(game["patch"])
            if reason:
                raise soloq.Skip(reason)
            timeline = self.client.timeline(platform, match_id)
            blobs = soloq.extract_timeline(timeline) if timeline else None
        except soloq.Skip as skip:
            self.conn.execute("INSERT OR REPLACE INTO soloq_skipped (match_id, reason) VALUES (?, ?)",
                              (match_id, str(skip)))
            self.conn.execute("DELETE FROM soloq_queue WHERE match_id = ?", (match_id,))
            self.conn.commit()
            return True
        tier = soloq.majority_tier(tiers)
        soloq.save_game(self.conn, game, tier, blobs)
        self.conn.execute("DELETE FROM soloq_queue WHERE match_id = ?", (match_id,))
        self.conn.commit()
        soloq.purge_old_patches(self.conn, self.settings["patches_kept"])
        if self.on_game:
            self.on_game(game, tier)
        return True

    def _patch_refused(self, patch: str | None) -> str | None:
        key = soloq.patch_key(patch)
        if not key:
            return "patch"
        minimum = soloq.patch_key(self.settings["min_patch"])
        if minimum and key < minimum:
            return "patch"
        keep = self.settings["patches_kept"]
        if keep:
            kept = soloq.kept_patches(self.conn, keep)
            if len(kept) >= keep and key < soloq.patch_key(kept[-1]):
                return "patch"  # would be purged right away
        return None

    def crawl_next_seed(self, platform: str, tier: str) -> bool:
        """Queue the recent ranked games of one not yet crawled seed of `tier`: 1 request."""
        row = self.conn.execute(
            "SELECT puuid FROM soloq_seeds WHERE platform = ? AND tier = ? AND crawled_at IS NULL"
            " ORDER BY rowid LIMIT 1", (platform, tier)).fetchone()
        if not row:
            return False
        puuid = row[0]
        self._activity(f"{label_of(platform)} · historique d'un joueur {tier.title()}")
        since = int(self.now() - self.settings["max_age_days"] * 86400)
        ids = self.client.match_ids(platform, puuid, MATCHES_PER_SEED, since)
        self.enqueue(platform, tier, ids)
        self.conn.execute("UPDATE soloq_seeds SET crawled_at = ? WHERE platform = ? AND puuid = ?",
                          (self.now(), platform, puuid))
        self.conn.commit()
        return True

    def enqueue(self, platform: str, tier: str, match_ids: list[str]) -> int:
        """Queue new match ids; ids already stored or skipped are dropped, already queued ones get the tier too."""
        added = 0
        prefix = f"{platform.upper()}_"
        for match_id in match_ids:
            if not match_id.startswith(prefix):
                continue  # a game played on another server (transferred account)
            if self.conn.execute("SELECT 1 FROM soloq_games WHERE match_id = ? UNION ALL "
                                 "SELECT 1 FROM soloq_skipped WHERE match_id = ?", (match_id, match_id)).fetchone():
                continue
            queued = self.conn.execute("SELECT tiers FROM soloq_queue WHERE match_id = ?", (match_id,)).fetchone()
            if queued:
                self.conn.execute("UPDATE soloq_queue SET tiers = ? WHERE match_id = ?", (f"{queued[0]},{tier}", match_id))
            else:
                self.conn.execute("INSERT INTO soloq_queue (match_id, platform, tiers, discovered_at) VALUES (?, ?, ?, ?)",
                                  (match_id, platform, tier, self.now()))
                added += 1
        return added

    def enumerate_ladder(self, platform: str, tier: str) -> bool:
        """Read the next ladder page of `tier` (rotating over its divisions) and sample seeds from it."""
        for division in divisions_of(tier):
            self.conn.execute("INSERT OR IGNORE INTO soloq_ladder (platform, tier, division) VALUES (?, ?, ?)",
                              (platform, tier, division))
        row = self.conn.execute(
            "SELECT division, next_page FROM soloq_ladder WHERE platform = ? AND tier = ? AND exhausted = 0"
            " ORDER BY next_page, division LIMIT 1", (platform, tier)).fetchone()
        if not row:
            return False
        division, page = row
        self._activity(f"{label_of(platform)} · classement {tier.title()} {division}, page {page}")
        entries = self.client.league_entries(platform, tier, division, page)
        active = [e for e in entries if not e.get("inactive")]
        for entry in self.rng.sample(active, min(SEEDS_PER_PAGE, len(active))):
            puuid = self.client.puuid_of(platform, entry)
            if puuid:
                self.conn.execute(
                    "INSERT OR IGNORE INTO soloq_seeds (platform, puuid, tier, division) VALUES (?, ?, ?, ?)",
                    (platform, puuid, tier, division))
        exhausted = len(entries) < LADDER_PAGE_SIZE or page >= MAX_LADDER_PAGES
        self.conn.execute("UPDATE soloq_ladder SET next_page = ?, exhausted = ? WHERE platform = ? AND tier = ?"
                          " AND division = ?", (page + 1, int(exhausted), platform, tier, division))
        self.conn.commit()
        return True

    def recycle_seeds(self, platform: str) -> bool:
        """Once every ladder page was read, seeds crawled long enough ago are read again (new games)."""
        tiers = self.settings["tiers"]
        marks = ",".join("?" for _ in tiers)
        n = self.conn.execute(
            f"UPDATE soloq_seeds SET crawled_at = NULL WHERE platform = ? AND tier IN ({marks}) AND crawled_at < ?",
            (platform, *tiers, self.now() - RECRAWL_AFTER)).rowcount
        self.conn.commit()
        return n > 0
