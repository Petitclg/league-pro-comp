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
