# Exigences

## Objectif

Analyser les drafts du LoL professionnel (2017 → aujourd'hui) et aider à construire une draft : statistiques par champion, synergies, counters, records, suggestions de comp avec suivi du champ select du client LoL.

## Périmètre

- Dans le périmètre : import depuis Leaguepedia, import de parties classées solo queue (Émeraude et plus) via l'API Riot avec la clé de l'utilisateur, analyses statistiques, interface web locale, application de bureau Windows (exécutable unique), mise à jour automatique.
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

### Solo queue (SOLOQ)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-SOLOQ-1 | Les parties classées solo/duo (file 420) des rangs choisis (Émeraude à Challenger) et des régions choisies sont importées via l'API Riot dans une base séparée `soloq.db` ; `lck.db` n'est pas modifiée. | `soloq.py`, `test_soloq.py` |
| REQ-SOLOQ-2 | **Stockage compact** : aucun JSON brut ; par partie, les statistiques de fin de partie par joueur, les bans, les totaux par côté et des blobs compressés (frames par minute, kills, objectifs, événements d'objets) ; quelques Ko par partie (au plus ~40 Ko). | `soloq.extract_timeline`, `test_soloq.py::test_timeline_is_reduced_to_compact_frames_and_events` |
| REQ-SOLOQ-3 | **Limite de débit** : au plus 20 requêtes/s et 100 requêtes/2 min par hôte de routage (clé personnelle) ; un 429 attend la durée `Retry-After` puis réessaie, sans arrêter l'import. | `riot.RateLimiter`, `test_soloq.py` |
| REQ-SOLOQ-4 | **Clé API** : saisie dans la page Données, vérifiée par une requête avant d'être enregistrée dans `settings.json` du dossier de données local ; jamais commitée, journalisée ni affichée en clair. Une clé refusée (401/403, clé de développement expirée après 24 h) arrête l'import et est signalée « expirée ou invalide ». | `test_soloq.py::test_api_key_endpoints_never_expose_the_key` |
| REQ-SOLOQ-5 | **Échantillonnage et rang** : joueurs graines tirés du classement, rang le moins rempli en premier ; leurs 20 dernières parties classées sont dédoublonnées contre les parties stockées, ignorées ou en attente. Une partie porte le tier majoritaire des graines qui l'ont trouvée (le plus haut en cas d'égalité). | `soloq_ingest.Sampler`, `test_soloq.py` |
| REQ-SOLOQ-6 | **Reprise** : l'état de l'import (classement, graines, file, parties ignorées) est en base ; un import arrêté reprend sans retélécharger, et un import interrompu par la fermeture de l'application reprend au lancement suivant. L'import continue jusqu'à l'objectif de volume ; seuls les N derniers patchs sont gardés. | `test_soloq.py::test_interrupted_import_resumes_where_it_stopped` |
| REQ-SOLOQ-7 | **Périmètre « Solo queue »** : proposé à côté des ligues, jamais combiné avec elles ; filtre de rang et de région ; toutes les pages fonctionnent. Une partie solo queue est une série d'une game Blue / Red, jamais Fearless ; l'ordre de pick, inconnu, est fictif et les statistiques de draft sont masquées. | `test_soloq.py::test_api_soloq_scope_with_rank_filter`, `scope.test.ts` |

### Analyses (STAT)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-STAT-1 | Filtres communs : ligues, équipes, années, tournois, patchs, Fearless, numéro de game (en solo queue : rang et région). OU au sein d'un filtre, ET entre filtres. | `test_multi_filters.py` |
| REQ-STAT-2 | Tableau des champions : présence, pick/ban rate, winrate ; fiche détaillée (KDA, CS/min, dégâts, side, game de série, patch, joueurs, synergies, matchups, bans). | `stats.champion_table`, `stats.champion_detail` |
| REQ-STAT-3 | Indice de force sur 100 : 60 % présence, 40 % winrate ajusté (réduit vers 50 % selon l'échantillon), paliers S→D. | `strength.py`, `test_strength.py` |
| REQ-STAT-4 | Paires (synergies) et matchups (counters) avec lift = fréquence réelle / attendue ; présence d'un duo corrigée des bans conjoints. | `stats.pair_stats`, `stats.pair_presence` |
| REQ-STAT-5 | Contre de synergie : anticipation (partenaire déjà banni), réaction (deny pick ou ban après), ban de l'un des deux ; comparé à une référence au même patch, moment de draft et type de game ; trois niveaux de fiabilité. | `denial.py`, `test_denial.py` |
| REQ-STAT-6 | Records : WR extrêmes, séries invaincues, dépendance au side, flex, signatures, one-tricks, perfs individuelles. | `stats.records` |
| REQ-STAT-7 | **Paire jouée quand elle est ouverte** : pour une équipe qui prend un membre d'une paire (A au poste ra, B au poste rb), la paire est ouverte si le partenaire est encore disponible (ni banni, ni pris par l'adversaire, ni verrouillé Fearless) au moment où l'équipe remplit le poste du partenaire. Taux = parties où l'équipe a les deux / cas ouverts (jouée + disponible mais non prise). Si l'adversaire a pris ou banni le partenaire avant ce moment, la paire est contrée : elle est comptée à part (`taken`, `banned`) et sort du dénominateur. Un partenaire déjà indisponible au premier pick, joué à un autre poste ou dont le poste est déjà rempli est ignoré. Affiché dans le tableau des paires (colonne et tri), la page d'une paire et les suggestions. | `combos.py`, `test_combos.py` |
| REQ-STAT-8 | **Matchups sans contradiction** : un face-à-face en lane est classé favorable ou difficile seulement si l'écart à 50 % ne s'explique pas par le hasard (z binomial et nombre de parties minimum par niveau : très fiable, fiable, indice) ; un adversaire n'apparaît que dans une liste. Les « spots » comparent un contexte à la moyenne du champion lui-même (pas à 50 %), n'incluent ni adversaires ni partenaires, et Blue/Red ne comptent que pour une trouvaille. | `strength.matchup_verdict`, `test_strength.py` |

### Objets (ITEM)

Analyses des achats d'objets, tirées des événements d'objets des timelines solo queue (REQ-SOLOQ-2). Leaguepedia ne fournit pas les achats : ces analyses n'existent qu'en solo queue. Le détail (objectif, exemples chiffrés, cas limites, critères d'acceptation `CA-ITEM-n`, interface) suit le tableau ; les seuils sont justifiés dans [Architecture](architecture.md#seuils-et-alternatives-rejetees) et le contrat est dans [API](api.md#contrat-des-analyses-dobjets-apiitems).

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-ITEM-1 | **Périmètre solo queue seulement** : `/api/items` répond 422 hors du périmètre `SOLOQ` ; l'onglet « Objets » n'apparaît qu'en solo queue et la page, ouverte depuis un autre périmètre (adresse, dernière page visitée), explique que les données pro n'ont pas d'achats d'objets, sans envoyer de requête. Les parties sans timeline stockée sont ignorées et comptées à part (`missing_timeline`, en parties distinctes). | CA-ITEM-1 ; `test_items.py::test_api_items_only_in_soloq_scope` |
| REQ-ITEM-2 | **Reconstruction du build** d'un joueur, événement par événement dans l'ordre stocké (ordre de la timeline, conservé à seconde égale) : `P` (achat) ajoute l'objet à l'inventaire ; `S` (vente) et `D` (destruction : composant fusionné, consommable, transformation) retirent l'exemplaire le plus récent encore détenu, sans effacer l'achat de l'ordre d'achat ; `U` (`ITEM_UNDO`) annule l'action qu'il défait : avec un objet *avant*, le dernier achat encore valide de cet objet est retiré de l'ordre d'achat, ainsi que les destructions du même joueur à la même seconde juste avant cet achat (composants rendus) ; avec un objet *après*, la dernière vente encore valide de cet objet est annulée (l'objet revient dans l'inventaire et son achat n'est plus « revendu »). Une annulation sans action correspondante, une vente ou une destruction d'un objet non détenu sont ignorées. Un achat retiré ensuite par une vente est marqué « revendu ». | CA-ITEM-2 ; `test_items.py::test_reconstruct_follows_purchases_sales_destructions_and_undos` |
| REQ-ITEM-3 | **Objet complété** (règle tirée de l'`item.json` Data Dragon du patch de la partie) : achetable en boutique sur la Faille de l'invocateur (`maps["11"]`, `gold.purchasable`, `inStore` non faux), non consommable (`consumed` ou tag `Consumable`), réservé à aucun champion (`requiredChampion`, `requiredAlly`), coût total **≥ 1 500 po** et plus aucune amélioration achetable (`into` vide une fois retirés les objets non achetables : la Manamune, transformée en Muramana, est complétée). Les **bottes** (tag `Boots`, construites à partir d'un autre objet : les bottes de base n'en sont pas) sont à part, jamais comptées comme objet complété ; les améliorations de bottes de niveau 3 sans tag `Boots` ne sont ni l'un ni l'autre. Le build d'un joueur = ses objets complétés distincts dans l'ordre de leur premier achat valide (6 au plus ; un 7e objet distinct est ignoré) et ses premières bottes. Un objet est « revendu » dans le build si l'achat retenu (le premier valide) a été revendu. | CA-ITEM-3 ; `test_items.py::test_completed_item_rule_and_build_of` |
| REQ-ITEM-4 | **Ordre et timing d'achat** : pour chaque emplacement (1er objet complété, 2e… jusqu'au 6e) atteint par au moins un build, les objets achetés à cet emplacement avec leur part des builds ayant atteint l'emplacement ; pour chaque objet (tous emplacements confondus) et pour les bottes : part de tous les builds, emplacement moyen (objets), part revendue. Le timing (minute de l'achat, secondes / 60) est donné par quartiles (25 %, médiane, 75 %, interpolation linéaire, 1 décimale) et par histogramme de 21 barres de 2 minutes (dernière barre : 40 min et plus), pour chaque ligne et pour chaque emplacement. | CA-ITEM-4 ; `test_items.py::test_analyse_slots_timing_boots_and_paths` |
| REQ-ITEM-5 | **Chemins de build** : séquences ordonnées des 2 et des 3 premiers objets complétés, avec leur part parmi les builds qui en ont au moins autant, le timing du dernier objet de la séquence et leur winrate (REQ-ITEM-6) ; les 10 plus fréquentes par longueur (parmi celles d'au moins `min_games` builds), avec la part des builds couverte par ces 10 lignes (`covered`). | CA-ITEM-5 ; `test_items.py::test_analyse_slots_timing_boots_and_paths` |
| REQ-ITEM-6 | **Garde-fous statistiques** : un objet est acheté *à cause* de l'état de la partie (une équipe en avance achète plus tôt et plus), son winrate n'est donc pas l'effet de l'objet, ce que l'interface rappelle. Chaque winrate (ligne d'emplacement, objet, bottes, chemin) est comparé à un **winrate attendu** au même champion, même emplacement (ou longueur de chemin, ou bottes) et même tranche de 5 minutes d'achat (dernière tranche : 35 min et plus), calculé sur les autres achats de la cellule (la ligne testée est retirée de sa propre référence ; à défaut, sur les autres achats de toutes les cellules de la même liste, sinon 50 %) ; l'attendu d'une ligne est la moyenne des attendus de ses achats décidés. Sont donnés : winrate brut avec intervalle de Wilson à 95 %, winrate lissé vers l'attendu (20 parties virtuelles, comme `strength.PRIOR_GAMES`), écart à l'attendu, z binomial sous l'hypothèse nulle (attendu borné à [25 %, 75 %]) et un niveau (`strong` : abs(z) ≥ 2,33 et 30 parties décidées ; `likely` : 1,64 et 20 ; `hint` : 1,28 et 12) avec le sens (`better`, `worse`). La réponse indique le nombre de lignes testées et le nombre de trouvailles que le hasard seul produirait à chaque niveau (bilatéral : 2 %, 10 %, 20 %). Les parties sans vainqueur comptent pour les fréquences, pas pour les winrates. Les lignes de moins de `min_games` achats sont omises des listes (mais comptées dans les totaux). | CA-ITEM-6 ; `test_items.py::test_contrast_against_the_expected_winrate_of_the_cell` |
| REQ-ITEM-7 | **Filtres** : un ensemble de **1 à 10 champions** distincts (builds regroupés ; 422 sinon), un rôle facultatif, un **joueur** (Riot ID exact, sans tenir compte de la casse) et un **matchup** (champion adverse au même rôle), en plus du périmètre (rang, patch, région, année). Un champion inconnu ou absent du périmètre n'est pas une erreur : il n'apporte aucun build. La réponse propose les joueurs et les adversaires de lane présents pour ces champions et ce rôle (sans les filtres joueur et matchup). Dans la page, les champions se choisissent dans une liste à choix multiples avec recherche tolérante (REQ-UI-4), limitée à 10 ; les réglages de la page sont conservés (REQ-UI-6). | CA-ITEM-7 ; `test_items.py::test_api_items_filters`, `fuzzy.test.ts` |
| REQ-ITEM-8 | **Comparaison au build moyen** : avec un filtre joueur ou matchup, chaque ligne (emplacement, objet, bottes, chemin) porte sa part et son timing médian dans le **build moyen**, c'est-à-dire les mêmes champions, rôle et périmètre sans ces filtres ; la comparaison n'est que descriptive (pas de z) et signalée peu fiable sous 10 builds filtrés. | CA-ITEM-8 ; `test_items.py::test_api_items_filters` |
| REQ-ITEM-9 | **Données d'objets** : noms (français, `fr_FR`) et images via Data Dragon (même CDN que les champions) ; l'`item.json` de chaque patch est gardé en mémoire et sur disque (dossier de données, `ddragon/`) pour fonctionner hors ligne une fois chargé ; version du patch de la partie, sinon la plus récente (alors gardée en mémoire seulement, pour reprendre la bonne version quand elle sort). Si le catalogue d'un des patchs concernés manque (Data Dragon injoignable et pas de cache) : 503 pour toute la requête, jamais de réponse partielle. | CA-ITEM-9 ; `test_items.py::test_catalogue_is_kept_in_memory_and_on_disk_for_offline_use`, `test_api_items_guardrails` |
| REQ-ITEM-10 | **Performance** : sur une base de 100 000 parties, `/api/items` répond en moins de 2 s (cible) et 3 s (maximum) pour un champion populaire (≈ 10 000 builds) quand les catalogues sont en cache, et en moins de 10 s pour 10 champions (≈ 50 000 builds) ; la mémoire ajoutée par une requête reste sous 50 Mo pour 10 000 builds (proportionnelle au-delà) et rien n'en reste après la réponse. Voir [Performance et volumes](architecture.md#performance-et-volumes). | CA-ITEM-10 ; `test_items.py::test_items_scales_to_10000_builds` |
| REQ-ITEM-11 | **Page Objets** : sections, états vides, messages et interactions décrits dans [Interface de la page Objets](#interface-de-la-page-objets) ; l'avertissement statistique est toujours visible au-dessus des résultats et lisible par un lecteur d'écran. | CA-ITEM-11 ; revue manuelle, `ItemsPage.tsx` |

#### Problème et objectif

En solo queue, la question « que faut-il acheter, dans quel ordre, et quand ? » se pose à chaque partie. Les sites grand public répondent par un build « le plus joué » et un winrate brut, que l'état de la partie fausse : un joueur en avance achète plus tôt et plus cher, si bien que les objets de fin de partie ou achetés tôt semblent « gagner ». Cette page donne les achats réels des joueurs Émeraude+ importés par l'utilisateur, dans son périmètre (rang, patch, région), en séparant ce qui est fréquent de ce qui est seulement corrélé à la victoire.

| Persona | Usage | Questions auxquelles la page répond |
|---|---|---|
| Joueur qui prépare un champion | Choisit son champion (ou un groupe de champions proches, 10 au plus) et son rôle | Quel 1er objet complété, puis 2e, 3e ? À quelle minute en moyenne ? Quelles bottes, ou aucune ? Quels enchaînements de 2 ou 3 objets sont les plus suivis ? |
| Joueur face à un adversaire précis | Ajoute le filtre matchup | Le build change-t-il contre ce champion par rapport au build moyen ? |
| Analyste / coach | Filtre un joueur (Riot ID) | Ce joueur s'écarte-t-il du build moyen (ordre, timing, bottes) ? Un objet est-il associé à plus de victoires que ce que le moment de l'achat laisse attendre, et avec quelle fiabilité ? |

Critères de succès :

1. Depuis la page Objets, choisir un champion suffit (1 sélection, rôle facultatif) pour voir l'ordre d'achat, sans autre réglage.
2. Aucun composant, objet de départ, consommable, quête de support ou objet réservé n'apparaît dans les emplacements, les objets ou les chemins (vérifié sur le catalogue Data Dragon du patch courant, CA-ITEM-3).
3. Aucune ligne n'est présentée comme meilleure ou pire sans niveau de fiabilité, et chaque niveau affiché est accompagné du nombre de trouvailles attendues par hasard.
4. Les temps de réponse de REQ-ITEM-10 sont tenus ; la page fonctionne hors ligne pour les patchs déjà chargés.

#### Comportements et exemples chiffrés

**Exemple 1 : reconstruction d'un build (REQ-ITEM-2, REQ-ITEM-3).** Ahri, participant 3, événements stockés `[seconde, participant, action, objet(, après)]` (recettes simplifiées pour l'exemple) :

| # | Événement | Effet |
|---|---|---|
| 1 | `[15, 3, "P", 1056]` | Anneau de Doran acheté (achat a) |
| 2 | `[15, 3, "P", 2003]` | Potion de soin (achat b) |
| 3 | `[16, 3, "P", 2003]` | Potion de soin (achat c) |
| 4 | `[17, 3, "U", 2003, 0]` | Annule le dernier achat valide de 2003 : c disparaît de l'ordre d'achat |
| 5 | `[300, 3, "D", 2003]` | Potion bue : l'exemplaire de b quitte l'inventaire, b reste dans l'ordre d'achat |
| 6 | `[480, 3, "P", 1052]` | Tome d'amplification (achat d) |
| 7 | `[600, 3, "P", 1001]` | Bottes (achat e) |
| 8 | `[690, 3, "P", 1058]` | Baguette surdimensionnée (achat f) |
| 9-11 | `[700, 3, "D", 1052]`, `[700, 3, "D", 1058]`, `[700, 3, "P", 3089]` | Coiffe de Rabadon (achat g) ; tome et baguette fusionnés |
| 12 | `[702, 3, "U", 3089, 0]` | Annule g ; les destructions 9 et 10 (même seconde, juste avant g) sont annulées : tome et baguette reviennent |
| 13-14 | `[705, 3, "D", 1001]`, `[705, 3, "P", 3020]` | Chaussures du sorcier (achat h) |
| 15-16 | `[900, 3, "D", 1052]`, `[900, 3, "P", 6653]` | Tourment de Liandry (achat i) |
| 17 | `[1500, 3, "P", 3157]` | Sablier de Zhonya (achat j) |
| 18 | `[1980, 3, "S", 1056]` | Anneau vendu : a « revendu » |
| 19 | `[1985, 3, "U", 0, 1056]` | Vente annulée : l'anneau revient, a n'est plus revendu |
| 20 | `[1990, 3, "S", 1056]` | Anneau revendu : a « revendu » |
| 21-22 | `[2400, 3, "D", 1058]`, `[2400, 3, "P", 3089]` | Coiffe de Rabadon (achat k) |

Achats valides, dans l'ordre : a (revendu), b, d, e, f, h, i, j, k. Build : objets complétés **Liandry (15,0 min), Zhonya (25,0 min), Rabadon (40,0 min)** ; bottes **Chaussures du sorcier (11,75 min)**. La Rabadon annulée à 11:40 n'occupe pas l'emplacement 1.

**Exemple 2 : agrégation (REQ-ITEM-4, REQ-ITEM-5).** 51 builds d'Ahri mid analysés (52 parties, dont 1 sans timeline) :

| Groupe | Builds | Victoires / décidées | Objets complétés (minute) | Bottes |
|---|---|---|---|---|
| A | 21 | 15 / 20 (1 partie sans vainqueur) | Liandry (12), Zhonya (22), Rabadon (30) | Chaussures du sorcier (14) |
| B | 20 | 5 / 20 | Flamme-ombre (13, revendue à 33), Zhonya (25) | Jambières du berzerker (10) |
| C | 10 | 10 / 10 | Rabadon (26) | aucune |

Résultat : `builds` 51, `decided` 50, `missing_timeline` 1, winrate de base 60 % (30 / 50). Emplacement 1 : 51 builds, Liandry 21 (41,18 %), Flamme-ombre 20, Rabadon 10 ; timing de l'emplacement p25 12,0, médiane 13,0, p75 13,0, histogramme barre 6 (12-14 min) = 41, barre 13 (26-28 min) = 10. Emplacement 2 : 41 builds, Zhonya 100 %. Emplacement 3 : 21 builds, Rabadon. Objets : Rabadon 31 achats, emplacement moyen 2,35 ((21 × 3 + 10 × 1) / 31) ; Flamme-ombre revendue 100 %. Bottes : 51 builds, `none` 10, Chaussures du sorcier 21, Jambières 20. Chemins de 2 : 41 builds, (Liandry, Zhonya) 21, (Flamme-ombre, Zhonya) 20 (médiane 25,0 : celle de Zhonya) ; chemins de 3 : 21 builds, (Liandry, Zhonya, Rabadon) 100 %.

**Exemple 3 : winrate attendu, z et niveau (REQ-ITEM-6), sur l'exemple 2, emplacement 1.**

| Ligne | Cellule (champion, emplacement, tranche) | Autres achats de la cellule | Attendu | Brut (Wilson 95 %) | Lissé | Écart | z | Niveau |
|---|---|---|---|---|---|---|---|---|
| Liandry | Ahri, 1, 10-15 min | Flamme-ombre : 5 / 20 | 25 % | 75 % (53,1 – 88,8 %) | 50 % = (15 + 20 × 0,25) / (20 + 20) | +50 pts | 5,16 | `likely`, `better` (20 décidées < 30 pour `strong`) |
| Flamme-ombre | Ahri, 1, 10-15 min | Liandry : 15 / 20 | 75 % | 25 % | 50 % | −50 pts | −5,16 | `likely`, `worse` |
| Rabadon | Ahri, 1, 25-30 min | aucun → autres achats de la liste : 20 / 40 | 50 % | 100 % | 66,7 % | +50 pts | 3,16 | aucun (10 décidées < 12) |
| Zhonya (emplacement 2) | Ahri, 2, 20-25 et 25-30 | aucun, et aucun autre objet dans la liste | 50 % | 50 % | 50 % | 0 | 0 | aucun |

z = (brut − attendu borné) / √(attendu borné × (1 − attendu borné) / décidées). Bornage : une ligne à 8 / 20 dans une cellule où les autres achats gagnent 10 % a z = 1,55 avec l'attendu borné à 25 % (`hint`), contre 4,47 sans bornage.

**Exemple 4 : trouvailles dues au hasard.** Dans l'exemple 2 (`min_games` 5), 13 lignes listées ont au moins 12 parties décidées (emplacements 2 + 1 + 1, objets 4, bottes 2, chemins 2 + 1) : `tested` 13, `by_chance` 0,3 (`strong`, 13 × 2 %), 1,3 (`likely`), 2,6 (`hint`). Règle d'alerte de l'interface : pour un niveau, si le nombre de lignes listées à ce niveau est au plus le double de `by_chance`, ces trouvailles ne prouvent rien et la section « Fiabilité » l'écrit (par exemple 3 lignes `hint` pour 2,6 attendues).

**Exemple 5 : filtres et build moyen (REQ-ITEM-7, REQ-ITEM-8).** Dans l'exemple 2, les parties 1 à 10 du groupe A sont jouées par `Alice#EUW`, les autres par `Bob#EUW` ; l'adversaire de lane est Syndra dans 5 parties du groupe C, Zed ailleurs. `player=alice#euw` : 10 builds, Liandry 100 % en emplacement 1 avec `ref` = {part 41,18 %, médiane 12,0} ; `reference` = {builds 51, decided 50, winrate 60 %, comparable vrai}. `vs=Syndra&min_games=1` : 5 builds, `comparable` faux. `options.players` = [Bob#EUW 42, Alice#EUW 10] (parties du périmètre, timeline ou non), identiques avec ou sans filtres.

#### Cas limites

| Cas | Comportement attendu |
|---|---|
| Partie sans timeline stockée | Ni build ni winrate ; comptée une fois dans `missing_timeline` même si deux champions choisis y jouent ; comptée dans `options` |
| Remake / partie très courte | Les remakes (< 5 min ou reddition anticipée) ne sont jamais stockés (REQ-SOLOQ-1). Une partie courte (reddition à 15 min) donne un build, souvent sans objet complété : il compte dans `builds`, dans les parts « tous objets » et « bottes » (`none` s'il n'a pas de bottes), pas dans les emplacements qu'il n'atteint pas |
| Undo en cascade | `P A, P B, U A→0, U B→0` : les deux achats sont annulés (chaque `U` vise le dernier achat valide de son objet). `P A, U A, U A` : le second `U` est ignoré. Une fusion annulée puis un `U` sur un composant rendu annule l'achat du composant |
| Rachat du même objet | `P X` à 10 min, `S X` à 15, `P X` à 20 : X une seule fois dans le build, à l'emplacement et au timing de 10 min, marqué revendu |
| 6 objets atteints puis vente | Le build garde les 6 premiers objets distincts ; l'objet vendu est marqué revendu ; un 7e objet distinct acheté ensuite est ignoré |
| Bottes jamais achetées | Le build compte dans `boots.none` ; aucune ligne de bottes (Cassiopée : `none` = `builds`) |
| Patch sans `item.json` | Patch plus récent que Data Dragon : version la plus récente, en mémoire seulement. Data Dragon injoignable et aucun cache pour un des patchs : 503 |
| Champion absent du périmètre | 200 avec 0 build ; pas de 404 (même logique que les autres filtres) |
| 0 build après filtres | 200, forme complète : `builds` 0, `decided` 0, `baseline.winrate` null, `slots` [], `by_item` [], `boots` {0, 0, []}, chemins `builds` 0 et `rows` [], `tested` 0, `by_chance` 0, `version` null, `names` {} ; `reference` présent si un filtre joueur ou matchup est posé (`comparable` faux) ; `options` rempli |
| Événements d'un autre participant | Jamais mêlés : seuls les événements du `participant` du pick (`soloq_players`) sont lus |
| Objet absent du catalogue du patch | Ni complété ni bottes (ignoré) ; un nom manquant n'empêche pas l'affichage (id à la place) |

#### Critères d'acceptation

| ID | Exigence | Données d'entrée minimales | Sortie attendue exacte |
|---|---|---|---|
| CA-ITEM-1 | REQ-ITEM-1 | `league=LCK&champion=Ahri` ; puis `champion=Ahri` sans ligue (LCK par défaut) | 422 dont le `detail` contient « solo queue », dans les deux cas ; la base solo queue n'est pas lue |
| CA-ITEM-2 | REQ-ITEM-2 | Exemple 1, et chacun de : `P 1055, U 1055` → aucun achat ; `P 2003 ×2, S 2003` → [non revendu, revendu] ; `D 1052` à 699 puis `P 3089` à 700 et `U` → le tome n'est pas rendu | Achats valides et drapeaux « revendu » exactement comme indiqué |
| CA-ITEM-3 | REQ-ITEM-3 | Catalogue de test : Rabadon 3 600, Manamune → Muramana non achetable, Bâton de l'archange → Étreinte achetable, potion, Élixir de fer 2 500 consommable, objets `requiredChampion`, `requiredAlly`, `maps["11"]` faux, `inStore` faux, Mejai 1 500 sans amélioration ; et le catalogue réel du patch courant | Complétés : exactement Rabadon, Zhonya, Flamme-ombre, Liandry, Manamune, Étreinte, Mejai ; bottes : celles qui ont `from` ; bottes de base, composants et le reste : aucun. Contrôle manuel sur le catalogue réel à chaque nouvelle saison (16.23.1 : 107 objets complétés à id de 4 chiffres, dont Mejai, le moins cher, puis 2 200 po ; aucune botte ni aucun objet hors bottes sous 1 500 po parmi eux) |
| CA-ITEM-4 | REQ-ITEM-4 | Exemple 2 | Exactement les valeurs de l'exemple 2 (parts arrondies à 4 décimales, minutes à 1) ; histogramme de 21 barres |
| CA-ITEM-5 | REQ-ITEM-5 | Exemple 2, puis 12 séquences distinctes de 2 objets d'au moins `min_games` builds | Chemins de l'exemple 2 avec `covered` 1,0 ; dans le second cas, 10 lignes et `covered` < 1 |
| CA-ITEM-6 | REQ-ITEM-6 | Exemple 2 | Exactement le tableau de l'exemple 3 et `tested` / `by_chance` de l'exemple 4 |
| CA-ITEM-7 | REQ-ITEM-7 | Exemple 5 ; `champion` répété 11 fois avec 11 noms ; 11 fois avec 10 noms distincts ; `role=jungler` ; `min_games=0` | Valeurs de l'exemple 5 ; 422 ; 200 ; 422 ; 422. Joueur en minuscules = même résultat qu'en casse exacte |
| CA-ITEM-8 | REQ-ITEM-8 | Exemple 5 | `ref` et `reference` de l'exemple 5 ; sans filtre joueur ni matchup : `reference` null et `ref` null partout |
| CA-ITEM-9 | REQ-ITEM-9 | Data Dragon simulé (versions 15.23.1, 15.22.1, 15.21.1) | `15.22` → 15.22.1, requêtes `versions.json` puis `/cdn/15.22.1/data/fr_FR/item.json`, fichier `ddragon/items-15.22.json` écrit ; 2e appel : aucune requête ; mémoire vidée et réseau coupé : lu sur disque ; `15.30` hors ligne : 503 ; `15.30` en ligne : 15.23.1 sans fichier disque |
| CA-ITEM-10 | REQ-ITEM-10 | 10 000 builds synthétiques (blob de ≈ 300 événements, ≈ 2 Ko compressés), catalogue en mémoire | Analyse complète en moins de 6 s sur la CI (2 × la cible de 3 s, machines plus lentes) ; pic `tracemalloc` < 50 Mo |
| CA-ITEM-11 | REQ-ITEM-11 | Page ouverte en LCK ; en solo queue sans champion ; avec un champion sans partie ; avec 10 champions choisis ; Data Dragon coupé sans cache | Messages exacts de l'interface ci-dessous ; 11e champion non cochable ; avertissement présent avec `role="note"` |

#### Interface de la page Objets

La page `#/items` (onglet « Objets », entre « Synergies contrées » et « Records ») n'est listée qu'en solo queue. Ordre des sections :

1. **Réglages** (barre `filters`) : « Champions » (liste à choix multiples, recherche tolérante, 10 au plus), « Rôle » (`Tous les rôles` + les 5 rôles), « Joueur » (`Tous les joueurs` + `options.players`, affichés « Riot ID (parties) »), « Adversaire » (`Tous les adversaires` + `options.opponents`), « Min achats » (nombre ≥ 1, 5 par défaut). Réglages conservés : `items.champions`, `items.role`, `items.player`, `items.vs`, `items.minGames` (`usePersistentState`).
2. **Avertissement statistique**, toujours visible au-dessus des résultats, non repliable : « Un objet est acheté à cause de l'état de la partie : une équipe en avance achète plus tôt et plus cher. Un winrate élevé ne veut donc pas dire que l'objet fait gagner. Chaque winrate est comparé à celui attendu pour le même champion, le même emplacement et le même moment d'achat ; seuls les écarts marqués d'un niveau de fiabilité dépassent le hasard. »
3. **Résumé** : builds analysés, parties décidées, parties sans timeline (si > 0), winrate de base avec son intervalle ; avec un filtre joueur ou matchup, « Build moyen : N builds, winrate X % » et, sous 10 builds filtrés, « Moins de 10 builds filtrés : la comparaison au build moyen est peu fiable. »
4. **Ordre d'achat** : un tableau par emplacement atteint (« 1er objet », « 2e objet »…), titre avec le nombre de builds et la médiane, histogramme de l'emplacement. Colonnes décrites par `ITEM_COLUMNS` : Objet (image 32 px + nom), Part, Achats, Timing (p25 – médiane – p75 et mini-histogramme), Winrate (barre, intervalle au survol), Attendu, Écart (pts) avec niveau et sens écrits en toutes lettres (« fiable · mieux que l'attendu »), Revendu, Build moyen (part et médiane, seulement avec un filtre joueur ou matchup).
5. **Bottes** : même tableau, plus « Sans bottes : N builds (X %) ».
6. **Chemins de build** : deux tableaux (2 et 3 objets), séquence d'images séparées par « → », et « Ces chemins couvrent X % des builds ».
7. **Tous les objets** : tableau par objet avec la colonne « Emplacement moyen ».
8. **Fiabilité** : lignes testées et, par niveau, trouvailles affichées contre trouvailles attendues par hasard (comme la page Synergies contrées), avec l'alerte de l'exemple 4 : « À ce niveau, le hasard seul produirait à peu près autant de trouvailles : elles ne prouvent rien. »

Comportement :

- Au chargement, les réglages conservés sont rétablis ; la requête part 300 ms après le dernier changement de réglage ; pendant le calcul, « Chargement… » et les résultats précédents restent affichés en grisé.
- Changer l'ensemble de champions ou le rôle efface les filtres joueur et adversaire (ils dépendent de ces choix) ; changer le périmètre global (rang, patch, région) les garde.
- Une fois 10 champions choisis, les autres cases sont désactivées avec l'info-bulle « 10 champions au plus » ; décocher en libère une. « Tout sélectionner » n'est pas proposé.
- Les images d'objets viennent de `https://ddragon.leagueoflegends.com/cdn/<version>/img/item/<id>.png` ; une image manquante est remplacée par les deux premières lettres du nom.

Messages exacts des états vides et d'erreur :

| Situation | Message |
|---|---|
| Hors solo queue | « Les analyses d'objets n'existent qu'en solo queue : Leaguepedia ne donne pas les achats d'objets des parties pro. Choisissez « Solo queue » dans le sélecteur de ligues. » |
| Aucun champion choisi | « Choisissez de 1 à 10 champions pour voir leurs achats d'objets. » |
| 0 build, aucune partie sans timeline | « Aucune partie avec ces champions dans ce périmètre (rôle, joueur, adversaire, rang, patch). » |
| 0 build, parties sans timeline | « Aucune partie analysable : les N parties trouvées n'ont pas de timeline stockée. » |
| Emplacement, bottes ou chemins sans ligne listée | « Aucun objet acheté au moins N fois. » / « Aucune botte achetée. » (si `none` = `builds`) / « Aucun chemin suivi au moins N fois. » |
| 503 | « Données d'objets indisponibles : Data Dragon est injoignable et ces patchs n'ont pas encore été chargés. Réessayez une fois connecté à Internet. » avec un bouton « Réessayer » |
| Autre erreur | « Erreur : » suivi du `detail` du serveur |

Accessibilité : l'avertissement porte `role="note"` et un titre de niveau 3 « Lire ces chiffres avec prudence » ; niveaux et sens sont écrits en texte, jamais signalés par la seule couleur ; les images ont l'attribut `alt` du nom de l'objet ; l'information des info-bulles (intervalle, cellule de l'attendu) est aussi décrite dans la section « Fiabilité ».

### Interface (UI)

| ID | Exigence | Vérifié par |
|---|---|---|
| REQ-UI-1 | Page Drafts : une carte par série (score, Bo, Fearless, stage), une ligne par game, Blue à gauche avec la première sélection ; l'équipe, les joueurs et les picks d'un bloc de côté sont cohérents (REQ-DATA-4). | `SeriesView.tsx` |
| REQ-UI-2 | Numéros d'ordre de draft sur les icônes (picks 1-10, bans 1-10) et highlights (synergie, counter de lane, signature, pick rare). | `stats.game_highlights` |
| REQ-UI-3 | Le périmètre (ligues, filtres) est global et persisté localement. | `scope.ts`, `App.tsx` |
| REQ-UI-6 | **Suivi de navigation** : chaque page garde ses réglages (tri, rôles, minimums, champion filtré) quand on la quitte et au redémarrage ; la draft en cours du tracker (picks, picks adverses, bans, champions Fearless, mode élargi) est conservée, avec un bouton « Nouvelle draft » pour la vider ; un lancement sans page dans l'adresse rouvre la dernière page visitée. Le tracker et l'envoi dans le chat restent désactivés au démarrage. | `usePersistentState` (`utils.ts`), `router.ts` |
| REQ-UI-4 | Recherche de champion tolérante (accents, apostrophes, surnoms, fautes). | `fuzzy.test.ts` |
| REQ-UI-5 | Pages : Drafts, Champions, Paires, Synergies contrées (pro), Objets (solo queue, REQ-ITEM-1), Records, Suggestions / Tracker, Données. | `App.tsx` |

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
| REQ-APP-6 | Un installateur `LoLProComp-Setup.exe` installe le même exécutable pour l'utilisateur courant, sans droits administrateur, dans `%LOCALAPPDATA%\Programs\LoLProComp` (raccourci menu Démarrer, raccourci bureau optionnel, désinstallateur). Il ferme l'application en cours avant de remplacer l'exécutable ; la désinstallation demande s'il faut supprimer les données (gardées par défaut). La mise à jour automatique reste celle de REQ-APP-2. | `installer/LoLProComp.iss`, tests manuels du README |

## Exigences non fonctionnelles

| ID | Exigence |
|---|---|
| REQ-NFR-1 | Au plus 3 combinaisons de ligues en mémoire ; rechargement automatique quand le fichier de base change (au plus une fois par minute pour `soloq.db` pendant un import). |
| REQ-NFR-2 | Les analyses lourdes du périmètre par défaut sont préchauffées au démarrage. |
| REQ-NFR-3 | Le serveur n'écoute qu'en local ; `/api/quit` exige un en-tête personnalisé (protection CORS). |
| REQ-NFR-4 | Les données importées conservent la licence CC BY-SA 3.0 de Leaguepedia ; images (champions et objets) via Data Dragon. |
| REQ-NFR-5 | Tests backend (pytest) et frontend (vitest) verts avant toute release ; la CI publie l'exécutable et l'installateur avec leur SHA-256. |

## Critères d'acceptation transverses

1. Après import, pour toute partie : `games.team1` est l'équipe du côté 1, et les joueurs de `picks` du côté 1 appartiennent à cette équipe.
2. Le score d'une série affiché en en-tête est égal au nombre de games gagnées par chaque équipe, quel que soit le côté joué.
3. Une analyse filtrée sur une équipe ne compte que les côtés joués par cette équipe.
