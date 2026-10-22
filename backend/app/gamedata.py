"""Data Dragon items and runes: what the pro item/rune names of the database stand for in the game."""
import httpx

CDN = "https://ddragon.leagueoflegends.com"
SUMMONERS_RIFT = "11"
MIN_FINAL_ITEM_GOLD = 1000
MIN_BOOTS_GOLD = 800  # tier 2 and 3 boots, not the 300 gold base ones

# Stat shards are not in runesReforged.json; ids are stable across patches.
SHARDS = {
    "Adaptive Force": 5008, "Attack Speed": 5005, "Ability Haste": 5007, "Move Speed": 5010,
    "Health": 5011, "Health Scaling": 5001, "Tenacity and Slow Resist": 5013, "Armor": 5002, "Magic Resist": 5003,
}

_catalog: dict | None = None


def _item_entry(item_id: str, raw: dict) -> dict:
    gold = raw.get("gold") or {}
    total = gold.get("total", 0)
    consumable = bool(raw.get("consumed"))
    boots = gold.get("purchasable", True) and not consumable and "Boots" in (raw.get("tags") or []) and total >= MIN_BOOTS_GOLD
    return {
        "id": int(item_id),
        "name": raw["name"],
        "boots": boots,
        # Not purchasable but complete: items a stacked item turns into (Muramana, Seraph's Embrace)
        "final": not consumable and not boots and not raw.get("into") and total >= MIN_FINAL_ITEM_GOLD,
    }


def parse_catalog(items: dict, styles: list[dict]) -> dict:
    """{"items": {lower-case name: {id, name, boots, final}}, "perks": {name: id}, "styles": {name: id}} from the
    two Data Dragon documents. Only Summoner's Rift items count and, for a repeated name, the first id wins."""
    by_name: dict[str, dict] = {}
    for item_id, raw in sorted(items.items(), key=lambda kv: int(kv[0])):
        if not (raw.get("maps") or {}).get(SUMMONERS_RIFT):
            continue
        by_name.setdefault(raw["name"].casefold(), _item_entry(item_id, raw))
    perks: dict[str, int] = {}
    style_ids: dict[str, int] = {}
    for style in styles:
        style_ids[style["name"]] = style["id"]
        for slot in style["slots"]:
            for rune in slot["runes"]:
                perks[rune["name"]] = rune["id"]
    return {"items": by_name, "perks": {**perks, **SHARDS}, "styles": style_ids}


def load_catalog() -> dict:
    """The current catalog, fetched once; empty when Data Dragon cannot be reached."""
    global _catalog
    if _catalog is None:
        try:
            version = httpx.get(f"{CDN}/api/versions.json", timeout=10).json()[0]
            items = httpx.get(f"{CDN}/cdn/{version}/data/en_US/item.json", timeout=15).json()["data"]
            styles = httpx.get(f"{CDN}/cdn/{version}/data/en_US/runesReforged.json", timeout=15).json()
        except (httpx.HTTPError, ValueError, KeyError):
            return {"items": {}, "perks": {}, "styles": {}}
        _catalog = parse_catalog(items, styles)
    return _catalog
