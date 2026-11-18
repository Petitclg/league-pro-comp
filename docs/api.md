# API

Source de vérité : [main.py](../backend/app/main.py). Documentation interactive OpenAPI : `/docs` quand le serveur tourne.

## Paramètres communs

| Paramètre | Type | Sémantique |
|---|---|---|
| `league` | liste | Ligues combinées (défaut `LCK`) ; 404 si inconnue. `SOLOQ` = parties solo queue (`soloq.db`), seul : 422 s'il est combiné à une ligue pro |
| `team`, `tournament`, `patch` | liste | OU au sein du filtre (en solo queue, `tournament` est la région : EUW, KR…) |
| `year` | liste `YYYY` | OU au sein du filtre |
| `fearless` | bool | Fearless ou draft classique |
| `game_number` | liste 1-7 | Numéro de game dans la série |
| `rank` | liste | Rang solo queue (`EMERALD`, `DIAMOND`, `MASTER`, `GRANDMASTER`, `CHALLENGER`) ; ne garde que des parties solo queue |

Les filtres entre eux sont combinés en ET.

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/api/meta` | Périmètre : ligues (plus l'entrée `SOLOQ` quand `soloq.db` contient des parties), équipes, années, tournois, patchs, version, mode bureau ; `soloq` (périmètre solo queue) et `ranks` (rangs présents) |
| GET | `/api/series` | Séries paginées (`limit` ≤ 100, `offset`) ; filtres `champion`, `role`, `with`, `versus`, `game` |
| GET | `/api/champions` | Tableau des champions |
| GET | `/api/champions/{name}` | Fiche d'un champion ; 404 si aucune donnée. `matchups` : `games`, `wins`, `winrate`, `z`, `level`, `verdict` (`favorable`, `difficult` ou `null`) |
| GET | `/api/pairs` | Paires ; `min_games`, `min_lift`, `role_a`, `role_b`, `champion`, `sort` (`games`, `winrate`, `lift`, `presence`, `realised`), `limit` ≤ 500. Chaque ligne porte `open` : `open`, `realised`, `taken`, `banned`, `unused`, `rate` (REQ-STAT-7) |
| GET | `/api/duo` | Détail d'une synergie ou d'un matchup (`kind`, `a`, `b`) ; pour une synergie, `open` donne les mêmes compteurs par combinaison de rôles |
| GET | `/api/denial` | Contre de synergie pour la paire `a`, `b` |
| GET | `/api/countered-synergies` | Classement des synergies contrées (`limit` ≤ 50) |
| GET | `/api/records` | Records |
| GET | `/api/items` | Analyses d'objets (solo queue seulement, REQ-ITEM-1 à 11) : `champion` (répété, 1 à 10), `role`, `player` (Riot ID), `vs` (adversaire au même rôle), `min_games` (≥ 1, défaut 5) et les filtres communs. 422 hors périmètre `SOLOQ`, sans champion, au-delà de 10 champions ou pour un rôle inconnu ; 503 si les données d'objets de Data Dragon sont indisponibles. Contrat ci-dessous |
| POST | `/api/suggest` | Suggestions de comp (corps : filtres, dont `ranks` en solo queue) |
| POST | `/api/live/send-recos` | Envoie les recommandations dans le chat du champ select |
| GET | `/api/live` | État du champ select ; `connected: false` hors client |
| GET | `/api/import` | Progression de l'import |
| POST | `/api/import` | Démarre un import (`leagues`, `first_year` ≥ 2017) ; 409 si déjà en cours, 422 si ligue inconnue |
| POST | `/api/import/cancel` | Annule l'import entre deux ligue-années |
| GET | `/api/soloq` | Solo queue : état de la clé API (`state` : `missing`, `unchecked`, `valid`, `invalid` ; `masked` : 4 derniers caractères seulement), réglages, progression de l'import, parties stockées (par rang, région, patch), plateformes et rangs disponibles |
| PUT | `/api/soloq/key` | Enregistre une clé (`key`, forme `RGAPI-…`) après une requête de vérification ; 422 si mal formée, 400 si Riot la refuse (non enregistrée), 503 si l'API Riot est injoignable |
| POST | `/api/soloq/key/check` | Revérifie la clé enregistrée (une clé de développement expire après 24 h) ; 404 sans clé |
| DELETE | `/api/soloq/key` | Supprime la clé |
| PUT | `/api/soloq/settings` | Réglages de l'import : `platforms` (`euw1`, `kr`…), `tiers`, `target` (parties), `min_patch` (`15.22` ou vide), `patches_kept` (0 = tout garder), `max_age_days` ; 422 si une valeur est inconnue |
| GET | `/api/soloq/import` | Progression : `state` (`idle`, `running`, `done`, `exhausted`, `error`, `cancelled`), `games`, `target`, `added`, `current`, `waiting` (pause de limite de débit), `log`, `error`, `key_rejected` |
| POST | `/api/soloq/import` | Démarre ou reprend l'import (corps facultatif : réglages, enregistrés) ; 400 sans clé ou clé invalide, 409 si déjà en cours |
| POST | `/api/soloq/import/cancel` | Arrête l'import (il reprendra là où il en était) |
| GET | `/api/health` | Identité et version (détection d'instance existante) |
| POST | `/api/heartbeat` | Ping de la fenêtre de bureau |
| POST | `/api/quit` | Arrêt demandé par une instance plus récente ; 403 sans en-tête `X-Requested-With`, 409 si import en cours ou hors mode bureau |

## Contrat d'une partie (`/api/series`)

```json
{
  "game_id": "string",
  "game_number": 2,
  "winner": 1,
  "sides": {
    "1": { "team": "string", "win": true, "stats": {"kills": 12}, "picks": [{"role": "top", "champion": "string", "player": "string", "slot": 1}], "bans": [{"champion": "string", "order": 1}] },
    "2": { "team": "string", "win": false, "stats": {}, "picks": [], "bans": [] }
  }
}
```

Invariant : `sides[n].team`, `sides[n].picks[*].player` et `sides[n].stats` concernent la même équipe (REQ-DATA-4). Le côté `"1"` est Blue.

En solo queue, chaque série contient une seule game, les équipes sont `Blue` et `Red`, `tournament` est la région, `rank` (série et game) donne le rang, `player` est le Riot ID et l'ordre de pick (`slot`) est fictif (attribué par rôle).

## Contrat des analyses d'objets (`/api/items`)

```json
{
  "champions": ["Ahri"], "role": "mid", "player": null, "vs": null, "min_games": 5,
  "version": "15.22.1",
  "names": {"3089": "Coiffe de Rabadon"},
  "builds": 120, "decided": 120, "missing_timeline": 2,
  "baseline": {"wins": 64, "decided": 120, "winrate": 0.5333, "wr_low": 0.44, "wr_high": 0.62},
  "slots": [{"slot": 1, "builds": 118, "timing": {}, "items": ["ItemStat"]}],
  "by_item": ["ItemStat + avg_slot"],
  "boots": {"builds": 120, "none": 6, "items": ["ItemStat"]},
  "paths": [{"length": 2, "builds": 110, "covered": 0.83, "rows": ["PathStat"]}, {"length": 3, "builds": 95, "covered": 0.4, "rows": []}],
  "tested": 42,
  "levels": [{"key": "strong", "z": 2.33, "min_games": 30, "by_chance": 0.8}],
  "reference": {"builds": 900, "decided": 899, "winrate": 0.51, "comparable": true},
  "options": {"players": [{"player": "Name#EUW", "games": 12}], "opponents": [{"champion": "Zed", "games": 30}]}
}
```

| Champ | Sémantique |
|---|---|
| `builds` | Builds analysés : un par pick des champions choisis (au rôle choisi, après les filtres `player` et `vs`) dans une partie du périmètre qui a sa timeline ; `decided` : ceux dont la partie a un vainqueur ; `missing_timeline` : parties distinctes sans timeline parmi ces picks |
| `names`, `version` | Nom français de chaque objet cité dans une liste (clé : id en texte ; catalogue du patch le plus récent qui le connaît) et version Data Dragon des images (`/cdn/<version>/img/item/<id>.png`) : celle du patch le plus récent des builds, `null` sans build |
| `ItemStat` | `item` (id), `games` (achats), `share` (part des builds de la liste), `wins`, `decided`, `winrate`, `wr_low` / `wr_high` (Wilson 95 %), `expected` (winrate attendu, REQ-ITEM-6), `wr_adjusted` (lissé vers l'attendu), `delta`, `z`, `level` (`strong`, `likely`, `hint` ou `null`), `verdict` (`better`, `worse` ou `null`), `timing` (`p25`, `median`, `p75` en minutes, `histogram` : achats par tranche de 2 min, la dernière = 40 min et plus), `sold` (part revendue), `ref` (`{share, median}` dans le build moyen, ou `null`) ; dans `by_item`, `avg_slot` |
| `PathStat` | `items` (ids dans l'ordre), mêmes champs qu'`ItemStat` ; `timing` et `sold` sont ceux du dernier objet de la séquence ; `paths[].covered` : part des `builds` de la longueur couverte par les lignes listées (`null` sans build) |
| `slots` | Emplacements atteints par au moins un build, dans l'ordre (1 à 6) ; `builds` : builds qui ont au moins autant d'objets complétés ; `timing` : tous les achats de l'emplacement |
| `boots.none` | Builds sans bottes améliorées |
| `reference` | Build moyen (mêmes champions, rôle et périmètre, sans `player` ni `vs`), `null` sans ces filtres ; `comparable` faux sous 10 builds filtrés |
| `tested`, `levels` | `tested` : lignes listées (toutes listes, après `min_games` et la limite de 10 chemins) d'au moins 12 parties décidées (assez pour un niveau) ; `levels[].by_chance` = `tested` × probabilité d'une fausse trouvaille au niveau (bilatérale), arrondi à 0,1 |
| `options` | Joueurs (Riot ID) et adversaires de lane des picks des champions choisis, au rôle choisi, dans le périmètre, timeline ou non, sans les filtres `player` et `vs` ; tri par parties décroissantes puis par nom |

Ordre et arrondis : les lignes sont triées par `games` décroissant puis par id (ou séquence d'ids) ; parts, winrates, attendus et écarts à 4 décimales, minutes à 1 décimale, `z` à 2, `avg_slot` à 2. Une ligne sans partie décidée a `winrate`, `wr_low`, `wr_high`, `expected`, `wr_adjusted`, `delta`, `z`, `level` et `verdict` à `null`.

| Statut | Cas | `detail` |
|---|---|---|
| 422 | Périmètre autre que `SOLOQ` (y compris la ligue par défaut) | `Item analyses need the solo queue scope (pro games have no item purchases)` |
| 422 | Aucun champion (paramètres vides ignorés) | `Choose at least one champion` |
| 422 | Plus de 10 champions distincts (les doublons ne comptent qu'une fois) | `At most 10 champions` |
| 422 | Rôle inconnu ; `min_games` < 1 | `Unknown role …` ; erreur de validation FastAPI |
| 503 | Catalogue d'un des patchs des builds introuvable (Data Dragon injoignable, pas de cache) | `Item data unavailable: …` |

Réponse sans build (champion absent du périmètre, filtres trop stricts) : statut 200 et forme complète.

```json
{
  "champions": ["Ahri"], "role": "top", "player": null, "vs": null, "min_games": 5,
  "version": null, "names": {},
  "builds": 0, "decided": 0, "missing_timeline": 0,
  "baseline": {"wins": 0, "decided": 0, "winrate": null, "wr_low": null, "wr_high": null},
  "slots": [], "by_item": [], "boots": {"builds": 0, "none": 0, "items": []},
  "paths": [{"length": 2, "builds": 0, "covered": null, "rows": []}, {"length": 3, "builds": 0, "covered": null, "rows": []}],
  "tested": 0,
  "levels": [{"key": "strong", "z": 2.33, "min_games": 30, "by_chance": 0.0}, {"key": "likely", "z": 1.64, "min_games": 20, "by_chance": 0.0}, {"key": "hint", "z": 1.28, "min_games": 12, "by_chance": 0.0}],
  "reference": null,
  "options": {"players": [{"player": "Name#EUW", "games": 12}], "opponents": [{"champion": "Zed", "games": 30}]}
}
```
