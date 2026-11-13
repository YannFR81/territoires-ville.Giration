# Giration — portage Python (version 1.3.0)

Épures de giration des véhicules : portage en Python du logiciel **Giration** du CERTU/CEREMA (VB6, 2007-2014),
modernisé. *L’intelligence artificielle au service de l’action publique efficiente.*

**Version 1.3.0 : bordures et obstacles, bâti BD TOPO, comparaison des variantes, note PDF, DXF en CC.** Depuis la 1.2.1, application de bureau : On construit la giration en cliquant sur la photo aérienne de l'IGN, avec des
guides pendant la saisie et les corrections du véhicule en fin de saisie (voir `NOUVEAUTES.md`). Le moteur, les
exports et la ligne de commande de la version 0.1 sont inchangés.

## Application de bureau (Windows : `Giration.exe`)

1. Cherchez la commune ou l'adresse, ou zoomez à la molette depuis la carte de France jusqu'à la photo 20 cm.
2. Choisissez le véhicule.
3. **Tracer (T)** : cliquez le départ dans la voie d'arrivée puis les points de passage du milieu de l'essieu
   avant. Restez au-delà du pointillé rouge. **Giratoire (G)** : branche d'entrée, branche de sortie.
4. **Entrée** (ou double-clic, ou clic droit) : fin de saisie et corrections du véhicule. Aucun point n'est
   déplacé ; Ctrl+Z rend votre saisie.
5. **Mesurer (M)** corrige la largeur et l'axe d'une route d'après la photo. **Anneau (A)** relève un giratoire sur
   la photo (3 clics sur la bordure extérieure, puis le bord intérieur de la chaussée et l'îlot) quand celui de la
   BD TOPO est décalé ; il s'enregistre pour Girabase (.gsite). Fichier > Ouvrir un site Girabase reprend un site
   existant.
6. **Bordure (B)** : relevez sur la photo les bordures (les roues ne les franchissent pas), les bordures
   franchissables et bateaux (réserve) et les obstacles — murs, poteaux (la caisse ne doit pas les toucher). Les
   bâtiments de la BD TOPO sont contrôlés aussi (réserve).
7. **Comparer** : variantes côte à côte ; « Même trajet, autre véhicule ».
8. Exportez en DXF (AutoCAD, COVADIS) en Lambert-93 ou dans la zone CC du lieu (CC44 dans le Tarn), avec les
   bordures et obstacles ; **note de giration en PDF** ; KML, GeoJSON ou JSON.

Depuis les sources : `pip install -r requirements.txt PySide6`, puis `python -m giration.gui.app`.

## Ce que fait la version 0.1

- **Moteur cinématique du CERTU** (modèle « bicyclette » sans glissement), tracteur + 0 à 2 remorques, intégré par
  Runge-Kutta d'ordre 4 indépendamment de tout affichage. Deux façons de conduire :
  - **suivre un tracé** : le milieu de l'essieu avant suit exactement une polyligne dessinée dans AutoCAD
    (droites et arcs) ; les angles vifs sont raccordés par un arc, comme la commande RACCORD ;
  - **grille du CERTU** : DEBUT, LIGNE, CERCLE, RACCORD, VIRAGE, avec les formules de Giration 3.3.
- **Vitesse automatique** : on ne saisit plus de vitesse. Le tracé est suivi exactement ; le logiciel calcule la
  vitesse à laquelle un chauffeur peut le faire. Il respecte le confort latéral, le temps de tourner le volant
  (15 °/s) et des accélérations et freinages progressifs. Il signale les passages au pas et les braquages presque
  à l'arrêt.
- **Enveloppes vraies** :
  - zone balayée par la carrosserie (« entre murs ») et par les roues (« entre trottoirs ») ;
  - traces des roues ;
  - largeur balayée maximale ;
  - contrôles du braquage maximal et de l'angle d'articulation.
- **Bibliothèque de 68 véhicules** sourcés (données constructeurs 2022-2026 et 24 véhicules du guide CERTU),
  lecture des `.veh` de Giration 3.3.
- **Exports** :
  - **DXF** pour AutoCAD/COVADIS : copie du plan avec les épures, calques `GIR_<véhicule>_…`, coordonnées du plan
    conservées ;
  - **KML** pour Google Earth, avec animation au curseur de temps ;
  - **GeoJSON** (RFC 7946) ;
  - **projet JSON** rejouable ;
  - compte rendu texte.
- **Géoréférencement** :
  - Lambert-93 et CC42 à CC50 reconnus d'après les coordonnées ;
  - dimensions du véhicule corrigées de l'altération linéaire de la projection ;
  - plan local possible avec une origine GPS.

## Utilisation la plus simple (Windows)

1. Dans AutoCAD, dessiner le trajet du **milieu de l'essieu avant** en polyligne, de préférence sur un calque
   `GIR_AXE`. Le sens de la polyligne est le sens de marche. Les arcs se font avec l'option Arc de PLINE ou avec
   RACCORD.
2. Enregistrer en DXF (AutoCAD 2000 ou plus récent), en mètres.
3. **Glisser le DXF sur `giration.exe`** et répondre aux questions : véhicule (numéro ou mot-clé), rayon de
   raccord si la polyligne a des angles vifs, style de conduite.
4. Les fichiers `<plan> - giration.dxf / .kml / .geojson / .json / .txt` sont écrits à côté du DXF.

## Ligne de commande

```
giration vehicules [mot]                    liste de la bibliothèque (ex. : giration vehicules bus)
giration vehicule ID                        fiche d'un véhicule
giration calculer PLAN.dxf -v ID [-v ID2]   épures sans question
        [--calque GIR_AXE] [--raccord 12] [--systeme L93|CC44|local|auto] [--origine 43.81,2.17]
        [--conduite prudent|normal|expérimenté] [--sortie DOSSIER] [--inverser]
giration valider                            contrôles de précision du moteur
```

Depuis les sources : `pip install -r requirements.txt`, puis `python -m giration …`.

## Précision

`giration valider` (et `pytest`) vérifient :

- **guide Giration 3.3** : les 8 virages chiffrés et les 2 longueurs minimales sont reproduits au millimètre ;
- **prototype fidèle au VB6** : 0,07 mm d'écart après 22 m (le VB intègre par trapèzes) ;
- **régime permanent** des 68 véhicules : rayons calculés pas à pas égaux aux formules fermées du CERTU à 10⁻⁹ m ;
- **convergence** : moins d'un dixième de micromètre d'écart entre le pas par défaut (5 cm) et le pas de 1 cm ;
- **cohérence** des deux modes de conduite (tracé imposé et volant imposé) à 10⁻¹² m près ;
- **rayons publiés par les constructeurs** : la caisse rectangulaire du modèle donne un rayon extérieur supérieur
  de 0,23 m en moyenne au rayon publié, jamais inférieur. L'épure est donc du côté de la sécurité.

La limite de précision n'est donc pas le calcul mais le **fond de plan**. L'orthophoto IGN suffit pour la
faisabilité ; pour un projet, il faut un levé topographique ou le PCRS.

## Exemples

- `exemples/banc-essai-covadis/` : 4 manœuvres types × 2 véhicules du CERTU, à refaire dans COVADIS pour
  comparaison (DXF, fiche PDF avec les valeurs de Giration et des cases à remplir).
- `exemples/lombers/` : épures géoréférencées sur le giratoire RD71/RD41 de Lombers conçu avec Girabase
  (`.gsite`). Contient un KML à ouvrir dans Google Earth et un aperçu sur l'orthophoto IGN.

## Licence et origine

GNU GPL v3, comme le code source de Giration publié par le CEREMA
(<https://github.com/CEREMA/territoires-ville.Giration>). Modèle, formules et véhicules de référence : guide
utilisateur Giration 3.3 (CERTU, 2007). Données véhicules : fiches des constructeurs citées dans la bibliothèque.
Orthophotographies : © IGN, BD ORTHO®, Licence Ouverte 2.0.
