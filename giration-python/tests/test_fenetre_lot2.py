"""Parcours du lot 2 dans la fenêtre, hors écran et hors ligne : bordure, franchissable, poteau, annulation,
comparaison de deux véhicules, note PDF, DXF en CC44, enregistrement."""
import json
import math
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ezdxf = pytest.importorskip("ezdxf")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from giration.georef import Georef  # noqa: E402

DONNEES = Path(__file__).resolve().parents[1] / "giration" / "carto" / "donnees"
LOMBERS = (43.816125913246864, 2.1697864490453114)


@pytest.fixture(scope="module")
def fenetre(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("Giration Tests")
    app.setApplicationName("Giration")
    from giration.gui.fenetre import FenetreGiration
    dossier = tmp_path_factory.mktemp("lot2")
    geo = Georef("L93")
    x0, y0 = geo.depuis_geo(*LOMBERS)
    x0 += 120.0                                            # à l'écart du giratoire : virage simple
    pts = [[x0 - 40, y0], [x0, y0], [x0 + 12, y0 - 12], [x0 + 12, y0 - 45]]
    projet = {"format": "giration-projet-interactif", "version": 1, "active": 0, "systeme": "L93",
              "variantes": [{"nom": "A", "couleur": "#E8730C", "vehicule_id": "certu-voiture-caravane",
                             "points": pts, "rayons": [None] * 4}]}
    chemin = dossier / "essai.giration"
    chemin.write_text(json.dumps(projet), encoding="utf-8")
    f = FenetreGiration()

    def faux_json(url, rappel, essai=1):
        if "batiment" in url:
            rappel(json.loads((DONNEES / "bdtopo_lombers_batiments.json").read_text(encoding="utf-8")), None)
        else:
            rappel(None, "hors ligne")
    f.client.obtenir_json = faux_json
    f.ouvrir(str(chemin))
    f.dossier_essai = dossier
    yield f
    f.modifie = False
    f.close()


def _attendre(f, n=80):
    for _ in range(n):
        f.pool.waitForDone(100)
        QCoreApplication.processEvents()
        time.sleep(0.01)


def _travers(f, i, j, demi=5.0):
    """Deux points d'une ligne en travers du segment de saisie i–j."""
    a, b = f.variante.points[i], f.variante.points[j]
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return [f.geo.vers_geo(mx - demi * dy / n, my + demi * dx / n), f.geo.vers_geo(mx + demi * dy / n, my - demi * dx / n)]


def test_parcours_lot2(fenetre, monkeypatch):
    f = fenetre
    _attendre(f)
    assert f.resultat is not None and f.conflits == []
    # bordure en travers du trajet : ne passe pas
    f.choisir_mode("bordure")
    for lat, lon in _travers(f, 0, 1):
        f._clic_carte(lat, lon)
    f.terminer_trace()
    _attendre(f)
    assert len(f.projet.contraintes) == 1 and f.conflits and f.conflits[0].traverse
    assert f.verdict.text().startswith("<b>Impossible")
    # bordure franchissable : réserve
    f._changer_type_contrainte(0, "franchissable")
    _attendre(f)
    assert "réserves" in f.verdict.text()
    # obstacle ponctuel sur le trajet : un clic suffit ; puis annulation
    f._choisir_type("ponctuel")
    lat, lon = f.geo.vers_geo(*f.variante.points[-1])
    f._clic_carte(lat, lon)
    _attendre(f)
    assert [c["type"] for c in f.projet.contraintes] == ["franchissable", "obstacle"]
    assert len(f.projet.contraintes[-1]["points"]) == 1
    k = next(k for k in f.conflits if k.type == "obstacle")
    assert k.ponctuel and "obstacle ponctuel" in k.message()
    # chaque conflit est surligné avec sa valeur (D13)
    from giration.gui.carte import Etiquette
    assert sum(isinstance(it, Etiquette) for it in f.carte.calques.get("contraintes", [])) == len(f.conflits)
    f._choisir_type("bordure")
    f.choisir_mode("modifier")
    f.annuler()
    _attendre(f)
    assert [c["type"] for c in f.projet.contraintes] == ["franchissable"]
    # même trajet, autre véhicule : comparaison
    f.nouvelle_variante()
    f.variante.vehicule_id = "articule-tracteur-4x2-semi-standard-1360"
    f._changer("Véhicule : semi-remorque")
    _attendre(f, 150)
    bs = f.bilans()
    assert len(bs) == 2 and bs[0].emprise_roues < bs[1].emprise_roues
    # note PDF et DXF en CC44
    monkeypatch.setattr(f, "_fichier_export", lambda titre, ext, filtre: str(f.dossier_essai / f"essai.{ext}"))
    f.exporter_note()
    assert (f.dossier_essai / "essai.pdf").stat().st_size > 5000
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (a[3][1], True)))
    f.exporter_dxf_systeme()
    d = ezdxf.readfile(str(f.dossier_essai / "essai.dxf"))
    assert "GIR_BORDURES_FRANCHISSABLES" in d.layers
    xs = [v[0] for e in d.modelspace().query("LWPOLYLINE") for v in e.get_points("xy")]
    assert min(xs) > 1_600_000                                         # coordonnées CC44
    # enregistrement et réouverture
    f.chemin = f.dossier_essai / "essai2.giration"
    f.enregistrer()
    f.ouvrir(str(f.chemin))
    assert [c["type"] for c in f.projet.contraintes] == ["franchissable"]


def test_points_de_relecture(fenetre):
    f = fenetre
    _attendre(f)
    # Ctrl+Z pendant le dessin enlève le dernier point, pas l'étape précédente
    n = len(f.projet.contraintes)
    f.choisir_mode("bordure")
    f._choisir_type("bordure")
    for lat, lon in _travers(f, 1, 2, demi=4.0):
        f._clic_carte(lat, lon)
    f.annuler()
    assert len(f._contrainte["points"]) == 1 and len(f.projet.contraintes) == n
    # changer d'outil garde une bordure valide, abandonne une incomplète sans message
    f.choisir_mode("modifier")
    assert f._contrainte is None and len(f.projet.contraintes) == n
    f.choisir_mode("bordure")
    for lat, lon in _travers(f, 1, 2, demi=4.0):
        f._clic_carte(lat, lon)
    f.choisir_mode("modifier")
    _attendre(f)
    assert len(f.projet.contraintes) == n + 1 and f.conflits
    # le trait d'une bordure ne répond qu'à quelques pixels autour de la ligne
    from giration.gui.outils_lot2 import _TraitCliquable
    traits = [it for it in f.carte.calques.get("contraintes", []) if isinstance(it, _TraitCliquable)]
    px = 1.0 / abs(f.carte.transform().m11())                          # unités de scène par pixel
    for it in traits:
        forme, chemin = it.shape().boundingRect(), it.path().boundingRect()
        assert forme.height() <= chemin.height() + 10 * px and forme.width() <= chemin.width() + 10 * px
        milieu = chemin.center()
        assert not it.shape().contains(milieu) or chemin.height() < 10 * px or chemin.width() < 10 * px
    # trajet effacé : plus de conflit affiché
    f.effacer_trace()
    _attendre(f)
    assert f.resultat is None and f.conflits == []
    f.annuler()
    _attendre(f)
    assert f.resultat is not None
