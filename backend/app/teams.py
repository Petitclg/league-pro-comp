"""Franchise lineage: LCK teams were renamed or sold many times, so stats are grouped by
the franchise's current name."""

TEAM_ALIASES = {
    "SK Telecom T1": "T1",
    "Samsung Galaxy": "Gen.G",
    "Longzhu Gaming": "DRX",
    "Kingzone DragonX": "DRX",
    "DragonX": "DRX",
    "Kiwoom DRX": "DRX",
    "Afreeca Freecs": "DN SOOPers",
    "Kwangdong Freecs": "DN SOOPers",
    "DN Freecs": "DN SOOPers",
    "DAMWON Gaming": "Dplus KIA",
    "DWG KIA": "Dplus KIA",
    "Dplus Kia": "Dplus KIA",
    "Fredit BRION": "HANJIN BRION",
    "BRION": "HANJIN BRION",
    "OKSavingsBank BRION": "HANJIN BRION",
    "SANDBOX Gaming": "BNK FearX",
    "Liiv SANDBOX": "BNK FearX",
    "FearX": "BNK FearX",
    "BNK FEARX": "BNK FearX",
    "APK Prince": "SeolHaeOne Prince",
}


def canonical(team: str | None) -> str | None:
    return TEAM_ALIASES.get(team, team) if team else team
