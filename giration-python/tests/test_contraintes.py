"""Bordures et obstacles dessinés, bâtiments de la BD TOPO : contrôle des roues et de la caisse."""
import math

import numpy as np
import pytest
from shapely import Polygon

from giration.calcul import calculer
from giration.georef import Georef
from giration.gui.contraintes import (Batiment, controler_bati, controler_contraintes, libelle, lire_batiments,
                                     nouvelle, valide)
from giration.trace import Trace
from giration.vehicule import Bibliotheque


@pytest.fixture(scope="module")
def virage():
    """Semi-remorque : 30 m vers l'est, virage à droite de R 12 m (90°), 30 m vers le sud."""
    veh = Bibliotheque().get("articule-tracteur-4x2-semi-standard-1360")
    tr = Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -12, -90), (30, None)])
    res = calculer(veh, tr)
    axe = np.column_stack([res.sim.x, res.sim.y])
    return res, axe


def _conflits(contraintes, virage):
    res, axe = virage
    return controler_contraintes(contraintes, res.enveloppes.roues, res.enveloppes.carrosserie, axe)


def test_validite_et_libelle():
    assert valide(nouvelle("bordure", [(0, 0), (5, 0)]))
    assert not valide(nouvelle("bordure", [(0, 0)]))
    assert valide(nouvelle("obstacle", [(0, 0)]))                  # poteau
    assert libelle(nouvelle("obstacle", [(1, 1)])) == "poteau"
    assert libelle(nouvelle("bordure", [(0, 0), (3, 4)])) == "bordure, 5,0 m"
    with pytest.raises(ValueError):
        nouvelle("trottoir", [(0, 0), (1, 1)])


def test_bordure_loin_sans_conflit(virage):
    # bordure de l'autre côté de la chaussée, à 6 m à gauche de la droite d'approche
    assert _conflits([nouvelle("bordure", [(-5, 6), (25, 6)])], virage) == []


def test_bordure_interieure_franchie(virage):
    res, axe = virage
    # bordure du coin intérieur, parallèle au trajet à 2,5 m (arc de rayon 9,5 m dans le virage de 12 m) :
    # la remorque coupe le virage (déport) et ses roues franchissent la bordure d'environ 1,5 m
    arc = [(30 + 9.5 * math.cos(math.radians(a)), -12 + 9.5 * math.sin(math.radians(a))) for a in range(90, -1, -10)]
    b = nouvelle("bordure", [(10, -2.5)] + arc + [(39.5, -40)])
    k = _conflits([b], virage)
    assert len(k) == 1 and k[0].type == "bordure" and k[0].grave and not k[0].traverse
    assert 1.0 < k[0].depassement < 2.2
    # la même ligne en bordure franchissable : réserve seulement
    k = _conflits([nouvelle("franchissable", b["points"])], virage)
    assert len(k) == 1 and not k[0].grave


def test_cote_deduit_du_trajet(virage):
    # bordure droite le long de l'approche, à 1,0 m à droite de l'axe : les roues du semi (2,55 m) la franchissent
    k = _conflits([nouvelle("bordure", [(0, -1.0), (20, -1.0)])], virage)
    assert k and abs(k[0].depassement - (1.275 - 1.0)) < 0.15
    # même ligne dessinée dans l'autre sens : même résultat
    k2 = _conflits([nouvelle("bordure", [(20, -1.0), (0, -1.0)])], virage)
    assert k2 and abs(k2[0].depassement - k[0].depassement) < 1e-6


def test_obstacle_caisse_et_poteau(virage):
    res, axe = virage
    # poteau au milieu de la trajectoire : touché ; poteau à 5 m de tout : rien
    k = _conflits([nouvelle("obstacle", [(15, 0.2)]), nouvelle("obstacle", [(15, 9)])], virage)
    assert [c.indice for c in k] == [0]
    assert k[0].depassement > 0.15
    # petit obstacle fermé entièrement sous l'emprise
    k = _conflits([nouvelle("obstacle", [(14, -0.3), (15, -0.3), (15, 0.3), (14, 0.3)], ferme=True)], virage)
    assert len(k) == 1 and k[0].depassement > 0.2


def test_ilot_ferme(virage):
    # îlot au-dedans du virage : le trajet est dehors → l'intérieur est interdit
    ilot = nouvelle("bordure", [(34, -5), (38, -5), (38, -9), (34, -9)], ferme=True)
    k = _conflits([ilot], virage)
    assert k and k[0].type == "bordure"
    # chaussée détourée autour du trajet : le trajet est dedans → l'extérieur est interdit
    large = nouvelle("bordure", [(-5, -15), (60, -15), (60, 15), (-5, 15)], ferme=True)
    k = _conflits([large], virage)
    assert k                                                          # la branche sud sort du contour


def test_bati(virage):
    res, _ = virage
    dans = Batiment(Polygon([(10, 0.5), (14, 0.5), (14, 8), (10, 8)]))
    loin = Batiment(Polygon([(10, 10), (14, 10), (14, 18), (10, 18)]))
    k = controler_bati([loin, dans], res.enveloppes.carrosserie)
    assert len(k) == 1 and k[0].indice == 1 and k[0].type == "bati" and not k[0].grave
    assert abs(k[0].depassement - (1.275 - 0.5)) < 0.15


def test_lire_batiments():
    geo = Georef("L93")
    x0, y0 = geo.depuis_geo(43.9280, 2.1460)
    pts = [geo.vers_geo(x0 + dx, y0 + dy) for dx, dy in ((0, 0), (10, 0), (10, 8), (0, 8), (0, 0))]
    donnees = {"features": [
        {"geometry": {"type": "MultiPolygon", "coordinates": [[[[lon, lat] for lat, lon in pts]]]},
         "properties": {"usage_1": "Résidentiel", "hauteur": 7.5, "precision_planimetrique": 3, "cleabs": "B1"}},
        {"geometry": {"type": "Polygon", "coordinates": [[[2.0, 43.0], [2.0, 43.0], [2.0, 43.0], [2.0, 43.0]]]},
         "properties": {}}]}
    b = lire_batiments(donnees, geo)
    assert len(b) == 1
    assert abs(b[0].polygone.area - 80.0) < 0.5
    assert b[0].hauteur == 7.5 and b[0].precision == 3 and b[0].usage == "Résidentiel"


def test_trajet_qui_traverse_une_bordure(virage):
    # ligne en travers de la droite d'approche : le véhicule devrait franchir la bordure
    k = _conflits([nouvelle("bordure", [(15, -5), (15, 5)])], virage)
    assert len(k) == 1 and k[0].traverse and k[0].grave and "traverse" in k[0].message()
    k = _conflits([nouvelle("franchissable", [(15, -5), (15, 5)])], virage)
    assert len(k) == 1 and k[0].traverse and not k[0].grave and "passe sur" in k[0].message()
