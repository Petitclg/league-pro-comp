"""A fake Riot API (httpx.MockTransport) serving ladders, match histories, matches and timelines.

No Riot API key is needed: every test talks to this instead of the network."""
import json
import random

import httpx

GOOD_KEY = "RGAPI-00000000-1111-2222-3333-444444444444"
POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
# championId -> display name, 8 per position
CHAMPIONS = {cid: f"Champ{cid}" for cid in range(1, 51)}
CHAMPIONS[62] = "Wukong"  # Match-V5 calls it "MonkeyKing": the app keeps the Data Dragon display name


def make_match(match_id: str, *, patch: str = "15.22", seed: int = 0, duration: int = 1800, queue: int = 420,
               winner: int = 100, positions: list[str] | None = None, remake: bool = False) -> dict:
    rng = random.Random(f"{match_id}{seed}")
    picked = {pos: rng.sample(range(1 + 8 * i, 9 + 8 * i), 2) for i, pos in enumerate(POSITIONS)}
    if rng.random() < 0.3:
        picked["JUNGLE"][0] = 62
    participants = []
    for i in range(10):
        team = 100 if i < 5 else 200
        pos = (positions or POSITIONS)[i % 5]
        cid = picked[POSITIONS[i % 5]][i // 5]
        participants.append({
            "participantId": i + 1, "teamId": team, "puuid": f"p-{match_id}-{i}",
            "riotIdGameName": f"Player{i}", "riotIdTagline": "EUW",
            "championId": cid, "championName": "MonkeyKing" if cid == 62 else f"Internal{cid}",
            "teamPosition": pos, "individualPosition": POSITIONS[i % 5],
            "kills": rng.randint(0, 12), "deaths": rng.randint(0, 9), "assists": rng.randint(0, 15),
            "goldEarned": rng.randint(7000, 16000), "totalMinionsKilled": rng.randint(20, 250),
            "neutralMinionsKilled": rng.randint(0, 150), "totalDamageDealtToChampions": rng.randint(5000, 40000),
            "totalDamageTaken": rng.randint(8000, 40000), "visionScore": rng.randint(5, 80),
            "champLevel": rng.randint(11, 18), "win": team == winner, "gameEndedInEarlySurrender": remake,
        })
    teams = []
    for team in (100, 200):
        teams.append({
            "teamId": team, "win": team == winner,
            "bans": [{"championId": 41 + i + (5 if team == 200 else 0), "pickTurn": i + 1 + (5 if team == 200 else 0)}
                     for i in range(5)][::-1] + ([] if team == 100 else []),
            "objectives": {"champion": {"kills": 20}, "tower": {"kills": 7}, "dragon": {"kills": 2},
                           "baron": {"kills": 1}, "riftHerald": {"kills": 1}, "horde": {"kills": 3},
                           "inhibitor": {"kills": 1}},
        })
    teams[1]["bans"][0]["championId"] = -1  # a player who did not ban
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": queue, "gameVersion": f"{patch}.712.3456", "gameDuration": duration,
            "gameEndTimestamp": 1_763_400_000_000 + duration * 1000, "gameStartTimestamp": 1_763_400_000_000,
            "gameCreation": 1_763_399_900_000, "platformId": match_id.split("_")[0],
            "participants": participants, "teams": teams,
        },
    }


def make_timeline(match_id: str, minutes: int = 30) -> dict:
    rng = random.Random(match_id)
    frames = []
    for m in range(minutes + 1):
        events = []
        if m:
            for _ in range(rng.randint(0, 3)):
                killer = rng.randint(1, 10)
                victim = rng.choice([p for p in range(1, 11) if (p > 5) != (killer > 5)])
                events.append({"type": "CHAMPION_KILL", "timestamp": m * 60000 - rng.randint(1, 59000),
                               "killerId": killer, "victimId": victim,
                               "assistingParticipantIds": rng.sample([p for p in range(1, 11) if (p > 5) == (killer > 5) and p != killer], 2)})
            for pid in range(1, 11):
                for _ in range(rng.choice([0, 0, 1, 2])):
                    events.append({"type": "ITEM_PURCHASED", "timestamp": m * 60000 - rng.randint(1, 59000),
                                   "participantId": pid, "itemId": rng.randint(1001, 6700)})
            # events the app ignores, as in real timelines
            events += [{"type": "WARD_PLACED", "timestamp": m * 60000 - 100, "creatorId": 1, "wardType": "YELLOW_TRINKET"}] * 4
            events += [{"type": "LEVEL_UP", "timestamp": m * 60000 - 200, "participantId": 2, "level": 3}]
        if m == 6:
            events += [{"type": "ITEM_SOLD", "timestamp": 355000, "participantId": 3, "itemId": 1055},
                       {"type": "ITEM_UNDO", "timestamp": 356000, "participantId": 4, "beforeId": 1036, "afterId": 0, "goldGain": 350},
                       {"type": "ITEM_DESTROYED", "timestamp": 357000, "participantId": 5, "itemId": 2010}]
        if m == 8:
            events += [{"type": "ELITE_MONSTER_KILL", "timestamp": 470000, "killerId": 2, "killerTeamId": 100,
                        "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON"},
                       {"type": "ELITE_MONSTER_KILL", "timestamp": 475000, "killerId": 7, "killerTeamId": 200,
                        "monsterType": "HORDE"},
                       {"type": "TURRET_PLATE_DESTROYED", "timestamp": 476000, "teamId": 200, "laneType": "MID_LANE"}]
        if m == 15:
            events += [{"type": "BUILDING_KILL", "timestamp": 880000, "teamId": 200, "killerId": 1,
                        "buildingType": "TOWER_BUILDING", "laneType": "TOP_LANE", "towerType": "OUTER_TURRET"},
                       {"type": "ELITE_MONSTER_KILL", "timestamp": 890000, "killerId": 7, "killerTeamId": 200,
                        "monsterType": "RIFTHERALD"}]
        if m == 25:
            events += [{"type": "ELITE_MONSTER_KILL", "timestamp": 1450000, "killerId": 2, "killerTeamId": 100,
                        "monsterType": "BARON_NASHOR"}]
        frames.append({
            "timestamp": m * 60000,
            "participantFrames": {str(pid): {
                "participantId": pid, "totalGold": 500 + m * 400 + pid, "xp": m * 500 + pid, "level": min(18, 1 + m // 2),
                "minionsKilled": m * 6, "jungleMinionsKilled": m if pid in (2, 7) else 0, "currentGold": 100,
                "damageStats": {"totalDamageDoneToChampions": m * 700 + pid, "totalDamageTaken": m * 650 + pid,
                                "magicDamageDone": m * 999, "physicalDamageDone": m * 888},
                "championStats": {"health": 1000, "armor": 50, "attackDamage": 80},
                "position": {"x": 1000 + pid, "y": 2000 + m},
            } for pid in range(1, 11)},
            "events": events,
        })
    return {"metadata": {"matchId": match_id}, "info": {"frameInterval": 60000, "frames": frames,
                                                        "participants": [{"participantId": p} for p in range(1, 11)]}}


class FakeRiot:
    """Routes Riot API URLs to generated answers and records every request.

    Ladder pages hold `page_size` players; each player's history holds `per_player` ranked games,
    and neighbouring players of a page share half of their games (as duo partners would), which
    exercises the de-duplication."""

    def __init__(self, page_size: int = 6, per_player: int = 4, patch_of=None, key: str = GOOD_KEY):
        self.page_size = page_size
        self.per_player = per_player
        self.patch_of = patch_of or (lambda match_id: "15.22")
        self.key = key
        self.requests: list[httpx.Request] = []
        self.fail_next: list[httpx.Response] = []  # answers served before the normal ones

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def count(self, fragment: str) -> int:
        return sum(fragment in r.url.path for r in self.requests)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-Riot-Token") != self.key:
            return httpx.Response(401, json={"status": {"message": "Unknown apikey", "status_code": 401}})
        if self.fail_next:
            return self.fail_next.pop(0)
        host = request.url.host.split(".")[0]
        path = request.url.path
        headers = {"X-App-Rate-Limit": "20:1,100:120"}
        if path == "/lol/status/v4/platform-data":
            return httpx.Response(200, json={"id": host.upper()}, headers=headers)
        if path.startswith("/lol/league-exp/v4/entries/RANKED_SOLO_5x5/"):
            tier, division = path.split("/")[-2:]
            page = int(request.url.params.get("page", 1))
            if page > 1:
                return httpx.Response(200, json=[], headers=headers)
            return httpx.Response(200, json=[
                {"puuid": f"{host}-{tier}-{division}-{i}", "tier": tier, "rank": division, "inactive": i == 0}
                for i in range(self.page_size)], headers=headers)
        if path.startswith("/lol/match/v5/matches/by-puuid/"):
            puuid = path.split("/")[-2]
            platform, tier, division, i = puuid.split("-")
            prefix = platform.upper()
            ids = [f"{prefix}_{tier[:2]}{division}{(int(i) * 2 + k)}" for k in range(self.per_player)]
            return httpx.Response(200, json=ids + [f"OTHER1_{i}"], headers=headers)
        if path.startswith("/lol/match/v5/matches/"):
            parts = path.split("/")
            match_id = parts[5]
            if parts[-1] == "timeline":
                return httpx.Response(200, json=make_timeline(match_id), headers=headers)
            return httpx.Response(200, content=json.dumps(make_match(match_id, patch=self.patch_of(match_id))),
                                  headers=headers)
        return httpx.Response(404, json={"status": {"status_code": 404}})
