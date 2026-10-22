import hashlib

import httpx
import pytest

from app import updater

EXE = b"MZ" + b"x" * 5000
SHA = hashlib.sha256(EXE).hexdigest()


def _transport(release_status=200, digest=True, body=EXE, tag="v1.2.0"):
    asset = {"name": "LoLProComp.exe", "browser_download_url": "https://dl.test/LoLProComp.exe", "size": len(EXE)}
    if digest:
        asset["digest"] = f"sha256:{SHA}"
    assets = [asset] if digest else [asset, {"name": "LoLProComp.exe.sha256", "browser_download_url": "https://dl.test/sum"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(release_status, json={"tag_name": tag, "body": "notes", "assets": assets})
        if request.url.path == "/sum":
            return httpx.Response(200, text=f"{SHA}  LoLProComp.exe\n")
        if request.url.path == "/LoLProComp.exe":
            return httpx.Response(200, content=body, headers={"content-length": str(len(body))})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_version_parsing_and_comparison():
    assert updater.parse_version("v1.2.3") == (1, 2, 3) and updater.parse_version("0.1") == (0, 1)
    assert updater.parse_version("nightly") == () and updater.parse_version(None) == ()
    assert updater.is_newer("v0.2.0", "0.1.0") and updater.is_newer("1.0", "0.9.9")
    assert not updater.is_newer("v0.1.0", "0.1.0") and not updater.is_newer("0.1", "0.1.0")
    assert not updater.is_newer("v0.0.9", "0.1.0") and not updater.is_newer("nightly", "0.1.0")
    assert updater.is_newer("v0.10.0", "0.9.0")  # numeric, not alphabetical


def test_check_finds_a_newer_release_with_checksum():
    release = updater.check("0.1.0", repo="me/league-pro-comp", transport=_transport())
    assert release["version"] == "v1.2.0" and release["sha256"] == SHA and release["size"] == len(EXE)
    assert updater.check("1.2.0", repo="me/league-pro-comp", transport=_transport()) is None  # already current
    assert updater.check("0.1.0", repo="me/league-pro-comp", transport=_transport(release_status=404)) is None
    assert updater.check("0.1.0", repo="OWNER/league-pro-comp", transport=_transport()) is None  # not published yet


def test_checksum_can_come_from_a_sidecar_asset():
    release = updater.check("0.1.0", repo="me/r", transport=_transport(digest=False))
    assert release["sha256"] == SHA


def test_check_is_silent_when_offline():
    def offline(request):
        raise httpx.ConnectError("no network")

    assert updater.check("0.1.0", repo="me/r", transport=httpx.MockTransport(offline)) is None


def test_download_verifies_size_and_checksum(tmp_path):
    release = updater.check("0.1.0", repo="me/r", transport=_transport())
    progress = []
    dest = tmp_path / "new.exe"
    updater.download(release, dest, on_progress=lambda d, t: progress.append((d, t)), transport=_transport())
    assert dest.read_bytes() == EXE and progress[-1] == (len(EXE), len(EXE))
    with pytest.raises(OSError):  # same size, other content: checksum mismatch
        updater.download(release, tmp_path / "bad.exe", transport=_transport(body=b"y" * len(EXE)))
    with pytest.raises(OSError):  # truncated
        updater.download(release, tmp_path / "short.exe", transport=_transport(body=EXE[:100]))


def test_install_swaps_the_executable_and_cleanup_removes_leftovers(tmp_path):
    current = tmp_path / "LoLProComp.exe"
    current.write_bytes(b"old version")
    new = tmp_path / "LoLProComp.exe.new"
    new.write_bytes(b"new version")
    updater.install(new, current)
    assert current.read_bytes() == b"new version" and (tmp_path / "LoLProComp.exe.old").read_bytes() == b"old version"
    assert not new.exists()
    updater.cleanup(current)
    assert not (tmp_path / "LoLProComp.exe.old").exists()


def test_install_restores_the_old_version_if_the_swap_fails(tmp_path):
    current = tmp_path / "LoLProComp.exe"
    current.write_bytes(b"old version")
    with pytest.raises(OSError):
        updater.install(tmp_path / "missing.new", current)
    assert current.read_bytes() == b"old version"
