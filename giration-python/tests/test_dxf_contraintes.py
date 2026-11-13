"""Bordures et obstacles dessinés, ajoutés au DXF ; puis conversion en CC44 avec le reste du dessin."""
import ezdxf

from giration.exports.dxf_contraintes import ajouter_au_fichier, ajouter_contraintes
from giration.exports.reprojection import changement, reprojeter_document
from giration.gui.contraintes import nouvelle


def test_calques_et_entites(tmp_path):
    doc = ezdxf.new("R2010")
    cs = [nouvelle("bordure", [(0, 0), (10, 0), (10, 5)]),
          nouvelle("franchissable", [(0, 2), (4, 2), (4, 6)], ferme=True),
          nouvelle("obstacle", [(3, 3)]),
          {"type": "bordure", "points": [(1, 1)]}]                    # invalide : ignorée
    assert ajouter_contraintes(doc, cs) == 3
    e = list(doc.modelspace())
    assert sorted(x.dxf.layer for x in e) == ["GIR_BORDURES", "GIR_BORDURES_FRANCHISSABLES", "GIR_OBSTACLES"]
    poly = next(x for x in e if x.dxf.layer == "GIR_BORDURES_FRANCHISSABLES")
    assert poly.closed and doc.layers.get("GIR_BORDURES_FRANCHISSABLES").dxf.linetype == "DASHED"
    poteau = next(x for x in e if x.dxf.layer == "GIR_OBSTACLES")
    assert poteau.dxftype() == "CIRCLE"
    chemin = tmp_path / "e.dxf"
    doc.saveas(chemin)
    assert ajouter_au_fichier(chemin, [nouvelle("obstacle", [(0, 0), (1, 0)])]) == 1
    assert len(ezdxf.readfile(chemin).modelspace()) == 4


def test_converties_avec_le_dessin():
    doc = ezdxf.new("R2010")
    x0, y0 = 633197.5, 6302254.0
    ajouter_contraintes(doc, [nouvelle("bordure", [(x0, y0), (x0 + 30, y0)]), nouvelle("obstacle", [(x0, y0 + 5)])])
    ch = changement("CC44")
    compte = reprojeter_document(doc, ch)
    assert compte == {"LWPOLYLINE": 1, "CIRCLE": 1}
    p = list(doc.modelspace().query("LWPOLYLINE")[0].get_points("xy"))
    assert abs(p[0][0] - ch.point(x0, y0)[0]) < 1e-6 and p[0][0] > 1_600_000
