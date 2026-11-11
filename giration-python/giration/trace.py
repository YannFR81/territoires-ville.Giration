"""Tracé guidé : la trajectoire du milieu de l'essieu avant, faite de droites et d'arcs tangents.

Un tracé est une suite de tronçons de courbure constante (0 pour une droite, ±1/R pour un arc,
positive quand on tourne à gauche). Chaque tronçon part de la fin du précédent : le tracé est
continu en position. Il doit aussi l'être en direction (pas d'angle vif) : une polyligne à angles
vifs est raccordée par des arcs, comme la commande RACCORD d'AutoCAD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

TOL_ANGLE = math.radians(0.05)         # écart de direction toléré entre deux tronçons (angle « vif » au-delà)


def norm_angle(a: float) -> float:
    """Angle ramené dans ]-π, π]."""
    a = math.fmod(a + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


@dataclass
class Troncon:
    x0: float
    y0: float
    th0: float               # direction de départ (rad, sens trigonométrique depuis l'axe X)
    longueur: float
    k: float = 0.0           # courbure (1/m), > 0 à gauche

    @property
    def est_arc(self) -> bool:
        return abs(self.k) > 1e-12

    @property
    def rayon(self) -> float:
        return math.inf if not self.est_arc else 1 / abs(self.k)

    def point(self, s: float) -> tuple[float, float, float]:
        """Position et direction à l'abscisse s (0 ≤ s ≤ longueur) du tronçon."""
        th = self.th0 + self.k * s
        if not self.est_arc:
            return self.x0 + s * math.cos(self.th0), self.y0 + s * math.sin(self.th0), th
        return (self.x0 + (math.sin(th) - math.sin(self.th0)) / self.k,
                self.y0 - (math.cos(th) - math.cos(self.th0)) / self.k, th)

    @property
    def fin(self) -> tuple[float, float, float]:
        return self.point(self.longueur)

    @property
    def centre(self) -> Optional[tuple[float, float]]:
        if not self.est_arc:
            return None
        return self.x0 - math.sin(self.th0) / self.k, self.y0 + math.cos(self.th0) / self.k

    @property
    def deviation(self) -> float:
        """Angle dont tourne la direction le long du tronçon (rad, signé)."""
        return self.k * self.longueur

    @property
    def bulge(self) -> float:
        """Renflement AutoCAD (tan du quart de l'angle au centre) du sommet de départ."""
        return math.tan(self.deviation / 4) if self.est_arc else 0.0


@dataclass
class Trace:
    troncons: list[Troncon] = field(default_factory=list)
    nom: str = ""
    remarques: list[str] = field(default_factory=list)

    # -------------------------------------------------------------- construction
    @classmethod
    def depuis_morceaux(cls, x0: float, y0: float, th0_deg: float, morceaux: Iterable[tuple], nom: str = "") -> "Trace":
        """Tracé tangent par construction. ``morceaux`` : suite de (longueur, rayon) ; rayon None ou 0 pour une
        droite, positif pour un virage à gauche, négatif à droite. Variante : ("angle", rayon, déviation en degrés)."""
        t = cls(nom=nom)
        x, y, th = x0, y0, math.radians(th0_deg)
        for m in morceaux:
            if m[0] == "angle":
                _, R, dev = m
                L = abs(math.radians(dev)) * abs(R)
                k = math.copysign(1 / abs(R), dev)
            else:
                L, R = m
                k = 0.0 if not R else 1 / R
            tr = Troncon(x, y, th, float(L), k)
            t.troncons.append(tr)
            x, y, th = tr.fin
        return t

    @classmethod
    def depuis_polyligne(cls, points: Sequence[tuple[float, float]], renflements: Optional[Sequence[float]] = None,
                         rayon_raccord: Optional[float] = None, nom: str = "") -> "Trace":
        """Polyligne AutoCAD (sommets et renflements). Les angles vifs entre deux droites sont raccordés par un arc
        de rayon ``rayon_raccord`` ; sans rayon, un angle vif est une erreur."""
        pts = [(float(x), float(y)) for x, y in points]
        bul = list(renflements) if renflements is not None else [0.0] * len(pts)
        bul += [0.0] * (len(pts) - len(bul))
        # sommets confondus
        p2, b2 = [pts[0]], [bul[0]]
        for p, b in zip(pts[1:], bul[1:]):
            if math.dist(p, p2[-1]) < 1e-6:
                b2[-1] = b if b else b2[-1]
                continue
            p2.append(p)
            b2.append(b)
        if len(p2) < 2:
            raise ValueError("La polyligne doit avoir au moins deux sommets distincts.")
        tr_list = []
        for (xa, ya), (xb, yb), b in zip(p2[:-1], p2[1:], b2[:-1]):
            corde = math.hypot(xb - xa, yb - ya)
            ang = math.atan2(yb - ya, xb - xa)
            if abs(b) < 1e-9:
                tr_list.append(Troncon(xa, ya, ang, corde, 0.0))
            else:
                dev = 4 * math.atan(b)                    # angle au centre signé
                R = corde / (2 * math.sin(abs(dev) / 2))
                tr_list.append(Troncon(xa, ya, ang - dev / 2, abs(dev) * R, math.copysign(1 / R, dev)))
        t = cls(tr_list, nom=nom)
        t._raccorder(rayon_raccord)
        return t

    def _raccorder(self, rayon: Optional[float]) -> None:
        res: list[Troncon] = [self.troncons[0]]
        for suivant in self.troncons[1:]:
            prec = res[-1]
            dth = norm_angle(suivant.th0 - prec.fin[2])
            if abs(dth) <= TOL_ANGLE:
                res.append(suivant)
                continue
            xs, ys = prec.fin[0], prec.fin[1]
            if rayon is None or rayon <= 0:
                raise ValueError(f"Angle vif de {math.degrees(dth):.1f}° au sommet ({xs:.3f} ; {ys:.3f}). "
                                 "Ajoutez un raccord (commande RACCORD d'AutoCAD) ou indiquez un rayon de raccord.")
            if prec.est_arc or suivant.est_arc:
                raise ValueError(f"Angle vif de {math.degrees(dth):.1f}° entre une droite et un arc au sommet "
                                 f"({xs:.3f} ; {ys:.3f}) : raccordez-le dans AutoCAD.")
            if abs(abs(dth) - math.pi) < 1e-6:
                raise ValueError(f"Demi-tour sur place au sommet ({xs:.3f} ; {ys:.3f}).")
            R = rayon
            T = R * math.tan(abs(dth) / 2)
            dispo = min(prec.longueur, suivant.longueur)
            if T > dispo:                                   # raccord réduit pour tenir dans les deux droites
                T = dispo
                R = T / math.tan(abs(dth) / 2)
                self.remarques.append(f"Raccord réduit à R = {R:.2f} m au sommet ({xs:.2f} ; {ys:.2f}) "
                                      "faute de longueur de droite.")
            prec.longueur -= T
            x1, y1, th1 = prec.fin
            arc = Troncon(x1, y1, th1, abs(dth) * R, math.copysign(1 / R, dth))
            res.append(arc)
            x2, y2, _ = arc.fin
            res.append(Troncon(x2, y2, suivant.th0, suivant.longueur - T, 0.0))
            self.remarques.append(f"Angle vif de {math.degrees(dth):.1f}° raccordé par un arc de {R:.2f} m.")
        self.troncons = [t for t in res if t.longueur > 1e-9]

    # -------------------------------------------------------------- lecture
    @property
    def longueur(self) -> float:
        return sum(t.longueur for t in self.troncons)

    @property
    def depart(self) -> tuple[float, float, float]:
        t = self.troncons[0]
        return t.x0, t.y0, t.th0

    @property
    def arrivee(self) -> tuple[float, float, float]:
        return self.troncons[-1].fin

    def bornes(self) -> list[float]:
        """Abscisses de début de chaque tronçon, plus la longueur totale."""
        b, s = [0.0], 0.0
        for t in self.troncons:
            s += t.longueur
            b.append(s)
        return b

    def point(self, s: float) -> tuple[float, float, float, float]:
        """(x, y, direction, courbure) à l'abscisse s du tracé."""
        reste = s
        for t in self.troncons:
            if reste <= t.longueur or t is self.troncons[-1]:
                x, y, th = t.point(min(max(reste, 0.0), t.longueur))
                return x, y, th, t.k
            reste -= t.longueur
        raise ValueError("tracé vide")

    def echantillons(self, pas: float = 0.5) -> list[tuple[float, float, float]]:
        pts = []
        for t in self.troncons:
            n = max(1, math.ceil(t.longueur / pas)) if t.est_arc else 1
            for i in range(n):
                pts.append(t.point(t.longueur * i / n))
        pts.append(self.arrivee)
        return pts

    def sommets_bulges(self) -> tuple[list[tuple[float, float]], list[float]]:
        """Polyligne AutoCAD équivalente (arcs exacts). Un arc est découpé en morceaux de 90° au plus : le renflement
        tan(angle/4) ne peut pas décrire un cercle complet (il vaut 0 pour 360°)."""
        pts, bul = [], []
        for t in self.troncons:
            n = max(1, math.ceil(abs(t.deviation) / (math.pi / 2) - 1e-9)) if t.est_arc else 1
            for i in range(n):
                x, y, _ = t.point(t.longueur * i / n)
                pts.append((x, y))
                bul.append(math.tan(t.deviation / n / 4) if t.est_arc else 0.0)
        pts.append(self.arrivee[:2])
        bul.append(0.0)
        return pts, bul

    def inverse(self) -> "Trace":
        x, y, th = self.arrivee
        t = Trace(nom=self.nom, remarques=list(self.remarques))
        for tr in reversed(self.troncons):
            n = Troncon(x, y, th + math.pi, tr.longueur, -tr.k)
            t.troncons.append(n)
            x, y, th = n.fin
        return t

    def decrire(self) -> str:
        lignes = []
        for i, t in enumerate(self.troncons, 1):
            if t.est_arc:
                lignes.append(f"  {i:2d}. arc     L = {t.longueur:8.3f} m  R = {t.rayon:8.3f} m  "
                              f"{'gauche' if t.k > 0 else 'droite'}, {abs(math.degrees(t.deviation)):6.1f}°")
            else:
                lignes.append(f"  {i:2d}. droite  L = {t.longueur:8.3f} m")
        return "\n".join(lignes)
