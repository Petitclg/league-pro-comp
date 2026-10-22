"""Desktop launcher: update at startup, run the API in the background, open the app in its own window
and quit when that window is closed.

    python -m app.desktop        (from backend/, after building the front-end)
"""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

from . import runtime, updater
from .config import APP_NAME, DATA_DIR, FROZEN, VERSION

PREFERRED_PORT = 8765
HEARTBEAT_TIMEOUT = 150  # seconds: browsers slow background timers down to once a minute
FIRST_BEAT_TIMEOUT = 90


def setup_logging() -> None:
    """A windowed executable has no console: keep what would be printed in a log file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = DATA_DIR / "app.log"
    if log.exists() and log.stat().st_size > 1_000_000:
        log.replace(DATA_DIR / "app.log.1")
    if FROZEN or sys.stdout is None or sys.stderr is None:
        stream = open(log, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = stream
    print(f"--- {APP_NAME} {VERSION} started {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


def running_version(port: int) -> str | None:
    """Version of the instance already serving `port`, or None when there is none."""
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=0.7)
        data = response.json()
        return data.get("version", "") if response.status_code == 200 and data.get("app") == APP_NAME else None
    except Exception:
        return None


def already_running(port: int) -> bool:
    return running_version(port) is not None


def replace_outdated_instance(port: int) -> bool:
    """A newer release exists and an older instance holds the port: ask it to quit. True once the port is free."""
    exe = updater.current_exe()
    if exe is None or os.environ.get("LOLPRO_NO_UPDATE"):
        return False
    release = updater.check()
    if not release or not updater.is_newer(release["version"], running_version(port) or ""):
        return False
    try:
        response = httpx.post(f"http://127.0.0.1:{port}/api/quit", headers={"X-Requested-With": APP_NAME}, timeout=3)
    except Exception:
        return False
    if response.status_code != 200:  # busy (import running) or too old to know /api/quit
        return False
    deadline = time.time() + 15
    while time.time() < deadline:
        if not already_running(port):
            return True
        time.sleep(0.3)
    return False


def pick_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def find_browser() -> str | None:
    """Edge ships with Windows 10/11; Chrome is the fallback. Both can open an app-style window."""
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    relative = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")
    for rel in relative:
        for root in filter(None, roots):
            candidate = Path(root) / rel
            if candidate.exists():
                return str(candidate)
    return shutil.which("msedge") or shutil.which("chrome")


def open_window(url: str) -> subprocess.Popen | None:
    browser = find_browser()
    if not browser:
        webbrowser.open(url)
        return None
    profile = DATA_DIR / "window"  # its own profile: the window is a separate process we can wait for
    return subprocess.Popen([
        browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--window-size=1500,950",
    ])


# --- update ------------------------------------------------------------------------------------

def update_with_splash(release: dict, exe: Path) -> bool:
    """Download the new version behind a small progress window. True once it is ready to install."""
    import tkinter as tk
    from tkinter import ttk

    state = {"done": 0, "total": 0, "finished": False, "ok": False}
    new = exe.with_name(exe.name + ".new")

    def work() -> None:
        try:
            def progress(done: int, total: int) -> None:
                state["done"], state["total"] = done, total

            updater.download(release, new, on_progress=progress)
            state["ok"] = True
        except Exception as exc:
            print(f"update failed: {exc}", flush=True)
        state["finished"] = True

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#0f131a")
    width, height = 420, 120
    root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 2}")
    tk.Label(root, text=f"{APP_NAME}", fg="#c8a24a", bg="#0f131a", font=("Segoe UI", 13, "bold")).pack(pady=(14, 0))
    tk.Label(root, text=f"Mise à jour vers {release['version']}…", fg="#e6e6e6", bg="#0f131a", font=("Segoe UI", 10)).pack()
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Update.Horizontal.TProgressbar", background="#c8a24a", troughcolor="#2a3140", borderwidth=0)
    bar = ttk.Progressbar(root, length=360, mode="determinate", style="Update.Horizontal.TProgressbar")
    bar.pack(pady=12)

    def poll() -> None:
        if state["total"]:
            bar["value"] = 100 * state["done"] / state["total"]
        if state["finished"]:
            root.destroy()
        else:
            root.after(100, poll)

    threading.Thread(target=work, daemon=True).start()
    poll()
    root.mainloop()
    return state["ok"]


def update_at_startup() -> None:
    """Newer release on GitHub? Download it, swap the executable and restart into it."""
    exe = updater.current_exe()
    if exe is None or os.environ.get("LOLPRO_NO_UPDATE"):
        return
    updater.cleanup(exe)
    release = updater.check()
    if not release:
        return
    print(f"update available: {release['version']}", flush=True)
    try:
        if update_with_splash(release, exe):
            updater.install(exe.with_name(exe.name + ".new"), exe)
            updater.restart(exe)
    except Exception as exc:  # not writable, antivirus lock...: keep running the current version
        print(f"update not installed: {exc}", flush=True)


# --- server and window -------------------------------------------------------------------------

def start_server(port: int):
    import uvicorn

    from .main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None,
                            http="h11", ws="none", loop="asyncio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("the server did not start")
    return server


def wait_for_window(window: subprocess.Popen | None) -> None:
    """Block until the user closes the app."""
    started = time.monotonic()
    while True:
        time.sleep(1)
        if window is not None and window.poll() is not None:
            if time.monotonic() - started > 5:
                return  # the window was closed
            window = None  # the browser handed over to another process: rely on the heartbeat
        if runtime.quit_requested:
            return
        if window is None:
            since = runtime.seconds_since_beat()
            if since is None and time.monotonic() - started > FIRST_BEAT_TIMEOUT:
                return
            if since is not None and since > HEARTBEAT_TIMEOUT:
                return


def main() -> None:
    setup_logging()
    runtime.desktop = True
    if "--after-update" in sys.argv:
        time.sleep(1.5)  # let the previous version finish exiting
    if already_running(PREFERRED_PORT) and not replace_outdated_instance(PREFERRED_PORT):
        open_window(f"http://127.0.0.1:{PREFERRED_PORT}/")
        return
    update_at_startup()
    port = pick_port()
    server = start_server(port)
    window = open_window(f"http://127.0.0.1:{port}/")
    try:
        wait_for_window(window)
    finally:
        if runtime.quit_requested and window is not None:
            window.terminate()  # the stale window would otherwise outlive the server
        server.should_exit = True
        print("bye", flush=True)


if __name__ == "__main__":
    main()
