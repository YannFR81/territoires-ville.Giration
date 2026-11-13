"""Bordures et obstacles dessinés sur la photo, ajoutés au DXF de l'épure (calques GIR_BORDURES…).

Ils servent à vérifier l'épure dans le dessin : ce sont les limites que l'utilisateur a relevées sur la photo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from ..gui.contraintes import RAYON_POTEAU, valide

CALQUES = {"bordure": ("GIR_BORDURES", 7), "franchissable": ("GIR_BORDURES_FRANCHISSABLES", 2),
           "obstacle": ("GIR_OBSTACLES", 1)}


def ajouter_contraintes(doc, contraintes: list) -> int:
    """Ajoute les contraintes valides à la vue modèle d'un document ezdxf ; retourne leur nombre."""
    msp = doc.modelspace()
    n = 0
    for c in contraintes or []:
        if not valide(c):
            continue
        nom, couleur = CALQUES[c["type"]]
        if nom not in doc.layers:
            lay = doc.layers.add(nom, color=couleur)
            if c["type"] == "franchissable":
                if "DASHED" not in doc.linetypes:
                    doc.linetypes.add("DASHED", pattern=[1.0, 0.6, -0.4], description="Tirets __ __ __")
                lay.dxf.linetype = "DASHED"
        pts = [(float(x), float(y)) for x, y in c["points"]]
        if len(pts) == 1:
            msp.add_circle(pts[0], RAYON_POTEAU, dxfattribs={"layer": nom})
        else:
            msp.add_lwpolyline(pts, close=bool(c.get("ferme")), dxfattribs={"layer": nom})
        n += 1
    return n


def ajouter_au_fichier(chemin: Union[str, Path], contraintes: list) -> int:
    import ezdxf
    doc = ezdxf.readfile(str(chemin))
    n = ajouter_contraintes(doc, contraintes)
    if n:
        doc.save()
    return n
