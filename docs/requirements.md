# Exigences

## Objectif

Analyser les drafts du LoL professionnel (2017 → aujourd'hui) et aider à construire une draft : statistiques par champion, synergies, counters, records, suggestions de comp avec suivi du champ select du client LoL.

## Périmètre

- Dans le périmètre : import depuis Leaguepedia, analyses statistiques, interface web locale, application de bureau Windows (exécutable unique), mise à jour automatique.
- Hors périmètre : compte utilisateur, hébergement multi-utilisateur, données de matchs en direct, affiliation Riot Games.

## Utilisateurs

| Persona | Besoin |
|---|---|
| Analyste / coach | Comparer des drafts, repérer synergies, counters, spots par équipe ou par joueur |
| Joueur en champ select | Recevoir des suggestions de pick alignées sur les comps pro |
| Mainteneur | Importer, mettre à jour, publier une version |

## Exigences fonctionnelles

### Données (DATA)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-DATA-1 | Les parties sont importées depuis Leaguepedia (Cargo) pour les ligues déclarées dans `leagues.py`, à partir de 2017. | `ingest.py`, `test_leagues.py` |
| REQ-DATA-2 | Réimporter une partie la remplace, sans doublon. | `db.save_game` |
| REQ-DATA-3 | Une partie dont la draft est incomplète (< 5 picks ou rôles non uniques par côté) est ignorée. | `test_app.py::test_parse_game_rejects_incomplete_draft` |
| REQ-DATA-4 | **Cohérence équipe / côté / joueurs** : pour un côté donné, l'équipe affichée, les 5 picks, les joueurs, les KDA et les statistiques de côté (kills, or, objectifs) concernent la même équipe. Le côté de la draft (Blue = côté 1, première sélection) fait foi ; les données du scoreboard sont rattachées par champion joué, car Leaguepedia liste parfois les équipes dans l'ordre inverse entre la draft et le scoreboard (constaté aux Worlds). | `test_app.py::test_parse_game_follows_champions_when_scoreboard_sides_are_swapped` |
| REQ-DATA-5 | Les franchises coréennes sont regroupées sous un nom unique. | `teams.py` |
| REQ-DATA-6 | Les parties des événements internationaux sont rattachées à la ligue organisatrice quand elle est connue. | `ingest.repair_leagues`, `test_leagues.py` |
| REQ-DATA-7 | Un tournoi est marqué Fearless quand ses séries multi-games ne répètent (presque) jamais de champion ; c'est déduit des données. | `dataset._flag_fearless`, `test_app.py` |
| REQ-DATA-8 | **Correction des données existantes** : chaque ligue porte une révision d'import (`INGEST_REVISION`). Au démarrage de l'application de bureau, les ligues importées sous une révision plus ancienne (événements internationaux compris) sont ré-importées automatiquement en arrière-plan, depuis la plus ancienne année stockée, avec une bannière de progression ; la page se recharge à la fin. Une ligue n'est marquée à jour que si l'import a couvert toutes ses parties stockées sans échec ; sinon (hors ligne, annulation) l'essai est refait au lancement suivant. Toute modification des règles d'import qui change des données déjà stockées incrémente `INGEST_REVISION`. | `test_repair.py`, `importer.outdated_leagues`, `RepairBanner.tsx` |

### Analyses (STAT)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-STAT-1 | Filtres communs : ligues, équipes, années, tournois, patchs, Fearless, numéro de game. OU au sein d'un filtre, ET entre filtres. | `test_multi_filters.py` |
| REQ-STAT-2 | Tableau des champions : présence, pick/ban rate, winrate ; fiche détaillée (KDA, CS/min, dégâts, side, game de série, patch, joueurs, synergies, matchups, bans). | `stats.champion_table`, `stats.champion_detail` |
| REQ-STAT-3 | Indice de force sur 100 : 60 % présence, 40 % winrate ajusté (réduit vers 50 % selon l'échantillon), paliers S→D. | `strength.py`, `test_strength.py` |
| REQ-STAT-4 | Paires (synergies) et matchups (counters) avec lift = fréquence réelle / attendue ; présence d'un duo corrigée des bans conjoints. | `stats.pair_stats`, `stats.pair_presence` |
| REQ-STAT-5 | Contre de synergie : anticipation (partenaire déjà banni), réaction (deny pick ou ban après), ban de l'un des deux ; comparé à une référence au même patch, moment de draft et type de game ; trois niveaux de fiabilité. | `denial.py`, `test_denial.py` |
| REQ-STAT-6 | Records : WR extrêmes, séries invaincues, dépendance au side, flex, signatures, one-tricks, perfs individuelles. | `stats.records` |
| REQ-STAT-7 | **Paire jouée quand elle est ouverte** : pour une équipe qui prend un membre d'une paire (A au poste ra, B au poste rb), la paire est ouverte si le partenaire est encore disponible (ni banni, ni pris par l'adversaire, ni verrouillé Fearless) au moment où l'équipe remplit le poste du partenaire. Taux = parties où l'équipe a les deux / cas ouverts (jouée + disponible mais non prise). Si l'adversaire a pris ou banni le partenaire avant ce moment, la paire est contrée : elle est comptée à part (`taken`, `banned`) et sort du dénominateur. Un partenaire déjà indisponible au premier pick, joué à un autre poste ou dont le poste est déjà rempli est ignoré. Affiché dans le tableau des paires (colonne et tri), la page d'une paire et les suggestions. | `combos.py`, `test_combos.py` |
| REQ-STAT-8 | **Matchups sans contradiction** : un face-à-face en lane est classé favorable ou difficile seulement si l'écart à 50 % ne s'explique pas par le hasard (z binomial et nombre de parties minimum par niveau : très fiable, fiable, indice) ; un adversaire n'apparaît que dans une liste. Les « spots » comparent un contexte à la moyenne du champion lui-même (pas à 50 %), n'incluent ni adversaires ni partenaires, et Blue/Red ne comptent que pour une trouvaille. | `strength.matchup_verdict`, `test_strength.py` |

### Interface (UI)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-UI-1 | Page Drafts : une carte par série (score, Bo, Fearless, stage), une ligne par game, Blue à gauche avec la première sélection ; l'équipe, les joueurs et les picks d'un bloc de côté sont cohérents (REQ-DATA-4). | `SeriesView.tsx` |
| REQ-UI-2 | Numéros d'ordre de draft sur les icônes (picks 1-10, bans 1-10) et highlights (synergie, counter de lane, signature, pick rare). | `stats.game_highlights` |
| REQ-UI-3 | Le périmètre (ligues, filtres) est global et persisté localement. | `scope.ts`, `App.tsx` |
| REQ-UI-6 | **Suivi de navigation** : chaque page garde ses réglages (tri, rôles, minimums, champion filtré) quand on la quitte et au redémarrage ; la draft en cours du tracker (picks, picks adverses, bans, champions Fearless, mode élargi) est conservée, avec un bouton « Nouvelle draft » pour la vider ; un lancement sans page dans l'adresse rouvre la dernière page visitée. Le tracker et l'envoi dans le chat restent désactivés au démarrage. | `usePersistentState` (`utils.ts`), `router.ts` |
| REQ-UI-4 | Recherche de champion tolérante (accents, apostrophes, surnoms, fautes). | `fuzzy.test.ts` |
| REQ-UI-5 | Pages : Drafts, Champions, Paires, Synergies contrées, Records, Suggestions / Tracker, Données. | `App.tsx` |

### Suggestions et tracker (SUG)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-SUG-1 | Chaque comp pro partageant *m* picks avec la draft vote pour ses champions aux rôles restants (poids *m²*, victoire ×1,25, défaite ×0,75). | `suggest.py`, `test_app.py` |
| REQ-SUG-2 | Les picks adverses connus ajustent chaque candidat par son winrate lissé contre eux (×0,5 à ×1,5). | `suggest.py` |
| REQ-SUG-3 | Bans et champions Fearless déjà joués sont exclus. | `suggest.py` |
| REQ-SUG-5 | **Bonus de paires** : un candidat est favorisé pour ses paires avec les champions déjà choisis (a) quand elles sont jouées systématiquement dès qu'elles sont ouvertes (REQ-STAT-7), d'autant plus qu'elles gagnent, et (b) quand elles ont un winrate élevé (lissé vers 50 %). Paramétré par `Params.combo_strength` et `Params.pair_wr_strength`, réglés par `python -m app.backtest`. Les suggestions exposent le taux de réalisation de chaque paire. | `suggest._pair_boost`, `test_combos.py` |
| REQ-SUG-4 | Le tracker lit le champ select via l'API locale du client LoL toutes les 2,5 s ; hors client, l'API répond « non connecté » sans erreur. | `live.py` |

### Application de bureau et distribution (APP)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-APP-1 | Un seul exécutable Windows, sans Python ni Node installé. | `build.ps1` |
| REQ-APP-2 | Au démarrage, l'application cherche une release GitHub plus récente, vérifie taille et SHA-256, remplace l'exécutable et relance. En cas d'échec, la version courante démarre. `LOLPRO_NO_UPDATE=1` désactive la recherche. | `updater.py`, `test_updater.py` |
| REQ-APP-3 | Fermer la fenêtre arrête l'application ; un second lancement réutilise l'instance en cours. | `desktop.py`, `test_desktop.py` |
| REQ-APP-4 | Les données sont dans `%LOCALAPPDATA%\LoLProComp` et survivent aux mises à jour. | `config.py` |
| REQ-APP-5 | L'import est lancé depuis la page Données, avec progression et annulation ; le serveur reste utilisable pendant l'import. | `importer.py` |

## Exigences non fonctionnelles

| ID | Exigence |
|---|---|
| REQ-NFR-1 | Au plus 3 combinaisons de ligues en mémoire ; rechargement automatique quand le fichier de base change. |
| REQ-NFR-2 | Les analyses lourdes du périmètre par défaut sont préchauffées au démarrage. |
| REQ-NFR-3 | Le serveur n'écoute qu'en local ; `/api/quit` exige un en-tête personnalisé (protection CORS). |
| REQ-NFR-4 | Les données importées conservent la licence CC BY-SA 3.0 de Leaguepedia ; images via Data Dragon. |
| REQ-NFR-5 | Tests backend (pytest) et frontend (vitest) verts avant toute release ; la CI publie l'exécutable avec son SHA-256. |

## Critères d'acceptation transverses

1. Après import, pour toute partie : `games.team1` est l'équipe du côté 1, et les joueurs de `picks` du côté 1 appartiennent à cette équipe.
2. Le score d'une série affiché en en-tête est égal au nombre de games gagnées par chaque équipe, quel que soit le côté joué.
3. Une analyse filtrée sur une équipe ne compte que les côtés joués par cette équipe.
