"""Modèle du projet affiché à l'écran : variantes de tracé, historique d'annulation, construction des tracés.

Sans dépendance à Qt : testable seul. Les coordonnées des points sont celles du plan de calcul (Lambert-93 en
métropole), en mètres.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Union

from ..trace import Trace, norm_angle

COULEURS_VARIANTES = ["#E8730C", "#7B3FB5", "#0F7C8C", "#C2185B", "#2E7D32", "#5D4037"]
FORMAT = "giration-projet-interactif"


@dataclass
class Variante:
    nom: str
    couleur: str
    vehicule_id: str
    points: list[list[float]] = field(default_factory=list)      # [x, y] : départ, sommets, arrivée
    rayons: list[Optional[float]] = field(default_factory=list)  # rayon imposé à chaque sommet (None : automatique)
    conduite: str = "normal"
    au_mieux: bool = False                # le véhicule fait au mieux là où la place manque (corrections de fin de saisie)
    giratoire: Optional[list] = None      # clics d'entrée et de sortie de l'outil Giratoire (pour le reconstruire)

    def normaliser(self) -> None:
        while len(self.rayons) < len(self.points):
            self.rayons.append(None)
        del self.rayons[len(self.points):]


@dataclass
class Projet:
    variantes: list[Variante]
    active: int = 0
    systeme: Optional[str] = "L93"
    origine: Optional[list[float]] = None             # (lat, lon) du plan local hors métropole
    vue: Optional[list[float]] = None                 # (lat, lon, niveau) à la réouverture
    nom: str = ""
    gauche: bool = False                              # circulation à gauche (option ; en France on roule à droite)
    largeurs: dict = field(default_factory=dict)      # largeur de chaussée mesurée sur la photo, par route
    recalages: dict = field(default_factory=dict)     # axe BD TOPO recalé sur la photo, par tronçon : [dx, dy]
    site: Optional[dict] = None                       # site Girabase (.gsite) : lat, lon, R, Bf, LA, nom
    marge_giratoire: float = 0.5                      # roue avant extérieure / bord extérieur de l'anneau (m)
    ignorer_chaussee: bool = False                    # ne pas tenir compte des routes BD TOPO (décalées de la photo)
    contraintes: list = field(default_factory=list)   # bordures, bordures franchissables, obstacles relevés sur la photo

    @property
    def variante(self) -> Variante:
        return self.variantes[self.active]

    def copie(self) -> "Projet":
        return copy.deepcopy(self)

    def en_dict(self) -> dict:
        return {"format": FORMAT, "version": 1, **asdict(self)}

    @classmethod
    def depuis_dict(cls, d: dict) -> "Projet":
        if d.get("format") != FORMAT:
            raise ValueError("Ce fichier n'est pas un projet Giration.")
        champs = Variante.__dataclass_fields__
        vs = [Variante(**{k: x for k, x in v.items() if k in champs}) for v in d["variantes"]]
        for v in vs:
            v.normaliser()
        return cls(vs, d.get("active", 0), d.get("systeme", "L93"), d.get("origine"), d.get("vue"), d.get("nom", ""),
                   bool(d.get("gauche", False)), dict(d.get("largeurs") or {}), dict(d.get("recalages") or {}),
                   d.get("site"), float(d.get("marge_giratoire", 0.5)), bool(d.get("ignorer_chaussee", False)),
                   [c for c in (d.get("contraintes") or []) if isinstance(c, dict)])

    def enregistrer(self, chemin: Union[str, Path]) -> None:
        Path(chemin).write_text(json.dumps(self.en_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def ouvrir(cls, chemin: Union[str, Path]) -> "Projet":
        return cls.depuis_dict(json.loads(Path(chemin).read_text(encoding="utf-8")))

    def nom_libre(self) -> str:
        pris = {v.nom for v in self.variantes}
        for k in range(26):
            n = chr(ord("A") + k)
            if n not in pris:
                return n
        return f"V{len(self.variantes) + 1}"

    def couleur_libre(self) -> str:
        prises = [v.couleur for v in self.variantes]
        for c in COULEURS_VARIANTES:
            if c not in prises:
                return c
        return COULEURS_VARIANTES[len(self.variantes) % len(COULEURS_VARIANTES)]


def projet_vide(vehicule_id: str) -> Projet:
    return Projet([Variante("A", COULEURS_VARIANTES[0], vehicule_id)])


class Historique:
    """Annulation et rétablissement par instantanés complets du projet (quelques kilo-octets chacun)."""

    LIMITE = 500

    def __init__(self, projet: Projet, libelle: str = "Nouveau projet"):
        self.etats: list[tuple[str, Projet]] = [(libelle, projet.copie())]
        self.index = 0

    def ajouter(self, libelle: str, projet: Projet) -> None:
        del self.etats[self.index + 1:]
        self.etats.append((libelle, projet.copie()))
        if len(self.etats) > self.LIMITE:
            del self.etats[1]
        self.index = len(self.etats) - 1

    @property
    def peut_annuler(self) -> bool:
        return self.index > 0

    @property
    def peut_retablir(self) -> bool:
        return self.index < len(self.etats) - 1

    def annuler(self) -> Optional[Projet]:
        if not self.peut_annuler:
            return None
        self.index -= 1
        return self.etats[self.index][1].copie()

    def retablir(self) -> Optional[Projet]:
        if not self.peut_retablir:
            return None
        self.index += 1
        return self.etats[self.index][1].copie()

    def aller(self, i: int) -> Projet:
        self.index = max(0, min(i, len(self.etats) - 1))
        return self.etats[self.index][1].copie()

    def libelles(self) -> list[str]:
        return [e[0] for e in self.etats]


# ---------------------------------------------------------------------- construction du tracé
@dataclass
class InfoSommet:
    indice: int                  # indice du point dans la variante
    deviation: float             # rad, signée (> 0 : virage à gauche)
    rayon_demande: float
    rayon: float                 # rayon effectivement construit
    reduit: bool                 # rayon réduit faute de place entre les points
    s_debut: float               # abscisses de l'arc sur le tracé
    s_fin: float


def rayon_automatique(rayon_min_vehicule: float) -> float:
    """Rayon proposé à un nouveau sommet : un peu plus large que le minimum du véhicule (conduite confortable)."""
    return max(8.0, math.ceil(1.25 * rayon_min_vehicule * 2) / 2)


def construire_trace(points: list, rayons: list, rayon_auto: float) -> tuple[Optional[Trace], list[InfoSommet]]:
    """Polyligne cliquée → tracé du milieu de l'essieu avant (droites et arcs tangents).

    Chaque sommet intérieur est raccordé par un arc : rayon imposé, ou automatique. Si deux raccords ne tiennent
    pas sur une même droite, leurs rayons sont réduits (signalé) : le tracé reste toujours constructible."""
    pts = []
    for p in points:
        if not pts or math.dist(p, pts[-1]) > 0.05:
            pts.append((float(p[0]), float(p[1])))
        elif len(pts) > 1:
            pass
    if len(pts) < 2:
        return None, []
    # correspondance avec les indices d'origine (points confondus retirés)
    origine, j = [], 0
    for i, p in enumerate(points):
        if j < len(pts) and math.dist(p, pts[j]) <= 0.05 and (not origine or origine[-1] != j):
            origine.append(i)
            j += 1
    n = len(pts)
    caps = [math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0]) for i in range(n - 1)]
    longs = [math.dist(pts[i], pts[i + 1]) for i in range(n - 1)]
    dev = [0.0] * n
    R = [0.0] * n
    T = [0.0] * n
    for i in range(1, n - 1):
        d = norm_angle(caps[i] - caps[i - 1])
        d = max(-math.radians(178), min(math.radians(178), d))
        dev[i] = d
        r = rayons[origine[i]] if origine[i] < len(rayons) and rayons[origine[i]] else rayon_auto
        R[i] = r
        T[i] = r * math.tan(abs(d) / 2)
    demande = list(T)
    # partage de chaque droite entre les raccords de ses deux extrémités
    for _ in range(3):
        for k in range(n - 1):
            besoin = T[k] + T[k + 1]
            if besoin > longs[k] + 1e-9:
                f = longs[k] / besoin
                T[k] *= f
                T[k + 1] *= f
    morceaux, infos, s = [], [], 0.0
    for k in range(n - 1):
        droite = longs[k] - T[k] - T[k + 1]
        if droite > 1e-6:
            morceaux.append((droite, None))
            s += droite
        i = k + 1
        if i < n - 1 and abs(dev[i]) > 1e-9:
            r_eff = T[i] / math.tan(abs(dev[i]) / 2) if T[i] > 0 else 0.0
            if r_eff > 1e-3:
                morceaux.append(("angle", math.copysign(r_eff, dev[i]), math.degrees(dev[i])))
                L = abs(dev[i]) * r_eff
                infos.append(InfoSommet(origine[i], dev[i], R[i], r_eff, T[i] < demande[i] * (1 - 2e-3) - 1e-4, s, s + L))
                s += L
    if not morceaux:
        return None, infos
    t = Trace.depuis_morceaux(pts[0][0], pts[0][1], math.degrees(caps[0]), morceaux)
    return t, infos


def depart_arrivee(depart: tuple, cap_depart: float, arrivee: tuple, cap_arrivee: float) -> list[list[float]]:
    """Points d'un mouvement défini par son départ et son arrivée (positions et directions, en rad) :
    un sommet à l'intersection des deux directions quand elle existe, sinon deux sommets (demi-tour, baïonnette)."""
    ux, uy = math.cos(cap_depart), math.sin(cap_depart)
    vx, vy = math.cos(cap_arrivee), math.sin(cap_arrivee)
    dx, dy = arrivee[0] - depart[0], arrivee[1] - depart[1]
    det = ux * vy - uy * vx                           # départ + t·u = arrivée − t'·v  ⇔  t·u + t'·v = arrivée − départ
    if abs(det) > 1e-6:
        t = (dx * vy - dy * vx) / det
        tp = (ux * dy - uy * dx) / det
        if t > 1.0 and tp > 1.0:
            return [list(depart), [depart[0] + t * ux, depart[1] + t * uy], list(arrivee)]
    d = max(10.0, 0.5 * math.hypot(dx, dy))
    return [list(depart), [depart[0] + d * ux, depart[1] + d * uy], [arrivee[0] - d * vx, arrivee[1] - d * vy],
            list(arrivee)]


# ---------------------------------------------------------------------- points de passage (sur le trajet)
def rayon_cercle(a, b, c) -> float:
    """Rayon du cercle passant par trois points (infini s'ils sont alignés)."""
    ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
    aire2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    return math.inf if aire2 < 1e-9 else ab * bc * ca / (2 * aire2)


RAYON_PROPOSE_MIN = 1.0
RAYON_PROPOSE_MAX = 14.0


def rayons_proposes(points: list, rayons: list, r_confort: float = 0.0) -> list[float]:
    """Rayon de chaque sommet : imposé, sinon celui du cercle passant par le point et ses deux voisins (l'intention
    de l'utilisateur), borné entre 1 et 14 m. Un virage trop serré pour le véhicule apparaît alors en rouge pendant la
    saisie ; les corrections de fin de saisie l'élargissent."""
    res = [0.0] * len(points)
    for i in range(1, len(points) - 1):
        r = rayons[i] if i < len(rayons) and rayons[i] else None
        if r is None:
            rc = rayon_cercle(points[i - 1], points[i], points[i + 1])
            r = min(max(rc, RAYON_PROPOSE_MIN), RAYON_PROPOSE_MAX)
        res[i] = float(r)
    return res


def construire_passages(points: list, rayons: list, r_confort: float):
    """Trajet passant par les points cliqués : chaque point intérieur est le sommet (milieu) d'un virage de rayon
    imposé ou proposé. On cherche les points d'intersection des droites (sommets du polygone d'appui) tels que
    chaque arc de raccord passe exactement par le point cliqué.

    Retourne (tracé, informations par point, polygone d'appui)."""
    pts = []
    for p in points:
        if not pts or math.dist(p, pts[-1]) > 0.05:
            pts.append((float(p[0]), float(p[1])))
    if len(pts) < 2:
        return None, [], pts
    if len(pts) != len(points):                     # points confondus : rayons réalignés sur les points gardés
        garde, j = [], 0
        for i, p in enumerate(points):
            if j < len(pts) and math.dist(p, pts[j]) <= 0.05 and len(garde) == j:
                garde.append(i)
                j += 1
        rayons = [rayons[i] if i < len(rayons) else None for i in garde]
    R = rayons_proposes(pts, rayons, r_confort)
    pis = list(pts)
    n = len(pts)
    infos: list[InfoSommet] = []
    for _ in range(40):
        _, infos = construire_trace(pis, R, r_confort)
        eff = {s.indice: s.rayon for s in infos}
        nouveaux, ecart = list(pis), 0.0
        for i in range(1, n - 1):
            ax, ay = pis[i][0] - pis[i - 1][0], pis[i][1] - pis[i - 1][1]
            bx, by = pis[i + 1][0] - pis[i][0], pis[i + 1][1] - pis[i][1]
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            if la < 1e-6 or lb < 1e-6:
                continue
            u1, u2 = (ax / la, ay / la), (bx / lb, by / lb)
            d = abs(norm_angle(math.atan2(u2[1], u2[0]) - math.atan2(u1[1], u1[0])))
            if d < 1e-4:
                nouveaux[i] = pts[i]
                continue
            bx2, by2 = u2[0] - u1[0], u2[1] - u1[1]
            nb = math.hypot(bx2, by2)
            e = eff.get(i, R[i]) * (1 / math.cos(min(d, math.radians(170)) / 2) - 1)
            nouveaux[i] = (pts[i][0] - e * bx2 / nb, pts[i][1] - e * by2 / nb)
            ecart = max(ecart, math.dist(nouveaux[i], pis[i]))
        pis = nouveaux
        if ecart < 1e-4:
            break
    trace, infos = construire_trace(pis, R, r_confort)
    return trace, infos, pis


def passages_depuis_sommets(sommets: list, r_confort: float) -> list[list[float]]:
    """Polygone d'appui → points de passage (milieux des arcs), pour l'outil départ → arrivée."""
    trace, infos = construire_trace(sommets, [None] * len(sommets), r_confort)
    if trace is None:
        return [list(p) for p in sommets]
    res = [list(sommets[0])]
    for s in infos:
        x, y, _, _ = trace.point((s.s_debut + s.s_fin) / 2)
        res.append([x, y])
    res.append(list(sommets[-1]))
    return res
