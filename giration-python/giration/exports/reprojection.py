"""Reprojection d'un DXF du plan de calcul (Lambert-93) vers une conique conforme CC42 à CC50.

Les plans des collectivités sont souvent en conique conforme de la zone (CC44 dans le Tarn, EPSG:3944), notamment
sous COVADIS. Giration calcule en Lambert-93 : à l'export, le DXF peut être converti dans la zone CC du lieu pour
tomber exactement sur le plan.

Méthode :

- les sommets (lignes, polylignes, points) sont convertis exactement, point par point (Lambert-93 → latitude et
  longitude → CC) ;
- les objets qui ont une forme propre (arcs, cercles, textes, blocs, hachures, ellipses, splines) subissent la
  similitude locale de la conversion autour de leur point de référence : translation exacte, rotation (différence
  de convergence des méridiens entre les deux projections) et facteur d'échelle. Sur l'emprise d'un carrefour,
  l'écart avec la conversion exacte reste inférieur au millimètre.

Les polylignes gardent leurs arrondis (« bulge ») : une similitude conserve les arcs. Seule la vue modèle est
convertie ; les définitions de blocs restent dans leur repère propre (leur insertion est convertie).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .. import projections as _P

SOURCE_DEFAUT = "L93"


def _systemes() -> dict:
    """{« L93 », « CC42 »… « CC50 »: projection} (projections Lambert de Giration, avec .epsg, vers_geo, depuis_geo)."""
    if hasattr(_P, "SYSTEMES"):
        return dict(_P.SYSTEMES)
    return {"L93": _P.LAMBERT93, **{f"CC{k}": v for k, v in _P.CC.items()}}


def zone_cc(lat: float) -> int:
    """Zone CC adaptée à une latitude (CC42 à CC50)."""
    return min(50, max(42, int(round(lat))))


def projection(code: str):
    """« L93 » ou « CC42 » à « CC50 » (sans tenir compte de la casse ; « EPSG:2154 », « EPSG:3944 » acceptés)."""
    c = code.strip().upper().replace(" ", "").replace("-", "")
    if c in ("L93", "LAMBERT93", "EPSG:2154", "2154"):
        c = "L93"
    if c.startswith("EPSG:"):
        c = c[5:]
    if c.startswith("39") and len(c) == 4:
        c = "CC" + c[2:]
    p = _systemes().get(c)
    if p is None:
        raise ValueError(f"Système de coordonnées inconnu : {code} (attendu : L93 ou CC42 à CC50)")
    return p


def systemes_proposes(lat: float) -> list[tuple[str, str]]:
    """Choix proposés à l'export : Lambert-93, puis la zone CC du lieu, puis les zones voisines."""
    z = zone_cc(lat)
    res = [("L93", "Lambert-93 (EPSG:2154)"), (f"CC{z}", f"CC{z} (EPSG:{3900 + z}) — zone du lieu")]
    for v in (z - 1, z + 1):
        if 42 <= v <= 50:
            res.append((f"CC{v}", f"CC{v} (EPSG:{3900 + v})"))
    return res


@dataclass(frozen=True)
class Changement:
    """Conversion d'un plan de projection conique à un autre (même ellipsoïde GRS80)."""
    source: object
    cible: object

    @property
    def identite(self) -> bool:
        return self.source.epsg == self.cible.epsg

    def point(self, x: float, y: float) -> tuple[float, float]:
        if self.identite:
            return x, y
        return self.cible.depuis_geo(*self.source.vers_geo(x, y))

    def similitude(self, x: float, y: float, h: float = 1.0) -> tuple[float, float, float, float]:
        """(X, Y, k, rotation) : image du point, facteur d'échelle et rotation (rad) de la conversion autour de lui.
        Les deux projections étant conformes, la différentielle est une similitude ; elle est estimée par
        différences centrées sur ``h`` mètres."""
        if self.identite:
            return x, y, 1.0, 0.0
        X, Y = self.point(x, y)
        x1, y1 = self.point(x + h, y)
        x0, y0 = self.point(x - h, y)
        ax, ay = (x1 - x0) / (2 * h), (y1 - y0) / (2 * h)
        return X, Y, math.hypot(ax, ay), math.atan2(ay, ax)

    def matrice(self, x: float, y: float):
        """Matrice ezdxf de la similitude locale autour de (x, y)."""
        from ezdxf.math import Matrix44
        X, Y, k, rot = self.similitude(x, y)
        return (Matrix44.translate(-x, -y, 0) @ Matrix44.scale(k, k, 1.0) @ Matrix44.z_rotate(rot)
                @ Matrix44.translate(X, Y, 0))


def changement(cible: str, source: str = SOURCE_DEFAUT) -> Changement:
    return Changement(projection(source), projection(cible))


# ---------------------------------------------------------------------- DXF
def _point_reference(e) -> Optional[tuple[float, float]]:
    t = e.dxftype()
    for attr in ("center", "insert", "location", "start"):
        if e.dxf.hasattr(attr):
            p = e.dxf.get(attr)
            return float(p[0]), float(p[1])
    if t == "SPLINE":
        pts = list(e.control_points) or list(e.fit_points)
        if pts:
            return float(pts[0][0]), float(pts[0][1])
    from ezdxf import bbox
    b = bbox.extents([e], fast=True)
    if b.has_data:
        c = b.center
        return float(c.x), float(c.y)
    return None


def _convertir_sommets(e, ch: Changement) -> bool:
    """Conversion exacte sommet par sommet ; False si l'entité n'est pas de ce genre."""
    t = e.dxftype()
    if t == "LINE":
        for attr in ("start", "end"):
            p = e.dxf.get(attr)
            X, Y = ch.point(p[0], p[1])
            e.dxf.set(attr, (X, Y, p[2]))
        return True
    if t == "POINT":
        p = e.dxf.location
        X, Y = ch.point(p[0], p[1])
        e.dxf.location = (X, Y, p[2])
        return True
    if t == "LWPOLYLINE":
        pts = list(e.get_points("xyseb"))
        if not pts:
            return True
        _, _, k, _ = ch.similitude(pts[0][0], pts[0][1])
        nouv = []
        for x, y, s, f, b in pts:
            X, Y = ch.point(x, y)
            nouv.append((X, Y, s * k, f * k, b))
        e.set_points(nouv, format="xyseb")
        if e.dxf.hasattr("const_width"):
            e.dxf.const_width = e.dxf.const_width * k
        return True
    if t == "POLYLINE" and not (e.is_polygon_mesh or e.is_poly_face_mesh):
        for v in e.vertices:
            p = v.dxf.location
            X, Y = ch.point(p[0], p[1])
            v.dxf.location = (X, Y, p[2])
        return True
    return False


def reprojeter_document(doc, ch: Changement) -> dict[str, int]:
    """Convertit la vue modèle d'un document ezdxf ; retourne le nombre d'entités par type converties."""
    compte: dict[str, int] = {}
    if ch.identite:
        return compte
    msp = doc.modelspace()
    for e in list(msp):
        t = e.dxftype()
        try:
            if not _convertir_sommets(e, ch):
                ref = _point_reference(e)
                if ref is None:
                    continue
                e.transform(ch.matrice(*ref))
        except (NotImplementedError, TypeError, ValueError, AttributeError):
            compte["non converti"] = compte.get("non converti", 0) + 1
            continue
        compte[t] = compte.get(t, 0) + 1
    doc.header["$INSUNITS"] = 6                       # mètres
    try:
        from ezdxf import zoom
        zoom.extents(msp)
    except Exception:                                   # vue initiale seulement : sans conséquence
        pass
    return compte


def reprojeter_fichier(entree: Union[str, Path], sortie: Union[str, Path], cible: str,
                       source: str = SOURCE_DEFAUT) -> dict[str, int]:
    """Lit un DXF en ``source`` (Lambert-93 par défaut), l'écrit converti en ``cible`` (CC42 à CC50)."""
    import ezdxf
    doc = ezdxf.readfile(str(entree))
    compte = reprojeter_document(doc, changement(cible, source))
    doc.saveas(str(sortie))
    return compte


def suffixe(cible: str) -> str:
    """Suffixe de nom de fichier : « -L93 », « -CC44 »."""
    p = projection(cible)
    return "-L93" if p.epsg == 2154 else f"-CC{p.epsg - 3900}"
