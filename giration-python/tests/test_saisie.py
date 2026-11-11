"""Saisie assistée : guides, corrections de fin de saisie, trajet au mieux, calage sur la voie, giratoire, mesure."""
import json
import math
from pathlib import Path

import pytest

from giration.calcul import calculer
from giration.carto.chaussee import aimanter, construire_chaussee, mesurer
from giration.carto.geoservices import lire_routes
from giration.georef import Georef
from giration.gui.modele import construire_passages
from giration.gui.saisie import (anneau_bdtopo, anneau_site, controler, corrections, elastique, etat_rayon, guides,
                                 suivre_au_mieux, tour_giratoire, trajet)
from giration.vehicule import Bibliotheque

DONNEES = Path(__file__).resolve().parents[1] / "giration" / "carto" / "donnees" / "bdtopo_lombers_d71_d41.json"
CENTRE = (43.816125913246864, 2.1697864490453114)          # giratoire RD71 / RD41 de Lombers


@pytest.fixture(scope="module")
def site():
    geo = Georef("L93")
    troncons = lire_routes(json.loads(DONNEES.read_text(encoding="utf-8")))
    ch = construire_chaussee(troncons, geo)
    cx, cy = geo.depuis_geo(*CENTRE)
    return geo, troncons, ch, (cx, cy)


@pytest.fixture(scope="module")
def biblio():
    return Bibliotheque()


def test_etat_rayon():
    assert etat_rayon(5.0, 6.0) == "impossible"
    assert etat_rayon(6.5, 6.0) == "limite"
    assert etat_rayon(8.0, 6.0) == "ok"


def test_guides_lieu_exact():
    """Un point posé sur le guide rouge donne exactement le rayon minimal, au-delà un rayon plus grand possible."""
    pts = [[0.0, 0.0], [40.0, 0.0]]
    rmin = 8.0
    g = guides(pts, [None, None], rmin)
    assert g is not None
    for q in [q for q in g.limite if q is not None and math.dist(q, (40, 0)) > 3][::7]:
        # virage au point 2 imposé à rmin : il tient tout juste
        _, infos, _ = construire_passages(pts + [list(q)], [None, rmin, None], rmin)
        s = next(s for s in infos if s.indice == 1)
        assert s.rayon == pytest.approx(rmin, rel=2e-3)
        # un peu en deçà (vers le dernier point) : le virage est réduit
        p2 = [40 + 0.8 * (q[0] - 40), 0.8 * q[1]]
        _, infos, _ = construire_passages(pts + [p2], [None, rmin, None], rmin)
        assert next(s for s in infos if s.indice == 1).reduit


def test_elastique_rouge(biblio):
    pts = [[0.0, 0.0], [30.0, 0.0]]
    e = elastique(pts, [None, None], (32.0, -3.0), 8.0)
    assert e.etat == "impossible"
    e = elastique(pts, [None, None], (60.0, -25.0), 8.0)
    assert e.etat in ("ok", "limite")


def test_au_mieux_exact_si_faisable():
    """Trajet faisable : le véhicule le suit exactement (écart centimétrique)."""
    pts = [[0, 0], [40, 0], [60, -20], [60, -60]]
    tr, infos, _ = construire_passages(pts, [None, 14, 14, None], 6.0)
    tv = suivre_au_mieux(tr, 6.0)
    import shapely
    d = shapely.distance(shapely.points([q[:2] for q in tv.echantillons(0.5)]),
                         shapely.linestrings([q[:2] for q in tr.echantillons(0.2)]))
    assert d.max() < 0.03
    assert abs(tv.longueur - tr.longueur) < 0.5


def test_corrections_rayon_puis_au_mieux():
    rmin = 8.2
    # virage serré mais avec de la place : élargi, les points ne bougent pas
    pts = [[0, 0], [40, 0], [41, -30]]
    c = corrections(pts, [None, 5.0, None], rmin)
    assert c.elargis and c.elargis[0][0] == 1 and c.rayons[1] == math.ceil(rmin * 1.05 * 2) / 2
    assert not c.manque
    # points trop rapprochés : pas la place, le véhicule fera au mieux
    pts = [[0, 0], [30, 0], [33, -3], [34, -30]]
    c = corrections(pts, [None, None, None, None], rmin)
    assert c.manque
    tr, infos, tv, am = trajet(pts, c.rayons, rmin, au_mieux=True)
    assert am is not None and max(am.ecarts) > 0.1
    assert all(abs(t.k) <= 1 / rmin + 1e-9 for t in tv.troncons)


def test_aimanter_sens_de_marche(site):
    geo, troncons, ch, (cx, cy) = site
    # D41 Est, à 50 m du centre : vers l'est (on s'éloigne) puis vers l'ouest (on arrive)
    p = (cx + 50, cy + 12)
    est = aimanter(ch, *p, cap=math.radians(13))
    ouest = aimanter(ch, *p, cap=math.radians(193))
    assert est and ouest and "droite" in est[2]
    # en roulant vers l'est, la voie de droite est au sud ; vers l'ouest, au nord
    assert est[1] < ouest[1]
    gauche = aimanter(ch, *p, cap=math.radians(13), gauche=True)
    assert gauche[1] == pytest.approx(ouest[1], abs=1e-6)


def test_giratoire_lombers(site, biblio):
    geo, troncons, ch, (cx, cy) = site
    an = anneau_bdtopo(ch, (cx, cy))
    assert an is not None
    assert math.dist(an.centre, (cx, cy)) < 3.0
    assert 9.5 < an.r_ext - 2.75 < 12.0                # rayon de l'axe de l'anneau BD TOPO ≈ 10,7 m
    veh = biblio.get("articule-tracteur-4x2-semi-standard-1360")
    rmin = veh.rayon_P_min()
    tg = tour_giratoire(ch, an, (cx - 14, cy + 44), (cx + 44, cy + 12), veh.tracteur.LEav or veh.tracteur.LEar, rmin)
    assert tg is not None and tg.de.startswith("D71") and tg.vers.startswith("D41")
    tr, infos, _ = construire_passages(tg.points, tg.rayons, rmin)
    assert tr is not None and all(not s.reduit for s in infos)
    # sens inverse des aiguilles d'une montre : le tour de l'anneau tourne à gauche
    k_anneau = [t.k for t in tr.troncons if t.est_arc and abs(1 / abs(t.k) - tg.r_anneau) < 0.05]
    assert k_anneau and all(k > 0 for k in k_anneau)
    tg2 = tour_giratoire(ch, an, (cx - 14, cy + 44), (cx + 44, cy + 12), 2.0, rmin, gauche=True)
    tr2, _, _ = construire_passages(tg2.points, tg2.rayons, rmin)
    k2 = [t.k for t in tr2.troncons if t.est_arc and abs(1 / abs(t.k) - tg2.r_anneau) < 0.05]
    assert k2 and all(k < 0 for k in k2)
    # contrôles : la semi passe, la zone des évasements n'est pas contrôlée
    res = calculer(veh, tr, echelle=geo.echelle(cx, cy), systeme="L93", rapide=True)
    ctl = controler(ch, res.enveloppes.roues, an)
    assert ctl.zone_non_controlee is not None
    assert ctl.ilot == 0.0


def test_site_girabase(site):
    geo, troncons, ch, (cx, cy) = site
    an = anneau_site({"lat": CENTRE[0], "lon": CENTRE[1], "R": 4, "Bf": 2, "LA": 7}, geo)
    assert an.r_ilot == 4 and an.r_int == 6 and an.r_ext == 13
    assert math.dist(an.centre, (cx, cy)) < 1e-6


def test_mesure_et_recalage(site):
    geo, troncons, ch, (cx, cy) = site
    # deux clics de part et d'autre de la D41 Est, milieu décalé de 0,8 m vers le nord de l'axe
    ax = next(a for a in ch.axes if a.nom.startswith("D41") and a.ligne.distance(
        __import__("shapely").Point(cx + 50, cy + 13)) < 3)
    s = ax.ligne.project(__import__("shapely").Point(cx + 50, cy + 13))
    q = ax.ligne.interpolate(s)
    q2 = ax.ligne.interpolate(s + 1)
    tx, ty = q2.x - q.x, q2.y - q.y
    n = math.hypot(tx, ty)
    nx, ny = -ty / n, tx / n
    m = (q.x + 0.8 * nx, q.y + 0.8 * ny)
    a, b = (m[0] + 2.95 * nx, m[1] + 2.95 * ny), (m[0] - 2.95 * nx, m[1] - 2.95 * ny)
    me = mesurer(ch, a, b)
    assert me.largeur_plausible and me.longueur == pytest.approx(5.9, abs=1e-6)
    assert me.decalage == pytest.approx(0.8, abs=0.02) and me.recaler
    ch2 = construire_chaussee(troncons, geo, {ax.nom: 5.9}, {ax.cle: me.recalage})
    ax2 = next(x for x in ch2.axes if x.cle == ax.cle)
    assert ax2.largeur == 5.9 and ax2.recale and ax2.corrigee
    assert ax2.ligne.distance(__import__("shapely").Point(m)) < 0.02


def test_anneau_releve_sur_photo(site, biblio):
    """Anneau relevé à la main (décalé de la BD TOPO) : il remplace les tronçons « Rond-point » et l'outil Giratoire
    prend les clics comme milieux de voie."""
    from giration.gui.saisie import cercle_3_points, gsite, site_releve
    geo, troncons, ch, (cx, cy) = site
    # anneau de la photo : 2,4 m au nord-ouest de celui de la BD TOPO, plus petit
    px, py, r_ext, r_int, r_ilot = cx - 1.0, cy + 2.2, 12.5, 7.0, 5.2
    pts = [(px + r_ext * math.cos(math.radians(a)), py + r_ext * math.sin(math.radians(a))) for a in (10, 130, 250)]
    c = cercle_3_points(*pts)
    assert c[0] == pytest.approx(px, abs=1e-6) and c[1] == pytest.approx(py, abs=1e-6)
    assert c[2] == pytest.approx(r_ext, abs=1e-6)
    s = site_releve(geo, px, py, r_ext, r_int, r_ilot)
    assert s["source"] == "photo" and s["R"] == 5.2 and s["Bf"] == pytest.approx(1.8) and s["LA"] == pytest.approx(5.5)
    an = anneau_site(s, geo)
    assert an.source == "photo" and math.dist(an.centre, (px, py)) < 1e-3 and an.r_ext == pytest.approx(r_ext)
    g = gsite(s)
    assert g["format"] == "girabase-site/1" and g["R"] == 5.2 and g["LA"] == pytest.approx(5.5)
    # chaussée : plus de tronçon « Rond-point » près de l'anneau relevé, remplacé par la couronne relevée
    ch2 = construire_chaussee(troncons, geo, anneaux=[(an.cx, an.cy, an.r_ext, an.r_int)])
    assert not any(a.anneau for a in ch2.axes)
    import shapely
    assert ch2.surface.contains(shapely.Point(px + 10.0, py))          # sur la couronne relevée
    assert not ch2.surface.contains(shapely.Point(px + 3.0, py))       # dans l'îlot
    # giratoire : clics au milieu des voies, sans calage BD TOPO
    veh = biblio.get("articule-tracteur-4x2-semi-standard-1360")
    entree, sortie = (cx - 12.2, cy + 40.0), (cx + 40.0, cy + 13.5)
    tg = tour_giratoire(ch2, an, entree, sortie, 2.04, veh.rayon_P_min())
    assert tg is not None
    tr, infos, _ = construire_passages(tg.points, tg.rayons, veh.rayon_P_min())
    import shapely as sh
    ligne = sh.linestrings([q[:2] for q in tr.echantillons(0.2)])
    assert sh.distance(sh.Point(entree), ligne) < 0.05 and sh.distance(sh.Point(sortie), ligne) < 0.05
    assert tg.r_anneau == pytest.approx(r_ext - 0.5 - 1.02, abs=1e-6)


def test_giratoire_sans_bdtopo(site, biblio):
    """Chaussée BD TOPO ignorée : un site Girabase suffit, les clics sont le milieu des voies (direction radiale)."""
    from giration.carto.chaussee import Chaussee
    geo, troncons, ch, (cx, cy) = site
    an = anneau_site({"lat": CENTRE[0], "lon": CENTRE[1], "R": 4, "Bf": 2, "LA": 7}, geo)
    veh = biblio.get("articule-tracteur-4x2-semi-standard-1360")
    vide = Chaussee()
    assert anneau_bdtopo(vide, (cx, cy)) is None
    entree, sortie = (cx - 12.0, cy + 40.0), (cx + 40.0, cy + 12.0)
    tg = tour_giratoire(vide, an, entree, sortie, 2.04, veh.rayon_P_min())
    assert tg is not None and tg.de == "branche" and tg.vers == "branche"
    tr, infos, _ = construire_passages(tg.points, tg.rayons, veh.rayon_P_min())
    import shapely as sh
    ligne = sh.linestrings([q[:2] for q in tr.echantillons(0.2)])
    assert sh.distance(sh.Point(entree), ligne) < 0.05 and sh.distance(sh.Point(sortie), ligne) < 0.05
    res = calculer(veh, tr, rapide=True)
    ctl = controler(vide, res.enveloppes.roues, an)
    assert ctl.hors == 0 and not ctl.zones_hors and ctl.ilot_connu
