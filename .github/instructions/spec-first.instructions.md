---
description: Cycle de développement en V de ce repo — spec d'abord, implémentation après approbation, validation par relecture des diffs, puis tests.
applyTo: '**'
---

# Cycle en V : spec d'abord

Toute évolution de ce repo suit les quatre étapes suivantes, dans l'ordre.

1. **Spécification d'abord.** Lire la spécification existante (`docs/requirements.md`, `docs/architecture.md`, `docs/data-model.md`, `docs/api.md`) et proposer une **modification de spec** décrivant le changement : exigences, impacts sur l'architecture, le modèle de données et l'API.
2. **Implémentation après approbation.** N'implémenter qu'une fois la modification de spec approuvée. L'implémentation doit couvrir exactement ce que la spec décrit — ni plus, ni moins.
3. **Validation par relecture des diffs.** Relire ensemble le **diff de spec** et le **diff de code** : chaque exigence ajoutée ou modifiée doit se retrouver dans le code, et chaque changement de code doit se rattacher à la spec. Signaler et résoudre tout écart avant de poursuivre.
4. **Tests en dernier.** Exécuter la suite existante (`python -m pytest -q` dans `backend/`, `npm test --prefix frontend`) et ajouter les tests qui couvrent les nouvelles exigences de la spec.

Exemple : pour ajouter un filtre de rang, on commence par amender `docs/requirements.md` (nouvelle exigence) et `docs/api.md` (paramètre de route), on attend l'approbation, on code, on vérifie que le diff de code correspond au diff de docs, puis on teste. Il est incorrect de committer du code dont le comportement n'est décrit dans aucune spec, ou une spec jamais reflétée dans le code.
