"""Bordures et obstacles dessinés sur la photo, bâtiments de la BD TOPO : contrôle des roues et de la caisse.

Sans dépendance à Qt : testable seul. Coordonnées du plan de calcul (Lambert-93 en métropole), en mètres.

Trois sortes de contraintes se dessinent sur la photo (la photo fait foi) :

- **bordure** : bordure de trottoir ou d'îlot, que les roues ne franchissent pas ; la caisse peut passer au-dessus
  (porte-à-faux) ;
- **franchissable** : bordure basse, bateau, bande franchissable : les roues peuvent y monter, à confirmer (réserve) ;
- **obstacle** : mur, façade, clôture, poteau, mât, panneau : rien du véhicule ne doit le toucher.

Une contrainte est une polyligne, un contour fermé, ou un seul point (poteau). Pour une ligne ouverte, le côté
interdit est celui où ne roule pas le véhicule (déduit de son trajet) ; pour un contour fermé de bordure, l'intérieur
si le trajet est dehors (îlot), l'extérieur s'il est dedans (chaussée détourée) ; un obstacle fermé est interdit à
l'intérieur.

Les bâtiments de la BD TOPO (précision annoncée de quelques mètres) sont contrôlés comme des obstacles, mais un
conflit n'y vaut que réserve : à vérifier sur la photo, ou à redessiner en obstacle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import shapely
from shapely import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

TYPES = {
    "bordure": "Bordure de trottoir ou d'îlot (les roues ne la franchissent pas)",
    "franchissable": "Bordure franchissable, bateau (les roues peuvent y monter : réserve)",
    "obstacle": "Obstacle : mur, façade, clôture, poteau (rien ne doit le toucher)",
}
NOMS_COURTS = {"bordure": "bordure", "franchissable": "bordure franchissable", "obstacle": "obstacle", "bati": "bâtiment"}
GRAVES = ("bordure", "obstacle")                # conflit → impossible ; sinon (franchissable, bâti BD TOPO) → réserve
TOLERANCE = 0.02                                # m : en deçà, contact négligé
RAYON_POTEAU = 0.15                             # m : obstacle d'un seul point
PORTEE_COTE = 40.0                              # m : demi-largeur de la zone où l'on juge le côté d'une ligne


@dataclass
class Conflit:
    type: str                    # bordure, franchissable, obstacle, bati
    indice: int                  # numéro de la contrainte (ou du bâtiment)
    depassement: float           # m au-delà de la ligne ou dans l'obstacle
    x: float                     # point le plus engagé
    y: float
    zone: BaseGeometry           # partie de l'emprise en conflit (dessin)
    traverse: bool = False       # le trajet lui-même coupe la ligne : le véhicule doit la franchir

    @property
    def grave(self) -> bool:
        return self.type in GRAVES

    def message(self) -> str:
        if self.traverse:
            return {"bordure": "Le trajet traverse une bordure : le véhicule devrait la franchir",
                    "franchissable": "Le trajet passe sur la bordure franchissable",
                    "obstacle": "Le trajet traverse un obstacle"}.get(self.type, "Le trajet traverse une contrainte")
        d = f"{self.depassement:.2f}".replace(".", ",")
        if self.type == "bordure":
            return f"Les roues franchissent une bordure de {d} m"
        if self.type == "franchissable":
            return f"Les roues montent sur la bordure franchissable ({d} m)"
        if self.type == "obstacle":
            return f"La caisse touche un obstacle ({d} m)"
        return (f"La caisse touche un bâtiment de la BD TOPO ({d} m) : vérifiez sur la photo (précision de quelques "
                "mètres), ou dessinez l'obstacle")


def nouvelle(type_: str, points: list, ferme: bool = False) -> dict:
    if type_ not in TYPES:
        raise ValueError(f"Type de contrainte inconnu : {type_}")
    return {"type": type_, "points": [[float(x), float(y)] for x, y in points], "ferme": bool(ferme and len(points) >= 3)}


def valide(c: dict) -> bool:
    n = len(c.get("points") or [])
    return c.get("type") in TYPES and (n >= 2 or (n == 1 and c["type"] == "obstacle"))


def geometrie(c: dict) -> BaseGeometry:
    pts = [tuple(p) for p in c["points"]]
    if len(pts) == 1:
        return Point(pts[0]).buffer(RAYON_POTEAU, 16)
    if c.get("ferme") and len(pts) >= 3:
        poly = Polygon(pts)
        return poly if poly.is_valid else shapely.make_valid(poly)
    return LineString(pts)


def longueur(c: dict) -> float:
    pts = c["points"]
    L = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    if c.get("ferme") and len(pts) >= 3:
        L += math.dist(pts[-1], pts[0])
    return L


def libelle(c: dict) -> str:
    n = len(c["points"])
    if n == 1:
        return "poteau"
    nom = NOMS_COURTS[c["type"]]
    if c.get("ferme"):
        nom += " (contour fermé)"
    return f"{nom}, {longueur(c):.1f} m".replace(".", ",")


def _profondeur(zone: BaseGeometry, bord: BaseGeometry) -> tuple[float, float, float]:
    """Plus grande distance des sommets de ``zone`` au ``bord`` : (distance, x, y)."""
    xy = shapely.get_coordinates(zone)
    if not len(xy):
        return 0.0, math.nan, math.nan
    d = shapely.distance(shapely.points(xy), bord)
    k = int(np.argmax(d))
    return float(d[k]), float(xy[k, 0]), float(xy[k, 1])


def _cote_interdit(ligne: LineString, axe: np.ndarray) -> Optional[BaseGeometry]:
    """Demi-zone (bande de PORTEE_COTE m) du côté de la ligne où le véhicule ne roule pas."""
    gauche = ligne.buffer(PORTEE_COTE, single_sided=True)
    droite = ligne.buffer(-PORTEE_COTE, single_sided=True)
    if not len(axe):
        return None
    pts = shapely.points(axe)
    n_g = int(np.count_nonzero(shapely.contains(gauche, pts)))
    n_d = int(np.count_nonzero(shapely.contains(droite, pts)))
    if n_g == n_d == 0:                              # trajet loin de la ligne : point du trajet le plus proche
        k = int(np.argmin(shapely.distance(pts, ligne)))
        p = Point(axe[k])
        s = ligne.project(p)
        q = ligne.interpolate(s)
        q2 = ligne.interpolate(min(ligne.length, s + 0.1))
        q1 = ligne.interpolate(max(0.0, s - 0.1))
        tx, ty = q2.x - q1.x, q2.y - q1.y
        n_g = 1 if tx * (p.y - q.y) - ty * (p.x - q.x) > 0 else 0
        n_d = 1 - n_g
    return droite if n_g >= n_d else gauche


def controler_contrainte(c: dict, i: int, roues: BaseGeometry, caisse: BaseGeometry, axe: np.ndarray) -> Optional[Conflit]:
    """Conflit entre une contrainte dessinée et l'emprise du véhicule (roues pour une bordure, caisse pour un
    obstacle). ``axe`` : points (N, 2) du trajet du véhicule, qui indiquent le côté où il roule."""
    if not valide(c):
        return None
    emprise = caisse if c["type"] == "obstacle" else roues
    if emprise is None or emprise.is_empty:
        return None
    g = geometrie(c)
    if not emprise.intersects(g):
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        dedans = c["type"] != "obstacle" and len(axe) and \
            np.count_nonzero(shapely.contains(g, shapely.points(axe))) > len(axe) / 2
        zone = emprise.difference(g) if dedans else emprise.intersection(g)
        bord = g.boundary
    else:
        if len(axe) >= 2:
            trajet = LineString(axe)
            croise = trajet.intersection(g)
            if not croise.is_empty:                  # le trajet coupe la ligne : pas de « côté » à respecter
                q = croise if croise.geom_type == "Point" else croise.representative_point()
                zone = emprise.intersection(g.buffer(0.3, cap_style="flat"))
                return Conflit(c["type"], i, math.inf, float(q.x), float(q.y), zone, traverse=True)
        interdit = _cote_interdit(g, axe)
        if interdit is None:
            return None
        zone = emprise.intersection(interdit)
        bord = g
    zone = shapely.make_valid(zone)
    zone = shapely.union_all([p for p in getattr(zone, "geoms", [zone]) if p.geom_type == "Polygon" and p.area > 0.002]) \
        if not zone.is_empty else zone
    if zone.is_empty:
        return None
    d, x, y = _profondeur(zone, bord)
    if len(c["points"]) == 1:                       # poteau : enfoncement mesuré depuis son centre
        ctr = Point(c["points"][0])
        d = RAYON_POTEAU + (float(emprise.boundary.distance(ctr)) if emprise.contains(ctr) else -float(emprise.distance(ctr)))
        x, y = ctr.x, ctr.y
    elif d <= TOLERANCE and zone.area > 0.01:       # obstacle entièrement sous l'emprise : rayon équivalent
        d = math.sqrt(zone.area / math.pi)
        x, y = zone.representative_point().x, zone.representative_point().y
    if d <= TOLERANCE:
        return None
    return Conflit(c["type"], i, d, x, y, zone)


def controler_contraintes(contraintes: list, roues: BaseGeometry, caisse: BaseGeometry, axe) -> list[Conflit]:
    axe = np.asarray(axe, dtype=float).reshape(-1, 2) if axe is not None else np.empty((0, 2))
    res = []
    for i, c in enumerate(contraintes or []):
        k = controler_contrainte(c, i, roues, caisse, axe)
        if k is not None:
            res.append(k)
    res.sort(key=lambda k: (not k.grave, -k.depassement))
    return res


# ---------------------------------------------------------------------- bâtiments de la BD TOPO
@dataclass
class Batiment:
    polygone: BaseGeometry       # coordonnées du plan
    usage: str = ""
    hauteur: Optional[float] = None
    precision: Optional[float] = None    # précision planimétrique annoncée par l'IGN (m)
    cle: str = ""


def lire_batiments(donnees: dict, geo) -> list[Batiment]:
    """Réponse WFS (GeoJSON) de la couche BDTOPO_V3:batiment → polygones dans le plan de calcul."""
    res = []
    for f in donnees.get("features", []):
        g = f.get("geometry") or {}
        t, coords = g.get("type"), g.get("coordinates") or []
        polys = [coords] if t == "Polygon" else coords if t == "MultiPolygon" else []
        p = f.get("properties", {})
        for poly in polys:
            if not poly or len(poly[0]) < 4:
                continue
            try:
                anneaux = [[geo.depuis_geo(c[1], c[0]) for c in a] for a in poly]
                pg = Polygon(anneaux[0], anneaux[1:])
            except (ValueError, ZeroDivisionError):
                continue
            if not pg.is_valid:
                pg = shapely.make_valid(pg)
            if pg.is_empty or pg.area < 1.0:
                continue
            try:
                h = float(p["hauteur"]) if p.get("hauteur") is not None else None
            except (TypeError, ValueError):
                h = None
            res.append(Batiment(pg, p.get("usage_1") or p.get("nature") or "", h, p.get("precision_planimetrique"),
                                p.get("cleabs") or ""))
    return res


def controler_bati(batiments: list[Batiment], caisse: BaseGeometry) -> list[Conflit]:
    if caisse is None or caisse.is_empty or not batiments:
        return []
    res = []
    env = caisse.envelope
    for i, b in enumerate(batiments):
        if not env.intersects(b.polygone) or not caisse.intersects(b.polygone):
            continue
        zone = caisse.intersection(b.polygone)
        if zone.is_empty or zone.area < 0.002:
            continue
        d, x, y = _profondeur(zone, b.polygone.boundary)
        if d <= TOLERANCE and zone.area > 0.01:
            d = math.sqrt(zone.area / math.pi)
            x, y = zone.representative_point().x, zone.representative_point().y
        if d > TOLERANCE:
            res.append(Conflit("bati", i, d, x, y, zone))
    res.sort(key=lambda k: -k.depassement)
    return res
