"""Update at startup, the way Discord does it: look for a newer GitHub release, download it, swap the
executable and restart. Everything is best effort: offline or any error simply starts the current version."""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from .config import VERSION

REPO = os.environ.get("LOLPRO_UPDATE_REPO", "Petitclg/league-pro-comp")  # "<github user>/<repository>"
ASSET_NAME = "LoLProComp.exe"
API_BASE = os.environ.get("LOLPRO_UPDATE_API", "https://api.github.com")
USER_AGENT = f"league-pro-comp/{VERSION}"


def parse_version(text: str | None) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); anything else (a dev build, a pre-release tag) -> ()."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def is_newer(latest: str | None, current: str = VERSION) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def latest_release(client: httpx.Client, repo: str = REPO) -> dict | None:
    """The newest published release with its executable: {version, url, size, sha256, notes}."""
    if repo.startswith("OWNER/"):
        return None
    response = client.get(f"{API_BASE}/repos/{repo}/releases/latest", headers={"Accept": "application/vnd.github+json"})
    if response.status_code != 200:
        return None
    data = response.json()
    assets = {a["name"]: a for a in data.get("assets", [])}
    exe = assets.get(ASSET_NAME)
    if not exe:
        return None
    sha = (exe.get("digest") or "").removeprefix("sha256:") or None
    if not sha and f"{ASSET_NAME}.sha256" in assets:
        text = client.get(assets[f"{ASSET_NAME}.sha256"]["browser_download_url"]).text
        sha = text.split()[0] if text.split() else None
    return {"version": data.get("tag_name", ""), "url": exe["browser_download_url"], "size": exe.get("size"),
            "sha256": sha, "notes": data.get("body") or ""}


def check(current: str = VERSION, repo: str = REPO, timeout: float = 4.0, transport=None) -> dict | None:
    """A release newer than `current`, or None (up to date, offline, rate limited, no release yet...)."""
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True,
                          transport=transport) as client:
            release = latest_release(client, repo)
    except Exception:
        return None
    return release if release and is_newer(release["version"], current) else None


def download(release: dict, dest: Path, on_progress=None, transport=None) -> None:
    """Download the release executable to `dest`, checking its size and SHA-256 when known."""
    digest = hashlib.sha256()
    done = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True,
                      transport=transport) as client:
        with client.stream("GET", release["url"]) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or release.get("size") or 0)
            with open(dest, "wb") as out:
                for chunk in response.iter_bytes(1 << 16):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    if release.get("size") and done != release["size"]:
        raise OSError(f"incomplete download ({done} of {release['size']} bytes)")
    if release.get("sha256") and digest.hexdigest().lower() != release["sha256"].lower():
        raise OSError("checksum mismatch")


def install(new_exe: Path, current_exe: Path) -> None:
    """Swap the executable. Windows lets a running .exe be renamed, but not overwritten."""
    old = current_exe.with_name(current_exe.name + ".old")
    if old.exists():
        old.unlink()
    current_exe.rename(old)
    try:
        new_exe.replace(current_exe)
    except OSError:
        old.rename(current_exe)  # put the working version back
        raise


def cleanup(current_exe: Path) -> None:
    """Remove what a previous update left behind."""
    for suffix in (".old", ".new"):
        try:
            current_exe.with_name(current_exe.name + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def restart(exe: Path) -> None:
    """Start the new version and leave. The environment reset is what makes a PyInstaller one-file
    executable unpack itself again instead of reusing the folder of the process that exits."""
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe), "--after-update"], env=env, creationflags=flags, close_fds=True)
    os._exit(0)


def current_exe() -> Path | None:
    return Path(sys.executable) if getattr(sys, "frozen", False) else None
