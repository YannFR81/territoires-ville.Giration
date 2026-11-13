"""Export KML pour Google Earth, QGIS et le Géoportail : épure géoréférencée et animation.

Le dossier « Animation » porte des intervalles de temps : dans Google Earth, le curseur temporel fait rouler le
véhicule à la vitesse automatique calculée.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

from .. import DEVISE, __version__
from ..calcul import Resultat
from ..enveloppe import ligne_simplifiee, simplifiee
from ..georef import Georef
from ..vitesse import KMH
from .dxf import positions_silhouettes

T0 = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

STYLES = """
  <Style id="axe"><LineStyle><color>ffd77800</color><width>2</width></LineStyle></Style>
  <Style id="enveloppe"><LineStyle><color>ff1a8ef0</color><width>2</width></LineStyle><PolyStyle><color>661a8ef0</color></PolyStyle></Style>
  <Style id="roues"><LineStyle><color>ff2e9b3b</color><width>1.5</width></LineStyle><PolyStyle><color>332e9b3b</color></PolyStyle></Style>
  <Style id="trace"><LineStyle><color>b4404040</color><width>1</width></LineStyle></Style>
  <Style id="silhouette"><LineStyle><color>ffffffff</color><width>1.2</width></LineStyle><PolyStyle><color>40ffffff</color></PolyStyle></Style>
  <Style id="anim"><LineStyle><color>ff00d7ff</color><width>2</width></LineStyle><PolyStyle><color>b300d7ff</color></PolyStyle></Style>
  <Style id="alerte"><IconStyle><color>ff2020e0</color><scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle></Style>
"""


def _c(geo: Georef, pts) -> str:
    return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in geo.lonlat(pts))


def _ligne(nom: str, style: str, coords: str, desc: str = "") -> str:
    d = f"<description>{escape(desc)}</description>" if desc else ""
    return (f"<Placemark><name>{escape(nom)}</name>{d}<styleUrl>#{style}</styleUrl>"
            f"<LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>")


def _polygone_kml(geo: Georef, poly) -> str:
    ext = _c(geo, list(poly.exterior.coords))
    trous = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{_c(geo, list(a.coords))}</coordinates></LinearRing></innerBoundaryIs>"
                    for a in poly.interiors)
    return (f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>{ext}</coordinates>"
            f"</LinearRing></outerBoundaryIs>{trous}</Polygon>")


def _zone(nom: str, style: str, geo: Georef, geom, desc: str = "") -> str:
    polys = list(getattr(geom, "geoms", [geom])) if not geom.is_empty else []
    if not polys:
        return ""
    corps = _polygone_kml(geo, polys[0]) if len(polys) == 1 else \
        "<MultiGeometry>" + "".join(_polygone_kml(geo, p) for p in polys) + "</MultiGeometry>"
    d = f"<description>{escape(desc)}</description>" if desc else ""
    return f"<Placemark><name>{escape(nom)}</name>{d}<styleUrl>#{style}</styleUrl>{corps}</Placemark>"


def _silhouette(geo: Georef, res: Resultat, i: int, caisses) -> str:
    morceaux = []
    for c in caisses:
        q = [tuple(p) for p in c[i]] + [tuple(c[i][0])]
        morceaux.append(f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>{_c(geo, q)}"
                        "</coordinates></LinearRing></outerBoundaryIs></Polygon>")
    return "<MultiGeometry>" + "".join(morceaux) + "</MultiGeometry>"


def _fiche(res: Resultat) -> str:
    m = res.mesures()
    lignes = [("Véhicule", res.vehicule.nom), ("Résultat", res.verdict()),
              ("Largeur balayée (carrosserie)", f"{m['largeur_carrosserie_max']:.2f} m"),
              ("Largeur balayée (roues)", f"{m['largeur_roues_max']:.2f} m"),
              ("Braquage maximal", f"{m['braquage_max_deg']:.1f}° ({m['braquage_max_pourcent']:.0f} %)"),
              ("Vitesse automatique", f"{m['vitesse_entree_kmh']:.0f} → {m['vitesse_min_kmh']:.1f} → {m['vitesse_sortie_kmh']:.0f} km/h"),
              ("Durée", f"{m['duree_s']:.1f} s")]
    if res.vehicule.nb_remorques:
        lignes.insert(5, ("Articulation maximale", f"{m['articulation_max_deg']:.1f}°"))
    t = "".join(f"<tr><td>{escape(a)}</td><td><b>{escape(b)}</b></td></tr>" for a, b in lignes)
    al = "".join(f"<li>{escape(a.message)}</li>" for a in res.alertes if a.niveau != "info")
    return f"<table>{t}</table>" + (f"<ul>{al}</ul>" if al else "")


def dossier_resultat(res: Resultat, geo: Georef, espacement: Optional[float] = None, animation: bool = True,
                     pas_animation: float = 0.5) -> str:
    sim, veh = res.sim, res.vehicule
    parts = [f"<Folder><name>{escape(veh.nom)}</name><description><![CDATA[{_fiche(res)}]]></description>"]
    parts.append(_ligne("Tracé (milieu de l'essieu avant)", "axe",
                        _c(geo, res.trace.echantillons(0.25) if res.trace is not None else zip(sim.x, sim.y))))
    parts.append(_zone("Zone balayée par la carrosserie", "enveloppe", geo, simplifiee(res.enveloppes.carrosserie)))
    parts.append(_zone("Zone balayée par les roues", "roues", geo, simplifiee(res.enveloppes.roues)))
    parts.append("<Folder><name>Traces des roues</name><visibility>0</visibility>")
    for nom, pts in res.enveloppes.traces_roues.items():
        parts.append(_ligne(nom, "trace", _c(geo, ligne_simplifiee(pts))).replace("<Placemark>", "<Placemark><visibility>0</visibility>", 1))
    parts.append("</Folder>")
    caisses = [sim.caisse(k) for k in range(len(veh.elements))]
    parts.append("<Folder><name>Positions du véhicule</name>")
    for i in positions_silhouettes(res, espacement):
        v = f", {sim.vitesse[i] / KMH:.0f} km/h" if sim.vitesse is not None else ""
        parts.append(f"<Placemark><name>{sim.s[i]:.0f} m{v}</name><styleUrl>#silhouette</styleUrl>"
                     f"{_silhouette(geo, res, i, caisses)}</Placemark>")
    parts.append("</Folder>")
    if animation and sim.temps is not None:
        parts.append("<Folder><name>Animation (curseur de temps de Google Earth)</name>")
        t, n = sim.temps, sim.n
        cibles = np.arange(0.0, t[-1] + pas_animation, pas_animation)
        idx = np.unique(np.clip(np.searchsorted(t, cibles), 0, n - 1))
        for j, i in enumerate(idx):
            debut = T0 + timedelta(seconds=float(t[i]))
            fin = T0 + timedelta(seconds=float(t[idx[j + 1]])) if j + 1 < len(idx) else debut + timedelta(seconds=2)
            parts.append(f"<Placemark><name>{t[i]:.1f} s</name><TimeSpan><begin>{debut:%Y-%m-%dT%H:%M:%S.%f}Z</begin>"
                         f"<end>{fin:%Y-%m-%dT%H:%M:%S.%f}Z</end></TimeSpan><styleUrl>#anim</styleUrl>"
                         f"{_silhouette(geo, res, int(i), caisses)}</Placemark>")
        parts.append("</Folder>")
    for a in res.alertes:
        if a.x is None or a.niveau == "info":
            continue
        lon, lat = geo.lonlat([(a.x, a.y)])[0]
        parts.append(f"<Placemark><name>{escape(a.niveau.capitalize())}</name><description>{escape(a.message)}</description>"
                     f"<styleUrl>#alerte</styleUrl><Point><coordinates>{lon:.8f},{lat:.8f},0</coordinates></Point></Placemark>")
    parts.append("</Folder>")
    return "".join(parts)


def contenu_kml(resultats: Union[Resultat, Sequence[Resultat]], geo: Georef, nom: str = "Épure de giration",
                extra: str = "", **options) -> str:
    if isinstance(resultats, Resultat):
        resultats = [resultats]
    desc = (f"Giration {__version__} — {escape(DEVISE)}<br/>Coordonnées : {escape(geo.nom())}. "
            "Modèle cinématique du CERTU, vitesse calculée automatiquement.")
    corps = "".join(dossier_resultat(r, geo, **options) for r in resultats)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>'
            f"<name>{escape(nom)}</name><description><![CDATA[{desc}]]></description>{STYLES}{extra}{corps}"
            "</Document>\n</kml>\n")


def exporter_kml(resultats, chemin: Union[str, Path], geo: Georef, nom: str = "Épure de giration", **options) -> Path:
    chemin = Path(chemin)
    chemin.write_text(contenu_kml(resultats, geo, nom=nom, **options), encoding="utf-8")
    return chemin
