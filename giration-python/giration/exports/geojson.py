"""Export GeoJSON (RFC 7946, WGS84) et projet JSON rejouable."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
import shapely
from shapely.geometry import mapping

from .. import __version__
from ..calcul import Resultat
from ..enveloppe import ligne_simplifiee, simplifiee
from ..georef import Georef
from ..vitesse import KMH
from .dxf import positions_silhouettes


def _arrondir(obj, nd=8):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, (list, tuple)):
        return [_arrondir(o, nd) for o in obj]
    if isinstance(obj, dict):
        return {k: _arrondir(v, nd) for k, v in obj.items()}
    return obj


def _geo(geom, geo: Optional[Georef]):
    if geo is None:
        return _arrondir(mapping(geom), 4)
    def f(coords: np.ndarray) -> np.ndarray:
        return np.array(geo.lonlat(coords), dtype=float).reshape(-1, 2)
    return _arrondir(mapping(shapely.transform(geom, f)), 8)


def _ligne(pts, geo: Optional[Georef]):
    from shapely import LineString
    return _geo(LineString([tuple(p) for p in pts]), geo)


def entites(res: Resultat, geo: Optional[Georef]) -> list[dict]:
    """Entités GeoJSON d'une épure. Sans géoréférencement, coordonnées du plan (hors norme RFC 7946)."""
    from shapely import Point, Polygon
    sim, veh = res.sim, res.vehicule
    commun = {"vehicule_id": veh.id, "vehicule": veh.nom}
    m = res.mesures()
    f = []
    axe = res.trace.echantillons(0.25) if res.trace is not None else list(zip(sim.x, sim.y))
    f.append({"type": "Feature", "geometry": _ligne([p[:2] for p in axe], geo),
              "properties": {**commun, "objet": "axe", "description": "tracé du milieu de l'essieu avant",
                             "verdict": res.verdict(), **{k: round(v, 3) for k, v in m.items() if math.isfinite(v)}}})
    f.append({"type": "Feature", "geometry": _geo(simplifiee(res.enveloppes.carrosserie), geo),
              "properties": {**commun, "objet": "enveloppe_carrosserie", "surface_m2": round(m["surface_carrosserie_m2"], 2),
                             "largeur_max_m": round(m["largeur_carrosserie_max"], 3)}})
    f.append({"type": "Feature", "geometry": _geo(simplifiee(res.enveloppes.roues), geo),
              "properties": {**commun, "objet": "enveloppe_roues", "surface_m2": round(m["surface_roues_m2"], 2),
                             "largeur_max_m": round(m["largeur_roues_max"], 3)}})
    for nom, pts in res.enveloppes.traces_roues.items():
        f.append({"type": "Feature", "geometry": _ligne(ligne_simplifiee(pts), geo), "properties": {**commun, "objet": "trace_roue", "roue": nom}})
    caisses = [sim.caisse(k) for k in range(len(veh.elements))]
    for i in positions_silhouettes(res):
        for k, c in enumerate(caisses):
            f.append({"type": "Feature", "geometry": _geo(Polygon([tuple(p) for p in c[i]]), geo),
                      "properties": {**commun, "objet": "position", "element": k, "abscisse_m": round(float(sim.s[i]), 2),
                                     "temps_s": round(float(sim.temps[i]), 2) if sim.temps is not None else None,
                                     "vitesse_kmh": round(float(sim.vitesse[i] / KMH), 1) if sim.vitesse is not None else None,
                                     "braquage_deg": round(math.degrees(float(sim.alpha[i])), 2)}})
    for a in res.alertes:
        if a.x is None:
            continue
        f.append({"type": "Feature", "geometry": _geo(Point(a.x, a.y), geo),
                  "properties": {**commun, "objet": "alerte", "niveau": a.niveau, "message": a.message,
                                 "abscisse_m": None if a.s is None else round(a.s, 2)}})
    return f


def exporter_geojson(resultats: Union[Resultat, Sequence[Resultat]], chemin: Union[str, Path], geo: Optional[Georef]) -> Path:
    if isinstance(resultats, Resultat):
        resultats = [resultats]
    feats = [e for r in resultats for e in entites(r, geo if geo and geo.georeference else None)]
    doc = {"type": "FeatureCollection", "name": "giration", "features": feats}
    if not (geo and geo.georeference):
        doc["remarque"] = "Coordonnées du plan local (non géoréférencé) : hors norme RFC 7946."
    chemin = Path(chemin)
    chemin.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return chemin


def projet(res: Resultat, geo: Optional[Georef] = None, source: str = "") -> dict:
    """Tout ce qu'il faut pour rejouer et vérifier le calcul."""
    sim = res.sim
    profil = []
    cible = 0.0
    for i in range(sim.n):
        if sim.s[i] + 1e-9 >= cible or i == sim.n - 1:
            profil.append({"s": round(float(sim.s[i]), 3), "x": round(float(sim.x[i]), 4), "y": round(float(sim.y[i]), 4),
                           "braquage_deg": round(math.degrees(float(sim.alpha[i])), 3),
                           "caps_deg": [round(math.degrees(float(t)), 3) for t in sim.theta[:, i]],
                           "vitesse_kmh": round(float(sim.vitesse[i] / KMH), 2) if sim.vitesse is not None else None,
                           "temps_s": round(float(sim.temps[i]), 2) if sim.temps is not None else None})
            cible = sim.s[i] + 1.0
    trace = None
    if res.trace is not None:
        pts, bul = res.trace.sommets_bulges()
        trace = {"polyligne": [[round(x, 4), round(y, 4), round(b, 8)] for (x, y), b in zip(pts, bul)],
                 "troncons": [{"x0": t.x0, "y0": t.y0, "cap0_deg": math.degrees(t.th0), "longueur": t.longueur,
                               "rayon": None if not t.est_arc else math.copysign(t.rayon, t.k)} for t in res.trace.troncons]}
    c = res.vitesse.conduite
    return {
        "format": "giration-projet", "version_format": 1, "logiciel": f"Giration {__version__}",
        "date": datetime.now().isoformat(timespec="seconds"), "source": source,
        "coordonnees": geo.nom() if geo else "plan local", "systeme": res.systeme, "echelle_projection": res.echelle,
        "vehicule": res.vehicule.en_dict(),
        "trace": trace,
        "parametres": {"pas_m": sim.pas, "conduite": c.nom, "v_approche_kmh": c.v_approche / KMH, "a_lat": c.a_lat,
                       "a_acc": c.a_acc, "a_dec": c.a_dec,
                       "vitesse_braquage_deg_s": math.degrees(res.vitesse.vitesse_braquage)},
        "resultat": {"verdict": res.verdict(), "mesures": {k: (round(v, 4) if math.isfinite(v) else None)
                                                           for k, v in res.mesures().items()},
                     "alertes": [{"niveau": a.niveau, "message": a.message, "abscisse_m": a.s} for a in res.alertes]},
        "profil": profil,
    }


def exporter_projet(resultats: Union[Resultat, Sequence[Resultat]], chemin: Union[str, Path], geo: Optional[Georef] = None,
                    source: str = "") -> Path:
    if isinstance(resultats, Resultat):
        resultats = [resultats]
    doc = {"format": "giration-projets", "epures": [projet(r, geo, source) for r in resultats]} if len(resultats) > 1 \
        else projet(resultats[0], geo, source)
    chemin = Path(chemin)
    chemin.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return chemin
