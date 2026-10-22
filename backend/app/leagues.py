"""Leagues available for import, keyed by the short name used in the app.

`sources` are the league names of Leaguepedia's `Tournaments.League` field. A league that was
renamed (EU LCS -> LEC, NA LCS -> LCS...) lists every name so its history stays in one place.
"""

LEAGUES: dict[str, dict] = {
    "LCK": {"name": "LCK (Corée)", "region": "Korea", "sources": ["LoL Champions Korea"]},
    "LPL": {"name": "LPL (Chine)", "region": "China", "sources": ["Tencent LoL Pro League"]},
    "LEC": {"name": "LEC (Europe)", "region": "Europe",
            "sources": ["LoL EMEA Championship", "Europe League Championship Series"]},
    "LCS": {"name": "LCS (Amérique du Nord)", "region": "North America",
            "sources": ["League of Legends Championship Series", "North America League Championship Series"]},
    "LTA": {"name": "LTA (Amériques)", "region": "Americas",
            "sources": ["League of Legends Championship of The Americas",
                        "League of Legends Championship of The Americas North",
                        "League of Legends Championship of The Americas South"]},
    "LCP": {"name": "LCP (Asie-Pacifique)", "region": "Asia Pacific",
            "sources": ["League of Legends Championship Pacific"]},
    "PCS": {"name": "PCS (Pacifique)", "region": "Asia Pacific", "sources": ["Pacific Championship Series"]},
    "VCS": {"name": "VCS (Vietnam)", "region": "Vietnam", "sources": ["Vietnam Championship Series"]},
    "CBLOL": {"name": "CBLOL (Brésil)", "region": "Brazil", "sources": ["Circuit Brazilian League of Legends"]},
    "LLA": {"name": "LLA (Amérique latine)", "region": "Latin America",
            "sources": ["Liga Latinoamerica", "Liga Latinoamérica Norte", "Copa Latinoamérica Sur"]},
    "LJL": {"name": "LJL (Japon)", "region": "Japan", "sources": ["LoL Japan League"]},
    "TCL": {"name": "TCL (Turquie)", "region": "Turkey", "sources": ["Turkish Championship League"]},
    "LCO": {"name": "LCO / OPL (Océanie)", "region": "Oceania",
            "sources": ["LoL Circuit Oceania", "Oceanic Pro League"]},
    "LMS": {"name": "LMS (Taïwan / HK / Macao)", "region": "Asia Pacific", "sources": ["LoL Master Series"]},
    "LST": {"name": "LST / GPL (Asie du Sud-Est)", "region": "SEA",
            "sources": ["League of Legends SEA Tour", "Garena Premier League"]},
    "LCL": {"name": "LCL (CEI)", "region": "CIS", "sources": ["LoL Continental League"]},
    "WORLDS": {"name": "Worlds", "region": "International", "sources": ["World Championship"]},
    "MSI": {"name": "MSI", "region": "International", "sources": ["Mid-Season Invitational"]},
    "FST": {"name": "First Stand", "region": "International", "sources": ["First Stand"]},
}
for _league in LEAGUES.values():
    _league["level"] = "major"

DEFAULT_LEAGUE = "LCK"

INTERNATIONAL = {"WORLDS", "MSI", "FST"}

# Game ids start with the organising league's name as written on Leaguepedia
PREFIX_LEAGUE = {
    "OPL": "LCO", "NA LCS": "LCS", "EU LCS": "LEC", "CLS": "LLA", "LLN": "LLA",
    "LTA North": "LTA", "LTA South": "LTA", "GPL": "LST",
}


def owner_league(game_id: str) -> str | None:
    """League that ran a game, from its id ("LCK/2024 Season/Regional Finals_..." -> LCK).

    Leaguepedia tags some regional qualifiers (Regional Finals, Play-Ins) as World Championship
    games: they still belong to the league that organised them. None for events with no league
    prefix (the Worlds Main Event itself)."""
    prefix = game_id.split("/", 1)[0] if "/" in game_id else ""
    return PREFIX_LEAGUE.get(prefix) or (prefix if prefix in LEAGUES else None)
