"""Exemple géoréférencé : épures sur le giratoire RD71 / RD41 de Lombers conçu avec Girabase (fichier .gsite).

    python outils/exemple_lombers.py

Lit le site Girabase (centre, îlot R, bande franchissable Bf, anneau LA, branches), construit les trajectoires
d'entrée, d'anneau et de sortie, calcule les épures et écrit KML (avec le giratoire), GeoJSON, DXF en Lambert-93,
projet JSON et aperçu PNG dans exemples/lombers/. Préfigure la vérification automatique des mouvements (étape 4).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from giration.apercu import enregistrer_apercu  # noqa: E402
from giration.calcul import calculer  # noqa: E402
from giration.exports.dxf import exporter_dxf  # noqa: E402
from giration.exports.geojson import exporter_geojson, exporter_projet  # noqa: E402
from giration.exports.kml import exporter_kml  # noqa: E402
from giration.georef import Georef  # noqa: E402
from giration.projections import LAMBERT93  # noqa: E402
from giration.trace import Trace, norm_angle  # noqa: E402
from giration.vehicule import Bibliotheque  # noqa: E402

DOSSIER = RACINE / "exemples" / "lombers"
DEMI_VOIE = 1.75          # axe de la voie d'entrée / de sortie par rapport à l'axe de la branche
R_RACCORD = 15.0          # rayon des courbes d'entrée et de sortie (tracé de l'essieu avant)
MARGE = 0.50              # marge entre le coin avant et le bord extérieur de l'anneau


def cap_depuis_azimut(az_deg: float) -> float:
    """Azimut géographique (horaire depuis le Nord) → cap trigonométrique depuis l'Est (rad)."""
    return math.radians(90.0 - az_deg)


def mouvement(site: dict, entree: str, sortie: str, R_anneau: float, X0: float, Y0: float, approche: float = 40.0) -> Trace:
    """Entrée par la branche ``entree``, anneau à gauche (sens trigonométrique), sortie par ``sortie``.

    Coordonnées Lambert-93 ; (X0, Y0) centre du giratoire."""
    br = {b["nom"]: b for b in site["branches"]}
    az_e, az_s = br[entree]["azimut"], br[sortie]["azimut"]
    d = cap_depuis_azimut(az_e) + math.pi                      # direction d'arrivée (vers le centre)
    e = cap_depuis_azimut(az_s)                                # direction de départ (depuis le centre)
    w, Re, Rr = DEMI_VOIE, R_RACCORD, R_anneau

    def centre_raccord(direction, signe):
        ux, uy = math.cos(direction), math.sin(direction)
        gx, gy = -uy, ux                                       # normale à gauche
        a = signe * math.sqrt((Rr + Re) ** 2 - (w + Re) ** 2)
        return X0 + a * ux - (w + Re) * gx, Y0 + a * uy - (w + Re) * gy, (ux, uy), (gx, gy)

    ce = centre_raccord(d, -1)
    T1 = (ce[0] + Re * ce[3][0], ce[1] + Re * ce[3][1])          # tangence avec la voie d'entrée
    ang_e = math.atan2(ce[1] - Y0, ce[0] - X0)                   # direction du point de tangence avec l'anneau
    cs = centre_raccord(e, +1)
    ang_s = math.atan2(cs[1] - Y0, cs[0] - X0)
    # caps (sens trigonométrique sur l'anneau : tangente = rayon + 90°)
    cap_anneau_e = ang_e + math.pi / 2
    dev_e = norm_angle(cap_anneau_e - d)                         # négatif : virage à droite
    tour = (ang_s - ang_e) % (2 * math.pi)                       # angle parcouru sur l'anneau
    dev_s = norm_angle(e - (ang_s + math.pi / 2))
    x0 = T1[0] - approche * ce[2][0]
    y0 = T1[1] - approche * ce[2][1]
    t = Trace.depuis_morceaux(x0, y0, math.degrees(d), [(approche, None), ("angle", -Re, math.degrees(dev_e)),
                                                        ("angle", Rr, math.degrees(tour)), ("angle", -Re, math.degrees(dev_s)),
                                                        (approche, None)], nom=f"{entree} → {sortie}")
    # contrôle : la fin du tracé doit être sur la voie de sortie
    xf, yf, _ = t.arrivee
    lat = (xf - X0) * cs[3][0] + (yf - Y0) * cs[3][1]
    assert abs(lat + w) < 0.01, f"construction du mouvement : écart {lat + w:.3f} m"
    return t


def anneaux_kml(site: dict, geo: Georef, X0: float, Y0: float) -> str:
    def cercle(r, n=120):
        pts = [(X0 + r * math.cos(2 * math.pi * k / n), Y0 + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
        return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in geo.lonlat(pts))
    R, Bf, LA = site["R"], site["Bf"], site["LA"]
    s = ('<Style id="gira"><LineStyle><color>ff00ffff</color><width>2</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>'
         '<Folder><name>Giratoire Girabase (site .gsite)</name>')
    for nom, r in ((f"Îlot central R = {R} m", R), (f"Bande franchissable (R + Bf = {R + Bf} m)", R + Bf),
                   (f"Bord extérieur de l'anneau (Rg = {R + Bf + LA} m)", R + Bf + LA)):
        s += (f"<Placemark><name>{nom}</name><styleUrl>#gira</styleUrl><LineString><tessellate>1</tessellate>"
              f"<coordinates>{cercle(r)}</coordinates></LineString></Placemark>")
    return s + "</Folder>"


def apercu_orthophoto(resultats, geo: Georef, X0: float, Y0: float, site: dict, chemin: Path, demi: float = 38.0) -> bool:
    """Épures dessinées sur l'orthophoto IGN (Géoplateforme, WMTS) : avant-goût de l'interface de l'étape 2."""
    import io
    import time
    import urllib.error
    import urllib.request

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from PIL import Image

    from giration.enveloppe import simplifiee
    from giration.projections import coin_tuile, taille_tuile, tuile_de, vers_mercator

    def merc(pts):
        return np.array([vers_mercator(lat, lon) for lon, lat in geo.lonlat(pts)])

    coins = merc([(X0 - demi, Y0 - demi), (X0 + demi, Y0 + demi)])
    for z in (20, 19):
        tx0, ty0 = tuile_de(coins[0, 0], coins[1, 1], z)
        tx1, ty1 = tuile_de(coins[1, 0], coins[0, 1], z)
        mosaique = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        try:
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    url = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
                           f"&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={ty}&TILECOL={tx}")
                    req = urllib.request.Request(url, headers={"User-Agent": "Giration-Python"})
                    for essai in range(5):
                        try:
                            with urllib.request.urlopen(req, timeout=20) as r:
                                donnees = r.read()
                            break
                        except urllib.error.HTTPError:
                            raise
                        except Exception:
                            if essai == 4:
                                raise
                            time.sleep(1.0 + essai)
                    mosaique.paste(Image.open(io.BytesIO(donnees)).convert("RGB"), ((tx - tx0) * 256, (ty - ty0) * 256))
            break
        except Exception:
            continue
    else:
        return False
    xg, yh = coin_tuile(tx0, ty0, z)
    t = taille_tuile(z)
    etendue = (xg, xg + (tx1 - tx0 + 1) * t, yh - (ty1 - ty0 + 1) * t, yh)
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    ax.imshow(mosaique, extent=etendue, interpolation="bilinear")
    couleurs = ["#ff8c1a", "#c77dff"]
    for j, res in enumerate(resultats):
        for poly in getattr(simplifiee(res.enveloppes.carrosserie), "geoms", [simplifiee(res.enveloppes.carrosserie)]):
            m = merc(list(poly.exterior.coords))
            ax.fill(m[:, 0], m[:, 1], color=couleurs[j], alpha=0.35, lw=0)
            ax.plot(m[:, 0], m[:, 1], color=couleurs[j], lw=1.6)
        m = merc(list(zip(res.sim.x, res.sim.y))[::10])
        ax.plot(m[:, 0], m[:, 1], color="white", lw=1, ls="--")
    for r, st in ((site["R"], "-"), (site["R"] + site["Bf"], ":"), (site["R"] + site["Bf"] + site["LA"], "-")):
        a = np.linspace(0, 2 * math.pi, 121)
        m = merc(list(zip(X0 + r * np.cos(a), Y0 + r * np.sin(a))))
        ax.plot(m[:, 0], m[:, 1], color="#ffe14d", lw=1.4, ls=st)
    ax.set_xlim(coins[0, 0], coins[1, 0])
    ax.set_ylim(coins[0, 1], coins[1, 1])
    ax.set_axis_off()
    ax.text(0.01, 0.01, "© IGN – BD ORTHO® (Licence Ouverte 2.0) · Giration", transform=ax.transAxes,
            color="white", fontsize=7, bbox={"facecolor": "black", "alpha": 0.45, "lw": 0, "pad": 2})
    fig.tight_layout(pad=0)
    fig.savefig(chemin, dpi=150)
    plt.close(fig)
    return True


def main() -> None:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    site = json.loads((DOSSIER / "Giratoire D71 - D41 - Lombers.gsite").read_text(encoding="utf-8"))
    X0, Y0 = LAMBERT93.depuis_geo(site["lat"], site["lon"])
    geo = Georef("L93")
    k = geo.echelle(X0, Y0)
    Rg = site["R"] + site["Bf"] + site["LA"]
    b = Bibliotheque()
    resultats = []
    for vid, entree, sortie in (("articule-tracteur-4x2-semi-standard-1360", "D71 Nord", "D41 Est"),
                                ("agricole-tracteur-remorque-benne-2-essieux", "D41 Ouest", "D71 Sud")):
        v = b.get(vid)
        t0 = v.tracteur
        Rt = math.sqrt((Rg - MARGE) ** 2 - (t0.E + t0.PAFav) ** 2) - t0.L / 2
        R_anneau = math.hypot(Rt, t0.E)
        tr = mouvement(site, entree, sortie, R_anneau, X0, Y0)
        res = calculer(v, tr, systeme="L93", echelle=k)
        resultats.append(res)
        print(f"{v.nom} : {entree} → {sortie}, anneau R {R_anneau:.2f} m")
        print(res.rapport())
        from shapely import Point
        pc = Point(X0, Y0)
        print(f"  rayon intérieur des roues autour du centre : {res.enveloppes.roues.distance(pc):.2f} m "
              f"(îlot {site['R']} m, bande franchissable jusqu'à {site['R'] + site['Bf']} m)")
    base = DOSSIER / "Lombers - giration"
    exporter_kml(resultats, base.with_suffix(".kml"), geo, nom="Giratoire D71 – D41, Lombers : épures de giration",
                 extra=anneaux_kml(site, geo, X0, Y0))
    exporter_geojson(resultats, base.with_suffix(".geojson"), geo)
    exporter_dxf(resultats, base.with_suffix(".dxf"))
    exporter_projet(resultats, base.with_suffix(".json"), geo, source="Giratoire D71 - D41 - Lombers.gsite")

    def annot(ax):
        import matplotlib.pyplot as plt
        for r, st in ((site["R"], "-"), (site["R"] + site["Bf"], ":"), (Rg, "-")):
            ax.add_patch(plt.Circle((X0, Y0), r, fill=False, color="#b8860b", ls=st, lw=1.2))
    enregistrer_apercu(resultats, base.with_suffix(".png"), annotations=annot, taille=(7, 7),
                       cadre=(X0 - 35, Y0 - 35, X0 + 35, Y0 + 35))
    if apercu_orthophoto(resultats, geo, X0, Y0, site, DOSSIER / "Lombers - giration sur orthophoto.png"):
        print("Aperçu sur orthophoto IGN écrit.")


if __name__ == "__main__":
    main()
