"""Ligne de commande de Giration.

Usage le plus simple (Windows) : glisser un DXF sur giration.exe. Le programme demande le véhicule et
écrit à côté du DXF une copie du dessin avec l'épure, un KML, un GeoJSON, un projet JSON et un compte rendu.

Commandes :
  giration                                   mode guidé (demande le fichier)
  giration PLAN.dxf                          mode guidé sur ce fichier
  giration vehicules [mot]                   liste de la bibliothèque
  giration vehicule ID                       fiche d'un véhicule
  giration calculer PLAN.dxf -v ID [...]     calcul sans question (voir --help)
  giration valider                           contrôles de précision du moteur
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from . import DEVISE, __version__


def _console_utf8() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _bibliotheque():
    from .vehicule import Bibliotheque
    return Bibliotheque()


# ---------------------------------------------------------------------- traitement d'un DXF
def traiter_dxf(chemin: Path, vehicules: list, calque: Optional[str] = None, rayon_raccord: Optional[float] = None,
                systeme: Optional[str] = "auto", origine: Optional[tuple[float, float]] = None, style: str = "normal",
                sortie: Optional[Path] = None, inverser: bool = False, indices: Optional[list[int]] = None,
                formats: tuple = ("dxf", "kml", "geojson", "json", "txt"), journal=print) -> list:
    from .calcul import calculer
    from .exports.dxf import exporter_dxf
    from .exports.geojson import exporter_geojson, exporter_projet
    from .exports.kml import exporter_kml
    from .georef import Georef
    from .lecture_dxf import UNITES, lire_dxf
    from .projections import detecter_systeme
    from .trace import Trace

    dessin = lire_dxf(chemin)
    if dessin.unites in (1, 2, 4, 5, 14):
        raise ValueError(f"Le dessin est en {UNITES[dessin.unites]} : Giration attend un plan en mètres "
                         "(commande UNITES d'AutoCAD, puis MISE À L'ÉCHELLE).")
    polys = dessin.candidates(calque)
    if not polys:
        raise ValueError("Aucune polyligne ouverte trouvée" + (f" sur le calque « {calque} »." if calque else ".")
                         + " Dessinez le trajet du milieu de l'essieu avant en polyligne (calque GIR_AXE conseillé).")
    if indices:
        polys = [polys[i] for i in indices if 0 <= i < len(polys)]
    x0, y0 = polys[0].points[0]
    if systeme == "auto":
        systeme = detecter_systeme(x0, y0)
    geo = Georef(systeme, origine if not systeme else None)
    journal(f"Coordonnées : {geo.nom()}")
    resultats = []
    for n, p in enumerate(polys, 1):
        for veh in vehicules:
            R = rayon_raccord
            trace = Trace.depuis_polyligne(p.points, p.renflements, rayon_raccord=R, nom=f"{p.calque} #{p.poignee}")
            if inverser:
                trace = trace.inverse()
            echelle = geo.echelle(x0, y0)
            res = calculer(veh, trace, style=style, echelle=echelle, systeme=systeme)
            resultats.append(res)
            journal("")
            journal(f"=== Tracé {n} (calque {p.calque}, {trace.longueur:.1f} m) — {veh.nom}")
            journal(res.rapport())
    if not resultats:
        return []
    dossier = Path(sortie) if sortie else chemin.parent
    dossier.mkdir(parents=True, exist_ok=True)
    base = dossier / f"{chemin.stem} - giration"
    ecrits = []
    if "dxf" in formats:
        fond = chemin if dessin.version >= "AC1015" else None
        exporter_dxf(resultats, base.with_suffix(".dxf"), fond=fond)
        ecrits.append(base.with_suffix(".dxf"))
    if geo.georeference:
        if "kml" in formats:
            exporter_kml(resultats, base.with_suffix(".kml"), geo, nom=f"{chemin.stem} — giration")
            ecrits.append(base.with_suffix(".kml"))
        if "geojson" in formats:
            exporter_geojson(resultats, base.with_suffix(".geojson"), geo)
            ecrits.append(base.with_suffix(".geojson"))
    else:
        journal("\nPlan local non géoréférencé : pas de KML ni de GeoJSON (donnez --origine lat,lon pour en obtenir).")
    if "json" in formats:
        exporter_projet(resultats, base.with_suffix(".json"), geo if geo.georeference else None, source=str(chemin.name))
        ecrits.append(base.with_suffix(".json"))
    if "txt" in formats:
        lignes = [f"Giration {__version__} — {DEVISE}", f"Plan : {chemin.name}", f"Coordonnées : {geo.nom()}", ""]
        for r in resultats:
            lignes += [f"Tracé : {r.trace.nom}", r.trace.decrire(), r.rapport(), ""]
        base.with_suffix(".txt").write_text("\n".join(lignes), encoding="utf-8")
        ecrits.append(base.with_suffix(".txt"))
    journal("\nFichiers écrits :")
    for f in ecrits:
        journal(f"  {f}")
    return resultats


# ---------------------------------------------------------------------- mode guidé
def _demander(question: str, defaut: str = "") -> str:
    try:
        r = input(question).strip()
    except EOFError:
        return defaut
    return r or defaut


def _choisir_vehicules(biblio) -> list:
    familles = biblio.familles()
    numeros = []
    print("\nVéhicules disponibles :")
    for fam, vs in familles.items():
        print(f"\n  {fam}")
        for v in vs:
            numeros.append(v)
            print(f"   {len(numeros):3d}. {v.nom}")
    while True:
        r = _demander("\nNuméro du véhicule (plusieurs : 15,22), ou un mot pour chercher (semi, bus, BOM…) : ")
        if not r:
            continue
        morceaux = [m.strip() for m in r.replace(";", ",").split(",") if m.strip()]
        if all(m.isdigit() for m in morceaux):
            choix = [numeros[int(m) - 1] for m in morceaux if 1 <= int(m) <= len(numeros)]
            if choix:
                return choix
            print("  Numéro hors liste.")
            continue
        trouves = biblio.chercher(r)
        if not trouves:
            print("  Aucun véhicule ne correspond.")
            continue
        for v in trouves:
            print(f"   {numeros.index(v) + 1:3d}. {v.nom}")


def mode_guide(chemin: Optional[str] = None) -> int:
    from .lecture_dxf import lire_dxf
    from .projections import detecter_systeme
    from .trace import Trace

    print(f"Giration {__version__} — épures de giration")
    print(DEVISE)
    print("Le trajet est celui du milieu de l'essieu avant, dessiné en polyligne (droites et arcs) dans AutoCAD.\n")
    if not chemin:
        chemin = _demander("Fichier DXF (glissez-le dans cette fenêtre puis Entrée) : ").strip('"').strip("'")
    p = Path(chemin)
    if not p.exists():
        print(f"Fichier introuvable : {p}")
        return 2
    dessin = lire_dxf(p)
    polys = dessin.candidates()
    if not polys:
        print("Aucune polyligne ouverte dans ce dessin. Dessinez le trajet sur le calque GIR_AXE.")
        return 2
    print(f"{len(polys)} tracé(s) trouvé(s) :")
    for i, q in enumerate(polys, 1):
        print(f"  {i}. calque {q.calque}, {len(q.points)} sommets, {q.longueur_approx:.1f} m environ")
    indices = None
    if len(polys) > 1:
        r = _demander("Numéros des tracés à calculer (Entrée = tous) : ")
        if r:
            indices = [int(m) - 1 for m in r.replace(";", ",").split(",") if m.strip().isdigit()]
    biblio = _bibliotheque()
    vehicules = _choisir_vehicules(biblio)
    # angles vifs ?
    rayon = None
    sel = polys if indices is None else [polys[i] for i in indices if 0 <= i < len(polys)]
    vifs = False
    for q in sel:
        try:
            Trace.depuis_polyligne(q.points, q.renflements)
        except ValueError:
            vifs = True
    if vifs:
        r_min = max(v.rayon_P_min() for v in vehicules)
        defaut = math.ceil(r_min * 1.15 * 2) / 2
        r = _demander(f"Le tracé a des angles vifs. Rayon de raccord en mètres (Entrée = {defaut:g} ; minimum du "
                      f"véhicule {r_min:.2f}) : ", str(defaut))
        rayon = float(r.replace(",", "."))
    x0, y0 = sel[0].points[0]
    systeme = detecter_systeme(x0, y0)
    origine = None
    if systeme is None:
        r = _demander("Plan local (ni Lambert-93 ni CC). Pour obtenir un KML, collez les coordonnées GPS du point (0 ; 0) "
                      "du plan, ex. 43.8161, 2.1698 (Entrée pour passer) : ")
        if r:
            from .projections import analyser_saisie
            a = analyser_saisie(r)
            if a:
                origine = (a[0], a[1])
    style = {"1": "prudent", "2": "normal", "3": "expérimenté"}.get(
        _demander("Conduite : 1 prudente, 2 normale, 3 expérimentée (Entrée = normale) : ", "2"), "normal")
    traiter_dxf(p, vehicules, rayon_raccord=rayon, systeme=systeme, origine=origine, style=style, indices=indices)
    return 0


# ---------------------------------------------------------------------- commandes
def cmd_vehicules(args) -> int:
    b = _bibliotheque()
    vs = b.chercher(" ".join(args.mot)) if args.mot else list(b)
    fam = None
    for v in vs:
        if v.categorie != fam:
            fam = v.categorie
            print(f"\n{fam}")
        print(f"  {v.id:48s} {v.nom}")
    print(f"\n{len(vs)} véhicule(s) — {b.titre}, version {b.version}")
    return 0


def cmd_vehicule(args) -> int:
    from .vehicule import rayons_permanents
    v = _bibliotheque().get(args.id)
    print(v.description())
    print(f"  rayon minimal du tracé (milieu de l'essieu avant) : {v.rayon_P_min():.2f} m")
    rp = rayons_permanents(v, v.alpha_max_permanent or v.alpha_max)
    print(f"  régime permanent au braquage maximal : rayon extérieur (caisse) {max(r.ext_murs for r in rp):.2f} m, "
          f"rayon intérieur (roues) {min(r.int_trottoirs for r in rp):.2f} m")
    if v.notes:
        print("  Notes : " + " / ".join(v.notes))
    return 0


def cmd_calculer(args) -> int:
    b = _bibliotheque()
    vehicules = [b.get(i) for i in args.vehicule]
    origine = None
    if args.origine:
        la, lo = (float(t) for t in args.origine.replace(";", ",").split(","))
        origine = (la, lo)
    systeme = None if args.systeme == "local" else args.systeme
    traiter_dxf(Path(args.dxf), vehicules, calque=args.calque, rayon_raccord=args.raccord, systeme=systeme,
                origine=origine, style=args.conduite, sortie=Path(args.sortie) if args.sortie else None,
                inverser=args.inverser)
    return 0


def cmd_valider(args) -> int:
    from .validation import executer
    ok, texte = executer()
    print(texte)
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    _console_utf8()
    argv = sys.argv[1:] if argv is None else argv
    guide = not argv or (len(argv) == 1 and argv[0].lower().endswith(".dxf"))
    if guide:
        try:
            return mode_guide(argv[0] if argv else None)
        except Exception as exc:                       # fenêtre console : on montre l'erreur avant de fermer
            print(f"\nErreur : {exc}")
            if os.environ.get("GIRATION_DEBUG"):
                traceback.print_exc()
            return 1
        finally:
            if getattr(sys, "frozen", False):
                _demander("\nAppuyez sur Entrée pour fermer.")
    ap = argparse.ArgumentParser(prog="giration", description=f"Giration {__version__} — épures de giration.")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("vehicules", help="liste de la bibliothèque")
    a.add_argument("mot", nargs="*")
    a.set_defaults(f=cmd_vehicules)
    a = sp.add_parser("vehicule", help="fiche d'un véhicule")
    a.add_argument("id")
    a.set_defaults(f=cmd_vehicule)
    a = sp.add_parser("calculer", help="épure(s) d'un tracé DXF")
    a.add_argument("dxf")
    a.add_argument("-v", "--vehicule", action="append", required=True, help="identifiant (répétable)")
    a.add_argument("--calque", help="calque des tracés (défaut : calques contenant GIR ou AXE)")
    a.add_argument("--raccord", type=float, help="rayon de raccord des angles vifs (m)")
    a.add_argument("--systeme", default="auto", help="L93, CC42…CC50, local ou auto (défaut)")
    a.add_argument("--origine", help="plan local : lat,lon du point (0 ; 0)")
    a.add_argument("--conduite", default="normal", choices=["prudent", "normal", "expérimenté"])
    a.add_argument("--sortie", help="dossier de sortie (défaut : celui du DXF)")
    a.add_argument("--inverser", action="store_true", help="parcourir la polyligne dans l'autre sens")
    a.set_defaults(f=cmd_calculer)
    a = sp.add_parser("valider", help="contrôles de précision du moteur")
    a.set_defaults(f=cmd_valider)
    args = ap.parse_args(argv)
    try:
        return args.f(args)
    except (ValueError, KeyError) as exc:
        print(f"Erreur : {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
