# LoL Pro Comp : spécification

Spécification versionnée avec le code (docs-as-code) : elle évolue dans la même PR que la fonctionnalité qu'elle décrit.

| Document | Contenu |
|---|---|
| [Exigences](requirements.md) | Objectifs, périmètre, exigences numérotées (`REQ-*`) et critères d'acceptation |
| [Architecture](architecture.md) | Composants, flux de données, déploiement, mise à jour |
| [Modèle de données](data-model.md) | Schéma SQLite, invariants, règles d'ingestion |
| [API](api.md) | Endpoints HTTP du backend |

## Conventions

- Chaque exigence a un identifiant stable `REQ-<domaine>-<n>`. Un test ou un module du dépôt la couvre (colonne « Vérifié par »).
- Les diagrammes sont en Mermaid (rendus par GitHub et MkDocs).
- Le français est la langue de l'interface et de la documentation ; le code et ses commentaires sont en anglais.
- Toute modification de comportement met à jour le document concerné et, si besoin, ajoute ou change une exigence.

## Générer le site

```powershell
pip install mkdocs-material
mkdocs serve    # http://127.0.0.1:8000
mkdocs build --strict
```
