# API

Source de vérité : [main.py](../backend/app/main.py). Documentation interactive OpenAPI : `/docs` quand le serveur tourne.

## Paramètres communs

| Paramètre | Type | Sémantique |
|---|---|---|
| `league` | liste | Ligues combinées (défaut `LCK`) ; 404 si inconnue |
| `team`, `tournament`, `patch` | liste | OU au sein du filtre |
| `year` | liste `YYYY` | OU au sein du filtre |
| `fearless` | bool | Fearless ou draft classique |
| `game_number` | liste 1-7 | Numéro de game dans la série |

Les filtres entre eux sont combinés en ET.

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/api/meta` | Périmètre : ligues, équipes, années, tournois, patchs, version, mode bureau |
| GET | `/api/series` | Séries paginées (`limit` ≤ 100, `offset`) ; filtres `champion`, `role`, `with`, `versus`, `game` |
| GET | `/api/champions` | Tableau des champions |
| GET | `/api/champions/{name}` | Fiche d'un champion ; 404 si aucune donnée |
| GET | `/api/pairs` | Paires ; `min_games`, `min_lift`, `role_a`, `role_b`, `champion`, `sort` (`games`, `winrate`, `lift`, `presence`), `limit` ≤ 500 |
| GET | `/api/duo` | Détail d'une synergie ou d'un matchup (`kind`, `a`, `b`) |
| GET | `/api/denial` | Contre de synergie pour la paire `a`, `b` |
| GET | `/api/countered-synergies` | Classement des synergies contrées (`limit` ≤ 50) |
| GET | `/api/records` | Records |
| POST | `/api/suggest` | Suggestions de comp |
| POST | `/api/live/send-recos` | Envoie les recommandations dans le chat du champ select |
| GET | `/api/live` | État du champ select ; `connected: false` hors client |
| GET | `/api/import` | Progression de l'import |
| POST | `/api/import` | Démarre un import (`leagues`, `first_year` ≥ 2017) ; 409 si déjà en cours, 422 si ligue inconnue |
| POST | `/api/import/cancel` | Annule l'import entre deux ligue-années |
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
