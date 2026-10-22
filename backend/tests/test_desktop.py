import threading
import time
from collections import OrderedDict

from fastapi.testclient import TestClient

from app import dataset, importer, main, runtime
from app.dataset import load

from .synth import build_db


def _wait(job, timeout=5.0):
    end = time.time() + timeout
    while job.running and time.time() < end:
        time.sleep(0.01)


def test_import_job_reports_progress_and_finishes(monkeypatch):
    seen = {}
    gate = threading.Event()

    def fake_ingest(leagues, years, on_step=None, cancelled=None):
        seen["args"] = (leagues, years)
        gate.wait(2)
        for i, year in enumerate(years, 1):
            on_step(leagues[0], year, 10, 1, i, len(years))
        return 10 * len(years)

    monkeypatch.setattr(importer, "ingest", fake_ingest)
    job = importer.ImportJob()
    assert job.start(["LCK"], 2024) and not job.start(["LCK"], 2024)  # one import at a time
    gate.set()
    _wait(job)
    status = job.status()
    assert status["state"] == "done" and status["done"] == status["total"] and status["games"] >= 10
    assert status["error"] is None and status["log"] and seen["args"][0] == ["LCK"]
    assert not dataset._reload_paused  # datasets reload again once the import is over


def test_import_job_error_and_cancel(monkeypatch):
    def failing(leagues, years, on_step=None, cancelled=None):
        raise RuntimeError("Leaguepedia down")

    monkeypatch.setattr(importer, "ingest", failing)
    job = importer.ImportJob()
    job.start(["LCK"], 2025)
    _wait(job)
    assert job.status()["state"] == "error" and "Leaguepedia down" in job.status()["error"]
    assert not dataset._reload_paused

    def waits_for_cancel(leagues, years, on_step=None, cancelled=None):
        while not cancelled():
            time.sleep(0.01)

    monkeypatch.setattr(importer, "ingest", waits_for_cancel)
    job = importer.ImportJob()
    job.start(["LCK"], 2025)
    job.cancel()
    _wait(job)
    assert job.status()["state"] == "cancelled"


def test_import_endpoints(monkeypatch):
    started = {}
    monkeypatch.setattr(importer.job, "start", lambda leagues, first_year: started.update(leagues=leagues, year=first_year) or True)
    monkeypatch.setattr(main, "league_summary", lambda: [{"league": "LPL"}, {"league": "LCK"}, {"league": "OTHER"}])
    client = TestClient(main.app)
    assert client.post("/api/import", json={"leagues": ["NOPE"]}).status_code == 422
    assert client.post("/api/import", json={"first_year": 1999}).status_code == 422
    assert client.post("/api/import", json={"leagues": ["LEC"], "first_year": 2025}).status_code == 200
    assert started == {"leagues": ["LEC"], "year": 2025}
    client.post("/api/import", json={})  # no league: those already in the database
    assert started["leagues"] == ["LPL", "LCK"] and started["year"] == 2017
    monkeypatch.setattr(importer.job, "start", lambda *a: False)
    assert client.post("/api/import", json={}).status_code == 409
    assert client.get("/api/import").json()["state"] in {"idle", "done", "error", "cancelled", "running"}


def test_health_heartbeat_and_version():
    client = TestClient(main.app)
    assert client.get("/api/health").json()["app"] == "LoL Pro Comp"
    assert client.post("/api/heartbeat").json() == {"ok": True}
    assert runtime.seconds_since_beat() < 5


def test_loaded_data_is_kept_while_an_import_rewrites_the_database(monkeypatch, tmp_path):
    ds = load(build_db(30))
    monkeypatch.setattr(dataset, "_cache", OrderedDict({("LCK",): (1.0, ds)}))
    monkeypatch.setattr(dataset, "DB_PATH", tmp_path / "missing.db")  # mtime 0: differs from the cached 1.0
    dataset.set_reload_paused(True)
    try:
        assert dataset.get_dataset(("LCK",)) is ds
    finally:
        dataset.set_reload_paused(False)


def test_quit_endpoint_is_guarded(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(runtime, "quit_requested", False)
    monkeypatch.setattr(runtime, "desktop", True)
    assert client.post("/api/quit").status_code == 403  # no custom header: a web page could send this
    monkeypatch.setattr(type(importer.job), "running", property(lambda self: True))
    assert client.post("/api/quit", headers={"X-Requested-With": "LoL Pro Comp"}).status_code == 409  # import running
    monkeypatch.setattr(type(importer.job), "running", property(lambda self: False))
    assert client.post("/api/quit", headers={"X-Requested-With": "LoL Pro Comp"}).json() == {"ok": True}
    assert runtime.quit_requested


def test_newer_launch_replaces_outdated_instance(monkeypatch):
    from app import desktop, updater

    posted = []
    alive = {"up": True}
    monkeypatch.setattr(updater, "current_exe", lambda: object())
    monkeypatch.setattr(updater, "check", lambda: {"version": "v0.3.0"})
    monkeypatch.setattr(desktop, "running_version", lambda port: "0.2.0" if alive["up"] else None)

    def fake_post(url, **kw):
        posted.append(url)
        alive["up"] = False
        return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(desktop.httpx, "post", fake_post)
    assert desktop.replace_outdated_instance(8765) and posted == ["http://127.0.0.1:8765/api/quit"]

    alive["up"] = True
    monkeypatch.setattr(desktop, "running_version", lambda port: "0.3.0")  # already the latest: leave it alone
    assert not desktop.replace_outdated_instance(8765)
