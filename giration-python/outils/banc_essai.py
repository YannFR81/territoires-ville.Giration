"""Banc d'essai Giration / COVADIS : tracés de référence, épures calculées, mesures et fiche PDF.

    python outils/banc_essai.py [dossier de sortie]

Produit, dans le dossier (par défaut exemples/banc-essai-covadis) :
- banc-essai-traces.dxf : les tracés seuls (calque GIR_AXE), avec repères et étiquettes, à donner à COVADIS ;
- banc-essai-giration-python.dxf : les mêmes avec les épures de Giration (calques GIR_<véhicule>_…) ;
- mesures.json et les aperçus PNG ;
- Fiche-banc-essai-COVADIS.html (et .pdf si Chromium est disponible).
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf.enums import TextEntityAlignment
from shapely import Point, Polygon

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from giration import DEVISE, __version__  # noqa: E402
from giration.apercu import enregistrer_apercu  # noqa: E402
from giration.calcul import calculer  # noqa: E402
from giration.exports.dxf import exporter_dxf  # noqa: E402
from giration.trace import Trace, Troncon  # noqa: E402
from giration.validation import executer  # noqa: E402
from giration.vehicule import Bibliotheque, rayons_permanents  # noqa: E402

VEHICULES = ["certu-camion-semi-remorque", "certu-bus"]
ESPACE_X, ESPACE_Y = 150.0, 150.0


@dataclass
class Cas:
    code: str
    titre: str
    description: str


CAS = [
    Cas("1", "Virage à droite à 90°", "Droite de 30 m, arc à droite de 12 m de rayon sur 90°, droite de 30 m."),
    Cas("2", "Traversée de giratoire (tout droit)", "Droite de 30 m, entrée à droite R 15 m sur 60°, anneau à gauche "
                                                   "R 12 m sur 120° (îlot à gauche), sortie à droite R 15 m sur 60°, droite "
                                                   "de 30 m. Le repère est le centre de l'anneau : le rayon intérieur des "
                                                   "roues est le rayon maximal de l'îlot (bande franchissable comprise), le "
                                                   "rayon extérieur de la carrosserie le rayon minimal du bord extérieur de "
                                                   "l'anneau, marges non comprises."),
    Cas("3", "Couronne de 12,50 m (régime permanent)", "Droite de 25 m puis cercle à gauche parcouru sur 720° (deux tours). "
                                                       "Rayon du cercle calculé pour que le coin avant extérieur décrive "
                                                       "12,50 m une fois le régime permanent atteint ; rayons intérieurs "
                                                       "mesurés sur le dernier demi-tour."),
    Cas("4", "Demi-tour au braquage maximal", "Droite de 30 m, demi-tour à gauche au rayon minimal du véhicule, "
                                              "droite de 25 m."),
]


def _decaler(t: Trace, dx: float, dy: float) -> Trace:
    for tr in t.troncons:
        tr.x0 += dx
        tr.y0 += dy
    return t


def rayon_couronne(veh, R_ext=12.5) -> float:
    """Rayon du tracé du milieu de l'essieu avant donnant un coin avant extérieur à R_ext en régime permanent."""
    t = veh.tracteur
    Rt = math.sqrt(R_ext ** 2 - (t.E + t.PAFav) ** 2) - t.L / 2
    return math.hypot(Rt, t.E)


def trace_cas(code: str, veh, ox: float, oy: float) -> tuple[Trace, tuple[float, float]]:
    """Tracé du cas et son repère (centre de l'arc principal), dans le repère local décalé de (ox, oy)."""
    if code == "1":
        t = Trace.depuis_morceaux(-30, 0, 0, [(30, None), ("angle", -12, -90), (30, None)])
        centre = (0.0, -12.0)
    elif code == "2":
        t = Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -15, -60), ("angle", 12, 120), ("angle", -15, -60), (30, None)])
        c = t.troncons[2].centre
        t = _decaler(t, -c[0], -c[1])
        centre = (0.0, 0.0)
    elif code == "3":
        R = rayon_couronne(veh)
        t = Trace.depuis_morceaux(-25, -R, 0, [(25, None), ("angle", R, 720)])
        centre = (0.0, 0.0)
    else:
        R = veh.rayon_P_min() * 1.001
        t = Trace.depuis_morceaux(-30, -R, 0, [(30, None), ("angle", R, 180), (25, None)])
        centre = (0.0, 0.0)
    _decaler(t, ox, oy)
    t.nom = f"Cas {code}"
    return t, (centre[0] + ox, centre[1] + oy)


def mesurer(code: str, res, centre) -> dict:
    sim, veh = res.sim, res.vehicule
    cx, cy = centre
    car, roues = res.enveloppes.carrosserie, res.enveloppes.roues
    m = res.mesures()
    out = {"largeur_balayee_carrosserie": m["largeur_carrosserie_max"], "largeur_balayee_roues": m["largeur_roues_max"],
           "braquage_max_pct": m["braquage_max_pourcent"], "vitesse_min_kmh": m["vitesse_min_kmh"]}
    if veh.nb_remorques:
        out["articulation_max_deg"] = m["articulation_max_deg"]
    pc = Point(cx, cy)
    if code in ("1", "2"):
        tr = res.trace.troncons
        # secteur du virage principal : directions radiales du début et de la fin de l'arc principal
        arc = tr[1] if code == "1" else tr[2]
        a0 = math.atan2(arc.y0 - cy, arc.x0 - cx)
        xf, yf, _ = arc.fin
        a1 = math.atan2(yf - cy, xf - cx)
        def dans_secteur(x, y):
            a = math.atan2(y - cy, x - cx)
            d0 = (a - a0) % (2 * math.pi)
            return d0 <= (a1 - a0) % (2 * math.pi) if code == "2" else ((a0 - a) % (2 * math.pi) <= (a0 - a1) % (2 * math.pi))
        pts = [p for g in getattr(car, "geoms", [car]) for p in g.exterior.coords if dans_secteur(*p)]
        out["rayon_exterieur_carrosserie"] = max(math.dist(p, centre) for p in pts)
        out["rayon_interieur_roues"] = roues.distance(pc) if not roues.contains(pc) else 0.0
        out["rayon_interieur_carrosserie"] = car.distance(pc) if not car.contains(pc) else 0.0
    if code == "3":
        # dernier demi-tour : régime permanent atteint (le braquage converge en un tour et demi environ)
        R = res.trace.troncons[-1].rayon
        s_debut = sim.s[-1] - math.pi * R
        idx = np.where(sim.s >= s_debut)[0]
        ext = max(math.dist(tuple(c[i, j]), centre) for c in (sim.caisse(k) for k in range(len(veh.elements)))
                  for i in idx for j in range(4))
        int_c = min(Polygon(sim.caisse(k)[i]).distance(pc) for k in range(len(veh.elements)) for i in idx[::2])
        int_r = min(float(np.min(np.hypot(*(c[idx] + sg * LE / 2 * n[idx] - np.array(centre)).T)))
                    for _, c, n, LE, _ in sim.essieux() for sg in (1, -1))
        theo = rayons_permanents(veh, math.asin(veh.tracteur.E / R))
        out.update({"rayon_trace": R, "rayon_exterieur_carrosserie": ext, "rayon_interieur_carrosserie": int_c,
                    "rayon_interieur_roues": int_r, "rayon_interieur_carrosserie_theorique": min(r.int_murs for r in theo),
                    "rayon_interieur_roues_theorique": min(r.int_trottoirs for r in theo)})
    if code == "4":
        x_arc = res.trace.troncons[1].x0
        b = car.bounds
        out.update({"rayon_trace": res.trace.troncons[1].rayon, "largeur_demi_tour": b[3] - b[1],
                    "debord_au_dela_du_virage": b[2] - x_arc})
    return {k: round(float(v), 3) for k, v in out.items()}


LIBELLES = {
    "largeur_balayee_carrosserie": "Largeur balayée maximale, carrosserie (m)",
    "largeur_balayee_roues": "Largeur balayée maximale, roues (m)",
    "rayon_exterieur_carrosserie": "Rayon extérieur de la carrosserie autour du repère (m)",
    "rayon_interieur_carrosserie": "Rayon intérieur de la carrosserie autour du repère (m)",
    "rayon_interieur_roues": "Rayon intérieur des roues autour du repère (m)",
    "rayon_interieur_carrosserie_theorique": "  (formule CERTU, régime permanent)",
    "rayon_interieur_roues_theorique": "  (formule CERTU, régime permanent)",
    "rayon_trace": "Rayon du tracé de l'essieu avant (m)",
    "largeur_demi_tour": "Largeur occupée perpendiculairement à l'approche (m)",
    "debord_au_dela_du_virage": "Débord au-delà du début du demi-tour (m)",
    "braquage_max_pct": "Braquage maximal atteint (% du maximum)",
    "articulation_max_deg": "Articulation maximale (°)",
    "vitesse_min_kmh": "Vitesse automatique minimale (km/h)",
}


def dxf_traces(chemin: Path, epures: list) -> None:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.layers.add("GIR_AXE", color=5)
    doc.layers.add("BANC_REPERES", color=1)
    doc.layers.add("BANC_TEXTES", color=7)
    msp = doc.modelspace()
    for e in epures:
        pts, bul = e["trace"].sommets_bulges()
        msp.add_lwpolyline([(x, y, 0, 0, b) for (x, y), b in zip(pts, bul)], format="xyseb", dxfattribs={"layer": "GIR_AXE"})
        cx, cy = e["centre"]
        msp.add_point((cx, cy), dxfattribs={"layer": "BANC_REPERES"})
        msp.add_line((cx - 1, cy), (cx + 1, cy), dxfattribs={"layer": "BANC_REPERES"})
        msp.add_line((cx, cy - 1), (cx, cy + 1), dxfattribs={"layer": "BANC_REPERES"})
        t = msp.add_text(f"Repère cas {e['cas'].code}", height=0.5, dxfattribs={"layer": "BANC_REPERES"})
        t.set_placement((cx + 1.2, cy + 0.3), align=TextEntityAlignment.BOTTOM_LEFT)
        x0, y0, _ = e["trace"].depart
        t = msp.add_text(f"Cas {e['cas'].code} - {e['cas'].titre} - {e['vehicule'].nom.split(' — ')[0]}", height=1.2,
                         dxfattribs={"layer": "BANC_TEXTES"})
        t.set_placement((x0, y0 + 25), align=TextEntityAlignment.BOTTOM_LEFT)
        t = msp.add_text("départ", height=0.6, dxfattribs={"layer": "BANC_TEXTES"})
        t.set_placement((x0, y0 + 1.0), align=TextEntityAlignment.BOTTOM_LEFT)
    doc.saveas(str(chemin))


def fr(x, nd: int = 3, signe: bool = False) -> str:
    """Nombre à la française (virgule décimale, vrai signe moins)."""
    if x is None or x == "—":
        return "—"
    t = f"{x:+.{nd}f}" if signe else f"{x:.{nd}f}"
    return t.replace("-", "−").replace(".", ",")


def table_vehicule(v) -> str:
    lignes = []
    for k, e in enumerate(v.elements):
        role = "Tracteur" if k == 0 else ("Semi-remorque" if v.elements[k - 1].D < 0 else f"Remorque {k}")
        lignes.append(f"<tr><td>{role}</td><td>{fr(e.PAFav)}</td><td>{fr(e.E)}</td><td>{fr(e.PAFar)}</td><td>{fr(e.L)}</td>"
                      f"<td>{fr(e.LEav) if k == 0 and e.LEav is not None else '—'}</td><td>{fr(e.LEar)}</td>"
                      f"<td>{fr(e.D, signe=True) if e.D is not None else '—'}</td></tr>")
    return ("<table class='t'><tr><th>Élément</th><th>Porte-à-faux avant</th><th>Empattement</th><th>Porte-à-faux arrière</th>"
            "<th>Largeur caisse</th><th>Largeur essieu avant</th><th>Largeur essieu arrière</th><th>Attelage D</th></tr>"
            + "".join(lignes) + "</table>")


def main(sortie: Path) -> None:
    sortie.mkdir(parents=True, exist_ok=True)
    b = Bibliotheque()
    epures, resultats = [], []
    for i, vid in enumerate(VEHICULES):
        v = b.get(vid)
        for j, cas in enumerate(CAS):
            trace, centre = trace_cas(cas.code, v, j * ESPACE_X, -i * ESPACE_Y)
            res = calculer(v, trace)
            mes = mesurer(cas.code, res, centre)
            png = sortie / f"cas{cas.code}-{vid}.png"

            def annot(ax, centre=centre):
                ax.plot(*centre, "+", color="#c62828", ms=12, mew=1.5)
            from shapely import box
            zone = res.enveloppes.carrosserie.intersection(box(centre[0] - 30, centre[1] - 30, centre[0] + 30, centre[1] + 30))
            bx = zone.bounds
            enregistrer_apercu(res, png, annotations=annot, taille=(6.0, 5.0),
                               cadre=(bx[0] - 2, bx[1] - 2, bx[2] + 2, bx[3] + 2))
            epures.append({"cas": cas, "vehicule": v, "trace": trace, "centre": centre, "res": res, "mesures": mes, "png": png})
            resultats.append(res)
            print(f"cas {cas.code} {vid}: {res.verdict()} {mes}")
    dxf_traces(sortie / "banc-essai-traces.dxf", epures)
    exporter_dxf(resultats, sortie / "banc-essai-giration-python.dxf", fond=sortie / "banc-essai-traces.dxf")
    (sortie / "mesures.json").write_text(json.dumps(
        [{"cas": e["cas"].code, "titre": e["cas"].titre, "vehicule": e["vehicule"].id, "repere": e["centre"],
          "mesures": e["mesures"], "verdict": e["res"].verdict(),
          "polyligne": [[round(x, 4), round(y, 4), round(bb, 8)] for (x, y), bb in zip(*e["trace"].sommets_bulges())]}
         for e in epures], ensure_ascii=False, indent=1), encoding="utf-8")
    ok, texte_validation = executer()
    fiche(sortie, epures, b, texte_validation, ok)


def fiche(sortie: Path, epures: list, b: Bibliotheque, texte_validation: str, ok: bool) -> None:
    css = """
    @page { size: A4; margin: 14mm 13mm; }
    body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 9.5pt; color: #1b1b1b; line-height: 1.38; }
    h1 { font-size: 17pt; margin: 0 0 2mm; color: #12385f; } h2 { font-size: 12.5pt; color: #12385f; margin: 6mm 0 2mm;
    border-bottom: 1px solid #c9d3de; padding-bottom: 1mm; } h3 { font-size: 10.5pt; margin: 4mm 0 1.5mm; }
    .devise { color: #5a6b7b; font-style: italic; margin-bottom: 4mm; }
    table.t { border-collapse: collapse; width: 100%; margin: 2mm 0; font-size: 8.6pt; }
    table.t th, table.t td { border: 1px solid #b9c4cf; padding: 1.2mm 1.6mm; text-align: center; }
    table.t th { background: #eef2f6; } table.t td:first-child { text-align: left; }
    td.vide { background: #fffdf2; min-width: 18mm; }
    .cas { margin-bottom: 4mm; } .grille { display: flex; gap: 3mm; } .grille figure { flex: 1; margin: 0; }
    .grille img { width: 100%; height: 66mm; object-fit: contain; border: 1px solid #e1e6ec; }
    figcaption { font-size: 8pt; color: #4a5866; text-align: center; }
    .note { background: #f4f7fa; border-left: 3px solid #12385f; padding: 2mm 3mm; margin: 2mm 0; }
    pre { font-size: 7.2pt; white-space: pre-wrap; background: #f7f7f7; padding: 2mm; }
    .saut { page-break-before: always; }
    """
    vehs = []
    for vid in VEHICULES:
        v = b.get(vid)
        vehs.append(f"<h3>{escape(v.nom)}</h3>{table_vehicule(v)}<p>Longueur {fr(v.longueur_totale, 2)} m, largeur "
                    f"{fr(v.largeur, 2)} m. Braquage maximal {fr(math.degrees(v.alpha_max), 2)}° (rayon intérieur entre "
                    f"trottoirs du tracteur {fr(v.RVehMin)} m). Vitesse de braquage {math.degrees(v.vitesse_braquage):.0f} °/s. "
                    f"Rayon minimal du tracé de l'essieu avant {fr(v.rayon_P_min())} m.</p>")
    blocs = []
    for cas in CAS:
        ep = [e for e in epures if e["cas"].code == cas.code]
        lignes = []
        cles = [k for k in LIBELLES if any(k in e["mesures"] for e in ep)]
        for k in cles:
            nd = 1 if k.endswith(("_pct", "_deg", "_kmh")) else 2
            cellules = "".join(f"<td>{fr(e['mesures'].get(k, '—'), nd)}</td><td class='vide'></td>" for e in ep)
            lignes.append(f"<tr><td>{escape(LIBELLES[k])}</td>{cellules}</tr>")
        entetes = "".join(f"<th>{escape(e['vehicule'].nom.split(' — ')[0].replace(' (CERTU)', ''))}<br/>Giration</th>"
                          f"<th>COVADIS</th>" for e in ep)
        reperes = ", ".join(f"{e['vehicule'].nom.split(' (')[0]} : X = {fr(e['centre'][0])} ; Y = {fr(e['centre'][1])}" for e in ep)
        images = "<div class='grille'>" + "".join(
            f"<figure><img src='{e['png'].name}'/><figcaption>{escape(e['vehicule'].nom.split(' — ')[0])}</figcaption></figure>"
            for e in ep) + "</div>"
        blocs.append(f"<div class='cas saut'><h2>Cas {cas.code} — {escape(cas.titre)}</h2><p>{escape(cas.description)}</p>"
                     f"<p>Repère (centre de mesure) : {reperes}.</p>{images}"
                     f"<table class='t'><tr><th>Mesure</th>{entetes}</tr>{''.join(lignes)}</table></div>")
    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Banc d'essai Giration / COVADIS</title>
<style>{css}</style></head><body>
<h1>Banc d'essai : Giration face à COVADIS</h1>
<div class="devise">Giration {__version__} — {date.today():%d/%m/%Y} — {escape(DEVISE)}</div>
<h2>But et méthode</h2>
<p>Quatre manœuvres types, pour deux véhicules du guide Giration du CERTU (semi-remorque et bus de 12 m), sont calculées par
Giration. Il s'agit de refaire les mêmes manœuvres dans COVADIS et de comparer les mesures dans les cases jaunes.</p>
<ol>
<li>Ouvrir <b>banc-essai-traces.dxf</b> dans AutoCAD / COVADIS. Les tracés à suivre sont sur le calque <b>GIR_AXE</b>, un par cas
et par véhicule (ligne du haut : semi-remorque, ligne du bas : bus). Ils représentent la trajectoire du <b>milieu de l'essieu
avant</b>. Une croix rouge (calque BANC_REPERES) marque le centre autour duquel mesurer les rayons.</li>
<li>Dans COVADIS, créer ou choisir le véhicule avec les dimensions ci-dessous, puis lancer l'épure de giration en suivant la
polyligne, à la <b>vitesse la plus basse proposée (5 km/h si possible)</b> et avec une vitesse de braquage de 15 °/s si le
paramètre existe (6 s de butée à butée environ).</li>
<li>Mesurer dans AutoCAD (commandes DIST ou MESUREGEOM) les mêmes grandeurs et les noter dans les cases jaunes.</li>
<li>Pour superposer : <b>banc-essai-giration-python.dxf</b> contient les mêmes tracés avec les épures de Giration
(calques GIR_C-CAMION-SEMI-REMORQ_… et GIR_C-BUS_…, que l'on peut geler). On peut aussi y lancer COVADIS directement.</li>
</ol>
<div class="note"><b>À vérifier dans COVADIS :</b> quel point du véhicule suit la polyligne (Giration et Giration du CERTU
font suivre le milieu de l'essieu avant) ; si COVADIS dévie du tracé quand la vitesse augmente (Giration suit le tracé
exactement et calcule la vitesse possible : c'est la différence de fond entre les deux logiciels) ; et si les dimensions de son
véhicule « semi-remorque » ou « bus » sont celles du guide CERTU (sinon, utiliser les valeurs ci-dessous).</div>
<p>Rappel : pour un même tracé suivi à basse vitesse, les deux logiciels résolvent les mêmes équations (modèle cinématique sans
glissement). Des écarts de quelques centimètres sont normaux (dessin des roues, définition des largeurs) ; des écarts de plus de
10 cm indiqueraient une différence de modèle ou de données, à comprendre avant d'aller plus loin.</p>
<h2>Véhicules (modèle du CERTU, en mètres)</h2>
{''.join(vehs)}
<p>D est la position de la sellette par rapport à l'essieu arrière du tracteur (négative : devant l'essieu). Pour la
semi-remorque, le porte-à-faux avant et l'empattement sont comptés depuis le pivot.</p>
{''.join(blocs)}
<div class="saut"><h2>Contrôles de précision du moteur</h2>
<p>{'Tous les contrôles sont satisfaits.' if ok else 'ATTENTION : au moins un contrôle échoue.'} Détail (commande
<code>giration valider</code>) :</p><pre>{escape(texte_validation)}</pre></div>
</body></html>"""
    chemin = sortie / "Fiche-banc-essai-COVADIS.html"
    chemin.write_text(html, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            nav = p.chromium.launch()
            page = nav.new_page()
            page.goto(chemin.resolve().as_uri())
            page.pdf(path=str(sortie / "Fiche-banc-essai-COVADIS.pdf"), format="A4", print_background=True,
                     margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
                     display_header_footer=True, header_template="<span></span>",
                     footer_template="<div style='font-size:7pt;width:100%;text-align:center;color:#777'>"
                                     "Banc d'essai Giration / COVADIS — page <span class='pageNumber'></span> / "
                                     "<span class='totalPages'></span></div>")
            nav.close()
    except Exception as exc:                     # Chromium absent : la fiche HTML reste utilisable
        print(f"PDF non produit : {exc}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RACINE / "exemples" / "banc-essai-covadis")
