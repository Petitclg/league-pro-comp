"""Read the running League client's champion select (like Porofessor / Blitz trackers do).

The League client exposes a local HTTPS API (LCU) whose port and password are written to the
`lockfile` of the install directory. Nothing leaves your machine except the public Data Dragon
request used to turn champion ids into names.
"""
import json
import os
import re
import subprocess
import uuid
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
_profiles: dict[str, dict] = {}


def _load_ddragon() -> None:
    version = httpx.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10).json()[0]
    data = httpx.get(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json", timeout=10
    ).json()["data"]
    _names.update({int(c["key"]): c["name"] for c in data.values()})
    _profiles.update({c["name"]: {"tags": c["tags"], "info": c["info"]} for c in data.values()})


def champion_names() -> dict[int, str]:
    """Data Dragon champion key -> display name (same naming as the database)."""
    if not _names:
        _load_ddragon()
    return _names


def champion_profiles() -> dict[str, dict]:
    """Display name -> {tags, info} from Data Dragon; empty when it cannot be reached."""
    if not _profiles:
        try:
            _load_ddragon()
        except httpx.HTTPError:
            pass
    return _profiles


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


TAG = "[Pro]"  # prefix of the item sets and rune page this app writes, so it only ever replaces its own
SUMMONERS_RIFT_MAP = 11
_imported: dict[str, str] = {}  # champion -> fingerprint of the last build written to the client


def item_set_payload(build: dict, champion_id: int) -> dict | None:
    blocks = [
        {"type": b["type"], "items": [{"id": str(i["id"]), "count": 1} for i in b["items"]]}
        for b in build["item_blocks"] if b["items"]
    ]
    if not blocks:
        return None
    return {
        "title": f"{TAG} {build['champion']} {build['role']} ({build['games']} games)",
        "type": "custom", "map": "any", "mode": "any", "sortrank": 0, "startedFrom": "blank",
        "associatedChampions": [champion_id], "associatedMaps": [SUMMONERS_RIFT_MAP],
        "preferredItemSlots": [], "blocks": blocks, "uid": str(uuid.uuid4()),
    }


def rune_page_payload(build: dict) -> dict | None:
    page = next((p for p in build["runes"] if p["ids"] and p["primary_style_id"] and p["sub_style_id"]), None)
    if not page:
        return None
    return {
        "name": f"{TAG} {build['champion']}"[:25], "primaryStyleId": page["primary_style_id"],
        "subStyleId": page["sub_style_id"], "selectedPerkIds": page["ids"], "current": True,
    }


def import_build(build: dict) -> dict:
    """Write the build into the running client as an item set and a rune page; returns a status per part.

    Only item sets of the same champion and rune pages whose name starts with `TAG` are replaced.
    A rune page is created only when the account has a free slot."""
    champion_id = next((cid for cid, name in champion_names().items() if name == build["champion"]), None)
    if champion_id is None:
        return {"sent": False, "reason": f"Champion inconnu : {build['champion']}"}
    items = item_set_payload(build, champion_id)
    runes = rune_page_payload(build)
    if not items and not runes:
        return {"sent": False, "reason": "Rien à importer (pas de données d'items ni de runes pour ce champion)."}
    fingerprint = json.dumps([items and items["blocks"], runes and runes["selectedPerkIds"]])
    if _imported.get(build["champion"]) == fingerprint:
        return {"sent": False, "skipped": True, "reason": "Déjà importé."}
    creds = find_credentials()
    if not creds:
        return {"sent": False, "reason": "Client League of Legends introuvable."}
    port, password = creds
    status: dict[str, str] = {}
    with httpx.Client(base_url=f"https://127.0.0.1:{port}", auth=("riot", password), verify=False, timeout=5) as client:
        if items:
            summoner = client.get("/lol-summoner/v1/current-summoner")
            summoner.raise_for_status()
            path = f"/lol-item-sets/v1/item-sets/{summoner.json()['summonerId']}/sets"
            current = client.get(path)
            current.raise_for_status()
            data = current.json()
            kept = [s for s in data.get("itemSets", [])
                    if not (str(s.get("title", "")).startswith(TAG) and s.get("associatedChampions") == [champion_id])]
            client.put(path, json={**data, "itemSets": kept + [items]}).raise_for_status()
            status["items"] = "ok"
        if runes:
            status["runes"] = _write_rune_page(client, runes)
    if "ok" in status.values():
        _imported[build["champion"]] = fingerprint
    return {"sent": "ok" in status.values(), **status}


def _write_rune_page(client: httpx.Client, page: dict) -> str:
    pages = client.get("/lol-perks/v1/pages")
    pages.raise_for_status()
    editable = [p for p in pages.json() if p.get("isEditable")]
    ours = next((p for p in editable if str(p.get("name", "")).startswith(TAG)), None)
    if ours:
        client.put(f"/lol-perks/v1/pages/{ours['id']}", json=page).raise_for_status()
        client.put("/lol-perks/v1/currentpage", json=ours["id"])
        return "ok"
    owned = client.get("/lol-perks/v1/inventory")
    owned.raise_for_status()
    if len(editable) >= owned.json().get("ownedPageCount", 0):
        return "Aucune page de runes libre : en supprimer une dans le client."
    client.post("/lol-perks/v1/pages", json=page).raise_for_status()
    return "ok"
