"""Lancement de l'application graphique Giration.

L'exécutable Windows est livré en deux zips (taille limite des pièces jointes) : la partie 1 « programme » contient
Giration.exe, Python et Qt ; la partie 2 « calcul » contient numpy, shapely et ezdxf. Si la partie 2 manque, Giration
la cherche (dossier du programme, Téléchargements, Bureau) et l'installe elle-même ; sinon il explique quoi faire.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

PAQUETS_CALCUL = ("numpy", "numpy.libs", "shapely", "Shapely.libs", "ezdxf", "fontTools", "pyparsing")
MOTIF_PARTIE_2 = "Giration-*-Windows-2-calcul*.zip"


def _journal_plantage(exc_type, exc, tb) -> None:
    texte = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        dossier = Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp")
        (dossier / "giration_plantage.txt").write_text(texte, encoding="utf-8")
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc, tb)


def _paquets_manquants() -> list[str]:
    base = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not base:
        return []
    return [p for p in PAQUETS_CALCUL if not (Path(base) / p).exists()]


def _chercher_partie_2() -> list[Path]:
    exe = Path(sys.executable).resolve().parent
    accueil = Path.home()
    dossiers = [exe, exe.parent, accueil / "Downloads", accueil / "Téléchargements", accueil / "Desktop",
                accueil / "Bureau", accueil / "OneDrive" / "Bureau", accueil / "OneDrive" / "Desktop"]
    trouves = []
    for d in dossiers:
        try:
            trouves += sorted(d.glob(MOTIF_PARTIE_2), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
    return trouves


def _installer_partie_2(zip_chemin: Path) -> None:
    """Extrait la partie 2 à côté de Giration.exe (le dossier racine du zip est retiré)."""
    import zipfile
    cible = Path(sys.executable).resolve().parent
    with zipfile.ZipFile(zip_chemin) as z:
        for info in z.infolist():
            morceaux = info.filename.split("/", 1)
            if len(morceaux) < 2 or not morceaux[1] or info.is_dir():
                continue
            dest = cible / morceaux[1]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as dst:
                dst.write(src.read())
    import importlib
    importlib.invalidate_caches()


def _verifier_installation(app) -> bool:
    """Partie 2 présente, ou installée à l'instant ; sinon explication et arrêt."""
    from PySide6.QtWidgets import QMessageBox
    if not _paquets_manquants():
        return True
    for z in _chercher_partie_2():
        try:
            _installer_partie_2(z)
        except (OSError, Exception) as exc:              # droits d'écriture, zip abîmé…
            QMessageBox.warning(None, "Giration", f"La partie 2 a été trouvée ({z}) mais n'a pas pu être installée :\n"
                                f"{exc}\n\nDécompressez-la à la main dans le dossier de Giration.exe.")
            return False
        if not _paquets_manquants():
            QMessageBox.information(None, "Giration", f"Partie 2 installée depuis :\n{z}\n\nGiration va démarrer.")
            return True
    QMessageBox.warning(None, "Giration", (
        "Il manque la partie 2 de Giration (bibliothèques de calcul).\n\n"
        "1. Téléchargez « Giration-…-Windows-2-calcul.zip » dans votre dossier Téléchargements.\n"
        "2. Relancez Giration.exe : il l'installera tout seul.\n\n"
        "Ou bien : ouvrez ce zip et faites glisser le dossier qu'il contient au même endroit que celui de "
        f"Giration.exe :\n{Path(sys.executable).resolve().parent}"))
    return False


def _autotest(chemin: str) -> int:
    """Contrôle de l'exécutable (GIRATION_AUTOTEST=fichier) : calcul, contrôles et export DXF, sans fenêtre."""
    lignes = []
    try:
        from ..calcul import calculer
        from ..exports.dxf import exporter_dxf
        from ..trace import Trace
        from ..vehicule import Bibliotheque
        from .saisie import suivre_au_mieux
        veh = Bibliotheque().get("articule-tracteur-4x2-semi-standard-1360")
        tr = Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", 12, 90), (30, None)])
        res = calculer(veh, tr)
        suivre_au_mieux(tr, veh.rayon_P_min())
        sortie = Path(chemin).with_suffix(".dxf")
        exporter_dxf([res], str(sortie))
        lignes.append(f"ok calcul {res.verdict()} dxf {sortie.stat().st_size}")
        # lot 2 : bordure franchie, note PDF, DXF en CC44
        import numpy as np

        from ..exports.dxf_contraintes import ajouter_au_fichier
        from ..exports.reprojection import reprojeter_fichier
        from .bilan import bilan_depuis_mesures, completer
        from .contraintes import controler_contraintes, nouvelle
        from .note import DonneesNote, exporter_pdf
        b = nouvelle("bordure", [(0, -1.0), (20, -1.0)])
        k = controler_contraintes([b], res.enveloppes.roues, res.enveloppes.carrosserie,
                                  np.column_stack([res.sim.x, res.sim.y]))
        bi = completer(bilan_depuis_mesures("A", veh.nom, res.mesures(), faisable=res.faisable), k)
        note = Path(chemin).with_suffix(".pdf")
        pages = exporter_pdf(DonneesNote("Autotest", [bi]), str(note))
        x0, y0 = 633197.5, 6302254.0                       # Lombers : dessin décalé en Lambert-93
        import ezdxf
        d = ezdxf.readfile(str(sortie))
        for e in d.modelspace():
            e.translate(x0, y0, 0)
        d.saveas(str(sortie))
        ajouter_au_fichier(sortie, [nouvelle("bordure", [(x0, y0 - 1.0), (x0 + 20, y0 - 1.0)])])
        cc = Path(chemin).with_name("autotest-CC44.dxf")
        compte = reprojeter_fichier(sortie, cc, "CC44")
        lignes.append(f"ok bordure {bi.verdict} note {pages} p {note.stat().st_size} cc44 {sum(compte.values())} "
                      f"entites")
    except Exception:
        lignes.append("ERREUR\n" + traceback.format_exc())
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    from PySide6.QtWidgets import QApplication
    argv = sys.argv if argv is None else argv
    sys.excepthook = _journal_plantage
    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName("Giration Python")
    app.setApplicationName("Giration")
    if not _verifier_installation(app):
        return 1
    if os.environ.get("GIRATION_AUTOTEST"):
        return _autotest(os.environ["GIRATION_AUTOTEST"])
    from PySide6.QtGui import QIcon

    from .fenetre import FenetreGiration
    icone = Path(__file__).resolve().parents[1] / "ressources" / "giration.ico"
    if icone.exists():
        app.setWindowIcon(QIcon(str(icone)))
    fichier = next((a for a in argv[1:] if a.lower().endswith(".giration")), None)
    f = FenetreGiration(fichier)
    f.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
