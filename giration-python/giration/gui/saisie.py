"""Saisie assistée sur la photo : guides pendant la saisie, corrections du véhicule en fin de saisie, trajet « au
mieux », giratoire en deux clics, contrôles de la chaussée et de l'îlot.

Règles (voir claude/giration-principes-ergonomie.md) :
- le trajet passe par les points cliqués (chaque point intérieur est le milieu d'un virage) ;
- un point posé par l'utilisateur ne bouge jamais sans son accord : les corrections changent un rayon ou font
  passer le véhicule « au mieux », la saisie reste affichée ;
- les données de l'IGN donnent des alertes, jamais des refus.

Sans dépendance à Qt : testable seul. Coordonnées du plan de calcul (Lambert-93 en métropole), en mètres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import shapely
from shapely import Point

from ..trace import Trace, Troncon, norm_angle
from .modele import InfoSommet, construire_passages

ECART_VISIBLE = 0.10          # m : écart du véhicule à un point saisi en deçà duquel on ne le signale pas
ECART_CONTRAINT = 0.15        # m : au-delà, l'axe du véhicule est dessiné en rouge (il quitte la saisie)
SEUIL_HORS = 0.50             # m : roues hors de la chaussée estimée en deçà : imprécision de la BD TOPO
ZONE_GIRATOIRE = 15.0         # m au-delà du bord extérieur de l'anneau : évasements des entrées, non contrôlés
RAYON_RACCORD_GIRATOIRE = 15.0
DEV_GUIDE_MAX = math.radians(150)


def rayon_confort(rmin: float) -> float:
    """Virage confortable : 1,25 fois le rayon minimal, au moins 8 m (guide bleu)."""
    return max(8.0, 1.25 * rmin)


def etat_rayon(R: float, rmin: float) -> str:
    """« impossible » sous le minimum du véhicule, « limite » jusqu'à 1,15 fois le minimum, sinon « ok »."""
    if R < rmin * 0.999:
        return "impossible"
    if R < rmin * 1.15:
        return "limite"
    return "ok"


def pct_braquage(R: float, empattement: float, alpha_max: float) -> float:
    """Braquage (en % du maximum) qu'il faut pour suivre en régime établi un cercle de rayon R (milieu de l'essieu avant)."""
    s = empattement / R if R > 0 else math.inf
    return math.inf if s >= 1 else math.degrees(math.asin(s)) / math.degrees(alpha_max) * 100


# ====================================================================== guides pendant la saisie
@dataclass
class Guide:
    limite: list[tuple[float, float]]        # pointillé rouge : en deçà, le virage au dernier point est impossible
    confort: list[tuple[float, float]]       # pointillé bleu : au-delà, le virage est confortable
    dernier: tuple[float, float]


def _courbe_guide(P: tuple, L: tuple, R: float) -> list[Optional[tuple[float, float]]]:
    """Lieu des fins de virage (tangente de sortie) pour un virage de rayon R dont le milieu est L, la droite
    d'arrivée passant par P. Un point cliqué en deçà de ce lieu demande un virage plus serré que R.

    Paramétré par la déviation δ (de −150° à +150°) ; None où la droite d'arrivée n'a plus la place."""
    D = math.dist(P, L)
    phi = math.atan2(P[1] - L[1], P[0] - L[0])
    res: list[Optional[tuple[float, float]]] = []
    for deg in list(range(-150, 0, 3)) + [0] + list(range(3, 151, 3)):
        if deg == 0:
            res.append((L[0], L[1]))
            continue
        s = 1.0 if deg > 0 else -1.0
        d = math.radians(abs(deg))
        h = R * (1 - math.cos(d / 2))
        if D <= h + 1e-9:
            res.append(None)
            continue
        x = s * h / D
        psi = phi - math.pi - math.asin(x)                  # direction d'arrivée
        if D * math.sqrt(1 - x * x) < R * math.sin(d / 2):  # le début du virage tomberait avant P
            res.append(None)
            continue
        u = (math.cos(psi), math.sin(psi))
        n = (-u[1] * s, u[0] * s)
        a = R * (math.sin(d) - math.sin(d / 2))
        b = R * (math.cos(d / 2) - math.cos(d))
        res.append((L[0] + a * u[0] + b * n[0], L[1] + a * u[1] + b * n[1]))
    return res


def guides(points: list, rayons: list, rmin: float, r_confort: Optional[float] = None) -> Optional[Guide]:
    """Guides autour du dernier point cliqué. La droite d'arrivée passe par la fin du virage précédent (ou le
    départ) : c'est là qu'elle commence sur le trajet actuel."""
    if len(points) < 2:
        return None
    L = (float(points[-1][0]), float(points[-1][1]))
    P = (float(points[-2][0]), float(points[-2][1]))
    if len(points) >= 3:
        tr, infos, _ = construire_passages(points, rayons, rmin)
        if tr is not None and tr.troncons and not tr.troncons[-1].est_arc:
            P = (tr.troncons[-1].x0, tr.troncons[-1].y0)
    if math.dist(P, L) < 0.5:
        return None
    rc = r_confort or rayon_confort(rmin)
    return Guide(_courbe_guide(P, L, rmin), _courbe_guide(P, L, rc), L)


@dataclass
class Elastique:
    morceau: list[tuple[float, float]]       # trajet du début du dernier virage jusqu'à la souris
    info: Optional[InfoSommet]               # virage au dernier point cliqué
    etat: str


def elastique(points: list, rayons: list, curseur: tuple, rmin: float) -> Optional[Elastique]:
    pts = [list(p) for p in points] + [list(curseur)]
    tr, infos, _ = construire_passages(pts, list(rayons) + [None], rmin)
    if tr is None:
        return None
    n = len(points)
    info = next((s for s in infos if s.indice == n - 1), None)
    L = tr.longueur
    s0 = max(0.0, info.s_debut - 0.5) if info else max(0.0, L - math.dist(points[-1], curseur))
    morceau, s = [], s0
    while s < L:
        x, y, _, _ = tr.point(s)
        morceau.append((x, y))
        s += 0.4
    x, y, _, _ = tr.point(L)
    morceau.append((x, y))
    return Elastique(morceau, info, etat_rayon(info.rayon, rmin) if info else "ok")


# ====================================================================== trajet « au mieux »
def suivre_au_mieux(trace: Trace, rmin: float, ds: float = 0.2, Lc: float = 3.0) -> Trace:
    """Le véhicule suit exactement le trajet saisi tant que c'est possible ; dans un virage plus serré que son rayon
    minimal, il braque au maximum, s'écarte vers l'extérieur, puis revient sur le trajet.

    Conduite par anticipation de la courbure du trajet et correction de l'écart latéral et du cap (amortissement
    critique sur une longueur Lc), courbure bornée à 1/rmin. Résultat : arcs courts de courbure constante."""
    L = trace.longueur
    n = max(2, int(math.ceil(L / 0.1)) + 1)
    ref = [trace.point(min(L, i * 0.1)) for i in range(n)]
    kmax = 1.0 / (rmin * 1.01)
    kth, ky = 2.0 / Lc, 1.0 / (Lc * Lc)
    x, y, th = trace.depart
    j, garde = 0, 0
    out: list[Troncon] = []
    while j < n - 2 and garde < 6 * n:
        garde += 1
        best, dmin = j, math.inf
        for q in range(max(0, j - 10), min(n, j + 60)):
            d = (ref[q][0] - x) ** 2 + (ref[q][1] - y) ** 2
            if d < dmin:
                dmin, best = d, q
        j = max(j, best)
        rx, ry, rth, rk = ref[j]
        e_lat = (rx - x) * -math.sin(rth) + (ry - y) * math.cos(rth)
        e_th = norm_angle(rth - th)
        k = max(-kmax, min(kmax, rk + kth * math.sin(e_th) + ky * e_lat))
        if out and abs(out[-1].k - k) < 1e-9:
            out[-1].longueur += ds
            x, y, th = out[-1].fin
        else:
            t = Troncon(x, y, th, ds, k)
            out.append(t)
            x, y, th = t.fin
    if out:                                           # ne pas dépasser l'arrivée
        ex, ey, eth, _ = ref[-1]
        depasse = (x - ex) * math.cos(eth) + (y - ey) * math.sin(eth)
        if depasse > 0:
            out[-1].longueur = max(1e-3, out[-1].longueur - depasse)
    return Trace(out, nom=trace.nom, remarques=list(trace.remarques))


def _dist_polyligne(p, pts) -> float:
    return float(shapely.distance(Point(p), shapely.linestrings(pts))) if len(pts) >= 2 else math.inf


@dataclass
class AuMieux:
    trace_vehicule: Trace
    ecarts: list[float]                                  # distance du trajet du véhicule à chaque point saisi
    contraint: list[list[tuple[float, float]]]           # parties où le véhicule quitte la saisie


def analyser_au_mieux(trace_saisie: Trace, trace_vehicule: Trace, points: list) -> AuMieux:
    ev = [(p[0], p[1]) for p in trace_vehicule.echantillons(0.3)]
    er = [(p[0], p[1]) for p in trace_saisie.echantillons(0.3)]
    ligne_v, ligne_r = shapely.linestrings(ev), shapely.linestrings(er)
    ecarts = [float(shapely.distance(Point(p), ligne_v)) for p in points]
    d = shapely.distance(shapely.points(ev), ligne_r)
    contraint, cur = [], []
    for q, dd in zip(ev, d):
        if dd > ECART_CONTRAINT:
            cur.append(q)
        elif cur:
            contraint.append(cur)
            cur = []
    if cur:
        contraint.append(cur)
    return AuMieux(trace_vehicule, ecarts, [c for c in contraint if len(c) >= 2])


def trajet(points: list, rayons: list, rmin: float, au_mieux: bool):
    """Trajet saisi et trajet suivi par le véhicule. Retourne (trace_saisie, infos, trace_vehicule, AuMieux|None)."""
    tr, infos, _ = construire_passages(points, rayons, rmin)
    if tr is None:
        return None, infos, None, None
    if au_mieux and any(s.rayon < rmin * 0.999 for s in infos):
        tv = suivre_au_mieux(tr, rmin)
        return tr, infos, tv, analyser_au_mieux(tr, tv, points)
    return tr, infos, tr, None


# ====================================================================== corrections en fin de saisie
@dataclass
class Corrections:
    rayons: list                                  # nouveaux rayons imposés
    elargis: list[tuple[int, float, float]]       # (point, rayon avant, rayon après)
    manque: list[int]                             # points où la place manque : le véhicule fera au mieux

    @property
    def vide(self) -> bool:
        return not self.elargis and not self.manque


def corrections(points: list, rayons: list, rmin: float) -> Corrections:
    """Virages plus serrés que le minimum du véhicule : rayon porté au minimum + 5 % (arrondi au demi-mètre) quand la
    place existe ; sinon le point est noté « manque de place » (le véhicule fera au mieux). Aucun point ne bouge."""
    _, infos, _ = construire_passages(points, rayons, rmin)
    nouv = list(rayons) + [None] * (len(points) - len(rayons))
    elargis, manque = [], []
    cible = math.ceil(rmin * 1.05 * 2) / 2
    for s in infos:
        if s.rayon >= rmin * 0.999:
            continue
        essai = list(nouv)
        essai[s.indice] = cible
        _, inf2, _ = construire_passages(points, essai, rmin)
        s2 = next((x for x in inf2 if x.indice == s.indice), None)
        if s2 is not None and not s2.reduit:
            nouv[s.indice] = cible
            elargis.append((s.indice, s.rayon, cible))
        else:
            manque.append(s.indice)
    return Corrections(nouv, elargis, manque)


# ====================================================================== giratoire
@dataclass
class Anneau:
    """Géométrie d'un giratoire : centre (plan), rayon de l'axe de l'anneau, bord extérieur ; îlot et bande
    franchissable quand on les connaît (site Girabase)."""
    cx: float
    cy: float
    r_ext: float
    r_int: float                         # bord intérieur de l'anneau (début de la bande franchissable ou de l'îlot)
    r_ilot: Optional[float] = None       # îlot infranchissable (Girabase : R)
    bf: Optional[float] = None           # bande franchissable (Girabase : Bf)
    source: str = "BD TOPO"
    nom: str = ""

    @property
    def centre(self) -> tuple[float, float]:
        return self.cx, self.cy


def ajuster_cercle(pts: list) -> Optional[tuple[float, float, float]]:
    """Cercle des moindres carrés (méthode de Kåsa) : (cx, cy, r)."""
    if len(pts) < 3:
        return None
    import numpy as np
    a = np.asarray(pts, dtype=float)
    A = np.column_stack([2 * a[:, 0], 2 * a[:, 1], np.ones(len(a))])
    b = (a ** 2).sum(axis=1)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy, c = sol
    r2 = c + cx * cx + cy * cy
    return (float(cx), float(cy), float(math.sqrt(r2))) if r2 > 0 else None


def anneau_bdtopo(chaussee, p: tuple, portee: float = 60.0) -> Optional[Anneau]:
    """Anneau du giratoire le plus proche du point, d'après les tronçons « Rond-point » de la BD TOPO."""
    if chaussee is None or chaussee.vide:
        return None
    anneaux = [a for a in chaussee.axes if getattr(a, "anneau", False)]
    if not anneaux:
        return None
    pp = Point(p)
    proches = [a for a in anneaux if a.ligne.distance(pp) < portee]
    if not proches:
        return None
    a0 = min(proches, key=lambda a: a.ligne.distance(pp))
    # tronçons du même anneau : ceux qui touchent le cercle ajusté sur le plus proche, de proche en proche
    groupe = [a0]
    for _ in range(4):
        for a in anneaux:
            if a not in groupe and any(a.ligne.distance(g.ligne) < 1.0 for g in groupe):
                groupe.append(a)
    pts = [c for a in groupe for c in a.ligne.coords]
    c = ajuster_cercle(pts)
    if c is None or not 3.0 < c[2] < 60.0:
        return None
    larg = sum(a.largeur for a in groupe) / len(groupe)
    return Anneau(c[0], c[1], c[2] + larg / 2, max(0.5, c[2] - larg / 2), source="BD TOPO",
                  nom=groupe[0].nom)


def anneau_site(site: dict, geo, chaussee=None) -> Optional[Anneau]:
    """Anneau d'un site : relevé sur la photo (source « photo ») ou site Girabase (.gsite) ; centre, R (îlot),
    Bf (bande franchissable), LA (largeur de l'anneau)."""
    try:
        lat, lon = float(site["lat"]), float(site["lon"])
        R, Bf, LA = float(site.get("R", 6.0)), float(site.get("Bf", 0.0)), float(site.get("LA", 7.0))
    except (KeyError, TypeError, ValueError):
        return None
    cx, cy = geo.depuis_geo(lat, lon)
    source = "photo" if site.get("source") == "photo" else "Girabase"
    return Anneau(cx, cy, R + Bf + LA, R + Bf, R, Bf, source=source, nom=str(site.get("nom") or ""))


def cercle_3_points(a: tuple, b: tuple, c: tuple) -> Optional[tuple[float, float, float]]:
    """Cercle passant par trois points (comme CERCLE 3P d'AutoCAD) : (cx, cy, r), None s'ils sont alignés."""
    ox, oy = a                                   # calcul relatif au premier point (coordonnées Lambert-93 grandes)
    ax, ay = 0.0, 0.0
    bx, by = b[0] - ox, b[1] - oy
    cx, cy = c[0] - ox, c[1] - oy
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux + ox, uy + oy, math.hypot(ux, uy)


def site_releve(geo, cx: float, cy: float, r_ext: float, r_int: float, r_ilot: float,
                nom: str = "Anneau relevé sur la photo") -> dict:
    """Site d'un anneau relevé sur la photo : mêmes grandeurs que Girabase (R îlot, Bf bande franchissable,
    LA largeur de l'anneau), centre en latitude et longitude."""
    r_ilot = min(r_ilot, r_int)
    lat, lon = geo.vers_geo(cx, cy)
    return {"lat": lat, "lon": lon, "R": round(r_ilot, 2), "Bf": round(r_int - r_ilot, 2),
            "LA": round(r_ext - r_int, 2), "nom": nom, "source": "photo"}


def gsite(site: dict) -> dict:
    """Site au format de Girabase Python (.gsite), pour y reprendre l'anneau relevé (les branches s'ajoutent dans
    Girabase)."""
    return {"format": "girabase-site/1", "nom": site.get("nom") or "Giratoire relevé avec Giration",
            "lat": site["lat"], "lon": site["lon"], "commune": {}, "R": site["R"], "Bf": site["Bf"], "LA": site["LA"],
            "branches": [], "branche1": 0, "fond": "auto", "opacite": 0.5, "cadastre": False, "zoom": 19.0,
            "rayon_axe_bdtopo": None,
            "sources": "Anneau relevé sur l'orthophotographie de l'IGN (Géoplateforme, Licence Ouverte 2.0) avec "
                       "Giration"}


def _voie(chaussee, p: tuple, centre: tuple, vers_centre: bool, gauche: bool, cliquee: bool = False):
    """Voie de la branche cliquée : point au milieu de la voie (sens de circulation) et direction de marche.

    ``cliquee`` (anneau relevé sur la photo) : le clic est lui-même le milieu de la voie ; la BD TOPO ne donne que
    la direction de la branche si un axe passe près du clic, sinon la direction est celle du centre de l'anneau."""
    from ..carto.chaussee import axe_proche
    r = axe_proche(chaussee, p, sans_anneau=True, portee=12.0) if chaussee is not None else None
    if cliquee:
        if r is not None:
            a, _, (tx, ty) = r
            nom = a.nom
        else:
            tx, ty = centre[0] - p[0], centre[1] - p[1]
            n = math.hypot(tx, ty) or 1.0
            tx, ty = tx / n, ty / n
            nom = "branche"
        if (tx * (centre[0] - p[0]) + ty * (centre[1] - p[1]) > 0) != vers_centre:
            tx, ty = -tx, -ty
        return (p[0], p[1]), (tx, ty), nom
    r = axe_proche(chaussee, p, sans_anneau=True)
    if r is None:
        return None
    a, q, (tx, ty) = r
    vers = (centre[0] - q[0]) * tx + (centre[1] - q[1]) * ty
    if (vers > 0) != vers_centre:
        tx, ty = -tx, -ty
    lv = a.largeur / max(1, a.voies)
    off = lv / 2 if (a.voies >= 2 and not a.sens_unique) else 0.0
    sg = -1.0 if gauche else 1.0                 # à droite : décalage côté droit de la marche
    nx, ny = ty, -tx                             # normale à droite de la marche
    return (q[0] + sg * off * nx, q[1] + sg * off * ny), (tx, ty), a.nom


@dataclass
class TourGiratoire:
    points: list[list[float]]
    rayons: list
    r_anneau: float
    de: str
    vers: str


def tour_giratoire(chaussee, anneau: Anneau, entree: tuple, sortie: tuple, largeur_essieu_av: float, rmin: float,
                   marge: float = 0.5, gauche: bool = False, approche: float = 32.0) -> Optional[TourGiratoire]:
    """Trajet d'un giratoire en deux clics : raccord d'entrée, anneau à rayon constant (roue avant extérieure à
    ``marge`` du bord extérieur), raccord de sortie ; sens inverse des aiguilles d'une montre à droite.

    Anneau relevé sur la photo : les deux clics sont le milieu des voies d'entrée et de sortie."""
    C = anneau.centre
    cliquee = anneau.source == "photo" or chaussee is None or chaussee.vide
    a = _voie(chaussee, entree, C, True, gauche, cliquee)
    b = _voie(chaussee, sortie, C, False, gauche, cliquee)
    if a is None or b is None:
        return None
    Rr = max(rmin * 1.02, anneau.r_ext - marge - largeur_essieu_av / 2)
    Re = RAYON_RACCORD_GIRATOIRE
    sg = -1.0 if gauche else 1.0                 # +1 : tour dans le sens trigonométrique (à droite en France)

    def centre_raccord(q, u, amont):
        nx, ny = sg * u[1], -sg * u[0]           # côté du raccord (à droite de la marche quand on roule à droite)
        bx, by = q[0] + Re * nx - C[0], q[1] + Re * ny - C[1]
        B = 2 * (bx * u[0] + by * u[1])
        Cc = bx * bx + by * by - (Rr + Re) ** 2
        disc = B * B - 4 * Cc
        if disc < 0:
            return None
        t = (-B - math.sqrt(disc)) / 2 if amont else (-B + math.sqrt(disc)) / 2
        return (C[0] + bx + t * u[0], C[1] + by + t * u[1]), (q[0] + t * u[0], q[1] + t * u[1])

    (qa, ua, nom_a), (qb, ub, nom_b) = a, b
    ce = centre_raccord(qa, ua, True)
    cs = centre_raccord(qb, ub, False)
    if ce is None or cs is None:
        return None
    ang_e = math.atan2(ce[0][1] - C[1], ce[0][0] - C[0])
    ang_s = math.atan2(cs[0][1] - C[1], cs[0][0] - C[0])
    cap_a, cap_b = math.atan2(ua[1], ua[0]), math.atan2(ub[1], ub[0])
    dev_e = norm_angle(ang_e + sg * math.pi / 2 - cap_a)
    dev_s = norm_angle(cap_b - (ang_s + sg * math.pi / 2))
    if sg > 0:
        tour = (ang_s - ang_e) % (2 * math.pi)
    else:
        tour = -((ang_e - ang_s) % (2 * math.pi))
    if abs(tour) < math.radians(5):                       # même branche : demi-tour complet
        tour = sg * 2 * math.pi + tour
    t_e = ce[1]
    S = (t_e[0] - approche * ua[0], t_e[1] - approche * ua[1])
    tr = Trace.depuis_morceaux(S[0], S[1], math.degrees(cap_a),
                               [(approche, None), ("angle", -sg * Re, math.degrees(dev_e)),
                                ("angle", sg * Rr, math.degrees(tour)), ("angle", -sg * Re, math.degrees(dev_s)),
                                (approche, None)])
    t = tr.troncons
    milieu = lambda tc: tc.point(tc.longueur / 2)[:2]
    pts, ray = [[S[0], S[1]]], [None]
    pts.append(list(milieu(t[1])))
    ray.append(Re)
    m = max(1, math.ceil(abs(tour) / math.radians(50)))
    for j in range(m):
        q = t[2].point(t[2].longueur * (j + 0.5) / m)
        pts.append([q[0], q[1]])
        ray.append(Rr)
    pts.append(list(milieu(t[3])))
    ray.append(Re)
    f = t[4].fin
    pts.append([f[0], f[1]])
    ray.append(None)
    return TourGiratoire(pts, ray, Rr, nom_a, nom_b)


# ====================================================================== contrôles de la chaussée et de l'îlot
@dataclass
class Controle:
    hors: float = 0.0                                   # plus grand dépassement des roues hors chaussée (m)
    zones_hors: list = field(default_factory=list)      # sorties (carto.chaussee.Sortie)
    ilot: float = 0.0                                   # empiètement sur l'îlot infranchissable (m)
    bande: float = 0.0                                  # empiètement sur la bande franchissable ou le bord intérieur (m)
    ilot_connu: bool = False                            # îlot et bande franchissable connus (site Girabase)
    zone_non_controlee: Optional[tuple[float, float, float]] = None   # (cx, cy, r)


def controler(chaussee, roues, anneau: Optional[Anneau]) -> Controle:
    """Roues hors de la chaussée estimée (au-delà de SEUIL_HORS), hors de la zone du giratoire ; roues sur l'îlot."""
    from ..carto.chaussee import sorties_de_chaussee
    c = Controle()
    exclure = None
    if anneau is not None:
        r = anneau.r_ext + ZONE_GIRATOIRE
        c.zone_non_controlee = (anneau.cx, anneau.cy, r)
        exclure = Point(anneau.centre).buffer(r, 64)
        centre = Point(anneau.centre)
        dmin = 0.0 if roues.contains(centre) else float(roues.distance(centre))
        if anneau.r_ilot is not None:
            c.ilot_connu = True
            c.ilot = max(0.0, anneau.r_ilot - dmin)
            c.bande = max(0.0, min(anneau.bf or 0.0, (anneau.r_ilot + (anneau.bf or 0.0)) - dmin))
        else:
            c.bande = max(0.0, anneau.r_int - dmin)
    if chaussee is not None and not chaussee.vide:
        zone = roues.difference(exclure) if exclure is not None else roues
        sorties = [s for s in sorties_de_chaussee(chaussee, zone, seuil=SEUIL_HORS)]
        c.zones_hors = sorties
        c.hors = sorties[0].depassement if sorties else 0.0
    return c
