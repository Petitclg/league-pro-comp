# LoL Pro Comp

Analyse des drafts du LoL pro (LCK, LPL, LEC, LCS, LTA, LCP, PCS, VCS, CBLOL, LLA, LJL, TCL, LCO, LMS, LST, LCL, Worlds, MSI, First Stand ; 2017 → aujourd'hui) : drafts par série (Bo1/3/5, **Fearless**), stats par champion, synergies, counters, records, et suggestions de comp avec tracker du client LoL. Un sélecteur multiple de ligues en haut de page change tout le périmètre : cocher plusieurs ligues (LCK + LPL + LEC…) combine leurs parties dans toutes les statistiques ; les autres filtres (équipes, années, tournois, patchs, games) sont aussi multiples (OU au sein d'un filtre, ET entre filtres).

## Installer l'application (Windows)

Deux distributions, même application, publiées dans chaque release (page **Releases** du dépôt) avec leur empreinte SHA-256 :

- **Installateur** `LoLProComp-Setup.exe` (recommandé) : installation classique pour l'utilisateur courant, **sans droits administrateur**, dans `%LOCALAPPDATA%\Programs\LoLProComp`. Il crée un raccourci dans le menu Démarrer (et sur le bureau si on coche la case), et une entrée dans « Applications installées » pour la désinstaller. Si l'application tourne, l'installateur la ferme avant de remplacer l'exécutable. À la désinstallation, il demande s'il faut aussi supprimer les données (`%LOCALAPPDATA%\LoLProComp`) ; par défaut elles sont gardées (et toujours gardées en désinstallation silencieuse).
- **Portable** `LoLProComp.exe` : un seul fichier à placer où l'on veut (dans un dossier où l'on peut écrire, pour la mise à jour automatique), rien à installer.

Ni Python ni Node ne sont nécessaires.

1. Lancer l'application (menu Démarrer, ou `LoLProComp.exe`) : une fenêtre s'ouvre (Microsoft Edge en mode application, présent sur Windows 10/11 ; Chrome sinon). Au premier lancement, la page **Données** propose d'importer la LCK (≈ 1 min) ou toutes les ligues (≈ 10 à 20 min) depuis Leaguepedia. Le bouton « Mettre à jour (année en cours) » rafraîchit ensuite les données quand on veut.
2. **Mise à jour automatique, comme Discord** : à chaque démarrage, l'application cherche une release plus récente sur GitHub ; si elle existe, une petite fenêtre télécharge la nouvelle version de `LoLProComp.exe` (taille et SHA-256 vérifiés), remplace l'exécutable et relance l'application. C'est identique en version installée ou portable : l'installateur n'est utilisé que pour la première installation (le dossier d'installation appartient à l'utilisateur, l'application peut donc s'y remplacer elle-même). Hors ligne ou en cas de problème, la version actuelle démarre normalement. `LOLPRO_NO_UPDATE=1` désactive la recherche.
3. Fermer la fenêtre arrête l'application. Les données sont dans `%LOCALAPPDATA%\LoLProComp` (bases `lck.db` et `soloq.db`, réglages `settings.json`, journal `app.log`) : elles survivent aux mises à jour, et sont partagées entre version installée et portable. Un second lancement ouvre simplement une fenêtre sur l'instance déjà démarrée.

Le tracker fonctionne tel quel : l'application tourne sur la même machine que le client LoL.

### Publier une version

```powershell
git tag v0.2.0
git push origin v0.2.0
```

Le workflow [release.yml](.github/workflows/release.yml) lance les tests, compile `LoLProComp.exe` (`build.ps1`, PyInstaller) puis l'installateur `LoLProComp-Setup.exe` (Inno Setup 6, script [installer/LoLProComp.iss](installer/LoLProComp.iss)) et les publie dans la release, chacun avec son empreinte SHA-256. C'est `LoLProComp.exe` que les applications installées téléchargent pour se mettre à jour. Pour compiler en local : `.\build.ps1` (résultat dans `backend\dist`) ; `-Version 1.2.0` fixe le numéro de version sans modifier le dépôt ; `-Installer` produit aussi `LoLProComp-Setup.exe` (nécessite [Inno Setup 6](https://jrsoftware.org/isdl.php) : `ISCC.exe` est cherché dans le `PATH` et les dossiers d'installation habituels).

### Tester l'installateur (manuel, avant une release)

L'installateur est compilé par la CI ; son comportement se vérifie à la main sur un Windows sans droits administrateur :

1. **Installation** : lancer `LoLProComp-Setup.exe` → aucune demande d'élévation (UAC) ; dossier proposé `%LOCALAPPDATA%\Programs\LoLProComp` ; case « raccourci sur le bureau » décochée par défaut. Vérifier le raccourci du menu Démarrer (avec l'icône), le raccourci bureau s'il a été coché et l'entrée « LoL Pro Comp » avec sa version dans Paramètres → Applications installées.
2. **Lancement** : la case « Exécuter LoL Pro Comp » de la dernière page ouvre la fenêtre ; relancer depuis le menu Démarrer ouvre une fenêtre sur la même instance.
3. **Réinstallation, application ouverte** : relancer l'installateur pendant que l'application tourne → elle se ferme d'elle-même (fenêtre comprise) avant la copie, sans message « fichier utilisé » ; les données (`%LOCALAPPDATA%\LoLProComp`) sont intactes. Pendant un import, l'application refuse de se fermer : la page « Préparation de l'installation » propose alors de la fermer de force.
4. **Mise à jour automatique** : installer une version N-1 (ou un build local `-Version 0.0.1`), la lancer alors qu'une release N existe → fenêtre de téléchargement, redémarrage en version N, `LoLProComp.exe` remplacé dans `%LOCALAPPDATA%\Programs\LoLProComp`, pas de fichier `.old` restant au lancement suivant. (L'entrée « Applications installées » garde le numéro de la version installée par le setup : c'est attendu.)
5. **Désinstallation en gardant les données** : depuis Applications installées, répondre **Non** à la question des données → dossier d'installation, raccourcis et entrée supprimés ; `%LOCALAPPDATA%\LoLProComp` conservé ; une réinstallation retrouve les parties importées.
6. **Désinstallation en supprimant les données** : répondre **Oui** → `%LOCALAPPDATA%\LoLProComp` supprimé aussi. Faire le test application ouverte : elle est fermée avant la suppression (confirmation demandée si elle refuse, par exemple pendant un import).
7. **Silencieux** : `LoLProComp-Setup.exe /VERYSILENT` installe sans fenêtre ; `unins000.exe /VERYSILENT` (dans le dossier d'installation) désinstalle en gardant les données.

## Développement

```powershell
# Backend (depuis backend/)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m app.ingest                           # toutes les ligues, toutes les années
.\.venv\Scripts\python -m app.ingest --leagues LPL LEC --years 2025 2026   # ou une partie seulement
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# Frontend (depuis frontend/)
npm install
npm run dev     # http://localhost:5173
```

Sans serveur Vite, l'API sert directement le front compilé (`npm run build`) sur son propre port ; `python -m app.desktop` lance l'application de bureau depuis les sources.

L'import peut aussi se faire depuis l'application (page **Données**) : le serveur reste utilisable pendant l'import. En ligne de commande, relancer `app.ingest` met la base à jour (les parties sont remplacées, pas dupliquées) ; **arrêtez le serveur pendant un import en ligne de commande**. Le serveur détecte le changement de base et se recharge tout seul. Les jeux de ligues sont chargés à la demande (3 combinaisons en mémoire au maximum, ~1 Go pour les plus grosses) : la première visite d'une combinaison large prend quelques secondes, et « Synergies contrées » plus encore (≈ 1 min pour LCK + LPL + LEC, ensuite en cache). Les moyennes de référence mélangent alors les régions, ce qui peut biaiser les comparaisons.

## Données

Source : Leaguepedia (tables Cargo `PicksAndBansS7`, `ScoreboardGames`, `ScoreboardPlayers`, `MatchSchedule`, `Tournaments`) via `Special:CargoExport`, qui n'est pas limité comme `api.php` (l'API classique renvoie « ratelimited » en permanence). Les CSV d'Oracle's Elixir sont sur Google Drive avec un quota dépassé, d'où ce choix. La table de drafts commence en 2017 (saison 7). Les ligues et leurs noms Leaguepedia (EU LCS → LEC, NA LCS → LCS...) sont déclarés dans [leagues.py](backend/app/leagues.py) ; ajouter une ligue = ajouter une entrée. Le contenu de Leaguepedia est publié sous licence CC BY-SA 3.0 : les données importées en gardent les conditions. Les images de champions viennent du CDN public Data Dragon de Riot ; ce projet n'est pas affilié à Riot Games.

Les équipes coréennes sont regroupées par franchise ([teams.py](backend/app/teams.py)) : DWG KIA / DAMWON → Dplus KIA, etc. Les autres ligues gardent les noms d'équipe de Leaguepedia, donc un changement de nom compte comme une autre équipe.

## Solo queue (API Riot)

En plus du pro, l'application peut importer des parties **classées solo/duo de rang Émeraude et plus** via l'API Riot, dans une base séparée `soloq.db` (la base pro `lck.db` n'est pas touchée). Le périmètre « Solo queue » apparaît alors dans le sélecteur de ligues (jamais combiné aux ligues pro), avec un filtre de **rang** et un filtre de **région** ; toutes les pages fonctionnent dessus.

- **Clé API** : à créer sur [developer.riotgames.com](https://developer.riotgames.com) puis à coller dans la page **Données** (une clé de développement expire au bout de 24 h : l'application signale une clé expirée ou invalide). Elle est vérifiée par une requête, puis stockée **en clair** dans `settings.json` du dossier de données local ; elle n'est jamais commitée, journalisée ni affichée (seuls ses 4 derniers caractères le sont).
- **Échantillonnage** : des joueurs « graines » sont tirés du classement des rangs choisis ; leurs 20 dernières parties classées sont dédoublonnées puis téléchargées (match + timeline, 2 requêtes par partie). Le rang d'une partie est celui des graines qui l'ont trouvée (tier majoritaire, le plus haut en cas d'égalité) : l'API ne donne pas le rang d'une partie.
- **Débit** : la limite des clés personnelles (20 requêtes/s, 100 requêtes/2 min, par hôte) est respectée côté client ; un 429 attend la durée `Retry-After` puis reprend. Compter ≈ 25 parties par minute et par région.
- **Volume** : l'import tourne jusqu'à l'objectif de parties, ne garde que les N derniers patchs et peut ignorer les patchs anciens. Arrêté ou interrompu par la fermeture de l'application, il reprend là où il en était (état en base).
- **Stockage compact** : pas de JSON brut ; statistiques de fin de partie, bans, frames par minute (or, XP, niveau, CS, K/D/A, dégâts), objectifs et achats d'objets compressés, soit quelques Ko par partie. Les noms de champions viennent de Data Dragon.
- **Limites** : une partie solo queue est une « série » d'une game entre Blue et Red, jamais Fearless ; l'ordre de pick n'est pas fourni par l'API, d'où un ordre fictif et des statistiques de draft (position de pick, synergies contrées) masquées dans ce périmètre.
- **Objets** (page propre à la solo queue) : pour 1 à 10 champions, ordre et minute d'achat des objets complétés, bottes et chemins de build, avec filtres rôle, joueur et adversaire de lane. Chaque winrate est comparé à celui attendu au même moment d'achat, car un objet acheté tôt l'est surtout par une équipe en avance. Noms et images des objets via Data Dragon, gardés dans `ddragon/` du dossier de données pour l'usage hors ligne. Détails : [exigences](docs/requirements.md#objets-item).

Détails : [architecture](docs/architecture.md#import-solo-queue-api-riot) et [modèle de données](docs/data-model.md#solo-queue-soloqdb).

## Fearless

Constaté dans les données : depuis la LCK Cup 2025 (15/01/2025), **aucun champion n'est jamais repris dans une série, par aucune des deux équipes** (hard Fearless, 0 répétition sur plus de 400 séries ; avant, des champions se répètent dans > 95 % des séries). Un tournoi est marqué Fearless quand ses séries multi-games ne répètent (presque) jamais de champion : c'est déduit des données, pas codé en dur. Chaque game affiche les champions devenus indisponibles et les filtres permettent d'isoler Fearless / draft classique / numéro de game.

## Fonctionnalités

- **Drafts** : une carte par série (score, Bo, Fearless, stage), une ligne par game. Le côté Blue a la 1ʳᵉ sélection et le 1er ban ; les numéros sur les icônes donnent l'ordre de la draft (picks 1-10, bans 1-10). Highlights : 🔗 synergie, ⚔ counter de lane, ★ signature du joueur, ◆ pick rare.
- **Champions** : tableau (présence, pick/ban rate, WR) puis page détaillée au clic : KDA, CS/min, dégâts, participation, WR par side / par game de série / par patch, position dans la draft, joueurs, synergies, matchups, bans, et toutes les parties.
- **Force et spots** : l'indice de force (S→D, sur 100) combine 60 % de présence (picks + bans, 50 % = max) et 40 % de winrate ajusté (ramené vers 50 % selon la taille de l'échantillon, avec intervalle de confiance à 95 %). Les « spots » sont les contextes (side, game de la série, moment de la draft, rôle, patch, équipe, joueur, adversaire, partenaire) où le winrate d'un champion s'écarte de sa propre moyenne, classés par z-score.
- **Paires / duos** : lift = fréquence réelle / fréquence attendue. Clic sur une paire ou un matchup → stats + parties concernées, plus la mesure de **contre de synergie** : quand A est pick, que devient B (banni avant = anticipation, pické ou banni après = réaction) comparé à ce qui arrive à B avec un autre trigger **au même patch**, même moment de la draft et même type de game ([denial.py](backend/app/denial.py)). L'onglet **Synergies contrées** classe les synergies les plus cassées, en phrases, avec trois niveaux de fiabilité (très fiable / probable / simple indice) et le nombre de cas que le hasard seul produirait. Trois façons de casser un duo : **bannir l'un ou l'autre** avant les picks (comparé à des bans indépendants, patch par patch), l'**anticipation** (le partenaire est déjà banni quand le premier est pick) et la **réaction** (deny pick ou ban après). Le calcul est préchauffé au démarrage du serveur. Dans le tableau des paires, la colonne **Présence** est l'équivalent pour un duo de la présence d'un champion : (parties où la paire est jouée + parties où un des deux est banni **au-delà de ce que donneraient des bans indépendants**, patch par patch et Game 1 / suivantes) / parties du périmètre. Deux champions forts simplement bannis ensemble ne comptent donc pas ; un duo qu'on neutralise systématiquement par un ban gagne en présence (barre rouge). Le tri « présence » porte sur les 500 paires les plus jouées. Détail dans [stats.py](backend/app/stats.py) (`pair_presence`).
- **Paire jouée quand elle est ouverte** : une paire est *ouverte* quand une équipe prend un des deux champions alors que l'autre est encore disponible (ni banni, ni pris par l'adversaire, ni verrouillé Fearless, poste libre). La colonne « Jouée si ouverte » donne la part des cas où l'équipe prend aussi le second, avec le détail (prise par l'adversaire ensuite, bannie ensuite, laissée de côté), dans le tableau des paires (tri possible), sur la page d'une paire et dans les suggestions ([combos.py](backend/app/combos.py)). Les paires systématiquement jouées dès qu'elles sont ouvertes, et celles qui ont un winrate élevé, sont favorisées dans les suggestions.
- **Records** : meilleurs/pires WR, séries invaincues, dépendance au side, flex, signatures, one-tricks, perfs individuelles, parties extrêmes, meilleures synergies/counters.
- **Suggestions / Tracker** : chaque comp pro partageant *m* picks avec votre draft vote pour ses champions aux rôles restants (poids *m²*, victoires ×1.25, défaites ×0.75). Si des picks adverses sont connus, chaque candidat est ajusté par son winrate lissé contre eux (×0.5 à ×1.5). Les bans et champions Fearless déjà joués sont exclus. Le **tracker** lit le champ select du client LoL ouvert (API locale LCU, comme Porofessor) et remplit picks, picks adverses et bans toutes les 2,5 s. Variable `LOL_LOCKFILE` si le jeu n'est pas dans `C:\Riot Games`.
- Recherche de champion tolérante (accents, apostrophes, surnoms `tf`/`mf`/`j4`, fautes de frappe).

## Tests

```powershell
cd backend;  .\.venv\Scripts\python -m pytest
cd frontend; npm test
```
