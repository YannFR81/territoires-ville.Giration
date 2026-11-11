"""Reprojection du DXF Lambert-93 → CC42 à CC50 : contrôle contre PROJ (pyproj) au millimètre."""
import math

import ezdxf
import pytest

from giration.exports.reprojection import (changement, projection, reprojeter_document, reprojeter_fichier,
                                           suffixe, systemes_proposes)

pyproj = pytest.importorskip("pyproj")
LAMBERT93 = projection("L93")

LOMBERS = (43.816125913246864, 2.1697864490453114)          # giratoire RD71 / RD41
X0, Y0 = LAMBERT93.depuis_geo(*LOMBERS)


def _proj(cible_epsg: int):
    return pyproj.Transformer.from_crs(2154, cible_epsg, always_xy=True)


def test_codes_et_choix():
    assert projection("l93").epsg == 2154
    assert projection("CC44").epsg == 3944
    assert projection("EPSG:3944").epsg == 3944
    assert projection("3943").epsg == 3943
    with pytest.raises(ValueError):
        projection("CC51")
    codes = [c for c, _ in systemes_proposes(LOMBERS[0])]
    assert codes[:2] == ["L93", "CC44"] and "CC43" in codes and "CC45" in codes
    assert suffixe("CC44") == "-CC44" and suffixe("L93") == "-L93"


@pytest.mark.parametrize("cible,lat,lon", [("CC44", *LOMBERS), ("CC43", 43.2, 5.4), ("CC49", 48.85, 2.35),
                                           ("CC46", 45.9, 6.1)])
def test_points_contre_proj(cible, lat, lon):
    ch = changement(cible)
    t = _proj(ch.cible.epsg)
    x0, y0 = LAMBERT93.depuis_geo(lat, lon)
    for dx, dy in ((0, 0), (250, -120), (-400, 300)):
        X, Y = ch.point(x0 + dx, y0 + dy)
        Xp, Yp = t.transform(x0 + dx, y0 + dy)
        assert math.hypot(X - Xp, Y - Yp) < 1e-3


def test_similitude():
    ch = changement("CC44")
    X, Y, k, rot = ch.similitude(X0, Y0)
    # convergences : γ = n (λ − 3°) ; la rotation L93 → CC44 vaut γ(L93) − γ(CC44), quelques centièmes de degré
    assert 0.9995 < k < 1.0005
    assert 0.005 < abs(math.degrees(rot)) < 0.08
    # image d'un point à 100 m : similitude et conversion exacte à moins d'un millimètre
    a = math.radians(37)
    xe, ye = ch.point(X0 + 100 * math.cos(a), Y0 + 100 * math.sin(a))
    xs = X + k * 100 * math.cos(a + rot)
    ys = Y + k * 100 * math.sin(a + rot)
    assert math.hypot(xe - xs, ye - ys) < 1e-3


def _document():
    doc = ezdxf.new("R2010")
    bloc = doc.blocks.new("VEHICULE")
    bloc.add_lwpolyline([(0, 0), (12, 0), (12, 2.55), (0, 2.55)], close=True)
    msp = doc.modelspace()
    msp.add_line((X0, Y0), (X0 + 80, Y0 + 15))
    msp.add_lwpolyline([(X0, Y0, 0, 0, 0.4142), (X0 + 20, Y0, 0, 0, 0), (X0 + 20, Y0 + 30, 0, 0, 0)],
                       format="xyseb")
    msp.add_circle((X0 + 10, Y0 + 10), 15.0)
    msp.add_arc((X0 - 20, Y0 + 5), 12.0, 30, 160)
    msp.add_text("D71", dxfattribs={"height": 2.0, "rotation": 15}).set_placement((X0 + 5, Y0 - 8))
    msp.add_blockref("VEHICULE", (X0 + 40, Y0 + 40), dxfattribs={"rotation": 30})
    h = msp.add_hatch()
    h.paths.add_polyline_path([(X0 + 50, Y0), (X0 + 60, Y0), (X0 + 60, Y0 + 8)], is_closed=True)
    msp.add_point((X0 - 3, Y0 - 3))
    msp.add_polyline2d([(X0, Y0 + 50), (X0 + 30, Y0 + 70)])
    return doc


def test_document_cc44():
    doc = _document()
    ch = changement("CC44")
    t = _proj(3944)
    msp = doc.modelspace()
    avant = {e.dxftype(): e for e in msp}
    arc0 = avant["ARC"]
    deb0, fin0 = arc0.start_point, arc0.end_point
    compte = reprojeter_document(doc, ch)
    assert "non converti" not in compte
    assert set(compte) >= {"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "TEXT", "INSERT", "HATCH", "POINT", "POLYLINE"}
    e = {x.dxftype(): x for x in doc.modelspace()}
    # sommets : conversion exacte
    Xp, Yp = t.transform(X0 + 80, Y0 + 15)
    assert math.hypot(e["LINE"].dxf.end.x - Xp, e["LINE"].dxf.end.y - Yp) < 1e-3
    pts = list(e["LWPOLYLINE"].get_points("xyb"))
    Xp, Yp = t.transform(X0 + 20, Y0 + 30)
    assert math.hypot(pts[2][0] - Xp, pts[2][1] - Yp) < 1e-3 and pts[0][2] == pytest.approx(0.4142)
    # arc : ses extrémités tombent sur la conversion exacte des extrémités d'origine
    a = e["ARC"]
    for p_av, p_ap in ((deb0, a.start_point), (fin0, a.end_point)):
        Xp, Yp = t.transform(p_av.x, p_av.y)
        assert math.hypot(p_ap.x - Xp, p_ap.y - Yp) < 1e-3
    _, _, k, rot = ch.similitude(X0 - 20, Y0 + 5)
    assert a.dxf.radius == pytest.approx(12.0 * k, abs=1e-6)
    # cercle, texte, bloc : point de référence exact, rotation reportée
    c = e["CIRCLE"].dxf.center
    Xp, Yp = t.transform(X0 + 10, Y0 + 10)
    assert math.hypot(c.x - Xp, c.y - Yp) < 1e-3
    assert e["INSERT"].dxf.rotation == pytest.approx(30 + math.degrees(rot), abs=1e-3)
    assert e["TEXT"].dxf.height == pytest.approx(2.0 * k, abs=1e-6)
    v = list(e["HATCH"].paths)[0].vertices[0]
    Xp, Yp = t.transform(X0 + 50, Y0)
    assert math.hypot(v[0] - Xp, v[1] - Yp) < 1e-3
    assert doc.header["$INSUNITS"] == 6


def test_aller_retour_fichier(tmp_path):
    doc = _document()
    src = tmp_path / "epure-L93.dxf"
    doc.saveas(src)
    cc = tmp_path / "epure-CC44.dxf"
    reprojeter_fichier(src, cc, "CC44")
    retour = tmp_path / "retour-L93.dxf"
    reprojeter_fichier(cc, retour, "L93", source="CC44")
    d0 = {e.dxftype(): e for e in ezdxf.readfile(src).modelspace()}
    d1 = {e.dxftype(): e for e in ezdxf.readfile(retour).modelspace()}
    assert math.dist(d0["LINE"].dxf.end, d1["LINE"].dxf.end) < 1e-4
    assert math.dist(d0["ARC"].dxf.center, d1["ARC"].dxf.center) < 1e-4
    assert d1["ARC"].dxf.radius == pytest.approx(12.0, abs=1e-6)
    assert d1["INSERT"].dxf.rotation == pytest.approx(30.0, abs=1e-6)


def test_identite():
    doc = _document()
    assert reprojeter_document(doc, changement("L93")) == {}
