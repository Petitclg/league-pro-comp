# Agents Copilot

Les *custom agents* de GitHub Copilot sont des profils spécialisés : un fichier Markdown avec un en-tête YAML (`name`, `description`, `tools`…) suivi des instructions de l'agent. Copilot les propose dans le sélecteur d'agents (Copilot CLI avec `/agent`, VS Code, agent cloud sur GitHub.com) et peut les choisir seul d'après leur `description`.

## Emplacement

| Dossier | Portée |
|---|---|
| `.github/agents/` | Agents du dépôt, versionnés et partagés avec tous les contributeurs |
| `~/.copilot/agents/` | Agents personnels, disponibles dans tous les dépôts de l'utilisateur |

Le nom du fichier (sans `.md` ou `.agent.md`) identifie l'agent ; à nom égal, la définition du dépôt prime sur l'agent personnel.

## Agents du dépôt

| Agent | Quand l'utiliser | Outils |
|---|---|---|
| [`stats-reviewer`](https://github.com/Petitclg/league-pro-comp/blob/main/.github/agents/stats-reviewer.md) | Relire un module d'analyse (`strength.py`, `denial.py`, `combos.py`, futures analyses d'items, modèle de probabilité de victoire) : lissage, intervalles de confiance, z-scores, seuils d'échantillon, tests multiples, biais de sélection | Lecture seule |
| [`riot-api`](https://github.com/Petitclg/league-pro-comp/blob/main/.github/agents/riot-api.md) | Écrire ou relire l'import solo-queue via l'API Riot : endpoints, routage plateforme / régional, limites de débit et `429`, format des timelines (`ITEM_PURCHASED`…), protection de la clé d'API | Lecture, écriture, terminal, web |
| [`release-checker`](https://github.com/Petitclg/league-pro-comp/blob/main/.github/agents/release-checker.md) | Avant de pousser un tag `vX.Y.Z` : version, tests, `build.ps1` (exe portable et installeur), fichiers SHA-256, assets de `release.yml`, chemin de mise à jour automatique. Rend un verdict GO / NO-GO sans rien modifier | Lecture, terminal |

Exemples avec Copilot CLI :

```powershell
copilot --agent stats-reviewer --prompt "Relis backend/app/denial.py"
copilot --agent release-checker --prompt "Vérifie la release v0.3.0"
```

## Maintenance

- Un agent décrit le dépôt tel qu'il est : quand un module, une commande ou le pipeline de release change, mettre à jour l'agent concerné dans la même PR.
- Les instructions des agents sont en anglais, comme le code.
