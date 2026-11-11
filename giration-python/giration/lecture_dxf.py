"""Lecture des tracés dessinés dans AutoCAD : polylignes (LWPOLYLINE, POLYLINE 2D) avec leurs arcs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import ezdxf

UNITES = {0: "sans unité", 1: "pouces", 2: "pieds", 4: "millimètres", 5: "centimètres", 6: "mètres", 14: "décimètres"}


@dataclass
class PolyligneDXF:
    calque: str
    poignee: str
    points: list[tuple[float, float]]
    renflements: list[float]
    fermee: bool

    @property
    def longueur_approx(self) -> float:
        import math
        return sum(math.dist(a, b) for a, b in zip(self.points[:-1], self.points[1:]))


@dataclass
class DessinDXF:
    chemin: Path
    version: str
    unites: int
    polylignes: list[PolyligneDXF]

    def candidates(self, calque: Optional[str] = None) -> list[PolyligneDXF]:
        """Tracés à calculer : calque demandé, sinon calques contenant GIR ou AXE, sinon toutes les polylignes ouvertes."""
        ouvertes = [p for p in self.polylignes if not p.fermee and len(p.points) >= 2]
        if calque:
            return [p for p in ouvertes if p.calque.lower() == calque.lower()]
        marquees = [p for p in ouvertes if "GIR" in p.calque.upper() or "AXE" in p.calque.upper()]
        marquees = [p for p in marquees if not _calque_resultat(p.calque)]
        if marquees:
            return marquees
        return [p for p in ouvertes if not _calque_resultat(p.calque)]


_SUFFIXES = ("_AXE", "_ENVELOPPE", "_ENVELOPPE_ROUES", "_REMPLISSAGE", "_TRACES_ROUES", "_VEHICULE", "_VITESSE",
             "_ALERTES", "_TEXTES")


def _calque_resultat(nom: str) -> bool:
    """Calques écrits par Giration lui-même (GIR_<véhicule>_<SUFFIXE>) : on ne recalcule pas une épure déjà tracée."""
    n = nom.upper()
    if not n.startswith("GIR_") or n.startswith("GIR_AXE"):
        return False
    return any(n.endswith(s) and len(n) > len(s) + 4 for s in _SUFFIXES)


def lire_dxf(chemin: Union[str, Path]) -> DessinDXF:
    chemin = Path(chemin)
    try:
        doc = ezdxf.readfile(str(chemin))
    except IOError as exc:
        raise ValueError(f"Fichier illisible : {exc}") from None
    except ezdxf.DXFStructureError as exc:
        raise ValueError(f"DXF invalide ou endommagé : {exc}") from None
    unites = int(doc.header.get("$INSUNITS", 0) or 0)
    polys = []
    for e in doc.modelspace().query("LWPOLYLINE POLYLINE"):
        if e.dxftype() == "POLYLINE" and not e.is_2d_polyline:
            continue
        miroir = e.dxf.hasattr("extrusion") and e.dxf.extrusion[2] < 0
        if e.dxftype() == "LWPOLYLINE":
            pts, bul = [], []
            for x, y, *_rest in e.get_points("xyb"):
                b = _rest[-1] if _rest else 0.0
                pts.append((-x if miroir else x, y))
                bul.append(-b if miroir else b)
            fermee = e.closed
        else:
            pts, bul = [], []
            for v in e.vertices:
                x, y = v.dxf.location[0], v.dxf.location[1]
                b = v.dxf.get("bulge", 0.0)
                pts.append((-x if miroir else x, y))
                bul.append(-b if miroir else b)
            fermee = e.is_closed
        polys.append(PolyligneDXF(e.dxf.layer, e.dxf.handle, pts, bul, fermee))
    return DessinDXF(chemin, doc.dxfversion, unites, polys)
