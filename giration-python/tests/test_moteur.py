"""Tests du moteur : exemples du guide CERTU, régime permanent, convergence, tracés, exports."""
import json
import math
import xml.dom.minidom
from pathlib import Path

import ezdxf
import pytest

from giration.calcul import calculer
from giration.cinematique import Commande, decomposer_virage, rayon_depuis_alpha, simuler_trace, simuler_volant
from giration.exports.dxf import exporter_dxf
from giration.exports.geojson import exporter_geojson, exporter_projet
from giration.exports.kml import exporter_kml
from giration.georef import Georef
from giration.lecture_dxf import lire_dxf
from giration.projections import LAMBERT93, detecter_systeme, facteur_echelle
from giration.trace import Trace
from giration.validation import CAS_GUIDE, executer, regime_permanent
from giration.vehicule import Bibliotheque, lire_veh

ICI = Path(__file__).parent


@pytest.fixture(scope="module")
def biblio():
    return Bibliotheque()


def test_bibliotheque(biblio):
    assert len(biblio) >= 60
    assert biblio.get("certu-camion-semi-remorque").nb_remorques == 1
    assert biblio.chercher("bus articulé")


@pytest.mark.parametrize("vid,db,L,a,R,tol,ref", CAS_GUIDE)
def test_exemples_du_guide(biblio, vid, db, L, a, R, tol, ref):
    v = biblio.get(vid)
    al, _, _ = decomposer_virage(v, db, L, a, 5.0)
    assert rayon_depuis_alpha(v, al) == pytest.approx(R, abs=tol)


def test_virage_impossible(biblio):
    with pytest.raises(ValueError, match="longueur minimale 15.72"):
        decomposer_virage(biblio.get("certu-grande-berline"), 120, 15.0, 10, 5.0)


def test_validation_complete():
    ok, texte = executer(rapide=True)
    assert ok, texte


def test_regime_permanent_semi(biblio):
    r = regime_permanent(biblio.get("certu-camion-semi-remorque"))
    for num, theo in r.values():
        assert num == pytest.approx(theo, abs=1e-3)


def test_polyligne_et_raccord():
    t = Trace.depuis_polyligne([(0, 0), (30, 0), (30, -30)], rayon_raccord=12)
    assert [round(x.rayon, 6) for x in t.troncons if x.est_arc] == [12]
    assert t.longueur == pytest.approx(18 + 18 + 12 * math.pi / 2)
    with pytest.raises(ValueError, match="Angle vif"):
        Trace.depuis_polyligne([(0, 0), (30, 0), (30, -30)])
    # arc AutoCAD (renflement) : quart de cercle de 15 m
    b = math.tan(math.radians(-90) / 4)
    t2 = Trace.depuis_polyligne([(0, 0), (10, 0), (25, -15), (25, -30)], [0, b, 0, 0])
    arc = t2.troncons[1]
    assert arc.rayon == pytest.approx(15)
    pts, bul = t2.sommets_bulges()
    assert bul[1] == pytest.approx(b)


def test_trace_suivi_exactement(biblio):
    v = biblio.get("bus-standard-12m")
    t = Trace.depuis_morceaux(0, 0, 0, [(20, None), ("angle", -14, -90), (20, None)])
    s = simuler_trace(v, t)
    xf, yf, _ = t.arrivee
    assert (s.x[-1], s.y[-1]) == pytest.approx((xf, yf), abs=1e-9)


def test_calcul_et_alertes(biblio):
    v = biblio.get("certu-camion-semi-remorque")
    ok = calculer(v, Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -12, -90), (30, None)]))
    assert ok.faisable and ok.verdict() == "POSSIBLE"
    trop = calculer(v, Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -6, -90), (30, None)]))
    assert not trop.faisable
    assert any("Braquage impossible" in a.message for a in trop.alertes)


def test_vitesse_automatique(biblio):
    v = biblio.get("certu-camion-semi-remorque")
    r = calculer(v, Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -12, -90), (30, None)]))
    # entrée dans l'arc : le volant doit tourner de 1/R rad par mètre -> v = 15 °/s × R
    assert r.mesures()["vitesse_min_kmh"] == pytest.approx(math.radians(15) * 12 * 3.6, abs=0.05)
    assert r.mesures()["vitesse_entree_kmh"] == pytest.approx(30)


def test_projections():
    x, y = LAMBERT93.depuis_geo(43.8161, 2.1698)
    assert detecter_systeme(x, y) == "L93"
    assert facteur_echelle(LAMBERT93, 44.0) == pytest.approx(1.0, abs=1e-9)
    assert facteur_echelle(LAMBERT93, 46.5) == pytest.approx(1 - 0.000949, abs=2e-6)


def test_exports(tmp_path, biblio):
    x0, y0 = LAMBERT93.depuis_geo(43.8161, 2.1698)
    t = Trace.depuis_morceaux(x0, y0, 0, [(25, None), ("angle", -12, -90), (25, None)])
    r = calculer(biblio.get("certu-bus"), t, systeme="L93")
    g = Georef("L93")
    exporter_dxf(r, tmp_path / "e.dxf")
    exporter_kml(r, tmp_path / "e.kml", g)
    exporter_geojson(r, tmp_path / "e.geojson", g)
    exporter_projet(r, tmp_path / "e.json", g)
    doc = ezdxf.readfile(tmp_path / "e.dxf")
    calques = {e.dxf.layer for e in doc.modelspace()}
    assert any(c.endswith("_ENVELOPPE") for c in calques) and any(c.endswith("_AXE") for c in calques)
    xml.dom.minidom.parse(str(tmp_path / "e.kml"))
    gj = json.loads((tmp_path / "e.geojson").read_text(encoding="utf-8"))
    lon, lat = gj["features"][0]["geometry"]["coordinates"][0]
    assert lat == pytest.approx(43.8161, abs=1e-3) and lon == pytest.approx(2.1698, abs=1e-3)
    pj = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))
    assert pj["resultat"]["verdict"] == "POSSIBLE"


def test_lecture_dxf_et_cli(tmp_path, biblio):
    from giration.cli import main
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    b = math.tan(math.radians(-90) / 4)
    doc.modelspace().add_lwpolyline([(0, 0, 0, 0, 0), (20, 0, 0, 0, b), (35, -15, 0, 0, 0), (35, -40, 0, 0, 0)],
                                    format="xyseb", dxfattribs={"layer": "GIR_AXE"})
    doc.saveas(tmp_path / "plan.dxf")
    d = lire_dxf(tmp_path / "plan.dxf")
    assert len(d.candidates()) == 1
    assert main(["calculer", str(tmp_path / "plan.dxf"), "-v", "certu-bus", "--systeme", "local",
                 "--origine", "43.8161,2.1698"]) == 0
    for ext in (".dxf", ".kml", ".geojson", ".json", ".txt"):
        assert (tmp_path / f"plan - giration{ext}").exists()
    # le DXF produit contient le plan d'origine et l'épure ; relancé dessus, seul le tracé d'origine est repris
    d2 = lire_dxf(tmp_path / "plan - giration.dxf")
    assert [p.calque for p in d2.candidates()] == ["GIR_AXE"]


def test_lecture_veh_origine():
    vs = lire_veh(ICI / "Giration_origine.veh")
    noms = [v.nom for v in vs]
    assert "Bus bi-articulé" in noms and len(vs) == 4
    bus = vs[noms.index("Bus bi-articulé")]
    assert bus.nb_remorques == 2 and bus.RVehMin == pytest.approx(7.788, abs=1e-3)
