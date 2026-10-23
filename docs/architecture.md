# Architecture

## Vue d'ensemble

```mermaid
flowchart LR
    LP[(Leaguepedia\nCargoExport)] -->|CSV| ING[ingest.py]
    ING --> DB[(SQLite lck.db)]
    DB --> DS[dataset.py\nDataset en mémoire]
    DS --> ST[stats / strength / denial / suggest]
    ST --> API[FastAPI main.py]
    API --> UI[Front React + Vite]
    LCU[Client LoL\nAPI locale] --> LIVE[live.py]
    LIVE --> API
    DESK[desktop.py\nfenêtre Edge/Chrome] --> API
    GH[GitHub Releases] --> UPD[updater.py]
```

## Composants

| Composant | Rôle |
|---|---|
| `backend/app/ingest.py` | Requêtes Cargo paginées, fusion draft + scoreboard, écriture en base |
| `backend/app/db.py` | Schéma SQLite, migrations, écriture des parties |
| `backend/app/dataset.py` | Chargement en mémoire par ensemble de ligues, filtres, comps par côté, détection Fearless |
| `backend/app/stats.py` | Tableaux champions, paires, duos, records, highlights |
| `backend/app/strength.py` | Indice de force et spots |
| `backend/app/denial.py` | Contre de synergie |
| `backend/app/suggest.py` | Suggestions de comp |
| `backend/app/live.py` | Lecture du champ select (LCU), envoi des recommandations |
| `backend/app/importer.py` | Import en arrière-plan piloté par l'interface |
| `backend/app/desktop.py`, `launcher.py`, `runtime.py` | Application de bureau, instance unique, arrêt |
| `backend/app/updater.py` | Mise à jour automatique |
| `frontend/src` | Pages React, client d'API (`api.ts`), filtres globaux (`scope.ts`), routage (`router.ts`) |

## Flux d'import

```mermaid
sequenceDiagram
    participant UI
    participant API
    participant Ingest
    participant Cargo as Leaguepedia
    participant DB
    UI->>API: POST /api/import
    API->>Ingest: thread (ligues, années)
    loop ligue x année
        Ingest->>Cargo: PicksAndBansS7 + ScoreboardGames
        Ingest->>Cargo: ScoreboardPlayers
        Ingest->>Ingest: parse_game (rattachement par champion)
        Ingest->>DB: save_game
        Ingest->>Cargo: MatchSchedule
        Ingest->>DB: save_match
    end
    UI->>API: GET /api/import (progression)
```

## Réparation au démarrage

```mermaid
flowchart TD
    S[Démarrage de l'application de bureau] --> O{Ligues stockées\navec une révision\nplus ancienne ?}
    O -- non --> N[Rien]
    O -- oui --> J[Import en arrière-plan\ndepuis la plus ancienne année stockée]
    J --> B[Bannière de progression]
    J -- succès complet --> M[Ligue marquée à jour\nrechargement de la page]
    J -- échec / annulation --> R[Nouvel essai au prochain lancement]
```

## Déploiement

- Développement : `uvicorn` (port 8000) + Vite (5173).
- Production : PyInstaller (`build.ps1`) embarque le front compilé ; le port préféré est 8765.
- Release : un tag `vX.Y.Z` déclenche le workflow GitHub Actions (tests, build, publication avec SHA-256).

## Décisions

| Décision | Raison |
|---|---|
| Leaguepedia via `Special:CargoExport` | `api.php` est limité en permanence ; Oracle's Elixir sur Google Drive a un quota dépassé |
| Côté de la draft comme référence | L'ordre de draft (première sélection = Blue) n'a de sens que pour la table de drafts |
| Rattachement du scoreboard par champion | Les deux sources peuvent lister les équipes dans l'ordre inverse (voir REQ-DATA-4) |
| Fearless déduit des données | Évite une liste de tournois à maintenir |
| SQLite + chargement mémoire | Application locale mono-utilisateur, analyses en Python |
