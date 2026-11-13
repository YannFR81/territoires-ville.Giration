"""Enveloppes balayées et mesures : zone balayée par la carrosserie (« entre murs ») et par les roues
(« entre trottoirs »), traces des roues, largeur balayée le long du tracé.

L'enveloppe est l'union, pour chaque élément, des enveloppes convexes de deux positions successives de la
caisse (ou de l'essieu, pour les roues). Chaque caisse est coupée en deux au droit de son essieu arrière : le
flanc intérieur tourne autour de ce point, et l'enveloppe convexe de la caisse entière « couperait » l'intérieur
du virage (écart proportionnel au pas). Avec cette coupe et un pas de 10 cm, l'écart avec la zone balayée exacte
reste de l'ordre du millimètre (vérifié contre un échantillonnage au centimètre).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely import MultiPoint, Polygon, LineString, unary_union
from shapely.geometry.base import BaseGeometry

from .cinematique import Simulation

PAS_ENVELOPPE = 0.10


def _indices(sim: Simulation, pas: float) -> np.ndarray:
    """Échantillons espacés d'au moins ``pas`` le long du tracé, plus ceux où le braquage varie vite."""
    idx = [0]
    s_dern = 0.0
    a_dern = sim.alpha[0]
    for i in range(1, sim.n - 1):
        if sim.s[i] - s_dern >= pas - 0.5 * sim.pas or abs(sim.alpha[i] - a_dern) > math.radians(1.0):
            idx.append(i)
            s_dern, a_dern = sim.s[i], sim.alpha[i]
    idx.append(sim.n - 1)
    return np.array(idx)


def _morceaux(formes: np.ndarray) -> list[BaseGeometry]:
    """formes : (N, m, 2) positions successives d'une forme convexe → enveloppes convexes de deux positions successives
    (calcul vectorisé par shapely)."""
    if len(formes) < 2:
        return [Polygon(formes[0])]
    paires = np.concatenate([formes[:-1], formes[1:]], axis=1)          # (N-1, 2m, 2)
    n, m2, _ = paires.shape
    mp = shapely.multipoints(paires.reshape(-1, 2), indices=np.repeat(np.arange(n), m2))
    return list(shapely.convex_hull(mp))


@dataclass
class Enveloppes:
    carrosserie: BaseGeometry                       # union de tous les éléments
    roues: BaseGeometry                             # union des essieux (pneus compris)
    par_element: list[BaseGeometry]
    traces_roues: dict[str, np.ndarray]             # nom → polyligne (N, 2) du bord extérieur d'un pneu
    traces_coins: dict[str, np.ndarray]
    indices: np.ndarray
    largeur: dict = field(default_factory=dict)     # mesures de largeur balayée
    morceaux_carrosserie: np.ndarray = None         # enveloppes convexes élémentaires (pour les mesures locales)
    morceaux_roues: np.ndarray = None
    s_morceaux_carrosserie: np.ndarray = None       # abscisse du véhicule pour chaque morceau
    s_morceaux_roues: np.ndarray = None


def calculer_enveloppes(sim: Simulation, pas: float = PAS_ENVELOPPE, mesures: bool = True) -> Enveloppes:
    """Zones balayées. ``mesures=False`` saute la mesure de largeur (aperçu pendant un déplacement à la souris)."""
    idx = _indices(sim, pas)
    s_idx = sim.s[idx[:-1]] if len(idx) > 1 else sim.s[idx]
    par_el, m_car, s_car = [], [], []
    for k in range(len(sim.vehicule.elements)):
        c = sim.caisse(k)[idx]
        el = sim.vehicule.elements[k]
        devant = el.E + el.PAFav                     # longueur de caisse devant l'essieu arrière
        if el.PAFar > 1e-6 and devant > 1e-6:
            # caisse coupée au droit de l'essieu : chaque moitié tourne autour d'un point de son bord, donc
            # l'enveloppe convexe de deux positions successives suit le flanc intérieur sans le « couper »
            _, nk = sim.axes(k)
            A = sim.essieu_arriere(k)[idx]
            h = el.L / 2 * nk[idx]
            avant = np.stack([c[:, 0], c[:, 1], A - h, A + h], 1)
            arriere = np.stack([A + h, A - h, c[:, 2], c[:, 3]], 1)
            m = _morceaux(avant) + _morceaux(arriere)
            s_k = list(s_idx) * 2
        else:
            m = _morceaux(c)
            s_k = list(s_idx)
        par_el.append(shapely.union_all(m))
        m_car += m
        s_car += s_k
    carrosserie = unary_union(par_el)

    roues_geoms, traces, m_rou, s_rou = [], {}, [], []
    for nom, centre, n, LE, _ in sim.essieux():
        g = centre[idx] + LE / 2 * n[idx]
        d = centre[idx] - LE / 2 * n[idx]
        m = _morceaux(np.stack([g, d], 1))
        roues_geoms.append(shapely.union_all(m))
        m_rou += m
        s_rou += list(s_idx)
        traces[f"{nom} gauche"] = g
        traces[f"{nom} droite"] = d
    roues = unary_union(roues_geoms)

    coins = {}
    c0 = sim.caisse(0)[idx]
    coins["avant gauche"] = c0[:, 0]
    coins["avant droit"] = c0[:, 1]
    cd = sim.caisse(len(sim.vehicule.elements) - 1)[idx]
    coins["arrière droit"] = cd[:, 2]
    coins["arrière gauche"] = cd[:, 3]
    env = Enveloppes(carrosserie, roues, par_el, traces, coins, idx,
                     morceaux_carrosserie=np.array(m_car, dtype=object), morceaux_roues=np.array(m_rou, dtype=object),
                     s_morceaux_carrosserie=np.array(s_car), s_morceaux_roues=np.array(s_rou))
    if mesures:
        env.largeur = mesurer_largeurs(sim, env)
    return env


def _largeur_locale(morceaux: np.ndarray, x: float, y: float, th: float, portee: float = 40.0) -> tuple[float, float]:
    """Distances à gauche et à droite du point (x, y), le long de la normale au tracé, jusqu'au bord de la zone balayée
    par les positions voisines du véhicule (``morceaux``) : intervalle continu contenant le point."""
    if len(morceaux) == 0:
        return 0.0, 0.0
    nx, ny = -math.sin(th), math.cos(th)
    ligne = LineString([(x - portee * nx, y - portee * ny), (x + portee * nx, y + portee * ny)])
    inter = shapely.intersection(morceaux, ligne)
    c, ind = shapely.get_coordinates(inter, return_index=True)
    if len(c) == 0:
        return 0.0, 0.0
    t = (c[:, 0] - x) * nx + (c[:, 1] - y) * ny
    _, debuts = np.unique(ind, return_index=True)
    a = np.minimum.reduceat(t, debuts)
    b = np.maximum.reduceat(t, debuts)
    ordre = np.argsort(a)
    a, b = a[ordre], b[ordre]
    bmax = np.maximum.accumulate(b)
    nouveau = np.concatenate([[True], a[1:] > bmax[:-1] + 1e-6])          # début d'un intervalle fusionné
    deb = np.flatnonzero(nouveau)
    fin = np.concatenate([deb[1:] - 1, [len(a) - 1]])
    ga, gb = a[deb], bmax[fin]
    contient = (ga <= 0) & (gb >= 0)
    j = int(np.flatnonzero(contient)[0]) if contient.any() else int(np.argmin(np.minimum(np.abs(ga), np.abs(gb))))
    return max(float(gb[j]), 0.0), max(float(-ga[j]), 0.0)


def mesurer_largeurs(sim: Simulation, env: Enveloppes, pas: float = 0.5) -> dict:
    """Largeur balayée perpendiculairement au tracé du milieu de l'essieu avant, maximum et position.

    Seules comptent les positions du véhicule voisines du point de mesure (de 5 m avant à une longueur de véhicule
    plus 5 m après) : sur un tracé qui se recoupe, le reste de l'épure n'est pas compté."""
    L = sim.vehicule.longueur_totale
    stations, cible = [], 0.0
    for i in range(sim.n):
        if sim.s[i] + 1e-9 >= cible or i == sim.n - 1:
            stations.append(i)
            cible = sim.s[i] + pas
    res = {}
    for nom, morceaux, s_m in (("carrosserie", env.morceaux_carrosserie, env.s_morceaux_carrosserie),
                               ("roues", env.morceaux_roues, env.s_morceaux_roues)):
        ordre = np.argsort(s_m, kind="stable")
        s_tri, m_tri = s_m[ordre], morceaux[ordre]
        meilleur = (0.0, 0.0, 0.0, 0.0)
        for i in stations:
            i0 = np.searchsorted(s_tri, sim.s[i] - 5.0, "left")
            i1 = np.searchsorted(s_tri, sim.s[i] + L + 5.0, "right")
            g, d = _largeur_locale(m_tri[i0:i1], sim.x[i], sim.y[i], sim.thP[i])
            if g + d > meilleur[0]:
                meilleur = (g + d, g, d, float(sim.s[i]))
        res[nom] = {"largeur_max": float(meilleur[0]), "a_gauche": float(meilleur[1]), "a_droite": float(meilleur[2]),
                    "abscisse": meilleur[3]}
    return res


TOLERANCE_EXPORT = 0.002     # m : simplification des contours exportés (Douglas-Peucker), sans effet visible


TROU_MIN = 0.05             # m² : trous plus petits = artefacts numériques de l'union (zones jamais vides en réalité)


def _sans_petits_trous(geom: BaseGeometry, aire_min: float = TROU_MIN) -> BaseGeometry:
    polys = []
    for p in getattr(geom, "geoms", [geom]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        polys.append(Polygon(p.exterior, [a for a in p.interiors if Polygon(a).area >= aire_min]))
    if not polys:
        return geom
    return polys[0] if len(polys) == 1 else unary_union(polys)


def simplifiee(geom: BaseGeometry, tol: float = TOLERANCE_EXPORT) -> BaseGeometry:
    return _sans_petits_trous(geom).simplify(tol, preserve_topology=True)


def ligne_simplifiee(pts: np.ndarray, tol: float = TOLERANCE_EXPORT) -> np.ndarray:
    if len(pts) < 3:
        return pts
    return np.asarray(LineString(pts).simplify(tol).coords)
