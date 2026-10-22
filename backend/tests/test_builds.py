from app import gamedata, live
from app.builds import champion_build
from app.dataset import load
from app.db import connect, save_game

from .synth import build_db

ITEMS = {
    "1001": {"name": "Boots", "gold": {"total": 300, "purchasable": True}, "into": ["3020"], "tags": ["Boots"], "maps": {"11": True}},
    "3020": {"name": "Sorcerer's Shoes", "gold": {"total": 1100, "purchasable": True}, "tags": ["Boots"], "maps": {"11": True}},
    "3157": {"name": "Zhonya's Hourglass", "gold": {"total": 3250, "purchasable": True}, "tags": ["SpellDamage"], "maps": {"11": True}},
    "3089": {"name": "Rabadon's Deathcap", "gold": {"total": 3600, "purchasable": True}, "maps": {"11": True}},
    "6655": {"name": "Luden's Companion", "gold": {"total": 2900, "purchasable": True}, "maps": {"11": True}},
    "2055": {"name": "Control Ward", "gold": {"total": 75, "purchasable": True}, "consumed": True, "maps": {"11": True}},
    "3340": {"name": "Stealth Ward", "gold": {"total": 0, "purchasable": True}, "maps": {"11": True}},
    "9999": {"name": "Arena Only", "gold": {"total": 3000, "purchasable": True}, "maps": {"11": False}},
    "3153": {"name": "Blade of The Ruined King", "gold": {"total": 3200, "purchasable": True}, "maps": {"11": True}},
    "3042": {"name": "Muramana", "gold": {"total": 2900, "purchasable": False}, "maps": {"11": True}},
    "3175": {"name": "Spellslinger's Shoes", "gold": {"total": 1100, "purchasable": True}, "tags": ["Boots"], "maps": {"11": True}},
}
STYLES = [
    {"id": 8100, "name": "Domination", "slots": [{"runes": [{"id": 8112, "name": "Electrocute"}]},
                                                  {"runes": [{"id": 8126, "name": "Cheap Shot"}]}]},
    {"id": 8200, "name": "Sorcery", "slots": [{"runes": [{"id": 8214, "name": "Summon Aery"}]},
                                              {"runes": [{"id": 8226, "name": "Manaflow Band"}]}]},
]
RUNES = ["Electrocute", "Cheap Shot", "Cheap Shot", "Cheap Shot", "Manaflow Band", "Manaflow Band",
         "Adaptive Force", "Adaptive Force", "Health"]


def catalog():
    return gamedata.parse_catalog(ITEMS, STYLES)


def comp(items, win=True, mid="Ahri", patch="25.1", day="2025-01-01", ally="Lee Sin", foe="Zed"):
    lo = {"items": tuple(items), "trinket": "Stealth Ward", "keystone": "Electrocute", "primary_tree": "Domination",
          "secondary_tree": "Sorcery", "runes": tuple(RUNES), "spells": ("Flash", "Ignite")}
    return {"game_id": f"{day}{items}{ally}{foe}", "team": "A", "opponent": "B", "win": win, "date": f"{day} 10:00:00",
            "patch": patch, "enemy": {"top": foe}, "picks": {"mid": mid, "jungle": ally}, "loadouts": {"mid": lo}}


def test_catalog_keeps_summoners_rift_final_items_and_boots():
    cat = catalog()
    assert cat["items"]["zhonya's hourglass"] == {"id": 3157, "name": "Zhonya's Hourglass", "boots": False, "final": True}
    assert cat["items"]["sorcerer's shoes"]["boots"] and cat["items"]["spellslinger's shoes"]["boots"]
    assert not cat["items"]["boots"]["boots"] and not cat["items"]["boots"]["final"]
    assert not cat["items"]["control ward"]["final"] and "arena only" not in cat["items"]
    assert cat["items"]["muramana"]["final"]  # a transformation of a purchasable item
    assert cat["perks"]["Electrocute"] == 8112 and cat["perks"]["Health"] == 5011 and cat["styles"]["Sorcery"] == 8200


def test_champion_build_prefers_games_like_the_draft_and_recent_patches():
    old = [comp(["Rabadon's Deathcap", "Luden's Companion"], patch="24.1", day="2024-01-01") for _ in range(3)]
    recent = [comp(["Zhonya's Hourglass", "Sorcerer's Shoes", "Control Ward"], patch="25.1") for _ in range(2)]
    build = champion_build(old + recent, "Ahri", "mid", catalog=catalog())
    core = build["item_blocks"][0]
    assert build["games"] == 5 and core["type"] == "Core build"
    assert core["items"][0]["name"] == "Zhonya's Hourglass" and core["items"][0]["id"] == 3157
    boots = next(b for b in build["item_blocks"] if b["type"] == "Boots")
    assert boots["items"][0]["name"] == "Sorcerer's Shoes"
    assert all(i["name"] != "Control Ward" for b in build["item_blocks"] for i in b["items"])
    assert build["spells"] == ["Flash", "Ignite"]


def test_item_names_match_ignoring_case_and_gone_runes_are_dropped():
    comps = [comp(["Blade of the Ruined King"]) for _ in range(2)]
    build = champion_build(comps, "Ahri", "mid", catalog=catalog())
    assert build["item_blocks"][0]["items"][0] == {"name": "Blade of The Ruined King", "id": 3153, "rate": 1.0}
    cat = catalog()
    del cat["perks"]["Cheap Shot"]  # a rune removed from the game cannot be imported
    assert champion_build(comps, "Ahri", "mid", catalog=cat)["runes"] == []


def test_champion_build_weights_similar_allies_and_enemies():
    comps = [comp(["Rabadon's Deathcap"], ally="Lee Sin", foe="Zed") for _ in range(3)]
    comps += [comp(["Luden's Companion"], ally="Vi", foe="Yone") for _ in range(3)]
    plain = champion_build(comps, "Ahri", "mid", catalog=catalog())
    near = champion_build(comps, "Ahri", "mid", locked={"jungle": "Vi"}, enemy=["Yone"], catalog=catalog())
    assert near["similar_games"] == 3 and plain["similar_games"] == 0
    assert near["item_blocks"][0]["items"][0]["name"] == "Luden's Companion"


def test_champion_build_rune_page_is_complete_and_unknown_champion_is_empty():
    build = champion_build([comp(["Zhonya's Hourglass"])] * 3, "Ahri", "mid", catalog=catalog())
    page = build["runes"][0]
    assert page["keystone"] == "Electrocute" and page["ids"] == [8112, 8126, 8126, 8126, 8226, 8226, 5008, 5008, 5011]
    assert (page["primary_style_id"], page["sub_style_id"]) == (8100, 8200)
    nothing = champion_build([comp(["Zhonya's Hourglass"])], "Zed", "mid", catalog=catalog())
    assert nothing["games"] == 0 and nothing["item_blocks"] == []


def test_client_payloads():
    build = champion_build([comp(["Zhonya's Hourglass", "Sorcerer's Shoes"])] * 2, "Ahri", "mid", catalog=catalog())
    item_set = live.item_set_payload(build, 103)
    assert item_set["associatedChampions"] == [103] and item_set["title"].startswith(live.TAG)
    assert item_set["blocks"][0]["items"] == [{"id": "3157", "count": 1}]
    page = live.rune_page_payload(build)
    assert page["name"].startswith(live.TAG) and len(page["selectedPerkIds"]) == 9 and len(page["name"]) <= 25
    assert live.rune_page_payload({"champion": "Ahri", "runes": []}) is None


def test_loadout_round_trips_through_the_database_and_dataset(tmp_path):
    conn = build_db(2)
    game = {"game_id": "LOADOUT", "match_id": "M", "game_number": 1, "date": "2025-02-01T10:00:00", "patch": "25.2",
            "tournament": "LCK/2025 Season/Cup", "team1": "T1", "team2": "KT", "winner": 1, "duration": 30.0,
            "sides": {1: {}, 2: {}}, "bans": [],
            "picks": [{"side": s, "pick_order": o, "role": r, "champion": f"C{s}{r}", "items": "Zhonya's Hourglass;;Boots",
                       "trinket": "Stealth Ward", "keystone": "Electrocute", "primary_tree": "Domination",
                       "secondary_tree": "Sorcery", "runes": ",".join(RUNES), "spells": "Flash,Ignite"}
                      for s in (1, 2) for o, r in enumerate(["top", "jungle", "mid", "bot", "support"], 1)]}
    save_game(conn, game)
    comps = [c for c in load(conn).comps_all if c["game_id"] == "LOADOUT"]
    lo = comps[0]["loadouts"]["mid"]
    assert lo["items"] == ("Zhonya's Hourglass", "Boots") and lo["runes"][0] == "Electrocute" and lo["spells"] == ("Flash", "Ignite")
    assert not any(c["loadouts"] for c in load(conn).comps_all if c["game_id"] != "LOADOUT")  # no loadout, no entry


def test_v3_database_gains_loadout_columns(tmp_path):
    path = tmp_path / "v3.db"
    conn = connect(path)
    conn.execute("DROP TABLE picks")
    conn.execute("CREATE TABLE picks (game_id TEXT, side INTEGER, pick_order INTEGER, role TEXT, champion TEXT,"
                 " player TEXT, kills INTEGER, deaths INTEGER, assists INTEGER, gold INTEGER, cs INTEGER,"
                 " damage INTEGER, vision INTEGER)")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    cols = {r[1] for r in connect(path).execute("PRAGMA table_info(picks)")}
    assert {"items", "runes", "spells", "keystone"} <= cols
