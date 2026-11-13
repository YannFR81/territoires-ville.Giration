"""Note de giration (PDF ou impression) : plan sur la photo, véhicule, verdict, mesures, points d'attention,
comparaison des variantes, hypothèses, méthode et sources.

Mise en page reprise de la note de calcul de Girabase (bandeau orange, en-tête et pied à chaque page, A4). Les
données arrivent déjà mises en forme (``DonneesNote``, bilans de ``bilan.py``) : ce module ne calcule rien.
"""
from __future__ import annotations

import datetime as _dt
import html
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QTextDocument

from .bilan import COULEURS, Bilan, resume, tableau

try:
    from .. import DEVISE
except ImportError:                                     # constante partagée avec Girabase
    DEVISE = "L’intelligence artificielle au service de l’action publique efficiente."

ORANGE, BLEU, LIE = "#DD590A", "#5E99C5", "#7F0541"


@dataclass
class EnteteNote:
    """En-tête et pied de la note, modifiables (organisme et service de la collectivité, par exemple)."""
    organisme: str = "Giration — épures de giration"
    service: str = DEVISE
    auteur: str = ""
    pied: str = ""


@dataclass
class DonneesNote:
    titre: str
    bilans: list[Bilan]
    actif: int = 0                                  # variante détaillée (les autres sont comparées)
    lieu: str = ""                                  # commune, routes
    coordonnees: str = ""                           # WGS84 et coordonnées légales du lieu
    systeme: str = "Lambert-93"                     # système des coordonnées du plan et du DXF
    carte: Optional[QImage] = None                  # capture de la carte avec l'épure
    profil_vitesse: Optional[QImage] = None
    remarques: list[str] = field(default_factory=list)      # alertes du calcul
    hypotheses: list[str] = field(default_factory=list)     # options de saisie et de contrôle
    sources: list[str] = field(default_factory=list)        # fonds et données, avec licences
    version: str = ""


def _e(t: object) -> str:
    return html.escape(str(t))


def _table(entetes: list[str], lignes: list[list[str]], echapper: bool = True) -> str:
    th = "".join(f'<th bgcolor="{BLEU}" style="color:#ffffff;">{_e(h) if echapper else h}</th>' for h in entetes)
    corps = []
    for i, ligne in enumerate(lignes):
        fond = "#f4f7fa" if i % 2 else "#ffffff"
        tds = "".join(f'<td align="{"left" if j == 0 else "right"}" bgcolor="{fond}">{_e(v) if echapper else v}</td>'
                      for j, v in enumerate(ligne))
        corps.append(f"<tr>{tds}</tr>")
    return ('<table width="100%" cellspacing="0" cellpadding="3" border="0.5" style="border-color:#c8d3dd;">'
            f"<tr>{th}</tr>{''.join(corps)}</table>")


def _liste(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(t)}</li>" for t in items) + "</ul>" if items else "<p><i>Aucun.</i></p>"


def construire_document(d: DonneesNote, entete: EnteteNote, largeur_px: float) -> QTextDocument:
    doc = QTextDocument()
    police = QFont("Arial")
    police.setPointSizeF(9)
    doc.setDefaultFont(police)
    doc.setDefaultStyleSheet(
        f"h1 {{ color:{ORANGE}; font-size:17pt; margin-bottom:2px; }}"
        f"h2 {{ color:{ORANGE}; font-size:12.5pt; margin-top:14px; border-bottom:1px solid {ORANGE}; }}"
        f"h3 {{ color:{LIE}; font-size:10.5pt; margin-top:10px; }}"
        "td, th { font-size:8.5pt; } p { margin:2px 0; } li { margin:1px 0; }"
        ".note { color:#555555; font-size:8pt; }")
    img_l = int(largeur_px)
    b = d.bilans[d.actif] if d.bilans else None

    h = [f"<h1>Épure de giration — {_e(d.titre)}</h1>",
         "<p>" + (f"<b>Lieu :</b> {_e(d.lieu)} &nbsp;&nbsp; " if d.lieu else "")
         + f"<b>Date :</b> {_dt.date.today():%d/%m/%Y}"
         + (f" &nbsp;&nbsp; <b>Établie par :</b> {_e(entete.auteur)}" if entete.auteur else "") + "</p>"]
    if d.coordonnees:
        h.append(f"<p><b>Coordonnées :</b> {_e(d.coordonnees)}</p>")

    # Synthèse
    h.append("<h2>Synthèse</h2>")
    if b is not None:
        coul = COULEURS.get(b.verdict, "#333333")
        h.append(f'<p style="font-size:12pt;"><b>{_e(b.variante)}</b> — {_e(b.vehicule)} : '
                 f'<font color="{coul}"><b>{_e(b.libelle)}</b></font></p>')
        if b.raisons:
            h.append(_liste(b.raisons))
    if len(d.bilans) > 1:
        h.append(f"<p>{_e(resume(d.bilans))}</p>")

    # Plan
    h.append("<h2>1. Plan</h2>")
    if d.carte is not None and not d.carte.isNull():
        doc.addResource(QTextDocument.ImageResource, QUrl("carte.png"), d.carte)
        h.append(f'<p align="center"><img src="carte.png" width="{img_l}"/></p>')
    h.append('<p class="note">Épure tracée sur la photographie aérienne de l\'IGN : la photo fait foi. Les routes de '
             'la BD TOPO peuvent en être décalées de quelques mètres.</p>')

    # Résultats de la variante détaillée
    if b is not None:
        h.append(f"<h2>2. Résultats — {_e(b.variante)}</h2>")
        _, lignes = tableau([b])
        h.append(_table(["Critère", "Valeur"], lignes))
        if d.profil_vitesse is not None and not d.profil_vitesse.isNull():
            doc.addResource(QTextDocument.ImageResource, QUrl("vitesse.png"), d.profil_vitesse)
            h.append(f'<p align="center"><img src="vitesse.png" width="{int(img_l * 0.7)}"/></p>'
                     '<p class="note" align="center">Vitesse calculée le long du trajet (km/h).</p>')

    # Points d'attention
    h.append("<h2>3. Points d'attention</h2>")
    attention = (list(b.conflits) if b is not None else []) + list(d.remarques)
    h.append(_liste(attention))

    # Comparaison
    n = 4
    if len(d.bilans) > 1:
        h.append(f"<h2>{n}. Comparaison des variantes</h2>")
        entetes, lignes = tableau(d.bilans, d.actif)
        h.append(_table(entetes, lignes))
        h.append(f'<p class="note">Entre parenthèses : écart avec la variante {_e(d.bilans[d.actif].variante)}.</p>')
        n += 1

    # Hypothèses
    h.append(f"<h2>{n}. Hypothèses</h2>")
    h.append(_liste(d.hypotheses + [f"Coordonnées du plan et du DXF : {d.systeme}."]))

    # Méthode et sources
    h.append("<h2>Annexe — Méthode et sources</h2>")
    h.append("<p>Le véhicule suit le trajet saisi : simulation cinématique du tracteur et de ses remorques, essieux "
             "sans glissement ; le rayon de braquage minimal du constructeur borne la courbure. La vitesse est "
             "calculée automatiquement d'après la courbure et la conduite choisie. Les emprises sont les surfaces "
             "balayées par la caisse et par les roues.</p>")
    h.append("<p>Contrôles : roues hors de la chaussée estimée au-delà de 0,50 m (hors abords immédiats de l'anneau "
             "d'un giratoire) ; îlot infranchissable et bordures dessinées : ne passe pas ; bande franchissable, "
             "bordures franchissables et bâtiments de la BD TOPO (précision de quelques mètres) : réserve.</p>")
    h.append(_liste(d.sources) if d.sources else "")
    h.append('<p class="note">Calcul effectué avec le portage Python (licence GNU GPL v3) du logiciel Giration publié '
             f'par le CEREMA{(" — version " + _e(d.version)) if d.version else ""}. {_e(DEVISE)}</p>')
    doc.setHtml("".join(h))
    return doc


def _peindre_pages(painter: QPainter, device, doc: QTextDocument, rect_page: QRectF, entete: EnteteNote,
                   titre: str, echelle: float) -> int:
    """Pagination manuelle avec en-tête et pied de page à chaque page."""
    h_ent, h_pied = 18 * echelle, 12 * echelle
    corps = QRectF(rect_page.left(), rect_page.top() + h_ent, rect_page.width(), rect_page.height() - h_ent - h_pied)
    doc.setPageSize(QSizeF(corps.width(), corps.height()))
    nb = doc.pageCount()
    f = QFont("Arial")
    for page in range(nb):
        if page:
            device.newPage()
        painter.save()
        painter.fillRect(QRectF(rect_page.left(), rect_page.top(), 6 * echelle, h_ent - 6 * echelle), QColor(ORANGE))
        f.setPointSizeF(9)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(ORANGE))
        painter.drawText(QRectF(rect_page.left() + 10 * echelle, rect_page.top(), rect_page.width(), 7 * echelle),
                         Qt.AlignLeft | Qt.AlignVCenter, entete.organisme or "Giration — épures de giration")
        f.setBold(False)
        f.setPointSizeF(8)
        painter.setFont(f)
        painter.setPen(QColor("#444444"))
        painter.drawText(QRectF(rect_page.left() + 10 * echelle, rect_page.top() + 6 * echelle, rect_page.width(),
                                6 * echelle), Qt.AlignLeft | Qt.AlignVCenter, entete.service)
        painter.drawText(QRectF(rect_page.left(), rect_page.top(), rect_page.width(), 7 * echelle),
                         Qt.AlignRight | Qt.AlignVCenter, titre)
        painter.setPen(QPen(QColor(ORANGE), 0.4 * echelle))
        painter.drawLine(rect_page.left(), rect_page.top() + h_ent - 4 * echelle,
                         rect_page.right(), rect_page.top() + h_ent - 4 * echelle)
        yp = rect_page.bottom() - h_pied + 4 * echelle
        painter.setPen(QPen(QColor(BLEU), 0.3 * echelle))
        painter.drawLine(rect_page.left(), yp, rect_page.right(), yp)
        painter.setPen(QColor("#444444"))
        painter.drawText(QRectF(rect_page.left(), yp, rect_page.width(), 7 * echelle),
                         Qt.AlignLeft | Qt.AlignVCenter, entete.pied)
        painter.drawText(QRectF(rect_page.left(), yp, rect_page.width(), 7 * echelle),
                         Qt.AlignHCenter | Qt.AlignVCenter, "Giration (portage Python, GPL)")
        painter.drawText(QRectF(rect_page.left(), yp, rect_page.width(), 7 * echelle),
                         Qt.AlignRight | Qt.AlignVCenter, f"Page {page + 1} / {nb}")
        painter.restore()
        painter.save()
        painter.translate(corps.left(), corps.top() - page * corps.height())
        doc.drawContents(painter, QRectF(0, page * corps.height(), corps.width(), corps.height()))
        painter.restore()
    return nb


def _titre(d: DonneesNote) -> str:
    b = d.bilans[d.actif] if d.bilans else None
    return f"{d.titre} — {b.variante}" if b else d.titre


def exporter_pdf(d: DonneesNote, chemin: str, entete: Optional[EnteteNote] = None) -> int:
    """Écrit la note en PDF A4 ; retourne le nombre de pages."""
    entete = entete or EnteteNote()
    writer = QPdfWriter(chemin)
    writer.setResolution(300)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(14, 12, 14, 12),
                                     QPageLayout.Millimeter))
    writer.setTitle(f"Épure de giration — {d.titre}")
    writer.setCreator("Giration (portage Python)")
    painter = QPainter(writer)
    rect = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
    rect.moveTo(0, 0)
    echelle = writer.resolution() / 25.4
    doc = construire_document(d, entete, rect.width() * 96 / writer.resolution())
    doc.documentLayout().setPaintDevice(writer)
    nb = _peindre_pages(painter, writer, doc, rect, entete, _titre(d), echelle)
    painter.end()
    return nb


def imprimer(d: DonneesNote, printer, entete: Optional[EnteteNote] = None) -> None:
    entete = entete or EnteteNote()
    painter = QPainter(printer)
    rect = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
    rect.moveTo(0, 0)
    echelle = printer.resolution() / 25.4
    doc = construire_document(d, entete, rect.width() * 96 / printer.resolution())
    doc.documentLayout().setPaintDevice(printer)
    _peindre_pages(painter, printer, doc, rect, entete, _titre(d), echelle)
    painter.end()
