"""Read the running League client's champion select (like Porofessor / Blitz trackers do).

The League client exposes a local HTTPS API (LCU) whose port and password are written to the
`lockfile` of the install directory. Nothing leaves your machine except the public Data Dragon
request used to turn champion ids into names.
"""
import os
import re
import subprocess
from pathlib import Path

import httpx

LOCKFILE_CANDIDATES = [
    r"C:\Riot Games\League of Legends\lockfile",
    r"D:\Riot Games\League of Legends\lockfile",
    r"C:\Program Files\Riot Games\League of Legends\lockfile",
    r"C:\Program Files (x86)\Riot Games\League of Legends\lockfile",
    "/Applications/League of Legends.app/Contents/LoL/lockfile",
]

POSITION_TO_ROLE = {
    "top": "top", "jungle": "jungle", "middle": "mid", "mid": "mid",
    "bottom": "bot", "bot": "bot", "utility": "support", "support": "support",
}

_names: dict[int, str] = {}


def champion_names() -> dict[int, str]:
    """Data Dragon champion key -> display name (same naming as the database)."""
    if not _names:
        version = httpx.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10).json()[0]
        data = httpx.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json", timeout=10
        ).json()["data"]
        _names.update({int(c["key"]): c["name"] for c in data.values()})
    return _names


def find_credentials() -> tuple[int, str] | None:
    """(port, password) of the running client, from the lockfile or the process command line."""
    extra = os.environ.get("LOL_LOCKFILE")
    for candidate in ([extra] if extra else []) + LOCKFILE_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            parts = path.read_text().split(":")
            if len(parts) >= 4:
                return int(parts[2]), parts[3]
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\").CommandLine"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    port = re.search(r"--app-port=(\d+)", out)
    token = re.search(r"--remoting-auth-token=([\w-]+)", out)
    return (int(port.group(1)), token.group(1)) if port and token else None


def parse_session(session: dict, names: dict[int, str]) -> dict:
    """Turn a /lol-champ-select/v1/session payload into picks and bans by name."""
    def name(cid: int) -> str | None:
        return names.get(cid) if cid and cid > 0 else None

    my_cells = {p["cellId"] for p in session.get("myTeam", [])}
    picked: dict[int, int] = {}
    banned: list[str] = []
    for group in session.get("actions", []):
        for a in group:
            if not a.get("completed") or not a.get("championId"):
                continue
            if a["type"] == "ban" and name(a["championId"]):
                banned.append(name(a["championId"]))
            elif a["type"] == "pick":
                picked[a["actorCellId"]] = a["championId"]

    my_team: dict[str, str] = {}
    unplaced: list[str] = []
    for p in session.get("myTeam", []):
        champ = name(picked.get(p["cellId"], 0))
        if not champ:
            continue
        role = POSITION_TO_ROLE.get((p.get("assignedPosition") or "").lower())
        if role and role not in my_team:
            my_team[role] = champ
        else:
            unplaced.append(champ)
    enemy = [name(cid) for cell, cid in picked.items() if cell not in my_cells and name(cid)]
    me = next((p for p in session.get("myTeam", []) if p["cellId"] == session.get("localPlayerCellId")), {})
    return {
        "in_champ_select": True,
        "phase": (session.get("timer") or {}).get("phase"),
        "my_role": POSITION_TO_ROLE.get((me.get("assignedPosition") or "").lower()),
        "my_team": my_team,
        "my_team_unplaced": unplaced,
        "enemy_picks": enemy,
        "bans": banned,
    }


def read_champ_select() -> dict:
    creds = find_credentials()
    if not creds:
        return {"connected": False, "in_champ_select": False,
                "reason": "Client League of Legends introuvable (lancé ? sinon définir LOL_LOCKFILE)."}
    port, password = creds
    try:
        response = httpx.get(
            f"https://127.0.0.1:{port}/lol-champ-select/v1/session",
            auth=("riot", password), verify=False, timeout=5,
        )
    except httpx.HTTPError as exc:
        return {"connected": False, "in_champ_select": False, "reason": f"Client injoignable : {exc}"}
    if response.status_code == 404:
        _sent.clear()  # the next champ select may send again
        return {"connected": True, "in_champ_select": False, "reason": "Pas de champ select en cours."}
    response.raise_for_status()
    return {"connected": True, **parse_session(response.json(), champion_names())}


ROLE_SHORT = {"top": "Top", "jungle": "Jungle", "mid": "Mid", "bot": "Bot", "support": "Support"}
_sent: dict[str, str] = {}  # role -> last line posted in this champ select


def format_recommendations(suggestions: dict[str, list[dict]], my_role: str | None, top_n: int = 5) -> dict[str, str]:
    """One short English line per open role (the player's role first), e.g. `[Pro] Mid: Azir, Ahri, Orianna`."""
    roles = sorted(suggestions, key=lambda r: r != my_role)  # stable: the other roles keep the app's order
    return {
        role: f"[Pro] {ROLE_SHORT.get(role, role)}: {', '.join(s['champion'] for s in suggestions[role][:top_n])}"
        for role in roles if suggestions[role]
    }


def send_lobby_messages(lines: dict[str, str]) -> list[str]:
    """Post each line that changed since the last one sent in the champ select chat; returns what was sent."""
    todo = {role: text for role, text in lines.items() if _sent.get(role) != text}
    creds = find_credentials() if todo else None
    if not creds:
        return []
    port, password = creds
    base = f"https://127.0.0.1:{port}/lol-chat/v1/conversations"
    posted = []
    with httpx.Client(auth=("riot", password), verify=False, timeout=5) as client:
        conversations = client.get(base)
        conversations.raise_for_status()
        chat = next((c for c in conversations.json() if c.get("type") == "championSelect"), None)
        if not chat:
            return []
        for role, text in todo.items():
            client.post(f"{base}/{chat['id']}/messages", json={"body": text, "type": "chat"}).raise_for_status()
            _sent[role] = text
            posted.append(text)
    return posted
