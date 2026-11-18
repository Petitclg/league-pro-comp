import sqlite3
import threading
import time
from collections import OrderedDict

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, dataset, importer, main, riot, soloq, stats, strength
from app.dataset import Filters
from app.riot import RateLimiter, RiotClient
from app.soloq_ingest import Sampler, normalize_settings

from .riot_fake import CHAMPIONS, GOOD_KEY, FakeRiot, make_match, make_timeline


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def no_limit(_routing: str) -> RateLimiter:
    return RateLimiter(limits=())


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Settings, soloq.db and the dataset cache in a temporary folder; the fake Riot API behind importer."""
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(soloq, "SOLOQ_DB_PATH", tmp_path / "soloq.db")
    monkeypatch.setattr(dataset, "_cache", OrderedDict())
    monkeypatch.setattr(dataset, "_loaded_at", {})
    fake = FakeRiot()
    monkeypatch.setattr(importer, "riot_transport", fake.transport())
    monkeypatch.setattr(importer, "riot_limiter", no_limit)
    monkeypatch.setattr(importer, "champion_names", lambda: CHAMPIONS)
    monkeypatch.setattr(importer, "soloq_job", importer.SoloqImportJob())
    monkeypatch.setattr(main, "league_summary", lambda: [])
    return fake


def _client(fake: FakeRiot, key: str = GOOD_KEY, clock: FakeClock | None = None) -> RiotClient:
    clock = clock or FakeClock()
    return RiotClient(key, transport=fake.transport(), limiter=no_limit, sleep=clock.sleep)


# --- rate limiting ------------------------------------------------------------------


def test_rate_limiter_honours_both_windows_of_a_personal_key():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, margin=0)
    sent = []
    for _ in range(130):
        limiter.acquire(clock.sleep)
        sent.append(clock.now)
    assert sent[19] == sent[0] and sent[20] >= sent[0] + 1  # 20 per second
    for i, t in enumerate(sent):
        assert sum(t - 1 < u <= t for u in sent[: i + 1]) <= 20
        assert sum(t - 120 < u <= t for u in sent[: i + 1]) <= 100
    assert sent[100] >= sent[0] + 120  # the 101st request waits for the 2-minute window
    assert sent[99] - sent[0] < 10  # ...but the first 100 go out quickly


def test_rate_limiter_blocks_after_a_429_and_follows_reported_limits():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, margin=0)
    limiter.block_for(30)
    assert limiter.wait_time() == pytest.approx(30)
    clock.now += 30
    assert limiter.wait_time() == 0
    limiter.update_limits("2:10")
    limiter.acquire(clock.sleep)
    limiter.acquire(clock.sleep)
    assert limiter.wait_time() == pytest.approx(10)
    limiter.update_limits("garbage")
    assert limiter.limits == [(2, 10.0)]


def test_client_waits_as_long_as_retry_after_then_retries():
    fake = FakeRiot()
    fake.fail_next = [httpx.Response(429, headers={"Retry-After": "7"})]
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, margin=0)
    waits = []
    client = RiotClient(GOOD_KEY, transport=fake.transport(), limiter=lambda _: limiter, sleep=clock.sleep,
                        on_wait=lambda s, reason: waits.append(s))
    assert client.platform_status("euw1") == {"id": "EUW1"}
    assert len(fake.requests) == 2 and sum(clock.slept) >= 7 and waits
    # Without Retry-After (service busy, not our key): back off on our own
    fake.fail_next = [httpx.Response(429), httpx.Response(429)]
    before = sum(clock.slept)
    assert client.platform_status("euw1")
    assert sum(clock.slept) - before >= 2 + 4


def test_client_errors_and_key_never_in_urls():
    fake = FakeRiot()
    clock = FakeClock()
    client = RiotClient(GOOD_KEY, transport=fake.transport(), limiter=no_limit, sleep=clock.sleep)
    assert client.get("euw1", "/lol/unknown") is None  # 404
    fake.fail_next = [httpx.Response(503), httpx.Response(500)]
    assert client.platform_status("euw1")  # server errors are retried
    assert all(GOOD_KEY not in str(r.url) for r in fake.requests)
    assert all(r.headers["X-Riot-Token"] == GOOD_KEY for r in fake.requests)
    fake.fail_next = [httpx.Response(500)] * 10
    with pytest.raises(riot.RiotError) as failed:
        client.platform_status("euw1")
    assert GOOD_KEY not in str(failed.value)
    bad = RiotClient("RGAPI-expired", transport=fake.transport(), limiter=no_limit, sleep=clock.sleep)
    with pytest.raises(riot.KeyRejected):
        bad.platform_status("euw1")


def test_regional_routing_of_match_requests():
    fake = FakeRiot()
    client = _client(fake)
    client.match("kr", "KR_1")
    client.match_ids("oc1", "oc1-EMERALD-I-1")
    client.league_entries("na1", "DIAMOND", "II")
    hosts = [r.url.host for r in fake.requests]
    assert hosts == ["asia.api.riotgames.com", "sea.api.riotgames.com", "na1.api.riotgames.com"]
    assert fake.requests[1].url.params["queue"] == "420"
    assert {riot.region_of(p) for p in riot.PLATFORMS} == {"europe", "asia", "americas", "sea"}


# --- compact extraction ----------------------------------------------------------------


def test_extract_match_keeps_compact_stats_with_display_names():
    game = soloq.extract_match(make_match("EUW1_1", patch="15.22"), "euw1", CHAMPIONS)
    assert game["patch"] == "15.22" and game["region"] == "europe" and game["duration"] == 30.0
    assert game["winner"] == 1 and game["date"].startswith("2025-11-17")
    assert len(game["players"]) == 10
    for side in (1, 2):
        assert sorted(p["role"] for p in game["players"] if p["side"] == side) == sorted(soloq.ROLES)
    assert all(not p["champion"].startswith("Internal") for p in game["players"])
    assert game["players"][0]["player"] == "Player0#EUW"
    assert game["sides"][1]["towers"] == 7 and game["sides"][2]["grubs"] == 3
    blue_bans = [b for b in game["bans"] if b[0] == 1]
    assert [b[1] for b in blue_bans] == [1, 2, 3, 4, 5]  # ordered by pick turn
    assert [b[2] for b in blue_bans] == ["Champ41", "Champ42", "Champ43", "Champ44", "Champ45"]
    assert len([b for b in game["bans"] if b[0] == 2]) == 4  # a player who did not ban


def test_extract_match_skips_remakes_other_queues_and_unknown_roles():
    with pytest.raises(soloq.Skip, match="queue"):
        soloq.extract_match(make_match("EUW1_2", queue=440), "euw1")
    with pytest.raises(soloq.Skip, match="remake"):
        soloq.extract_match(make_match("EUW1_3", duration=200), "euw1")
    with pytest.raises(soloq.Skip, match="remake"):
        soloq.extract_match(make_match("EUW1_4", remake=True), "euw1")
    # teamPosition missing: the individual position is used
    game = soloq.extract_match(make_match("EUW1_5", positions=[""] * 5), "euw1")
    assert {p["role"] for p in game["players"]} == set(soloq.ROLES)
    broken = make_match("EUW1_6", positions=["TOP"] * 5)
    for p in broken["info"]["participants"]:
        p["individualPosition"] = "Invalid"
    with pytest.raises(soloq.Skip, match="roles"):
        soloq.extract_match(broken, "euw1")


def test_timeline_is_reduced_to_compact_frames_and_events(tmp_path):
    timeline = make_timeline("EUW1_7", minutes=35)
    blobs = soloq.extract_timeline(timeline)
    raw = len(httpx.Response(200, json=timeline).content)
    size = sum(len(b) for b in blobs.values())
    assert size < 40_000 and size < raw / 10

    conn = soloq.connect(tmp_path / "s.db")
    game = soloq.extract_match(make_match("EUW1_7"), "euw1", CHAMPIONS)
    soloq.save_game(conn, game, "DIAMOND", blobs)
    conn.commit()
    tl = soloq.load_timeline(conn, "EUW1_7")
    assert len(tl["frames"]) == 36
    last = tl["frames"][-1]
    assert last[1]["gold"] == 500 + 35 * 400 + 1 and last[2]["cs"] == 35 * 6 + 35
    assert last[3]["damage_dealt"] == 35 * 700 + 3 and last[3]["damage_taken"] == 35 * 650 + 3
    # cumulative K/D/A per minute match the kill events
    kills = tl["kills"]
    assert sum(f["kills"] for f in last.values()) == len(kills) == sum(f["deaths"] for f in last.values())
    minute10 = tl["frames"][10]
    assert sum(f["kills"] for f in minute10.values()) == sum(k["t"] <= 600 for k in kills)
    kinds = {(o["kind"], o["side"], o["detail"]) for o in tl["objectives"]}
    assert ("dragon", 1, "FIRE") in kinds and ("grub", 2, None) in kinds and ("baron", 1, None) in kinds
    assert ("tower", 1, "top") in kinds  # team 200 lost the tower: Blue (side 1) took it
    assert ("plate", 1, "mid") in kinds and ("herald", 2, None) in kinds
    actions = {i["action"] for i in tl["items"]}
    assert actions == {"P", "S", "U", "D"}
    undo = next(i for i in tl["items"] if i["action"] == "U")
    assert undo == {"t": 356, "participant": 4, "action": "U", "item": 1036, "after": 0}
    per_game = conn.execute("SELECT SUM(LENGTH(frames) + LENGTH(kills) + LENGTH(objectives) + LENGTH(items))"
                            " FROM soloq_timelines").fetchone()[0]
    assert per_game == size


# --- sampling -------------------------------------------------------------------------


def _sampler(fake, path, **settings) -> Sampler:
    conn = soloq.connect(path)
    base = {"platforms": ["euw1"], "tiers": ["EMERALD", "DIAMOND"], "patches_kept": 0}
    return Sampler(conn, _client(fake), {**base, **settings}, CHAMPIONS, rng=__import__("random").Random(1))


def _run(sampler: Sampler, max_steps: int = 500) -> int:
    steps = 0
    while steps < max_steps and sampler.step():
        steps += 1
    return steps


def test_ladder_sampling_dedupes_games_and_balances_tiers(tmp_path):
    fake = FakeRiot(page_size=6, per_player=4)
    sampler = _sampler(fake, tmp_path / "s.db")
    _run(sampler)
    conn = sampler.conn
    fetched = [r.url.path for r in fake.requests if r.url.path.startswith("/lol/match/v5/matches/")
               and "by-puuid" not in r.url.path and not r.url.path.endswith("timeline")]
    assert len(fetched) == len(set(fetched))  # no game fetched twice despite shared histories
    games = conn.execute("SELECT match_id, tier FROM soloq_games").fetchall()
    # 5 active players per page (one inactive), 4 divisions per tier, overlapping histories: 2*i .. 2*i+3
    per_division = len({2 * i + k for i in range(1, 6) for k in range(4)})
    assert len(games) == 2 * 4 * per_division
    assert {t for _, t in games} == {"EMERALD", "DIAMOND"}
    assert all(t[:2] == m.split("_")[1][:2] for m, t in games)  # tagged with the tier of the seed that found it
    assert conn.execute("SELECT COUNT(*) FROM soloq_queue").fetchone()[0] == 0
    assert not any("OTHER1" in p for p in fetched)  # games of another server are left out
    assert conn.execute("SELECT COUNT(*) FROM soloq_seeds WHERE crawled_at IS NULL").fetchone()[0] == 0
    # nothing new: a second pass only re-reads histories after RECRAWL_AFTER
    before = len(fake.requests)
    assert not sampler.step() and len(fake.requests) == before


def test_rank_tag_is_the_majority_tier_of_the_seeds_that_found_the_game(tmp_path):
    fake = FakeRiot()
    sampler = _sampler(fake, tmp_path / "s.db")
    sampler.enqueue("euw1", "EMERALD", ["EUW1_X", "EUW1_Y"])
    sampler.enqueue("euw1", "DIAMOND", ["EUW1_X"])
    sampler.enqueue("euw1", "DIAMOND", ["EUW1_Y", "KR_Z"])
    sampler.enqueue("euw1", "EMERALD", ["EUW1_Y"])
    assert sampler.fetch_next("euw1") and sampler.fetch_next("euw1")
    tiers = dict(sampler.conn.execute("SELECT match_id, tier FROM soloq_games").fetchall())
    assert tiers == {"EUW1_X": "DIAMOND", "EUW1_Y": "EMERALD"}  # tie: the higher tier; else the majority
    assert sampler.enqueue("euw1", "MASTER", ["EUW1_X"]) == 0  # already stored
    assert soloq.majority_tier("MASTER,CHALLENGER,MASTER") == "MASTER"


def test_patch_rules_skip_games_without_fetching_their_timeline(tmp_path):
    patches = {"EUW1_A": "15.20", "EUW1_B": "15.21", "EUW1_C": "15.22", "EUW1_D": "15.19", "EUW1_E": "15.23"}
    fake = FakeRiot(patch_of=patches.get)
    sampler = _sampler(fake, tmp_path / "s.db", min_patch="15.20", patches_kept=2)
    sampler.enqueue("euw1", "EMERALD", list(patches))
    while sampler.fetch_next("euw1"):
        pass
    stored = {r[0] for r in sampler.conn.execute("SELECT patch FROM soloq_games")}
    assert stored == {"15.22", "15.23"}  # 15.19 is too old, 15.20/15.21 fell out of the 2 kept patches
    skipped = dict(sampler.conn.execute("SELECT match_id, reason FROM soloq_skipped").fetchall())
    assert skipped == {"EUW1_D": "patch"}
    assert fake.count("EUW1_D/timeline") == 0


def test_interrupted_import_resumes_where_it_stopped(tmp_path):
    path = tmp_path / "s.db"
    fake = FakeRiot()
    first = _sampler(fake, path)

    def queued():
        return first.conn.execute("SELECT COUNT(*) FROM soloq_queue").fetchone()[0]

    while not (first.stored() >= 3 and queued()):  # stop in the middle of a seed's history
        first.step()
    first.conn.close()

    second = _sampler(fake, path)  # a new run (after a restart) on the same database
    _run(second)
    fetched = [r.url.path for r in fake.requests if r.url.path.endswith("timeline")]
    assert len(fetched) == len(set(fetched)) == second.stored()
    ladder_reads = [(r.url.path, r.url.params.get("page")) for r in fake.requests if "league-exp" in r.url.path]
    assert len(ladder_reads) == len(set(ladder_reads))  # no ladder page read twice


def test_settings_are_normalized():
    s = normalize_settings({"platforms": ["kr", "nope", "kr"], "tiers": ["CHALLENGER", "IRON", "EMERALD"],
                            "target": 0, "patches_kept": -1, "other": 1})
    assert s["platforms"] == ["kr"] and s["tiers"] == ["EMERALD", "CHALLENGER"]
    assert s["target"] == 1 and s["patches_kept"] == 0 and "other" not in s


# --- background job -----------------------------------------------------------------------


def _wait(job, timeout=10.0):
    end = time.time() + timeout
    while job.running and time.time() < end:
        time.sleep(0.01)


def test_import_job_runs_to_the_target_and_reports_progress(isolated):
    job = importer.soloq_job
    assert job.start(GOOD_KEY, {"platforms": ["euw1"], "tiers": ["MASTER"], "target": 10})
    assert not job.start(GOOD_KEY, {})  # one at a time
    _wait(job)
    status = job.status()
    assert status["state"] == "done" and status["games"] == 10 and status["added"] == 10, status
    assert status["log"] and status["error"] is None
    conn = soloq.connect()
    assert soloq.get_state(conn, "running") is None  # finished: nothing to resume
    conn.close()


def test_import_job_stops_on_a_rejected_key_and_flags_it(isolated):
    importer.store_key(GOOD_KEY)
    job = importer.soloq_job
    job.start("RGAPI-expired-key", {"target": 10})
    _wait(job)
    status = job.status()
    assert status["state"] == "error" and status["key_rejected"] and "24 h" in status["error"]
    assert importer.key_status()["state"] == "invalid"


def test_cancel_interrupts_a_rate_limit_wait_and_unfinished_import_resumes(isolated, monkeypatch):
    isolated.fail_next = [httpx.Response(429, headers={"Retry-After": "600"})]
    job = importer.soloq_job
    job.start(GOOD_KEY, {"target": 10})
    end = time.time() + 5
    while not job.status().get("waiting") and time.time() < end:
        time.sleep(0.01)
    assert job.status()["waiting"]["seconds"] >= 600
    job.cancel()
    _wait(job, 2)
    assert job.status()["state"] == "cancelled"

    # A run that the app's closing interrupted left the flag: the next launch resumes it
    importer.store_key(GOOD_KEY)
    conn = soloq.connect()
    soloq.set_state(conn, "running", "1")
    conn.close()
    started = []
    monkeypatch.setattr(importer.soloq_job, "start", lambda key, settings, resumed=False: started.append(resumed) or True)
    assert importer.resume_soloq() and started == [True]


# --- dataset and API ----------------------------------------------------------------------


def _fill(path, n=60):
    conn = soloq.connect(path)
    tiers = ["EMERALD", "DIAMOND", "MASTER"]
    for i in range(n):
        match_id = f"EUW1_{i}"
        game = soloq.extract_match(make_match(match_id, patch="15.21" if i % 4 else "15.22",
                                              winner=100 if i % 2 else 200), "euw1", CHAMPIONS)
        soloq.save_game(conn, game, tiers[i % 3], soloq.extract_timeline(make_timeline(match_id, 20)))
    conn.commit()
    return conn


def test_soloq_games_load_into_the_shared_dataset(tmp_path):
    conn = _fill(tmp_path / "s.db")
    ds = soloq.load(conn)
    assert len(ds.games) == 60 and len(ds.series) == 60 and ds.matches == {}
    assert not ds.fearless_tournaments and ds.teams == ["Blue", "Red"]
    g = ds.games[0]
    assert g["league"] == "SOLOQ" and g["tournament"] == "EUW" and g["rank"] in {"EMERALD", "DIAMOND", "MASTER"}
    assert [p["role"] for p in g["sides"][1]["picks"]] == soloq.ROLES
    diamond = ds.filter_games(Filters(ranks=("DIAMOND",)))
    assert len(diamond) == 20 and all(x["rank"] == "DIAMOND" for x in diamond)
    recent = ds.filter_games(Filters(ranks=("DIAMOND", "MASTER"), patches=("15.22",)))
    assert recent and all(x["rank"] in ("DIAMOND", "MASTER") and x["patch"] == "15.22" for x in recent)
    table = stats.champion_table(ds, Filters(ranks=("EMERALD",)))
    assert sum(r["picks"] for r in table) == 20 * 10
    assert sum(r["bans"] for r in table) == 20 * 9
    assert stats.records(ds, Filters())
    assert stats.pair_stats(ds.filter_comps(Filters()), 2, None, None, None, "games", 10, 0)
    wukong = stats.champion_detail(ds, "Wukong", Filters())
    assert wukong["picks"] > 0 and wukong["stats"]["cs_min"] and wukong["players"][0]["player"].endswith("#EUW")
    # no draft order, series, format or team in solo queue: the rank replaces them in the spots
    comp = ds.filter_comps(Filters())[0]
    dims = {d for d, _, _ in strength._spot_keys(comp, "mid", None)}
    assert dims == {"Side", "Rôle", "Rang", "Patch", "Joueur"}


def test_retention_keeps_the_most_recent_patches(tmp_path):
    conn = _fill(tmp_path / "s.db", 9)
    assert soloq.purge_old_patches(conn, 1) == 6
    assert {r[0] for r in conn.execute("SELECT patch FROM soloq_games")} == {"15.22"}
    assert conn.execute("SELECT COUNT(*) FROM soloq_players").fetchone()[0] == 30  # children deleted too
    assert conn.execute("SELECT COUNT(*) FROM soloq_timelines").fetchone()[0] == 3


def test_api_soloq_scope_with_rank_filter(isolated):
    _fill(soloq.SOLOQ_DB_PATH).close()
    client = TestClient(main.app)
    meta = client.get("/api/meta?league=SOLOQ").json()
    assert meta["soloq"] and meta["selected"] == ["SOLOQ"] and meta["games"] == 60
    assert meta["ranks"] == ["EMERALD", "DIAMOND", "MASTER"]
    assert [t["name"] for t in meta["tournaments"]] == ["EUW"] and meta["fearless_since"] is None
    assert any(lg["key"] == "SOLOQ" and lg["games"] == 60 for lg in meta["leagues"])
    assert client.get("/api/meta?league=SOLOQ&league=LCK").status_code == 422  # never mixed with pro games
    all_rows = client.get("/api/champions?league=SOLOQ").json()
    master = client.get("/api/champions?league=SOLOQ&rank=MASTER").json()
    assert sum(r["picks"] for r in all_rows) == 600 and sum(r["picks"] for r in master) == 200
    series = client.get("/api/series?league=SOLOQ&rank=DIAMOND&limit=5").json()
    assert series["total"] == 20 and series["series"][0]["rank"] == "DIAMOND"
    assert series["series"][0]["teams"] == ["Blue", "Red"]
    assert client.get("/api/pairs?league=SOLOQ&rank=EMERALD").status_code == 200
    assert client.get("/api/records?league=SOLOQ").status_code == 200
    champ = all_rows[0]["champion"]
    assert client.get(f"/api/champions/{champ}?league=SOLOQ&rank=DIAMOND").status_code == 200
    assert client.post("/api/suggest?league=SOLOQ", json={"picks": {"mid": champ}, "ranks": ["MASTER"]}).status_code == 200
    # draft-order analyses get placeholder slots: they answer without failing
    pair = client.get("/api/pairs?league=SOLOQ&limit=1").json()["pairs"][0]
    a, b = pair["a"]["champion"], pair["b"]["champion"]
    assert client.get(f"/api/duo?league=SOLOQ&kind=synergy&a={a}&b={b}").status_code == 200
    assert client.get(f"/api/denial?league=SOLOQ&a={a}&b={b}").status_code == 200
    assert client.get("/api/countered-synergies?league=SOLOQ").status_code == 200


def test_api_key_endpoints_never_expose_the_key(isolated):
    client = TestClient(main.app)
    assert client.get("/api/soloq").json()["key"]["state"] == "missing"
    assert client.put("/api/soloq/key", json={"key": "not a key"}).status_code == 422
    rejected = client.put("/api/soloq/key", json={"key": "RGAPI-ffffffff-ffff"})
    assert rejected.status_code == 400 and "expired" in rejected.json()["detail"]
    assert importer.riot_key() is None  # a rejected key is not stored

    ok = client.put("/api/soloq/key", json={"key": GOOD_KEY})
    assert ok.status_code == 200 and ok.json() == {"state": "valid", "masked": "RGAPI-…4444",
                                                   "checked_at": ok.json()["checked_at"]}
    overview = client.get("/api/soloq")
    assert GOOD_KEY not in overview.text and overview.json()["key"]["state"] == "valid"
    assert config.load_settings()["riot_api_key"] == GOOD_KEY  # kept in the local settings file only
    assert {p["key"] for p in overview.json()["platforms"]} >= {"euw1", "kr", "na1"}

    isolated.key = "RGAPI-rotated"  # the 24-hour development key expired
    assert client.post("/api/soloq/key/check").json()["state"] == "invalid"
    assert client.post("/api/soloq/import").status_code == 400
    assert client.delete("/api/soloq/key").json()["state"] == "missing"
    assert client.post("/api/soloq/import").status_code == 400


def test_api_settings_and_import_start(isolated, monkeypatch):
    client = TestClient(main.app)
    assert client.put("/api/soloq/settings", json={"platforms": ["xx1"]}).status_code == 422
    assert client.put("/api/soloq/settings", json={"tiers": ["IRON"]}).status_code == 422
    assert client.put("/api/soloq/settings", json={"min_patch": "latest"}).status_code == 422
    saved = client.put("/api/soloq/settings", json={"platforms": ["kr", "euw1"], "tiers": ["CHALLENGER"],
                                                     "target": 200, "min_patch": "15.22"}).json()
    assert saved["platforms"] == ["kr", "euw1"] and importer.soloq_settings()["target"] == 200
    client.put("/api/soloq/key", json={"key": GOOD_KEY})
    started = []
    monkeypatch.setattr(importer.soloq_job, "start", lambda key, settings, resumed=False: started.append(settings) or True)
    assert client.post("/api/soloq/import").status_code == 200
    assert started[-1]["tiers"] == ["CHALLENGER"]
    assert client.post("/api/soloq/import", json={"platforms": ["na1"], "target": 50}).status_code == 200
    assert started[-1]["platforms"] == ["na1"] and importer.soloq_settings()["target"] == 50
    monkeypatch.setattr(importer.soloq_job, "start", lambda *a, **k: False)
    assert client.post("/api/soloq/import").status_code == 409


def test_soloq_dataset_is_reloaded_at_most_every_minute(isolated, monkeypatch):
    _fill(soloq.SOLOQ_DB_PATH, 3).close()
    first = dataset.get_dataset(("SOLOQ",))
    assert len(first.games) == 3
    _fill(soloq.SOLOQ_DB_PATH, 6).close()  # the import keeps writing
    assert dataset.get_dataset(("SOLOQ",)) is first
    monkeypatch.setattr(dataset, "SOLOQ_RELOAD_SECONDS", 0)
    assert len(dataset.get_dataset(("SOLOQ",)).games) == 6


def test_soloq_db_is_separate_from_the_pro_db(isolated):
    conn = soloq.connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "soloq_games" in tables and "games" not in tables
    assert soloq.SOLOQ_DB_PATH.name == "soloq.db"
    conn = soloq.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO soloq_players (match_id, side, role, participant, champion)"
                         " VALUES ('nope', 1, 'top', 1, 'X')")
    finally:
        conn.close()
