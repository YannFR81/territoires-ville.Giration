"""Export DXF pour AutoCAD et COVADIS : coordonnées du plan conservées, calques GIR_….

Calques (``<code>`` identifie le véhicule, pour superposer plusieurs épures dans un même dessin) :

- ``GIR_<code>_AXE`` : tracé suivi par le milieu de l'essieu avant (polyligne à arcs exacts) ;
- ``GIR_<code>_ENVELOPPE`` : contour de la zone balayée par la carrosserie (« entre murs ») ;
- ``GIR_<code>_ENVELOPPE_ROUES`` : contour de la zone balayée par les roues (« entre trottoirs ») ;
- ``GIR_<code>_REMPLISSAGE`` : hachure transparente de la zone balayée (à geler si besoin) ;
- ``GIR_<code>_TRACES_ROUES`` : traces du bord extérieur des pneus de chaque essieu ;
- ``GIR_<code>_VEHICULE`` : silhouettes successives (caisses et roues) ;
- ``GIR_<code>_VITESSE`` : vitesse automatique le long du tracé ;
- ``GIR_<code>_ALERTES`` : points à revoir ;
- ``GIR_<code>_TEXTES`` : cartouche.
"""
from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path
from typing import Optional, Sequence, Union

import ezdxf
import numpy as np
from ezdxf.enums import TextEntityAlignment

from .. import __version__
from ..calcul import Resultat
from ..enveloppe import ligne_simplifiee, simplifiee
from ..vitesse import KMH

COULEURS = {"AXE": 5, "ENVELOPPE": 30, "ENVELOPPE_ROUES": 3, "REMPLISSAGE": 30, "TRACES_ROUES": 8, "VEHICULE": 7,
            "VITESSE": 4, "ALERTES": 1, "TEXTES": 7}


def code_vehicule(res: Resultat) -> str:
    base = res.vehicule.nom_court or res.vehicule.id
    c = re.sub(r"[^A-Z0-9]+", "-", _ascii(base).upper()).strip("-")
    return c[:24] or "VEHICULE"


def _ascii(t: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn")


def _calques(doc, code: str) -> dict[str, str]:
    noms = {}
    for suffixe, couleur in COULEURS.items():
        nom = f"GIR_{code}_{suffixe}"
        if nom not in doc.layers:
            lay = doc.layers.add(nom, color=couleur)
            if suffixe == "REMPLISSAGE":
                lay.transparency = 0.75
        noms[suffixe] = nom
    return noms


def _polygones(geom):
    if geom.is_empty:
        return []
    return list(getattr(geom, "geoms", [geom]))


def _roues(centre, n, u, LE, alpha, lourd):
    """Rectangles des deux pneus d'un essieu (dessin seulement)."""
    lg, lp = (0.95, 0.32) if lourd else (0.62, 0.20)
    ca, sa = math.cos(alpha), math.sin(alpha)
    ur = (u[0] * ca - u[1] * sa, u[0] * sa + u[1] * ca)
    nr = (-ur[1], ur[0])
    res = []
    for cote in (1, -1):
        cx = centre[0] + cote * (LE / 2 - lp / 2) * n[0]
        cy = centre[1] + cote * (LE / 2 - lp / 2) * n[1]
        pts = []
        for a, b in ((lg / 2, lp / 2), (lg / 2, -lp / 2), (-lg / 2, -lp / 2), (-lg / 2, lp / 2)):
            pts.append((cx + a * ur[0] + b * nr[0], cy + a * ur[1] + b * nr[1]))
        res.append(pts)
    return res


def positions_silhouettes(res: Resultat, espacement: Optional[float] = None) -> list[int]:
    sim = res.sim
    if espacement is None:
        espacement = max(3.0, round(res.vehicule.longueur_totale / 2))
    idx, cible = [], 0.0
    for i in range(sim.n):
        if sim.s[i] + 1e-9 >= cible:
            idx.append(i)
            cible = sim.s[i] + espacement
    if idx[-1] != sim.n - 1:
        if sim.s[-1] - sim.s[idx[-1]] < espacement / 3:
            idx[-1] = sim.n - 1
        else:
            idx.append(sim.n - 1)
    return idx


def ajouter_resultat(msp, doc, res: Resultat, espacement: Optional[float] = None, hachure: bool = True,
                     hauteur_texte: float = 0.6, rang: int = 0) -> str:
    code = code_vehicule(res)
    cal = _calques(doc, code)
    sim, veh = res.sim, res.vehicule

    # axe
    if res.trace is not None:
        pts, bul = res.trace.sommets_bulges()
        msp.add_lwpolyline([(x, y, 0, 0, b) for (x, y), b in zip(pts, bul)], format="xyseb",
                           dxfattribs={"layer": cal["AXE"]})
    else:
        msp.add_lwpolyline(list(zip(sim.x, sim.y)), dxfattribs={"layer": cal["AXE"]})

    # enveloppes
    carrosserie, roues = simplifiee(res.enveloppes.carrosserie), simplifiee(res.enveloppes.roues)
    for cle, geom in (("ENVELOPPE", carrosserie), ("ENVELOPPE_ROUES", roues)):
        for poly in _polygones(geom):
            for anneau in [poly.exterior, *poly.interiors]:
                msp.add_lwpolyline(list(anneau.coords)[:-1], close=True, dxfattribs={"layer": cal[cle]})
    if hachure:
        for poly in _polygones(carrosserie):
            h = msp.add_hatch(color=COULEURS["REMPLISSAGE"], dxfattribs={"layer": cal["REMPLISSAGE"]})
            h.paths.add_polyline_path(list(poly.exterior.coords)[:-1], is_closed=True,
                                      flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
            for anneau in poly.interiors:
                h.paths.add_polyline_path(list(anneau.coords)[:-1], is_closed=True,
                                          flags=ezdxf.const.BOUNDARY_PATH_OUTERMOST)
            h.transparency = 0.75

    # traces des roues
    for nom, pts in res.enveloppes.traces_roues.items():
        msp.add_lwpolyline([tuple(p) for p in ligne_simplifiee(pts)], dxfattribs={"layer": cal["TRACES_ROUES"]})

    # silhouettes
    caisses = [sim.caisse(k) for k in range(len(veh.elements))]
    essieux = sim.essieux()
    lourd = veh.largeur > 2.2
    for i in positions_silhouettes(res, espacement):
        for k in range(len(veh.elements)):
            msp.add_lwpolyline([tuple(p) for p in caisses[k][i]], close=True, dxfattribs={"layer": cal["VEHICULE"]})
        for nom, centre, n, LE, directeur in essieux:
            c, nn = centre[i], n[i]
            u = (nn[1], -nn[0])
            a = float(sim.alpha[i]) if directeur else 0.0
            for r in _roues(c, nn, u, LE, a, lourd):
                msp.add_lwpolyline(r, close=True, dxfattribs={"layer": cal["VEHICULE"]})
            msp.add_line(tuple(c + LE / 2 * nn), tuple(c - LE / 2 * nn), dxfattribs={"layer": cal["VEHICULE"]})

    # vitesse automatique
    if sim.vitesse is not None:
        cible = 0.0
        for i in range(sim.n):
            if sim.s[i] + 1e-9 < cible:
                continue
            cible = sim.s[i] + 10.0
            nx, ny = -math.sin(sim.thP[i]), math.cos(sim.thP[i])
            p = (sim.x[i] + 0.6 * nx, sim.y[i] + 0.6 * ny)
            msp.add_circle((sim.x[i], sim.y[i]), 0.12, dxfattribs={"layer": cal["VITESSE"]})
            t = msp.add_text(f"{sim.vitesse[i] / KMH:.0f} km/h", height=hauteur_texte * 0.6,
                             dxfattribs={"layer": cal["VITESSE"], "rotation": math.degrees(sim.thP[i])})
            t.set_placement(p, align=TextEntityAlignment.BOTTOM_CENTER)

    # alertes
    for a in res.alertes:
        if a.x is None or a.niveau == "info":
            continue
        msp.add_circle((a.x, a.y), 1.0, dxfattribs={"layer": cal["ALERTES"]})
        t = msp.add_text(a.message[:120], height=hauteur_texte * 0.6, dxfattribs={"layer": cal["ALERTES"]})
        t.set_placement((a.x + 1.3, a.y), align=TextEntityAlignment.MIDDLE_LEFT)

    # cartouche au départ
    x0, y0 = sim.x[0], sim.y[0]
    m = res.mesures()
    lignes = [f"{veh.nom}",
              f"Résultat : {res.verdict()} - largeur balayée max {m['largeur_carrosserie_max']:.2f} m - "
              f"braquage max {m['braquage_max_deg']:.1f}° ({m['braquage_max_pourcent']:.0f} %)",
              f"Vitesse automatique : mini {m['vitesse_min_kmh']:.1f} km/h - {res.vitesse.conduite.nom}",
              f"Giration {__version__} - {date.today():%d/%m/%Y}"]
    mt = msp.add_mtext("\\P".join(lignes), dxfattribs={"layer": cal["TEXTES"], "char_height": hauteur_texte})
    th = sim.thP[0]
    l_veh = veh.longueur_totale
    # derrière la position de départ (attachement en bas à droite)
    decalage = rang * hauteur_texte * 1.75 * (len(lignes) + 1)       # épures superposées : cartouches empilés
    mt.set_location((x0 - (l_veh + 2) * math.cos(th), y0 - (l_veh + 2) * math.sin(th) - decalage), attachment_point=9)
    return code


def exporter_dxf(resultats: Union[Resultat, Sequence[Resultat]], chemin: Union[str, Path], fond: Optional[Union[str, Path]] = None,
                 espacement: Optional[float] = None, hachure: bool = True) -> Path:
    """Écrit un DXF (AutoCAD 2010) en mètres. Si ``fond`` est un DXF, les épures y sont ajoutées (copie)."""
    if isinstance(resultats, Resultat):
        resultats = [resultats]
    if fond:
        doc = ezdxf.readfile(str(fond))
    else:
        doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    departs: list[tuple[float, float]] = []
    for r in resultats:
        d = (float(r.sim.x[0]), float(r.sim.y[0]))
        rang = sum(1 for e in departs if math.dist(e, d) < 1.0)
        departs.append(d)
        ajouter_resultat(msp, doc, r, espacement=espacement, hachure=hachure, rang=rang)
    chemin = Path(chemin)
    doc.saveas(str(chemin))
    return chemin
