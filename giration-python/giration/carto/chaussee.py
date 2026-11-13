"""Chaussée estimée à partir de la BD TOPO de l'IGN : axe, largeur et nombre de voies de chaque tronçon.

Sert à rester sur la route : caler le départ et l'arrivée au milieu de la voie (selon le sens de marche) et
repérer où les roues sortent de la chaussée. C'est une estimation (largeur moyenne du tronçon, bordures arrondies
forfaitairement aux carrefours) : la photographie aérienne fait foi. L'utilisateur peut corriger la largeur d'une
route et recaler l'axe d'un tronçon d'après une mesure sur la photo ; ces corrections font partie du projet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import shapely
from shapely import LineString, Point
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry

from ..georef import Georef
from .geoservices import Troncon

LARGEUR_DEFAUT = {"Route à 2 chaussées": 7.0, "Route à 1 chaussée": 5.5, "Rond-point": 7.0, "Bretelle": 4.0,
                  "Route empierrée": 3.0, "Chemin": 3.0, "Quasi-autoroute": 7.0, "Type autoroutier": 7.0}
NATURES_IGNOREES = {"Sentier", "Escalier", "Piste cyclable", "Bac ou liaison maritime"}
RAYON_BORDURE = 5.0              # m : arrondi forfaitaire des angles rentrants (bordures des carrefours)
PORTEE_AIMANT = 6.0              # m : au-delà de cette distance à la chaussée, un clic n'est pas calé
DECALAGE_RECALAGE = 0.20         # m : en deçà, le milieu mesuré est confondu avec l'axe BD TOPO


@dataclass
class AxeRoute:
    ligne: LineString            # coordonnées du plan (axe éventuellement recalé)
    largeur: float
    voies: int
    sens_unique: bool
    nom: str
    largeur_estimee: bool        # largeur absente de la BD TOPO, valeur forfaitaire selon la nature
    anneau: bool = False         # tronçon d'anneau de giratoire (nature « Rond-point »)
    cle: str = ""                # identifiant stable du tronçon (pour retrouver un recalage)
    largeur_bdtopo: float = 0.0
    corrigee: bool = False       # largeur mesurée sur la photo
    recale: bool = False         # axe recalé sur la photo


@dataclass
class Chaussee:
    axes: list[AxeRoute] = field(default_factory=list)
    surface: Optional[BaseGeometry] = None

    @property
    def vide(self) -> bool:
        return not self.axes


def cle_troncon(pts: list) -> str:
    """Identifiant d'un tronçon : extrémités arrondies au décimètre (stable d'un chargement à l'autre)."""
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    a, b = (round(x0, 1), round(y0, 1)), (round(x1, 1), round(y1, 1))
    a, b = min(a, b), max(a, b)
    return f"{a[0]:.1f},{a[1]:.1f};{b[0]:.1f},{b[1]:.1f}"


def construire_chaussee(troncons: list[Troncon], geo: Georef, largeurs: Optional[dict] = None,
                        recalages: Optional[dict] = None, anneaux: Optional[list] = None) -> Chaussee:
    """``largeurs`` : {nom de route: largeur mesurée} ; ``recalages`` : {clé de tronçon: (dx, dy)} ;
    ``anneaux`` : anneaux relevés sur la photo [(cx, cy, r_ext, r_int)], qui remplacent les tronçons « Rond-point »
    de la BD TOPO voisins (souvent décalés de la photo)."""
    largeurs, recalages, anneaux = largeurs or {}, recalages or {}, anneaux or []
    axes, morceaux = [], []
    for t in troncons:
        if t.nature in NATURES_IGNOREES or len(t.points) < 2:
            continue
        pts = [geo.depuis_geo(la, lo) for la, lo in t.points]
        ligne = LineString(pts)
        if ligne.length < 0.5:
            continue
        cle = cle_troncon(pts)
        largeur = t.largeur if t.largeur and t.largeur > 1.0 else None
        estimee = largeur is None
        if largeur is None:
            largeur = LARGEUR_DEFAUT.get(t.nature, 5.0)
        voies = int(t.voies) if t.voies else (1 if t.sens_unique or largeur < 4.5 else 2)
        nom = t.numero or t.nom or t.nature
        anneau = t.anneau
        if anneau and any(ligne.distance(Point(cx, cy)) < r_ext + 8.0 for cx, cy, r_ext, _ in anneaux):
            continue                               # remplacé par l'anneau relevé sur la photo
        bd = float(largeur)
        corr = (not anneau) and nom in largeurs
        if corr:
            largeur = float(largeurs[nom])
        rec = None if anneau else recalages.get(cle)
        if rec:
            ligne = translate(ligne, rec[0], rec[1])
        axes.append(AxeRoute(ligne, float(largeur), max(1, voies), t.sens_unique, nom, estimee, anneau, cle, bd,
                             corr, bool(rec)))
        morceaux.append(ligne.buffer(largeur / 2, cap_style="flat", join_style="round"))
    for cx, cy, r_ext, r_int in anneaux:
        morceaux.append(Point(cx, cy).buffer(r_ext, 96).difference(Point(cx, cy).buffer(r_int, 96)))
    if not morceaux:
        return Chaussee()
    union = shapely.union_all(morceaux)
    # fermeture : comble les angles rentrants des carrefours par des arrondis de bordure de 5 m
    surface = union.buffer(RAYON_BORDURE, join_style="round").buffer(-RAYON_BORDURE, join_style="round")
    return Chaussee(axes, shapely.union_all([surface, union]))


def axe_proche(ch: Chaussee, p: tuple, sans_anneau: bool = True, portee: float = PORTEE_AIMANT):
    """Tronçon le plus proche (distance au bord de la chaussée) : (axe, point projeté, tangente unitaire) ou None."""
    if ch is None or ch.vide:
        return None
    pt = Point(p)
    meilleur, d_min = None, portee
    for a in ch.axes:
        if sans_anneau and a.anneau:
            continue
        d = a.ligne.distance(pt) - a.largeur / 2
        if d < d_min:
            meilleur, d_min = a, d
    if meilleur is None:
        return None
    a = meilleur
    s = a.ligne.project(pt)
    f = a.ligne.interpolate(s)
    f2 = a.ligne.interpolate(min(a.ligne.length, s + 0.5))
    f1 = a.ligne.interpolate(max(0.0, s - 0.5))
    tx, ty = f2.x - f1.x, f2.y - f1.y
    n = math.hypot(tx, ty) or 1.0
    return a, (f.x, f.y), (tx / n, ty / n)


def aimanter(ch: Chaussee, x: float, y: float, cap: Optional[float] = None,
             gauche: bool = False) -> Optional[tuple[float, float, str]]:
    """Milieu de la voie où l'on roule, selon le sens de marche ``cap`` (rad) : voie de droite (de gauche avec
    l'option) d'une route à double sens ; axe d'une route à sens unique ou à une voie. Sans cap : voie du côté du
    clic. Retourne (x, y, libellé) ou None si le clic est loin de toute route. Les anneaux ne sont pas concernés."""
    r = axe_proche(ch, (x, y))
    if r is None:
        return None
    a, (fx, fy), (tx, ty) = r
    if a.sens_unique or a.voies < 2:
        return fx, fy, f"axe de {a.nom}"
    lv = a.largeur / a.voies
    decalage = (a.largeur / 2 - lv / 2) if a.voies > 2 else lv / 2
    if cap is not None:
        if tx * math.cos(cap) + ty * math.sin(cap) < 0:
            tx, ty = -tx, -ty
        nx, ny = (-ty, tx) if gauche else (ty, -tx)          # à droite : normale à droite de la marche
        return fx + decalage * nx, fy + decalage * ny, f"voie de {'gauche' if gauche else 'droite'} ({a.nom})"
    nx, ny = -ty, tx
    cote = 1.0 if (x - fx) * nx + (y - fy) * ny >= 0 else -1.0
    return fx + cote * decalage * nx, fy + cote * decalage * ny, f"milieu de voie, {a.nom}"


@dataclass
class Mesure:
    longueur: float
    axe: Optional[AxeRoute]
    decalage: float              # milieu mesuré − axe BD TOPO, le long de la normale à gauche de l'axe (m)
    normale: tuple[float, float]

    @property
    def largeur_plausible(self) -> bool:
        return self.axe is not None and 1.5 <= self.longueur <= 15.0

    @property
    def recalage(self) -> tuple[float, float]:
        return self.decalage * self.normale[0], self.decalage * self.normale[1]

    @property
    def recaler(self) -> bool:
        return self.largeur_plausible and abs(self.decalage) >= DECALAGE_RECALAGE


def mesurer(ch: Chaussee, a: tuple, b: tuple) -> Mesure:
    """Mesure d'un bord de chaussée à l'autre sur la photo : largeur, et écart entre le milieu mesuré et l'axe BD TOPO."""
    L = math.dist(a, b)
    m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    r = axe_proche(ch, m, portee=8.0)
    if r is None:
        return Mesure(L, None, 0.0, (0.0, 0.0))
    axe, q, (tx, ty) = r
    nx, ny = -ty, tx
    return Mesure(L, axe, (m[0] - q[0]) * nx + (m[1] - q[1]) * ny, (nx, ny))


@dataclass
class Sortie:
    zone: BaseGeometry           # partie de la zone balayée par les roues hors de la chaussée estimée
    depassement: float           # m, plus grande distance au bord de la chaussée
    x: float
    y: float


def sorties_de_chaussee(ch: Chaussee, roues: BaseGeometry, seuil: float = 0.15) -> list[Sortie]:
    """Zones où les roues sortent de la chaussée estimée de plus de ``seuil`` mètres, de la plus forte à la plus faible."""
    if ch is None or ch.vide or ch.surface is None or roues.is_empty:
        return []
    hors = roues.difference(ch.surface)
    res = []
    bord = ch.surface.boundary
    for g in getattr(hors, "geoms", [hors]):
        if g.is_empty or g.area < 0.05:
            continue
        coords = shapely.get_coordinates(g)
        d = shapely.distance(shapely.points(coords), bord)
        k = int(d.argmax())
        if d[k] >= seuil:
            res.append(Sortie(g, float(d[k]), float(coords[k, 0]), float(coords[k, 1])))
    res.sort(key=lambda s: -s.depassement)
    return res
