from collections import OrderedDict

import httpx
import pytest
from fastapi.testclient import TestClient

from app import dataset, items, main, soloq
from app.dataset import Filters

from .riot_fake import CHAMPIONS, make_match


def _item(name, total, **extra):
    return {"name": name, "gold": {"total": total, "purchasable": extra.pop("purchasable", True)},
            "maps": extra.pop("maps", {"11": True, "12": True}), **extra}


ITEMS = {
    "1001": _item("Bottes", 300, into=["3006", "3020"], tags=["Boots"]),
    "3006": _item("Jambières du berserker", 1100, **{"from": ["1001"]}, tags=["Boots", "AttackSpeed"]),
    "3020": _item("Chaussures du sorcier", 1100, **{"from": ["1001"]}, tags=["Boots", "MagicPenetration"]),
    "1052": _item("Tome d'amplification", 400, into=["3089", "4645", "3802"]),
    "1058": _item("Baguette surdimensionnée", 1200, into=["3089"]),
    "3802": _item("Le Chapitre perdu", 1200, into=["6653"], **{"from": ["1052"]}),
    "3089": _item("Coiffe de Rabadon", 3600, **{"from": ["1058", "1052"]}),
    "3157": _item("Sablier de Zhonya", 3250),
    "4645": _item("Flamme-ombre", 3200),
    "6653": _item("Tourment de Liandry", 3000, **{"from": ["3802"]}),
    "3004": _item("Manamune", 2900, into=["3042"]),
    "3042": _item("Muramana", 2900, purchasable=False, **{"from": ["3004"]}),
    "3003": _item("Bâton de l'archange", 2900, into=["3040"]),
    "3040": _item("Étreinte séraphique", 3000, **{"from": ["3003"]}),
    "3041": _item("Voleur d'âmes de Mejai", 1500, **{"from": ["1082"]}),
    "1082": _item("Sceau noir", 350, into=["3041"]),
    "3172": _item("Jambières de métal", 1100, **{"from": ["3006"]}, tags=["AttackSpeed", "NonbootsMovement"]),
    "2003": _item("Potion de soin", 50, consumed=True, tags=["Consumable"]),
    "2138": _item("Élixir de fer", 2500, consumed=True),
    "9001": _item("Objet de Gangplank", 3000, requiredChampion="Gangplank"),
    "9002": _item("Objet d'Arena", 3000, maps={"11": False, "30": True}),
    "9003": _item("Objet retiré de la boutique", 3000, inStore=False),
    "9004": _item("Chef-d'œuvre d'Ornn", 3300, requiredAlly="Ornn", purchasable=False),
}


class FakeDDragon:
    def __init__(self, versions=("15.23.1", "15.22.1", "15.21.1")):
        self.versions = list(versions)
        self.down = False
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("offline", request=request)
        self.requests.append(request.url.path)
        if request.url.path == "/api/versions.json":
            return httpx.Response(200, json=self.versions)
        if request.url.path.endswith("/data/fr_FR/item.json"):
            return httpx.Response(200, json={"type": "item", "data": ITEMS})
        return httpx.Response(404)


@pytest.fixture
def ddragon(tmp_path, monkeypatch):
    """soloq.db, the dataset cache and the Data Dragon cache in a temporary folder; Data Dragon served from memory."""
    monkeypatch.setattr(soloq, "SOLOQ_DB_PATH", tmp_path / "soloq.db")
    monkeypatch.setattr(dataset, "_cache", OrderedDict())
    monkeypatch.setattr(dataset, "_loaded_at", {})
    monkeypatch.setattr(main, "league_summary", lambda: [])
    monkeypatch.setattr(items, "DDRAGON_DIR", tmp_path / "ddragon")
    monkeypatch.setattr(items, "_catalogues", {})
    fake = FakeDDragon()
    monkeypatch.setattr(items, "ddragon_transport", httpx.MockTransport(fake.handle))
    return fake


def _bought(events):
    return [(p["t"], p["item"], p["sold"]) for p in items.reconstruct(events)]


# --- build reconstruction ------------------------------------------------------------------------------


def test_reconstruct_follows_purchases_sales_destructions_and_undos():
    assert _bought([[10, 1, "P", 1055], [11, 1, "U", 1055, 0]]) == []
    # Undoing a fused item gives its components back (destroyed at the same second just before it)
    fusion = [[100, 1, "P", 1052], [200, 1, "P", 1058], [700, 1, "D", 1052], [700, 1, "D", 1058],
              [700, 1, "P", 3089], [702, 1, "U", 3089, 0], [710, 1, "S", 1058]]
    assert _bought(fusion) == [(100, 1052, False), (200, 1058, True)]
    # ...but not what was destroyed at another second
    earlier = [[100, 1, "P", 1052], [699, 1, "D", 1052], [700, 1, "P", 3089], [701, 1, "U", 3089, 0],
               [710, 1, "S", 1052]]
    assert _bought(earlier) == [(100, 1052, False)]
    # Undoing a sale puts the item back: no longer resold, and it can be sold again
    sale = [[100, 1, "P", 3089], [200, 1, "S", 3089], [201, 1, "U", 0, 3089]]
    assert _bought(sale) == [(100, 3089, False)]
    assert _bought(sale + [[300, 1, "S", 3089]]) == [(100, 3089, True)]
    # A sale removes the most recent copy; an undo cancels the last valid purchase
    assert _bought([[10, 1, "P", 2003], [20, 1, "P", 2003], [30, 1, "S", 2003]]) == [(10, 2003, False), (20, 2003, True)]
    twice = [[10, 1, "P", 1001], [20, 1, "P", 1001], [21, 1, "U", 1001, 0]]
    assert _bought(twice) == [(10, 1001, False)]
    assert _bought(twice + [[22, 1, "U", 1001, 0]]) == []
    # Undos without a matching action, and sales or destructions of items not held, are ignored
    stray = [[5, 1, "D", 2010], [6, 1, "S", 1001], [7, 1, "U", 3089, 0], [8, 1, "U", 0, 1001], [9, 1, "P", 1001],
             [10, 1, "U", 0, 1001]]
    assert _bought(stray) == [(9, 1001, False)]
    # Undos in cascade: each one cancels the last valid purchase of its own item
    assert _bought([[10, 1, "P", 3089], [11, 1, "P", 3157], [12, 1, "U", 3089, 0], [13, 1, "U", 3157, 0]]) == []
    assert _bought([[10, 1, "P", 3089], [11, 1, "U", 3089, 0], [12, 1, "U", 3089, 0]]) == []
    cascade = fusion[:6] + [[703, 1, "U", 1058, 0]]  # the component given back by the undone fusion is undone too
    assert _bought(cascade) == [(100, 1052, False)]


def test_reconstruct_worked_example_of_the_specification():
    events = [[15, 3, "P", 1056], [15, 3, "P", 2003], [16, 3, "P", 2003], [17, 3, "U", 2003, 0], [300, 3, "D", 2003],
              [480, 3, "P", 1052], [600, 3, "P", 1001], [690, 3, "P", 1058], [700, 3, "D", 1052], [700, 3, "D", 1058],
              [700, 3, "P", 3089], [702, 3, "U", 3089, 0], [705, 3, "D", 1001], [705, 3, "P", 3020], [900, 3, "D", 1052],
              [900, 3, "P", 6653], [1500, 3, "P", 3157], [1980, 3, "S", 1056], [1985, 3, "U", 0, 1056],
              [1990, 3, "S", 1056], [2400, 3, "D", 1058], [2400, 3, "P", 3089]]
    purchases = items.reconstruct(events)
    assert [(p["t"], p["item"], p["sold"]) for p in purchases] == [
        (15, 1056, True), (15, 2003, False), (480, 1052, False), (600, 1001, False), (690, 1058, False),
        (705, 3020, False), (900, 6653, False), (1500, 3157, False), (2400, 3089, False)]
    build = items.build_of(purchases, items.Catalogue("15.22.1", ITEMS))
    assert build == {"items": [(6653, 15.0, False), (3157, 25.0, False), (3089, 40.0, False)], "boots": (3020, 11.75, False)}


def test_build_edge_cases():
    cat = items.Catalogue("15.22.1", ITEMS)

    def build(events):
        return items.build_of(items.reconstruct([[t, 1, *e] for t, *e in events]), cat)

    # Bought back: once, at the first purchase, marked resold
    rebuy = build([(600, "P", 3089), (900, "S", 3089), (1200, "P", 3089)])
    assert rebuy["items"] == [(3089, 10.0, True)]
    # Six items, one sold, then a 7th distinct item: ignored
    six = [(60 * (i + 1), "P", item) for i, item in enumerate([3089, 3157, 4645, 6653, 3004, 3040])]
    full = build(six + [(2100, "S", 3157), (2160, "P", 3041)])
    assert [i for i, _, _ in full["items"]] == [3089, 3157, 4645, 6653, 3004, 3040] and full["items"][1][2] is True
    # No boots, unknown items, starters only (a short game): an empty build
    assert build([(15, "P", 1056), (20, "P", 99999), (300, "P", 2003)]) == {"items": [], "boots": None}


def test_completed_item_rule_and_build_of(ddragon):
    cat = items.catalogue("15.22")
    kinds = {int(k): cat.kind(int(k)) for k in ITEMS}
    assert {k for k, v in kinds.items() if v == "completed"} == {3089, 3157, 4645, 6653, 3004, 3040, 3041}
    assert {k for k, v in kinds.items() if v == "boots"} == {3006, 3020}  # plain boots are a component
    assert cat.kind(3172) is None and cat.kind(1082) is None  # tier-3 boots upgrade without the Boots tag, component
    assert cat.kind(99999) is None
    purchases = [{"t": t, "item": i, "sold": s} for t, i, s in [
        (60, 1001, False), (300, 1052, False), (600, 3006, False), (720, 6653, False), (900, 3020, False),
        (1000, 3157, True), (1100, 2003, False), (1200, 6653, False), (1300, 3089, False), (1400, 4645, False),
        (1500, 3004, False), (1600, 3040, False), (1700, 9001, False), (1800, 3157, False)]]
    build = items.build_of(purchases, cat)
    assert [i for i, _, _ in build["items"]] == [6653, 3157, 3089, 4645, 3004, 3040]  # distinct, first purchase, 6 max
    assert build["items"][0] == (6653, 12.0, False) and build["items"][1][2] is True
    assert build["boots"] == (3006, 10.0, False)  # the first boots


def test_catalogue_is_kept_in_memory_and_on_disk_for_offline_use(ddragon):
    cat = items.catalogue("15.22")
    assert cat.version == "15.22.1" and cat.name(3089) == "Coiffe de Rabadon"
    assert ddragon.requests == ["/api/versions.json", "/cdn/15.22.1/data/fr_FR/item.json"]
    assert (items.DDRAGON_DIR / "items-15.22.json").exists()
    assert items.catalogue("15.22") is cat and len(ddragon.requests) == 2

    items._catalogues.clear()
    ddragon.down = True
    offline = items.catalogue("15.22")
    assert offline.version == "15.22.1" and offline.kind(3089) == "completed"
    with pytest.raises(items.ItemDataUnavailable):
        items.catalogue("15.30")
    ddragon.down = False
    newest = items.catalogue("15.30")  # not on Data Dragon yet: the most recent version, not cached on disk
    assert newest.version == "15.23.1" and not (items.DDRAGON_DIR / "items-15.30.json").exists()
    assert items.catalogue(None).version == "15.23.1"


# --- analyses ---------------------------------------------------------------------------------------------


def _store(conn, match_id, events, *, win=True, player="Alice#EUW", opponent="Zed", timeline=True):
    """A game with Ahri blue mid (participant 3) against `opponent` (participant 8)."""
    game = soloq.extract_match(make_match(match_id, winner=100 if win else 200), "euw1", CHAMPIONS)
    if win is None:
        game["winner"] = None
    for p in game["players"]:
        p["champion"] = f"Filler{p['participant']}"
        if p["participant"] == 3:
            p["champion"], p["player"] = "Ahri", player
        elif p["participant"] == 8:
            p["champion"] = opponent
    stream = [[t, 3, *e] for t, *e in events] + [[60, 8, "P", 3157]]  # the opponent's purchases are not Ahri's
    blobs = {k: soloq._pack([]) for k in ("frames", "kills", "objectives")} | {"items": soloq._pack(sorted(stream, key=lambda e: e[0]))}
    soloq.save_game(conn, game, "DIAMOND", blobs if timeline else None)


# Group A: Liandry at 12 min (after an undone Rabadon), sorcerer boots, Zhonya, Rabadon
BUILD_A = [(100, "P", 1052), (200, "P", 1058), (700, "D", 1052), (700, "D", 1058), (700, "P", 3089), (702, "U", 3089, 0),
           (710, "P", 3802), (720, "D", 3802), (720, "P", 6653), (840, "P", 1001), (840, "D", 1001), (840, "P", 3020),
           (1320, "P", 3157), (1800, "D", 1052), (1800, "D", 1058), (1800, "P", 3089)]
# Group B: Shadowflame at 13 min (sold later), berserker boots, Zhonya
BUILD_B = [(600, "P", 3006), (780, "P", 4645), (1500, "P", 3157), (2000, "S", 4645)]
# Group C: Rabadon first, at 26 min, no boots
BUILD_C = [(1560, "P", 3089)]


def _fill(path):
    conn = soloq.connect(path)
    for i in range(52):
        build = BUILD_A if i < 20 or i == 51 else BUILD_B if i < 40 else BUILD_C
        win = None if i == 51 else i < 15 or 20 <= i < 25 or 40 <= i < 50
        _store(conn, f"EUW1_{i}", build, win=win, player="Alice#EUW" if i < 10 else "Bob#EUW",
               opponent="Syndra" if 45 <= i < 50 else "Zed", timeline=i != 50)
    conn.commit()
    return conn


def _row(rows, key):
    return next(r for r in rows if r.get("item", r.get("items")) == key)


def test_analyse_slots_timing_boots_and_paths(ddragon, tmp_path):
    conn = _fill(tmp_path / "s.db")
    r = items.analyse(soloq.load(conn), Filters(), ["Ahri"], "mid", conn=conn)
    assert r["builds"] == 51 and r["decided"] == 50 and r["missing_timeline"] == 1
    assert r["baseline"]["wins"] == 30 and r["baseline"]["winrate"] == 0.6
    assert r["version"] == "15.22.1" and r["names"]["6653"] == "Tourment de Liandry"
    assert r["reference"] is None and all(row["ref"] is None for row in r["by_item"])

    slot1, slot2, slot3 = r["slots"]
    assert [s["slot"] for s in r["slots"]] == [1, 2, 3] and [s["builds"] for s in r["slots"]] == [51, 41, 21]
    assert [(x["item"], x["games"]) for x in slot1["items"]] == [(6653, 21), (4645, 20), (3089, 10)]  # undone Rabadon not first
    liandry = _row(slot1["items"], 6653)
    assert liandry["share"] == round(21 / 51, 4) and liandry["decided"] == 20 and liandry["wins"] == 15
    assert liandry["timing"]["median"] == 12.0 and liandry["sold"] == 0
    assert slot1["timing"]["p25"] == 12.0 and slot1["timing"]["median"] == 13.0 and slot1["timing"]["p75"] == 13.0
    assert slot1["timing"]["histogram"][6] == 41 and slot1["timing"]["histogram"][13] == 10 and len(slot1["timing"]["histogram"]) == 21
    assert [(x["item"], x["games"], x["share"]) for x in slot2["items"]] == [(3157, 41, 1.0)]
    assert [x["item"] for x in slot3["items"]] == [3089]

    rabadon = _row(r["by_item"], 3089)
    assert rabadon["games"] == 31 and rabadon["avg_slot"] == round((21 * 3 + 10) / 31, 2)
    assert _row(r["by_item"], 4645)["sold"] == 1.0
    assert r["boots"]["builds"] == 51 and r["boots"]["none"] == 10
    assert [(x["item"], x["games"]) for x in r["boots"]["items"]] == [(3020, 21), (3006, 20)]

    two, three = r["paths"]
    assert two["length"] == 2 and two["builds"] == 41 and three["builds"] == 21
    assert two["covered"] == 1.0 and three["covered"] == 1.0
    assert [(p["items"], p["games"]) for p in two["rows"]] == [([6653, 3157], 21), ([4645, 3157], 20)]
    assert _row(two["rows"], [4645, 3157])["timing"]["median"] == 25.0  # timing of the last item
    assert [(p["items"], p["share"]) for p in three["rows"]] == [([6653, 3157, 3089], 1.0)]
    # Listed rows with at least 12 decided games: 2 + 1 + 1 (slots) + 4 (items) + 2 (boots) + 2 + 1 (paths)
    assert r["tested"] == 13
    assert [(lv["key"], lv["by_chance"]) for lv in r["levels"]] == [("strong", 0.3), ("likely", 1.3), ("hint", 2.6)]


def test_contrast_against_the_expected_winrate_of_the_cell(ddragon, tmp_path):
    conn = _fill(tmp_path / "s.db")
    r = items.analyse(soloq.load(conn), Filters(), ["Ahri"], conn=conn)
    slot1 = r["slots"][0]["items"]
    liandry, shadowflame, rabadon = (_row(slot1, k) for k in (6653, 4645, 3089))
    # Liandry (12 min) and Shadowflame (13 min) share a cell: each is expected to win like the other one
    assert liandry["expected"] == 0.25 and shadowflame["expected"] == 0.75
    assert liandry["winrate"] == 0.75 and liandry["delta"] == 0.5
    assert liandry["wr_adjusted"] == 0.5  # (15 + 20 x 0.25) / (20 + 20)
    assert (liandry["wr_low"], liandry["wr_high"]) == (0.5313, 0.8881)
    assert shadowflame["winrate"] == 0.25 and shadowflame["wr_adjusted"] == 0.5 and shadowflame["delta"] == -0.5
    assert liandry["z"] == 5.16 and liandry["level"] == "likely" and liandry["verdict"] == "better"  # 20 games < 30
    assert shadowflame["z"] == -5.16 and shadowflame["verdict"] == "worse"
    # Rabadon at 26 min is alone in its cell: the other purchases of the slot (20 wins / 40), too few games for a level
    assert rabadon["expected"] == 0.5 and rabadon["winrate"] == 1.0 and rabadon["level"] is None
    assert rabadon["z"] == 3.16 and rabadon["wr_adjusted"] == 0.6667
    # A row alone in its list falls back on 50 %
    zhonya = r["slots"][1]["items"][0]
    assert zhonya["expected"] == 0.5 and zhonya["z"] == 0 and zhonya["level"] is None
    assert _row(r["boots"]["items"], 3020)["expected"] == 0.25


def test_api_items_filters(ddragon):
    _fill(soloq.SOLOQ_DB_PATH).close()
    client = TestClient(main.app)
    base = "/api/items?league=SOLOQ&champion=Ahri"
    every = client.get(base).json()
    assert every["builds"] == 51 and every["reference"] is None
    assert every["options"]["players"] == [{"player": "Bob#EUW", "games": 42}, {"player": "Alice#EUW", "games": 10}]
    assert every["options"]["opponents"] == [{"champion": "Zed", "games": 47}, {"champion": "Syndra", "games": 5}]

    alice = client.get(base + "&player=alice%23euw").json()  # Riot ID, case-insensitive
    assert alice["builds"] == 10 and alice["player"] == "alice#euw"
    assert alice["reference"] == {"builds": 51, "decided": 50, "winrate": 0.6, "comparable": True}
    liandry = _row(alice["slots"][0]["items"], 6653)
    assert liandry["share"] == 1.0 and liandry["ref"] == {"share": round(21 / 51, 4), "median": 12.0}
    assert alice["options"] == every["options"]  # the options ignore the player and matchup filters
    exact_case = client.get(base + "&player=Alice%23EUW").json()
    assert {k: v for k, v in exact_case.items() if k != "player"} == {k: v for k, v in alice.items() if k != "player"}

    syndra = client.get(base + "&vs=Syndra&min_games=1").json()
    assert syndra["builds"] == 5 and syndra["reference"]["comparable"] is False
    assert [(x["item"], x["ref"]["share"]) for x in syndra["slots"][0]["items"]] == [(3089, round(10 / 51, 4))]
    assert syndra["boots"]["none"] == 5

    assert client.get(base + "&role=top").json()["builds"] == 0
    assert client.get(base + "&role=mid&player=Nobody%23EUW").json()["builds"] == 0
    # Several champions are grouped: the opponents' Zhonya at 1 min is Zed's slot 1
    both = client.get(base + "&champion=Zed").json()
    assert both["builds"] == 51 + 46 and _row(both["slots"][0]["items"], 3157)["games"] == 46
    assert both["missing_timeline"] == 1  # Ahri and Zed in the same game without timeline: one game
    # Rows under `min_games` purchases are left out of the lists, not out of the totals
    strict = client.get(base + "&min_games=15").json()
    assert [x["item"] for x in strict["slots"][0]["items"]] == [6653, 4645] and strict["slots"][0]["builds"] == 51


def test_api_items_guardrails(ddragon):
    _fill(soloq.SOLOQ_DB_PATH).close()
    client = TestClient(main.app)

    def detail(url):
        answer = client.get(url)
        return answer.status_code, answer.json().get("detail")

    assert detail("/api/items?league=SOLOQ&champion=") == (422, "Choose at least one champion")
    eleven = "".join(f"&champion=C{i}" for i in range(11))
    assert detail(f"/api/items?league=SOLOQ{eleven}") == (422, "At most 10 champions")
    assert client.get(f"/api/items?league=SOLOQ{eleven.replace('C10', 'C0')}").status_code == 200  # 10 distinct
    assert detail("/api/items?league=SOLOQ&champion=Ahri&role=jungler") == (422, "Unknown role jungler")
    assert client.get("/api/items?league=SOLOQ&champion=Ahri&min_games=0").status_code == 422
    ddragon.down = True
    status, message = detail("/api/items?league=SOLOQ&champion=Ahri")
    assert status == 503 and message.startswith("Item data unavailable: Data Dragon unreachable")
    ddragon.down = False
    assert client.get("/api/items?league=SOLOQ&champion=Ahri").status_code == 200
    ddragon.down = True
    assert client.get("/api/items?league=SOLOQ&champion=Ahri&rank=DIAMOND").status_code == 200  # cached now


def test_api_items_answer_without_builds_has_the_full_shape(ddragon):
    _fill(soloq.SOLOQ_DB_PATH).close()
    client = TestClient(main.app)
    empty = client.get("/api/items?league=SOLOQ&champion=Teemo").json()  # not in the scope: no error
    assert empty == {
        "champions": ["Teemo"], "role": None, "player": None, "vs": None, "min_games": 5,
        "version": None, "names": {}, "builds": 0, "decided": 0, "missing_timeline": 0,
        "baseline": {"wins": 0, "decided": 0, "winrate": None, "wr_low": None, "wr_high": None},
        "slots": [], "by_item": [], "boots": {"builds": 0, "none": 0, "items": []},
        "paths": [{"length": 2, "builds": 0, "covered": None, "rows": []},
                  {"length": 3, "builds": 0, "covered": None, "rows": []}],
        "tested": 0,
        "levels": [{"key": "strong", "z": 2.33, "min_games": 30, "by_chance": 0.0},
                   {"key": "likely", "z": 1.64, "min_games": 20, "by_chance": 0.0},
                   {"key": "hint", "z": 1.28, "min_games": 12, "by_chance": 0.0}],
        "reference": None, "options": {"players": [], "opponents": []},
    }
    filtered = client.get("/api/items?league=SOLOQ&champion=Ahri&player=Nobody%23EUW").json()
    assert filtered["builds"] == 0 and filtered["version"] is None and filtered["slots"] == []
    assert filtered["reference"] == {"builds": 51, "decided": 50, "winrate": 0.6, "comparable": False}
    assert filtered["options"]["players"]


def test_paths_keep_the_10_most_frequent_with_their_coverage(ddragon, tmp_path):
    conn = soloq.connect(tmp_path / "s.db")
    finished = [3089, 3157, 4645, 6653]
    pairs = [(a, b) for a in finished for b in finished if a != b]  # 12 distinct sequences of 2
    for i, (a, b) in enumerate(pairs):
        _store(conn, f"EUW1_{i}", [(600, "P", a), (1200, "P", b)])
    _store(conn, "EUW1_short", [(15, "P", 1056), (300, "P", 2003)])  # a 15-minute surrender: no finished item
    conn.commit()
    r = items.analyse(soloq.load(conn), Filters(), ["Ahri"], min_games=1, conn=conn)
    two = r["paths"][0]
    assert r["builds"] == 13 and r["slots"][0]["builds"] == 12 and r["boots"]["none"] == 13
    assert two["builds"] == 12 and len(two["rows"]) == 10 and two["covered"] == round(10 / 12, 4)
    assert two["rows"][0]["items"] == [3089, 3157]  # equal counts: ordered by ids
    assert r["paths"][1] == {"length": 3, "builds": 0, "covered": None, "rows": []}


def test_items_scales_to_10000_builds(ddragon, tmp_path):
    """REQ-ITEM-10: 10 000 builds in under 6 s on CI (twice the 3 s budget) and under 50 MB of extra memory.

    tracemalloc slows Python down ~10 times, so memory is traced on 2 000 of the builds: the per-request
    memory is a fixed chunk of blobs plus a part proportional to the builds, so 10 MB for 2 000 builds
    bounds 10 000 builds under 50 MB."""
    import json
    import random
    import time
    import tracemalloc
    import zlib

    rng = random.Random(7)
    pool = [3089, 3157, 4645, 6653, 3004, 3040, 3041, 1052, 1058, 2003, 1001, 3020, 3006]
    blobs = []
    for _ in range(40):
        events = []
        for pid in range(1, 11):
            t = 0
            for _ in range(30):
                t += rng.randint(10, 90)
                action = rng.choices("PSDU", [70, 5, 22, 3])[0]
                item = rng.choice(pool)
                events.append([t, pid, "U", item, 0] if action == "U" else [t, pid, action, item])
        blobs.append(zlib.compress(json.dumps(sorted(events, key=lambda e: e[0])).encode(), 9))
    conn = soloq.connect(tmp_path / "s.db")
    roles = soloq.ROLES
    for i in range(10_000):
        mid = f"EUW1_{i}"
        conn.execute("INSERT INTO soloq_games (match_id, platform, region, tier, patch, date, duration, winner)"
                     " VALUES (?, 'euw1', 'europe', 'DIAMOND', ?, '2025-11-17T10:00:00', 30, ?)",
                     (mid, "15.21" if i < 2000 else "15.22", 1 + i % 2))
        conn.executemany("INSERT INTO soloq_players (match_id, side, role, participant, champion) VALUES (?, ?, ?, ?, ?)",
                         [(mid, side, role, (side - 1) * 5 + k + 1, "Ahri" if (side, role) == (1, "mid") else f"F{side}{k}")
                          for side in (1, 2) for k, role in enumerate(roles)])
        conn.execute("INSERT INTO soloq_timelines (match_id, items) VALUES (?, ?)", (mid, blobs[i % len(blobs)]))
    conn.commit()
    ds = soloq.load(conn)
    items.catalogue("15.21")
    items.catalogue("15.22")

    start = time.perf_counter()
    r = items.analyse(ds, Filters(), ["Ahri"], conn=conn)
    elapsed = time.perf_counter() - start
    assert r["builds"] == 10_000 and r["slots"]
    assert elapsed < 6, elapsed

    tracemalloc.start()
    try:
        subset = items.analyse(ds, Filters(patches=("15.21",)), ["Ahri"], conn=conn)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert subset["builds"] == 2000
    assert peak < 10_000_000, peak


def test_api_items_only_in_soloq_scope(ddragon, monkeypatch):
    def no_dataset(*_):
        raise AssertionError("the solo queue data must not be read")

    monkeypatch.setattr(main, "get_dataset", no_dataset)
    client = TestClient(main.app)
    message = "Item analyses need the solo queue scope (pro games have no item purchases)"
    for url in ("/api/items?league=LCK&champion=Ahri", "/api/items?champion=Ahri"):
        answer = client.get(url)
        assert answer.status_code == 422 and answer.json()["detail"] == message
