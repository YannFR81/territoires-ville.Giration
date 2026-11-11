"""Petits composants de l'interface : silhouette du véhicule, courbe de vitesse, choix du véhicule, bandeau de
messages posé sur la carte."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..vehicule import Bibliotheque, Vehicule, rayons_permanents


def geometrie_plan(veh: Vehicule) -> tuple[list, list]:
    """Caisses (x_avant, x_arrière, largeur) et essieux (x, largeur) en ligne droite, x vers l'avant depuis l'essieu
    avant du tracteur."""
    els = veh.elements
    caisses, essieux = [], []
    t = els[0]
    caisses.append((t.PAFav, -(t.E + t.PAFar), t.L))
    essieux.append((0.0, t.LEav or t.LEar, True))
    x_ess = -t.E
    essieux.append((x_ess, t.LEar, False))
    for k in range(1, len(els)):
        prec, el = els[k - 1], els[k]
        x_att = x_ess - prec.D
        x_ess = x_att - el.E
        caisses.append((x_att + el.PAFav, x_ess - el.PAFar, el.L))
        essieux.append((x_ess, el.LEar, False))
    return caisses, essieux


class Silhouette(QWidget):
    """Vue en plan schématique du véhicule (caisses et essieux équivalents), à l'échelle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.veh: Optional[Vehicule] = None
        self.setMinimumHeight(54)

    def sizeHint(self) -> QSize:
        return QSize(220, 60)

    def definir(self, veh: Optional[Vehicule]) -> None:
        self.veh = veh
        self.update()

    def paintEvent(self, _ev) -> None:
        if self.veh is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        caisses, essieux = geometrie_plan(self.veh)
        x_max = max(c[0] for c in caisses)
        x_min = min(c[1] for c in caisses)
        larg = max(c[2] for c in caisses + [(0, 0, e[1]) for e in essieux])
        w, h = self.width() - 12, self.height() - 10
        ech = min(w / (x_max - x_min), h / larg)
        cx = 6 + (w - (x_max - x_min) * ech) / 2
        cy = self.height() / 2

        def X(x):                                  # avant du véhicule à gauche
            return cx + (x_max - x) * ech
        texte = self.palette().color(QPalette.WindowText)
        corps = QColor(94, 153, 197, 70)
        p.setPen(QPen(texte, 1.2))
        for xa, xr, L in caisses:
            p.setBrush(QBrush(corps))
            p.drawRoundedRect(QRectF(X(xa), cy - L / 2 * ech, (xa - xr) * ech, L * ech), 2, 2)
        p.setPen(QPen(texte, 1.0))
        p.setBrush(QBrush(texte))
        for x, LE, directeur in essieux:
            lg, lp = 0.9 * ech, max(2.5, 0.3 * ech)
            for cote in (-1, 1):
                yc = cy + cote * (LE / 2 - 0.15) * ech
                p.drawRoundedRect(QRectF(X(x) - lg / 2, yc - lp / 2, lg, lp), 1, 1)
            p.drawLine(QPointF(X(x), cy - (LE / 2 - 0.15) * ech), QPointF(X(x), cy + (LE / 2 - 0.15) * ech))
        p.end()


class GrapheVitesse(QWidget):
    """Vitesse automatique le long du tracé, avec la position du véhicule."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s: Optional[np.ndarray] = None
        self.v: Optional[np.ndarray] = None
        self.s_pos: Optional[float] = None
        self.setMinimumHeight(92)

    def sizeHint(self) -> QSize:
        return QSize(240, 100)

    def definir(self, s, v_kmh, s_pos=None) -> None:
        self.s, self.v, self.s_pos = s, v_kmh, s_pos
        self.update()

    def position(self, s_pos: Optional[float]) -> None:
        self.s_pos = s_pos
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        texte = self.palette().color(QPalette.WindowText)
        discret = QColor(texte)
        discret.setAlpha(110)
        g, d, hau, bas = 30, 8, 8, 18
        w, h = self.width() - g - d, self.height() - hau - bas
        f = QFont()
        f.setPointSizeF(7.5)
        p.setFont(f)
        if self.s is None or len(self.s) < 2:
            p.setPen(discret)
            p.drawText(self.rect(), Qt.AlignCenter, "Vitesse calculée après le tracé")
            return
        v_max = max(10.0, math.ceil(float(np.nanmax(self.v)) / 10) * 10)
        L = float(self.s[-1]) or 1.0

        def P(s, v):
            return QPointF(g + s / L * w, hau + h - v / v_max * h)
        p.setPen(QPen(discret, 1))
        for vv in range(0, int(v_max) + 1, 10):
            y = P(0, vv).y()
            p.drawLine(QPointF(g, y), QPointF(g + w, y))
            p.drawText(QRectF(0, y - 7, g - 4, 14), Qt.AlignRight | Qt.AlignVCenter, f"{vv}")
        p.drawText(QRectF(g, hau + h + 2, w, 14), Qt.AlignRight, f"{L:.0f} m")
        p.drawText(QRectF(g, hau + h + 2, w, 14), Qt.AlignLeft, "km/h")
        pas = max(1, len(self.s) // 400)
        chemin = QPainterPath(P(float(self.s[0]), float(self.v[0])))
        for i in range(pas, len(self.s), pas):
            chemin.lineTo(P(float(self.s[i]), float(self.v[i])))
        chemin.lineTo(P(float(self.s[-1]), float(self.v[-1])))
        p.setPen(QPen(QColor("#2F7CC4"), 2))
        p.drawPath(chemin)
        i = int(np.argmin(self.v))
        pm = P(float(self.s[i]), float(self.v[i]))
        p.setBrush(QColor("#E8730C"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(pm, 3.5, 3.5)
        p.setPen(texte)
        p.drawText(QRectF(pm.x() - 40, pm.y() + 3, 80, 13), Qt.AlignCenter, f"{self.v[i]:.0f} km/h")
        if self.s_pos is not None:
            x = P(self.s_pos, 0).x()
            p.setPen(QPen(QColor("#C62828"), 1.2, Qt.DashLine))
            p.drawLine(QPointF(x, hau), QPointF(x, hau + h))
        p.end()


class ChoixVehicule(QDialog):
    """Arborescence de la bibliothèque, recherche, silhouette et caractéristiques."""

    def __init__(self, biblio: Bibliotheque, actuel: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choisir le véhicule")
        self.resize(820, 560)
        self.biblio = biblio
        self.choix: Optional[str] = actuel
        lay = QVBoxLayout(self)
        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Rechercher : semi, bus, BOM, pompiers, tracteur, citadine…")
        self.recherche.setClearButtonEnabled(True)
        self.recherche.textChanged.connect(self._filtrer)
        lay.addWidget(self.recherche)
        sep = QSplitter(Qt.Horizontal)
        self.arbre = QTreeWidget()
        self.arbre.setHeaderHidden(True)
        self.arbre.currentItemChanged.connect(self._courant)
        self.arbre.itemDoubleClicked.connect(lambda it, _c: it.data(0, Qt.UserRole) and self.accept())
        sep.addWidget(self.arbre)
        droite = QWidget()
        dl = QVBoxLayout(droite)
        self.titre = QLabel()
        self.titre.setWordWrap(True)
        f = self.titre.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1.5)
        self.titre.setFont(f)
        dl.addWidget(self.titre)
        self.silhouette = Silhouette()
        self.silhouette.setMinimumHeight(90)
        dl.addWidget(self.silhouette)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.RichText)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        dl.addWidget(self.details, 1)
        sep.addWidget(droite)
        sep.setSizes([360, 460])
        lay.addWidget(sep, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Choisir")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._remplir()

    def _remplir(self) -> None:
        self.arbre.clear()
        familles: dict[str, QTreeWidgetItem] = {}
        courant = None
        for v in self.biblio:
            fam = v.categorie or "Autres"
            if fam not in familles:
                it = QTreeWidgetItem([fam])
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
                self.arbre.addTopLevelItem(it)
                familles[fam] = it
            sous = v.arborescence[1] if len(v.arborescence) > 1 else ""
            it = QTreeWidgetItem([f"{sous} — {v.nom.split(' — ')[-1]}" if sous and not v.id.startswith("certu") else v.nom])
            it.setData(0, Qt.UserRole, v.id)
            it.setToolTip(0, v.nom)
            familles[fam].addChild(it)
            if v.id == self.choix:
                courant = it
        if courant is not None:
            courant.parent().setExpanded(True)
            self.arbre.setCurrentItem(courant)

    def _filtrer(self, texte: str) -> None:
        trouves = {v.id for v in self.biblio.chercher(texte)} if texte.strip() else None
        premier = None
        for i in range(self.arbre.topLevelItemCount()):
            fam = self.arbre.topLevelItem(i)
            visibles = 0
            for j in range(fam.childCount()):
                it = fam.child(j)
                ok = trouves is None or it.data(0, Qt.UserRole) in trouves
                it.setHidden(not ok)
                visibles += ok
                if ok and premier is None and trouves is not None:
                    premier = it
            fam.setHidden(visibles == 0)
            fam.setExpanded(trouves is not None and visibles > 0)
        if premier is not None:
            self.arbre.setCurrentItem(premier)

    def _courant(self, it, _prec) -> None:
        vid = it.data(0, Qt.UserRole) if it else None
        if not vid:
            return
        self.choix = vid
        v = self.biblio.get(vid)
        self.titre.setText(v.nom)
        self.silhouette.definir(v)
        rp = rayons_permanents(v, v.alpha_max_permanent or v.alpha_max)
        lignes = [
            f"<b>Longueur</b> {v.longueur_totale:.2f} m · <b>largeur</b> {v.largeur:.2f} m · "
            f"{'tracteur seul' if not v.nb_remorques else f'{v.nb_remorques} remorque(s)'}",
            f"<b>Braquage maximal</b> {math.degrees(v.alpha_max):.1f}° · <b>rayon minimal du tracé</b> "
            f"{v.rayon_P_min():.2f} m (milieu de l'essieu avant)",
            f"<b>Au braquage maximal</b> : rayon extérieur {max(r.ext_murs for r in rp):.2f} m, intérieur "
            f"{min(r.int_trottoirs for r in rp):.2f} m",
        ]
        if v.constructeur:
            lignes.append(f"<b>Constructeur</b> {v.constructeur}" + (f" — {v.modele}" if v.modele else ""))
        if v.fiabilite:
            lignes.append(f"<b>Fiabilité des données</b> {v.fiabilite} (A : tout publié par le constructeur ; "
                          "B : quelques valeurs déduites ; C : valeurs majeures manquantes)")
        if v.origine:
            lignes.append(f"<b>Origine</b> {v.origine}")
        self.details.setText("<p>" + "</p><p>".join(lignes) + "</p>")


class Bandeau(QFrame):
    """Message posé en haut de la carte, avec des boutons d'action : jamais de fenêtre qui bloque la saisie.

    Sans bouton, il disparaît seul après ``duree_ms`` ; avec des boutons, il reste jusqu'au choix (ou « × »)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("bandeau")
        self.setStyleSheet("QFrame#bandeau { background: palette(base); border: 1px solid palette(mid); "
                           "border-radius: 8px; } QLabel { background: transparent; }")
        ombre = QGraphicsDropShadowEffect(self)
        ombre.setBlurRadius(18)
        ombre.setOffset(0, 3)
        ombre.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(ombre)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 8, 9)
        haut = QHBoxLayout()
        self.texte = QLabel()
        self.texte.setWordWrap(True)
        self.texte.setTextFormat(Qt.RichText)
        haut.addWidget(self.texte, 1)
        fermer = QPushButton("×")
        fermer.setFlat(True)
        fermer.setFixedWidth(24)
        fermer.setToolTip("Fermer")
        fermer.clicked.connect(self.fermer)
        haut.addWidget(fermer, 0, Qt.AlignTop)
        lay.addLayout(haut)
        self.zone_boutons = QHBoxLayout()
        self.zone_boutons.addStretch(1)
        lay.addLayout(self.zone_boutons)
        self.minuteur = QTimer(self)
        self.minuteur.setSingleShot(True)
        self.minuteur.timeout.connect(self.fermer)
        self.au_fermer = None
        parent.installEventFilter(self)
        self.hide()

    def afficher(self, html: str, actions=(), duree_ms: int = 0, au_fermer=None) -> None:
        while self.zone_boutons.count() > 1:
            it = self.zone_boutons.takeAt(1)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        for texte, action in actions:
            b = QPushButton(texte)
            b.clicked.connect(lambda _=False, a=action: (self.fermer(appeler=False), a()))
            self.zone_boutons.addWidget(b)
        self.texte.setText(html)
        self.au_fermer = au_fermer
        self.placer()
        self.show()
        self.raise_()
        self.minuteur.stop()
        if duree_ms and not actions:
            self.minuteur.start(duree_ms)

    def fermer(self, appeler: bool = True) -> None:
        f, self.au_fermer = self.au_fermer, None
        self.minuteur.stop()
        self.hide()
        if appeler and f is not None:
            f()

    def placer(self) -> None:
        """Largeur bornée par la carte, hauteur calculée d'après le texte (retours à la ligne) et les boutons."""
        p = self.parentWidget()
        w = max(260, min(640, p.width() - 40))
        lay = self.layout()
        m = lay.contentsMargins()
        esp = max(0, lay.spacing())
        largeur_texte = w - m.left() - m.right() - 24 - esp
        h_texte = max(24, self.texte.heightForWidth(largeur_texte))
        boutons = [self.zone_boutons.itemAt(i).widget() for i in range(self.zone_boutons.count())]
        boutons = [b for b in boutons if b is not None]
        h_boutons = max((b.sizeHint().height() for b in boutons), default=0)
        h = m.top() + h_texte + (esp + h_boutons if boutons else 0) + m.bottom() + 2
        self.setFixedSize(w, h)
        self.move((p.width() - w) // 2, 12)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Resize and self.isVisible():
            self.placer()
        return False
