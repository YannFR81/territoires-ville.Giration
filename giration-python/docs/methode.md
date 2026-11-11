# Méthode de calcul de Giration

## Modèle du véhicule (CERTU)

C'est le modèle du guide Giration 3.3, annexes A et B :

- un tracteur à deux essieux équivalents (avant directeur, arrière fixe), suivi de 0, 1 ou 2 remorques à un essieu
  équivalent ;
- les groupes d'essieux fixes sont remplacés par leur centre ;
- les essieux directeurs ou suiveurs arrière sont exclus (essieu fictif, annexe B) ;
- les caisses sont des rectangles.

Les dimensions utilisées sont décrites dans `giration/vehicule.py` : PAFav, E, PAFar, L, LEav, LEar, D.
L'angle de braquage maximal α_max vient du rayon intérieur entre trottoirs du tracteur :
`α_max = atan(E0 / (RVehMin + LEar0/2))`.

Pour les ensembles articulés de la bibliothèque, on retient le braquage **mécanique du tracteur**. L'angle
d'articulation est contrôlé à part : au-delà de 80°, il faut vérifier le dégagement ; au-delà de 90°, c'est la mise
en portefeuille. Giration 3.3 réduisait au contraire le braquage de tout l'ensemble pour qu'un cercle parcouru
indéfiniment reste faisable. Cette valeur réduite est conservée dans la bibliothèque (`alpha_max_permanent`).

## Équations

On note P le milieu de l'essieu avant, s la longueur parcourue par P, β le cap du tracteur, α le braquage et
θk le cap de l'élément k (vecteurs u = axe de l'élément, n = normale à gauche). Les vitesses sont exprimées par
mètre parcouru :

```
tracteur   : dβ/ds = sin α / E0 ; vitesse de l'essieu arrière = cos α · u0
attelage k : vH = vA(k-1) − D(k-1) · ω(k-1) · n(k-1)
remorque k : ωk = (vH · nk) / Ek ; vAk = (vH · uk) uk
```

Ces équations sont celles de TRAJECT.FRM, écrites sous forme vectorielle.

- **Mode « tracé imposé »** : la direction de P est celle du tracé, et α = direction du tracé − β.
- **Mode « volant imposé »** (grille du CERTU) : α est donné et P en découle.

L'intégration se fait par Runge-Kutta d'ordre 4, au pas de 5 cm, avec des pas alignés sur les changements de
tronçon.

## Vitesse automatique

En mode tracé imposé, la trajectoire ne dépend pas de la vitesse. La vitesse retenue en chaque point est la plus
petite de ces limites :

- **confort latéral** : v ≤ √(a_lat · R) ;
- **temps de tourner le volant** : v ≤ vitesse de braquage / |dα/ds| ;
- **vitesse d'approche**.

Le profil est ensuite lissé par l'accélération et le freinage admissibles (passage aller puis retour). Les valeurs
par famille de véhicules sont dans `giration/vitesse.py`. Ce sont des hypothèses de départ, à confronter au
terrain :

| Famille | a_lat (m/s²) |
|---|---|
| VL | 2,0 |
| PL et cars | 1,5 |
| Bus avec voyageurs debout | 1,2 |
| Agricole | 1,2 |
| Convois | 1,0 |

Le style de conduite prudent / normal / expérimenté multiplie a_lat et la vitesse de braquage. Le style
« normal » garde 15 °/s, la valeur du CERTU, cohérente avec les 6 s de butée à butée d'AutoTURN.

Exemple : à l'entrée d'un arc de 12 m pris directement depuis une droite, le volant doit tourner de 1/12 rad par
mètre. À 15 °/s, cela impose v ≤ 0,262 × 12 = 3,1 m/s, soit **11,3 km/h**.

## Enveloppes et mesures

- **Zone balayée** : union des enveloppes convexes de deux positions successives de chaque caisse, ou de
  l'essieu, pneus compris, pour les roues. Les positions sont espacées de 10 cm.
- **Largeur balayée** : mesurée perpendiculairement au tracé de P, tous les 50 cm. Seules comptent les positions
  du véhicule voisines du point de mesure.
- **Exports** : les contours sont simplifiés à 2 mm près.

## Géoréférencement

Les calculs se font dans les coordonnées du plan :

- en Lambert-93 et en CC42 à CC50, les dimensions du véhicule sont multipliées par le facteur d'échelle local de
  la projection. Dans le Tarn en Lambert-93, il vaut 1,00014 (0,14 mm/m) ;
- un plan local (sans coordonnées légales) peut recevoir une origine GPS pour produire le KML et le GeoJSON.

Les tuiles Web Mercator ne servent jamais au calcul : elles agrandissent les longueurs d'environ 39 % à la
latitude du Tarn.
