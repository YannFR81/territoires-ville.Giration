"""Note de giration en PDF : contenu, pagination, polices présentes (pas de pavés noirs)."""
import os
import unicodedata

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pypdf = pytest.importorskip("pypdf")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

from giration.gui.bilan import IMPOSSIBLE, PASSE, RESERVE, Bilan  # noqa: E402
from giration.gui.note import DonneesNote, EnteteNote, exporter_pdf  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def _texte(chemin):
    t = " ".join(p.extract_text() for p in pypdf.PdfReader(str(chemin)).pages)
    return " ".join(unicodedata.normalize("NFKC", t).split())


def _carte():
    img = QImage(1200, 800, QImage.Format_RGB32)
    img.fill(QColor("#6f7f5a"))
    p = QPainter(img)
    p.setPen(QPen(QColor("#E8730C"), 14))
    p.drawLine(QPointF(50, 700), QPointF(1150, 100))
    p.end()
    return img


def _donnees(n=2):
    a = Bilan("A — Car 13 m", "Car interurbain 13 m", RESERVE, emprise_max=6.56, emprise_roues=4.49, surface=512.0,
              rayon_mini=9.4, braquage_pct=83, braquage_deg=40.9, vitesses=(30, 6, 27), ecart_saisie=0.18,
              raisons=["Les roues montent sur la bande franchissable (0,30 m)"],
              conflits=["Les roues montent sur la bande franchissable (0,30 m)"])
    b = Bilan("B — Semi", "Semi-remorque 13,60 m", IMPOSSIBLE, emprise_max=7.10, emprise_roues=5.20, surface=560.0,
              rayon_mini=9.8, braquage_pct=95, braquage_deg=38.0, articulation_deg=52.0, vitesses=(30, 5, 25))
    c = Bilan("C — Car, variante large", "Car interurbain 13 m", PASSE, emprise_roues=4.30)
    return DonneesNote("Giratoire RD71 / RD41", [a, b, c][:n], lieu="Lombers (81)",
                       coordonnees="43°48'58,1\"N 2°10'11,2\"E — CC44 X 1 633 212,2 Y 3 179 909,0", systeme="CC44",
                       carte=_carte(), remarques=["Vitesse mini 6 km/h dans l'anneau"],
                       hypotheses=["Circulation à droite", "Marge à l'anneau : 0,50 m"],
                       sources=["Photographies aériennes et BD TOPO © IGN — Géoplateforme, licence Etalab 2.0"],
                       version="1.3.0")


def _polices():
    from PySide6.QtGui import QFontDatabase
    return bool(QFontDatabase.families())


def test_note_pdf(app, tmp_path):
    if not _polices():                    # Windows en mode offscreen : Qt ne voit aucune police (texte illisible)
        pytest.skip("aucune police visible par Qt (plateforme offscreen sous Windows)")
    chemin = tmp_path / "note.pdf"
    nb = exporter_pdf(_donnees(3), str(chemin), EnteteNote(auteur="Y. S."))
    assert chemin.exists() and nb >= 2
    r = pypdf.PdfReader(str(chemin))
    assert len(r.pages) == nb
    texte = _texte(chemin)
    for attendu in ("Épure de giration", "Passe avec réserves", "Comparaison des variantes", "Emprise des roues",
                    "C — Car, variante large", "Variante la plus favorable", "Lombers", "CC44", "Etalab",
                    "Page 1 /"):
        assert attendu in texte, attendu
    polices = r.pages[0]["/Resources"]["/Font"]
    assert len(polices) >= 1                          # du texte réel, pas des pavés


def test_note_une_variante(app, tmp_path):
    if not _polices():
        pytest.skip("aucune police visible par Qt (plateforme offscreen sous Windows)")
    chemin = tmp_path / "note1.pdf"
    exporter_pdf(_donnees(1), str(chemin))
    texte = _texte(chemin)
    assert "Comparaison des variantes" not in texte and "Hypothèses" in texte
