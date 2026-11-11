# Nouveautés de Giration

## 1.3.0 — 11 novembre 2026

Lot 2 des principes de fonctionnement : les limites relevées sur la photo font partie du contrôle.

- **Outil Bordure (B)** : on relève sur la photo, clic par clic, une **bordure** de trottoir ou d'îlot (les roues
  ne la franchissent pas : ne passe pas), une **bordure franchissable** ou un bateau (réserve) ou un **obstacle**
  — mur, façade, clôture, poteau (la caisse ne doit pas le toucher : ne passe pas). Entrée, double-clic ou clic
  droit pour finir ; clic sur le premier point pour fermer un contour ; un seul point puis Entrée : un poteau. La
  flèche du bouton choisit le type ; clic droit sur une bordure pour en changer le type ou la supprimer. Le côté
  interdit d'une ligne est déduit du trajet ; si le trajet la coupe, le véhicule devrait la franchir.
- **Bâtiments de la BD TOPO** chargés avec les routes et contrôlés comme obstacles pour la caisse : réserve
  seulement (précision de quelques mètres) ; menu Couches pour les masquer.
- Les **conflits** apparaissent sur la carte (zones rouges ou orange), dans le verdict et dans les remarques.
- **Comparer** : les variantes côte à côte (emprises, surface balayée, rayon, braquage, articulation, vitesse,
  écart à la saisie, conflits), avec la variante la plus favorable. Édition > **Même trajet, autre véhicule**.
- **Note de giration (PDF)** : synthèse, plan sur la photo, résultats, points d'attention, comparaison des
  variantes, hypothèses, méthode et sources (mise en page de la note de Girabase).
- **DXF en Lambert-93 ou dans la zone CC du lieu** (CC44 dans le Tarn) : sommets convertis exactement, arcs, textes
  et blocs par similitude locale (écart inférieur au millimètre, vérifié contre PROJ) ; bordures et obstacles sur
  les calques GIR_BORDURES, GIR_BORDURES_FRANCHISSABLES, GIR_OBSTACLES.
- La note et le DXF donnent aussi les coordonnées CC du lieu.
- Pendant le dessin d'une bordure, Ctrl+Z enlève le dernier point ; Échap ou un changement d'outil gardent la
  bordure si elle a au moins deux points (comme la polyligne d'AutoCAD).
- Relecture indépendante avant livraison : côté interdit d'une bordure jugé sur toute la polyligne (coudes,
  retours en L, îlots dessinés sans être fermés), clic droit précis sur les bordures, conflits effacés avec le
  trajet, comparaison toujours faite sur le dernier calcul et avec le giratoire de chaque variante.

## 1.2.1 — 11 octobre 2026

Numérotation alignée sur Girabase (1.2.1) : les deux logiciels portent le même numéro de version. Les versions 0.1 et
0.2 ci-dessous sont les versions de travail qui ont précédé.

- **Options > Ne pas tenir compte de la chaussée BD TOPO** (pointillé bleu ciel) : quand les routes de l'IGN sont
  décalées de la photo, plus de calage sur la voie ni d'alerte « roues hors chaussée » ; enregistré avec le projet. Le
  message de fin de saisie propose aussi « La photo est juste : ignorer la chaussée BD TOPO » en un clic.
- L'outil Giratoire fonctionne alors avec un anneau relevé ou un site Girabase : les deux clics sont le milieu des voies.
- Poignées de l'anneau en petits carrés (comme les grips d'AutoCAD) et croix au centre, pour ne plus les confondre avec
  les points du trajet ; points construits par l'outil Giratoire plus discrets.

### Versions de travail 0.2.1

Les routes de la BD TOPO peuvent être décalées de la photo (2,5 m au giratoire RD71 / RD41 de Lombers, la couche
« Routes » de l'IGN l'est autant). L'anneau d'un giratoire se relève donc directement sur la photo.

- **Outil Anneau (A)** : 3 clics sur la bordure extérieure (cercle par 3 points, comme CERCLE 3P d'AutoCAD), 1 clic sur
  le bord intérieur de la chaussée, 1 clic sur le bord de l'îlot (Entrée s'il n'y a pas de bande franchissable).
  Le cercle suit la souris et les rayons s'affichent pendant le relevé ; Retour arrière enlève le dernier clic.
- L'anneau relevé **remplace celui de la BD TOPO** : chaussée dessinée, outil Giratoire, contrôles de l'îlot (impossible)
  et de la bande franchissable (réserve).
- **Poignées** pour ajuster ensuite le centre, la bordure extérieure, le bord intérieur et l'îlot ; le logiciel propose
  de reconstruire le trajet du giratoire.
- **Outil Giratoire sans la BD TOPO** : avec un anneau relevé, les deux clics sont le milieu des voies d'entrée et de
  sortie ; la BD TOPO ne sert plus qu'à donner la direction de la branche si un axe passe à proximité.
- **Fichier > Enregistrer l'anneau pour Girabase (.gsite)** : l'anneau relevé (R, Bf, LA, centre) s'ouvre dans Girabase
  Python, où l'on ajoute les branches pour le calcul de capacité.

### Version de travail 0.2.0 — 11 octobre 2026

Application de bureau : la giration se construit en cliquant sur la photo aérienne de l'IGN, sans DXF. Elle suit
les principes validés le 11 octobre (document de projet « principes et ergonomie »).

- **Carte de France** (plan IGN), recherche d'adresse ou de commune, zoom jusqu'à la photo 20 cm.
- **Tracer (T)** : le trajet passe exactement par vos clics ; chaque point intérieur est le milieu d'un virage.
  - Rayon proposé : cercle passant par le point et ses voisins (entre 1 et 14 m) ; Ctrl + molette pour l'imposer.
  - Départ et arrivée calés au milieu de la voie de droite selon le sens de marche (Alt + clic pour ne pas caler).
- **Guides pendant la saisie** : pointillé rouge (limite du braquage) et zone hachurée, pointillé bleu (virage
  confortable), élastique coloré (bleu, orange, rouge), bulle au curseur (rayon, % du braquage, vitesse) et
  fantôme du véhicule.
- **Fin de saisie (Entrée, double-clic ou clic droit)** : les corrections du véhicule s'appliquent.
  - Un virage trop serré est porté au rayon minimal du véhicule plus 5 % quand la place existe.
  - Sinon le véhicule fait au mieux : il braque au maximum, s'écarte, puis revient ; son axe est en rouge plein là
    où il quitte la saisie, et l'écart à chaque point est chiffré.
  - Aucun point n'est déplacé : la saisie reste en pointillé rouge et « Revenir à ma saisie » (Ctrl+Z) la rend.
  - Option : corrections d'office ou proposées (en vert, Appliquer / Ignorer).
- **Giratoire (G)** : deux clics (branche d'entrée, branche de sortie) ; raccords de 15 m, anneau à rayon constant
  avec la roue avant extérieure à 0,50 m du bord (réglable), sens inverse des aiguilles d'une montre.
- **Site Girabase (.gsite)** : îlot, bande franchissable et anneau exacts ; roues sur l'îlot = impossible, sur la
  bande franchissable = réserve.
- **Mesurer (M)** : deux clics d'un bord de la chaussée à l'autre corrigent la largeur d'une route et recalent l'axe
  d'un tronçon de la BD TOPO ; le logiciel propose ensuite de remettre le trajet sur la voie corrigée.
- **Contrôle de la chaussée** : roues hors de la chaussée estimée au-delà de 0,50 m ; la zone des évasements autour
  d'un giratoire (15 m au-delà de l'anneau) n'est pas contrôlée et le dit.
- **Circulation à gauche** en option.
- **Bandeau de messages** sur la carte, sans fenêtre bloquante ; verdict en trois états (Passe, Passe avec réserves,
  Impossible en l'état) et une phrase d'aide qui dit quoi faire ensuite.
- `Giration.exe` : application de bureau Windows (la ligne de commande `giration.exe` de la 0.1 reste disponible).

### Version de travail 0.1.0 — 11 octobre 2026

Étape 1 de la feuille de route : moteur, exports et preuve de précision, en ligne de commande.

- **Moteur cinématique du CERTU**, intégré par Runge-Kutta d'ordre 4 indépendamment de tout affichage.
  - Il traite un tracteur suivi de 0 à 2 remorques.
  - Deux modes : suivre exactement une polyligne d'AutoCAD, ou utiliser la grille DEBUT/LIGNE/CERCLE/RACCORD/VIRAGE de Giration 3.3.
- **Vitesse automatique** : le tracé est suivi exactement et la vitesse possible est calculée. Elle tient compte du confort latéral, du temps de tourner le volant, de l'accélération et du freinage. Les passages au pas et les braquages presque à l'arrêt sont signalés.
- **Enveloppes vraies** :
  - zone balayée par la carrosserie et par les roues ;
  - traces des roues ;
  - largeur balayée ;
  - contrôles du braquage et de l'articulation.
- **Bibliothèque de 68 véhicules** sourcés (constructeurs 2022-2026 et guide CERTU) et lecture des `.veh` de Giration 3.3.
- **Exports** :
  - DXF (copie du plan avec calques `GIR_…`) ;
  - KML animé ;
  - GeoJSON ;
  - projet JSON ;
  - compte rendu texte.
- **Coordonnées** : Lambert-93 et CC42 à CC50 sont reconnus, avec correction de l'altération linéaire.
- **`giration.exe`** : il suffit de glisser un DXF sur l'exécutable.
- **`giration valider`** : contrôles du guide CERTU, des régimes permanents de toute la bibliothèque, de la convergence et de la cohérence des modes.
- **Exemples** : banc d'essai à comparer avec COVADIS, et giratoire de Lombers (Girabase) avec aperçu sur l'orthophoto IGN.
