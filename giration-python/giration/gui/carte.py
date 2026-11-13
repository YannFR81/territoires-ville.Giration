"""Vue cartographique : tuiles IGN (Plan IGN, photographie aérienne, routes, cadastre) et dessin de l'épure.

Reprise de la carte de Girabase Python (même navigation : France entière, molette, bascule automatique vers la
photographie aérienne), avec les outils de Giration : tracer, giratoire, mesurer, poignées déplaçables.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from ..carto.geoservices import CADASTRE, FONDS, ROUTES, Fond, fond_effectif, url_tuile
from ..projections import DEMI_MONDE, coin_tuile, depuis_mercator, taille_tuile, tuile_de, vers_mercator
from .client import delai_reessai, requete

NIVEAU_MIN, NIVEAU_MAX = 5.0, 21.0
NIVEAU_PHOTO_20CM = 19.0          # orthophotographie IGN à sa pleine résolution
FRANCE = (46.6, 2.45, 560000.0)
MAX_TUILES = 450


def geo_vers_scene(lat: float, lon: float) -> QPointF:
    x, y = vers_mercator(lat, lon)
    return QPointF(x, -y)


def scene_vers_geo(p: QPointF) -> tuple[float, float]:
    return depuis_mercator(p.x(), -p.y())


def crayon(couleur: QColor, largeur: float, style=Qt.SolidLine) -> QPen:
    p = QPen(couleur, largeur, style)
    p.setCosmetic(True)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


class Poignee(QGraphicsItem):
    """Point déplaçable, de taille constante à l'écran. Formes : « rond » (points du trajet), « carre » (poignées de
    construction, comme les grips d'AutoCAD), « croix » (centre). La zone de prise reste d'au moins 7 px."""

    def __init__(self, donnee: tuple, rempli: QColor, trait: QColor, rayon: float = 7.0, epais: float = 2.5,
                 forme: str = "rond"):
        super().__init__()
        self.donnee = donnee
        self.rempli, self.trait, self.rayon, self.epais, self.forme = rempli, trait, rayon, epais, forme
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)
        self.setData(0, donnee)

    def boundingRect(self) -> QRectF:
        r = max(7.0, self.rayon + self.epais + 2)
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rayon
        if self.forme == "carre":
            carre = QRectF(-r, -r, 2 * r, 2 * r)
            p.setPen(QPen(QColor(255, 255, 255, 230), self.epais + 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(carre)
            p.setPen(QPen(self.trait, self.epais))
            p.setBrush(QBrush(self.rempli))
            p.drawRect(carre)
            return
        if self.forme == "croix":
            for couleur, ep in ((QColor(255, 255, 255, 230), self.epais + 2.5), (self.trait, self.epais)):
                p.setPen(QPen(couleur, ep, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(-r, 0), QPointF(r, 0))
                p.drawLine(QPointF(0, -r), QPointF(0, r))
            return
        p.setPen(QPen(QColor(255, 255, 255, 220), self.epais + 2.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(QPen(self.trait, self.epais))
        p.setBrush(QBrush(self.rempli))
        p.drawEllipse(QPointF(0, 0), r, r)


class Etiquette(QGraphicsItem):
    """Cartouche de texte ancré sur un point de la carte, de taille constante à l'écran."""

    def __init__(self, texte: str, fond: QColor, texte_couleur: QColor, bord: QColor, decalage=QPointF(14, -14)):
        super().__init__()
        self.lignes = texte.split("\n")
        self.fond, self.couleur, self.bord, self.decalage = fond, texte_couleur, bord, decalage
        self.police = QFont()
        self.police.setPointSizeF(9.5)
        self.police.setBold(True)
        fm = QFontMetricsF(self.police)
        self.l = max(fm.horizontalAdvance(t) for t in self.lignes) + 16
        self.h = fm.lineSpacing() * len(self.lignes) + 10
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)

    def boundingRect(self) -> QRectF:
        return QRectF(self.decalage.x() - 2, self.decalage.y() - self.h - 2, self.l + 4, self.h + 4)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.decalage.x(), self.decalage.y() - self.h, self.l, self.h)
        p.setPen(QPen(self.bord, 1.2))
        p.setBrush(QBrush(self.fond))
        p.drawRoundedRect(r, 5, 5)
        p.setFont(self.police)
        p.setPen(self.couleur)
        p.drawText(r.adjusted(8, 5, -8, -5), Qt.AlignLeft | Qt.AlignVCenter, "\n".join(self.lignes))


class CarteVue(QGraphicsView):
    curseur = Signal(float, float)                    # position de la souris (lat, lon)
    clic = Signal(float, float)                       # clic simple sur la carte (outil actif)
    double_clic = Signal(float, float)
    fleche = Signal(float, float, float, float, bool)  # outil « départ → arrivée » : appui, position, terminé
    poignee_glissee = Signal(object, float, float, bool)   # donnée, lat, lon, terminé
    poignee_cliquee = Signal(object)
    poignee_molette = Signal(object, float)           # Ctrl + molette sur une poignée (crans)
    menu = Signal(object, float, float, QPoint)       # clic droit : donnée sous la souris (ou None), lat, lon
    mentions_cliquees = Signal()
    vue_changee = Signal()

    SEUIL_GLISSER = 5

    def __init__(self, nam: QNetworkAccessManager, parent=None):
        super().__init__(parent)
        self.nam = nam
        scene = QGraphicsScene(self)
        scene.setSceneRect(-DEMI_MONDE, -DEMI_MONDE, 2 * DEMI_MONDE, 2 * DEMI_MONDE)
        self.setScene(scene)
        self.setBackgroundBrush(QColor("#ffffff"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.niveau = 6.0
        self.centre_vue = geo_vers_scene(*FRANCE[:2])
        self.mode_fond = "auto"
        self.fond: Fond = FONDS[fond_effectif(self.mode_fond, self.niveau)]
        self.opacite = 1.0
        self.cadastre = False
        self.routes = False
        self.consigne = ""
        self.voile = QGraphicsRectItem(QRectF(-DEMI_MONDE, -DEMI_MONDE, 2 * DEMI_MONDE, 2 * DEMI_MONDE))
        self.voile.setPen(QPen(Qt.NoPen))
        self.voile.setZValue(-60)
        scene.addItem(self.voile)
        self.mode = "modifier"
        self.tuiles: dict[tuple, QGraphicsPixmapItem] = {}
        self.attente: dict[tuple, QNetworkReply] = {}
        self.essais: dict[tuple, int] = {}
        self.calques: dict[str, list[QGraphicsItem]] = {}
        self._pan: Optional[QPointF] = None
        self._appui: Optional[QPointF] = None
        self._deplacee = False
        self._glisse = None
        self._fleche_depart: Optional[QPointF] = None
        self._ajustement: Optional[tuple] = None
        self.minuteur = QTimer(self)
        self.minuteur.setSingleShot(True)
        self.minuteur.setInterval(60)
        self.minuteur.timeout.connect(self._charger_tuiles)
        self._maj_voile()
        self._appliquer()

    # ------------------------------------------------------------------ vue
    @property
    def echelle(self) -> float:
        return 256 * 2 ** self.niveau / (2 * DEMI_MONDE)

    def metres_par_pixel(self) -> float:
        lat, _ = self.centre_geo()
        return math.cos(math.radians(lat)) / self.echelle

    def _appliquer(self) -> None:
        self._maj_fond()
        for cle, it in self.tuiles.items():
            if cle[0] == "routes":
                it.setOpacity(0.8 if self.niveau <= 18.25 else 0.45)
        s = self.echelle
        self.setTransform(QTransform.fromScale(s, s))
        self.centerOn(self.centre_vue)
        self.minuteur.start()
        self.vue_changee.emit()
        self.viewport().update()

    def centrer(self, lat: float, lon: float, niveau: Optional[float] = None) -> None:
        self._ajustement = None
        if niveau is not None:
            self.niveau = max(NIVEAU_MIN, min(NIVEAU_MAX, niveau))
        self.centre_vue = geo_vers_scene(lat, lon)
        self._appliquer()

    def ajuster(self, lat: float, lon: float, rayon: float) -> None:
        if not self.isVisible():
            self.centrer(lat, lon, 19 if rayon < 1000 else 6)
            self._ajustement = (lat, lon, rayon)
            return
        self._ajustement = None
        demi = max(100.0, min(self.viewport().width(), self.viewport().height()) / 2 - 10)
        n = math.log2(demi * math.cos(math.radians(lat)) * 2 * DEMI_MONDE / (256 * max(rayon, 5.0)))
        self.centrer(lat, lon, min(20.0, math.floor(n * 4) / 4))

    def centre_geo(self) -> tuple[float, float]:
        return scene_vers_geo(self.centre_vue)

    def zoomer(self, delta: float, ancre: Optional[QPointF] = None) -> None:
        avant = self.echelle
        self.niveau = max(NIVEAU_MIN, min(NIVEAU_MAX, self.niveau + delta))
        apres = self.echelle
        if ancre is not None:
            milieu = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
            p = self.centre_vue + (ancre - milieu) / avant
            self.centre_vue = p - (ancre - milieu) / apres
        self._appliquer()

    def voir_france(self) -> None:
        self.ajuster(*FRANCE)

    def set_fond(self, mode: str) -> None:
        self.mode_fond = mode
        self._appliquer()

    def _maj_fond(self) -> None:
        code = fond_effectif(self.mode_fond, self.niveau)
        if code == self.fond.code:
            return
        self.fond = FONDS[code]
        for cle in [k for k in self.tuiles if k[0] not in (code, "cadastre", "routes")]:
            self.scene().removeItem(self.tuiles.pop(cle))
        for cle, it in self.tuiles.items():
            if cle[0] == "routes":
                it.setVisible(self.routes_visibles)
        self._maj_voile()

    @property
    def photo(self) -> bool:
        return self.fond.code == "ortho"

    @property
    def routes_visibles(self) -> bool:
        return self.routes and self.photo

    def _maj_voile(self) -> None:
        self.voile.setBrush(QBrush(QColor(255, 255, 255, int(round(255 * (1 - self.opacite))))))
        self.voile.setVisible(self.photo and self.opacite < 1)

    def set_opacite(self, v: float) -> None:
        self.opacite = v
        self._maj_voile()

    def set_routes(self, actif: bool) -> None:
        self.routes = actif
        for cle in [k for k in self.tuiles if k[0] == "routes"]:
            self.tuiles[cle].setVisible(self.routes_visibles)
        self.minuteur.start()
        self.viewport().update()

    def set_cadastre(self, actif: bool) -> None:
        self.cadastre = actif
        for cle in [k for k in self.tuiles if k[0] == "cadastre"]:
            self.tuiles[cle].setVisible(actif)
        self.minuteur.start()

    def set_consigne(self, texte: str) -> None:
        if texte != self.consigne:
            self.consigne = texte
            self.viewport().update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._fleche_depart = None
        self.viewport().setCursor(Qt.CrossCursor if mode in ("tracer", "giratoire", "mesurer", "anneau", "bordure",
                                                             "depart_arrivee")
                                  else Qt.OpenHandCursor)

    # ------------------------------------------------------------------ tuiles
    def _charger_tuiles(self) -> None:
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        couches = [self.fond]
        if self.cadastre and self.niveau >= 13:
            couches.append(CADASTRE)
        if self.routes_visibles and self.niveau >= 6:
            couches.append(ROUTES)
        for fond in couches:
            z = max(0, min(fond.zoom_max, int(round(self.niveau))))
            x0, y0 = tuile_de(r.left(), -r.top(), z)
            x1, y1 = tuile_de(r.right(), -r.bottom(), z)
            n = 2 ** z
            centre = tuile_de(self.centre_vue.x(), -self.centre_vue.y(), z)
            cles = [(fond.code, z, tx % n, ty) for tx in range(x0, x1 + 1) for ty in range(max(0, y0), min(n, y1 + 1))]
            cles.sort(key=lambda c: (c[2] - centre[0]) ** 2 + (c[3] - centre[1]) ** 2)
            for cle in cles:
                if cle not in self.tuiles and cle not in self.attente:
                    self._demander(fond, cle)
        self._nettoyer(r)

    def _demander(self, fond: Fond, cle: tuple) -> None:
        _, z, tx, ty = cle
        rep = self.nam.get(requete(url_tuile(fond, tx, ty, z)))
        self.attente[cle] = rep
        rep.finished.connect(lambda: self._recue(fond, cle, rep))

    def _recue(self, fond: Fond, cle: tuple, rep: QNetworkReply) -> None:
        self.attente.pop(cle, None)
        donnees = bytes(rep.readAll())
        ok = rep.error() == QNetworkReply.NoError
        statut = rep.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        rep.deleteLater()
        if fond.code not in ("cadastre", "routes") and fond.code != self.fond.code:
            return
        pm = QPixmap()
        if not ok or not pm.loadFromData(donnees):
            n = self.essais.get(cle, 0) + 1
            self.essais[cle] = n
            if n < 3:
                delai = int(delai_reessai(rep) * 1000) if statut == 429 else 700 * n
                QTimer.singleShot(delai, lambda: (cle not in self.tuiles and cle not in self.attente)
                                  and self._demander(fond, cle))
            return
        _, z, tx, ty = cle
        it = QGraphicsPixmapItem(pm)
        it.setTransformationMode(Qt.SmoothTransformation)
        it.setScale(taille_tuile(z) / pm.width())
        cx, cy = coin_tuile(tx, ty, z)
        it.setPos(cx, -cy)
        if fond.code == "cadastre":
            it.setZValue(-50 + z / 100)
            it.setOpacity(0.65)
            it.setVisible(self.cadastre)
        elif fond.code == "routes":
            it.setZValue(-40 + z / 100)
            it.setOpacity(0.8 if self.niveau <= 18.25 else 0.45)
            it.setVisible(self.routes_visibles)
        else:
            it.setZValue(-100 + z / 100)
        self.scene().addItem(it)
        self.tuiles[cle] = it

    def _nettoyer(self, visible: QRectF) -> None:
        if len(self.tuiles) <= MAX_TUILES:
            return
        z_actuel = int(round(self.niveau))
        marge = visible.adjusted(-visible.width(), -visible.height(), visible.width(), visible.height())
        for cle, it in list(self.tuiles.items()):
            if abs(cle[1] - min(z_actuel, 19)) > 2 or not marge.intersects(it.sceneBoundingRect()):
                self.scene().removeItem(it)
                del self.tuiles[cle]

    # ------------------------------------------------------------------ calques de dessin
    def vider(self, nom: str) -> None:
        for it in self.calques.pop(nom, []):
            self.scene().removeItem(it)

    def ajouter(self, nom: str, it: QGraphicsItem, z: float) -> QGraphicsItem:
        it.setZValue(z)
        self.scene().addItem(it)
        self.calques.setdefault(nom, []).append(it)
        return it

    # ------------------------------------------------------------------ souris
    def _donnee_sous(self, pos) -> Optional[tuple]:
        for it in self.items(pos):
            d = it.data(0)
            if d:
                return d
        return None

    def _geo(self, pos: QPointF) -> tuple[float, float]:
        return scene_vers_geo(self.mapToScene(pos.toPoint()))

    def mousePressEvent(self, ev):
        pos = ev.position()
        if ev.button() == Qt.MiddleButton:
            self._pan, self._appui, self._deplacee = pos, None, True
            return
        d = self._donnee_sous(pos.toPoint())
        if ev.button() == Qt.RightButton:
            self.menu.emit(d, *self._geo(pos), ev.globalPosition().toPoint())
            return
        if ev.button() != Qt.LeftButton:
            return super().mousePressEvent(ev)
        if getattr(self, "_rect_mentions", None) is not None and self._rect_mentions.contains(pos):
            self.mentions_cliquees.emit()
            return
        if d is not None and d[0] in ("sommet", "variante", "anneau"):
            self._glisse, self._appui, self._deplacee = d, pos, False
            return
        if self.mode == "depart_arrivee":
            self._fleche_depart = pos
            self.fleche.emit(*self._geo(pos), *self._geo(pos), False)
            return
        self._pan, self._appui, self._deplacee = pos, pos, False

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        lat, lon = self._geo(pos)
        self.curseur.emit(lat, lon)
        if self._glisse is not None:
            if not self._deplacee and (pos - self._appui).manhattanLength() < self.SEUIL_GLISSER:
                return
            self._deplacee = True
            self.poignee_glissee.emit(self._glisse, lat, lon, False)
            return
        if self._fleche_depart is not None:
            self.fleche.emit(*self._geo(self._fleche_depart), lat, lon, False)
            return
        if self._pan is not None:
            if not self._deplacee:
                if (pos - self._appui).manhattanLength() < self.SEUIL_GLISSER:
                    return
                self._deplacee = True
                self.viewport().setCursor(Qt.ClosedHandCursor)
            dp = pos - self._pan
            self._pan = pos
            self.centre_vue = self.centre_vue - dp / self.echelle
            self._appliquer()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        pos = ev.position()
        if self._glisse is not None:
            d, deplacee = self._glisse, self._deplacee
            self._glisse = None
            if deplacee:
                self.poignee_glissee.emit(d, *self._geo(pos), True)
            else:
                self.poignee_cliquee.emit(d)
            return
        if self._fleche_depart is not None:
            depart = self._fleche_depart
            self._fleche_depart = None
            self.fleche.emit(*self._geo(depart), *self._geo(pos), True)
            return
        if self._pan is not None:
            clic = not self._deplacee and self._appui is not None
            appui = self._appui
            self._pan, self._appui, self._deplacee = None, None, False
            self.set_mode(self.mode)
            if clic:
                self.clic.emit(*self._geo(appui))
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(ev)
        if self.mode in ("tracer", "anneau", "bordure"):
            self.double_clic.emit(*self._geo(ev.position()))
            return
        if self._donnee_sous(ev.position().toPoint()) is None:
            self._pan = None
            self.zoomer(1.0, ev.position())

    def wheelEvent(self, ev):
        crans = ev.angleDelta().y() / 120
        if not crans:
            return
        if ev.modifiers() & Qt.ControlModifier:
            d = self._donnee_sous(ev.position().toPoint())
            if d is not None and d[0] in ("sommet", "anneau"):
                self.poignee_molette.emit(d, crans)
                return
        self.zoomer((0.5 if ev.modifiers() & Qt.ShiftModifier else 1.0) * crans, ev.position())

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._ajustement:
            QTimer.singleShot(0, lambda: self._ajustement and self.ajuster(*self._ajustement))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.centerOn(self.centre_vue)
        self.minuteur.start()

    # ------------------------------------------------------------------ échelle, mentions, consigne
    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.resetTransform()
        h = self.viewport().height()
        w = self.viewport().width()
        m_par_px = self.metres_par_pixel()
        cible = 120 * m_par_px
        puissance = 10 ** math.floor(math.log10(cible))
        longueur = max(v * puissance for v in (1, 2, 5) if v * puissance <= cible)
        px = longueur / m_par_px
        f = QFont()
        f.setPointSizeF(8.5)
        painter.setFont(f)
        painter.setRenderHint(QPainter.Antialiasing, True)
        x0, y0 = 14, h - 18
        resolution = m_par_px * 100
        txt_res = f"{resolution:.0f} cm/pixel" if resolution < 100 else f"{resolution / 100:.1f} m/pixel"
        lg_txt = painter.fontMetrics().horizontalAdvance(txt_res) + 14
        painter.fillRect(QRectF(x0 - 6, y0 - 22, px + 12 + lg_txt, 30), QColor(255, 255, 255, 205))
        painter.setPen(QColor("#333333"))
        painter.drawText(QRectF(x0 + px + 8, y0 - 14, lg_txt, 16), Qt.AlignLeft | Qt.AlignVCenter, txt_res)
        painter.setPen(QPen(QColor("#222222"), 2))
        painter.drawLine(QPointF(x0, y0), QPointF(x0 + px, y0))
        painter.drawLine(QPointF(x0, y0 - 5), QPointF(x0, y0 + 2))
        painter.drawLine(QPointF(x0 + px, y0 - 5), QPointF(x0 + px, y0 + 2))
        txt = f"{longueur:g} m" if longueur < 1000 else f"{longueur / 1000:g} km"
        painter.drawText(QRectF(x0, y0 - 22, px, 16), Qt.AlignCenter, txt)
        mention = (self.fond.attribution + (" · " + ROUTES.attribution if self.routes_visibles else "")
                   + (" · " + CADASTRE.attribution if self.cadastre and self.niveau >= 13 else "") + "  ⓘ")
        fm = painter.fontMetrics()
        lw = fm.horizontalAdvance(mention) + 12
        self._rect_mentions = QRectF(w - lw, h - 18, lw, 18)
        painter.fillRect(QRectF(w - lw, h - 18, lw, 18), QColor(255, 255, 255, 205))
        painter.setPen(QColor("#333333"))
        painter.drawText(QRectF(w - lw, h - 18, lw, 18), Qt.AlignCenter, mention)
        painter.setPen(QPen(QColor("#222222"), 1.5))
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawEllipse(QPointF(w - 26, 28), 15, 15)
        fl = QPolygonF([QPointF(w - 26, 16), QPointF(w - 31, 32), QPointF(w - 26, 28), QPointF(w - 21, 32)])
        painter.setBrush(QColor("#185FA5"))
        painter.drawPolygon(fl)
        painter.drawText(QRectF(w - 41, 30, 30, 14), Qt.AlignCenter, "N")
        if self.consigne:
            f.setPointSizeF(10)
            f.setBold(True)
            painter.setFont(f)
            fm = painter.fontMetrics()
            lignes = self.consigne.split("\n")
            lw = max(fm.horizontalAdvance(t) for t in lignes) + 28
            lh = fm.height() * len(lignes) + 14
            cadre = QRectF((w - lw) / 2, 10, lw, lh)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(24, 95, 165, 230))
            painter.drawRoundedRect(cadre, 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(cadre, Qt.AlignCenter, self.consigne)
        painter.restore()
