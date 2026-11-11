"""Fenêtre principale de Giration : carte IGN, saisie assistée sur la photo, épure en direct, annulation à tout moment.

Principes (claude/giration-principes-ergonomie.md) : le trajet passe par les clics ; pendant la saisie, des guides
montrent ce que le véhicule peut faire ; en fin de saisie, les corrections du véhicule s'appliquent (rayons, puis
trajet « au mieux ») sans jamais déplacer un point ; on roule à droite (option à gauche) ; la photo fait foi.
"""
from __future__ import annotations

import copy
import json
import math
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPainterPath,
                           QPalette, QPen, QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsSimpleTextItem,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QStyle,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import DEVISE, __version__
from ..calcul import Resultat, calculer
from ..carto.chaussee import Chaussee, aimanter, construire_chaussee, mesurer
from ..carto.geoservices import MODES_FOND, SEUIL_PHOTO, lire_recherche, lire_routes, url_recherche, url_routes
from ..enveloppe import simplifiee
from ..georef import Georef
from ..projections import analyser_saisie, en_metropole, format_dms, systeme_legal, vers_local, vers_mercator
from ..vehicule import Bibliotheque
from ..vitesse import KMH, conduite_pour
from .carte import NIVEAU_PHOTO_20CM, CarteVue, Etiquette, Poignee, crayon
from .client import ClientIGN, creer_gestionnaire
from .modele import Historique, InfoSommet, Projet, Variante, construire_passages, projet_vide
from .saisie import (ECART_VISIBLE, ZONE_GIRATOIRE, Anneau, Controle, anneau_bdtopo, anneau_site, cercle_3_points,
                     controler, corrections, elastique, etat_rayon, gsite, guides, pct_braquage, site_releve,
                     tour_giratoire, trajet)
from .outils_lot2 import OutilsLot2
from .widgets import Bandeau, ChoixVehicule, GrapheVitesse, Silhouette

TITRE = "Giration"
VEHICULE_DEFAUT = "articule-tracteur-4x2-semi-standard-1360"
BLEU = QColor("#185FA5")
ROUGE = QColor("#E53935")
ORANGE = QColor("#E8730C")
VERT = QColor("#2E9D3A")
JAUNE = QColor("#F2C200")
COULEUR_ETAT = {"ok": BLEU, "limite": ORANGE, "impossible": ROUGE}
ZOOM_LIEU = {"municipality": 15, "street": 17, "locality": 17, "housenumber": 18}
RAYON_ROUTES = 250.0
MODES = {"modifier": "Modifier", "tracer": "Tracer", "giratoire": "Giratoire", "mesurer": "Mesurer", "anneau": "Anneau",
         "bordure": "Bordure"}
ANGLES_POIGNEES = {"ext": -35.0, "int": -55.0, "ilot": -75.0}     # position des poignées de rayon sur les cercles (°)


def _fr(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}".replace(".", ",")


def _liste_points(nums: list[int]) -> str:
    return ("les points " + ", ".join(map(str, nums[:-1])) + " et " + str(nums[-1])) if len(nums) > 1 else f"le point {nums[0]}"


# ====================================================================== calcul en arrière-plan
class _Signaux(QObject):
    fini = Signal(int, object, object, str)           # génération, résultat, compléments, nature


class _Tache(QRunnable):
    def __init__(self, gen, fonction, signaux, nature):
        super().__init__()
        self.gen, self.fonction, self.signaux, self.nature = gen, fonction, signaux, nature

    def run(self):
        try:
            res, extra = self.fonction()
        except Exception as exc:                 # le message est affiché dans le panneau des résultats
            res, extra = exc, traceback.format_exc()
        self.signaux.fini.emit(self.gen, res, extra, self.nature)


class Repere:
    """Plan de calcul (Lambert-93) → scène de la carte (Mercator), par une application affine locale.

    L'écart avec la conversion exacte reste inférieur au centimètre à 1 km du point de référence : il ne sert
    qu'à l'affichage, les calculs se font dans le plan."""

    def __init__(self, geo: Georef, x0: float, y0: float):
        self.geo, self.x0, self.y0 = geo, x0, y0

        def sc(x, y):
            lat, lon = geo.vers_geo(x, y)
            mx, my = vers_mercator(lat, lon)
            return np.array([mx, -my])
        self.o = sc(x0, y0)
        self.j = np.column_stack([(sc(x0 + 100, y0) - self.o) / 100, (sc(x0, y0 + 100) - self.o) / 100])

    def proche(self, x: float, y: float) -> bool:
        return math.hypot(x - self.x0, y - self.y0) < 1500

    def points(self, pts) -> list[QPointF]:
        a = np.asarray(pts, dtype=float).reshape(-1, 2) - [self.x0, self.y0]
        s = a @ self.j.T + self.o
        return [QPointF(x, y) for x, y in s]

    def point(self, x: float, y: float) -> QPointF:
        return self.points([(x, y)])[0]


# ====================================================================== fenêtre
class FenetreGiration(OutilsLot2, QMainWindow):
    def __init__(self, chemin: Optional[str] = None):
        super().__init__()
        self._init_lot2()
        self.reglages = QSettings()
        self.biblio = Bibliotheque()
        vid = self.reglages.value("vehicule", VEHICULE_DEFAUT)
        self.projet = projet_vide(vid if vid in self.biblio.vehicules else VEHICULE_DEFAUT)
        self.historique = Historique(self.projet)
        self.chemin: Optional[Path] = None
        self.modifie = False
        self.geo = Georef("L93")
        self.repere: Optional[Repere] = None
        # résultats du calcul de la variante active
        self.resultat: Optional[Resultat] = None
        self.infos: list[InfoSommet] = []
        self.trace_saisie = None
        self.au_mieux = None                              # saisie.AuMieux quand le véhicule fait au mieux
        self.controle = Controle()
        self.inactifs: dict[str, Resultat] = {}           # clé de variante → résultat complet
        self.selection: Optional[int] = None
        self.position_auto = True
        # routes BD TOPO et corrections de l'utilisateur
        self.troncons: list = []
        self.chaussee = Chaussee()
        self._cle_chaussee = None
        self.zone_routes: Optional[tuple[float, float]] = None
        self._cache_anneau: tuple = (None, None)
        # saisie en cours
        self.guide = None
        self.apercu: Optional[Resultat] = None            # fantôme : le véhicule si l'on cliquait ici
        self.avant_correction = None                      # saisie d'avant les corrections (pointillé rouge)
        self.proposition = None                           # corrections proposées : trajet du véhicule (vert)
        self._mesure_a: Optional[tuple] = None
        self._girat_entree: Optional[tuple] = None
        self._releve: Optional[dict] = None              # anneau en cours de relevé sur la photo
        self._site_edite: Optional[dict] = None          # anneau pendant le glisser d'une poignée
        self._curseur_plan: Optional[tuple] = None
        self._apres_calcul: Optional[Callable] = None
        # calcul en arrière-plan : un seul fil, la dernière demande seule compte
        self._gen = 0
        self._gen_affiche = 0
        self._gen_apercu = 0
        self._occupe = False
        self._attente: Optional[tuple] = None
        self._attente_apercu: Optional[tuple] = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signaux = _Signaux()
        self.signaux.fini.connect(self._calcul_fini)
        self.nam = creer_gestionnaire(self)
        self.client = ClientIGN(self.nam, self)
        self.resize(1500, 900)

        self.carte = CarteVue(self.nam, self)
        self.bandeau = Bandeau(self.carte.viewport())
        gauche, droite = self._panneau_gauche(), self._panneau_droit()
        sep = QSplitter(Qt.Horizontal)
        for w in (self._defilant(gauche, 250), self.carte, self._defilant(droite, 300)):
            sep.addWidget(w)
        sep.setStretchFactor(1, 1)
        sep.setSizes([260, 1000, 320])
        centre = QWidget()
        lc = QVBoxLayout(centre)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.setSpacing(0)
        lc.addWidget(sep, 1)
        lc.addWidget(self._assistant())
        self.setCentralWidget(centre)
        self._barres_outils()
        self._menus()
        self.lbl_curseur = QLabel()
        self.statusBar().addPermanentWidget(self.lbl_curseur)

        c = self.carte
        c.curseur.connect(self._curseur)
        c.clic.connect(self._clic_carte)
        c.double_clic.connect(lambda *_: self.terminer_trace())
        c.poignee_glissee.connect(self._poignee_glissee)
        c.poignee_cliquee.connect(self._poignee_cliquee)
        c.poignee_molette.connect(self._poignee_molette)
        c.menu.connect(self._menu_carte)
        c.vue_changee.connect(self._maj_assistant)
        for touche, slot in ((Qt.Key_T, lambda: self.choisir_mode("tracer")),
                             (Qt.Key_G, lambda: self.choisir_mode("giratoire")),
                             (Qt.Key_M, lambda: self.choisir_mode("mesurer")),
                             (Qt.Key_A, lambda: self.choisir_mode("anneau")),
                             (Qt.Key_B, lambda: self.choisir_mode("bordure")),
                             (Qt.Key_Escape, self._echap),
                             (Qt.Key_Return, self.terminer_trace), (Qt.Key_Enter, self.terminer_trace),
                             (Qt.Key_Backspace, self._retour_arriere), (Qt.Key_Delete, self.supprimer_point)):
            r = QShortcut(QKeySequence(touche), self.carte, slot)
            r.setContext(Qt.WidgetWithChildrenShortcut)

        self.anim = QTimer(self)
        self.anim.setInterval(40)
        self.anim.timeout.connect(self._animer)
        self._restaurer()
        if chemin:
            self.ouvrir(chemin)
        self._tout_rafraichir()

    # ================================================================== construction de l'interface
    def _defilant(self, w: QWidget, largeur: int) -> QScrollArea:
        d = QScrollArea()
        d.setWidgetResizable(True)
        d.setWidget(w)
        d.setMinimumWidth(largeur)
        d.setFrameShape(QFrame.NoFrame)
        return d

    def _titre_section(self, texte: str) -> QLabel:
        l = QLabel(texte)
        f = l.font()
        f.setBold(True)
        l.setFont(f)
        l.setStyleSheet("color: palette(mid); margin-top: 6px;")
        return l

    def _panneau_gauche(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._titre_section("Véhicule"))
        self.lbl_vehicule = QLabel()
        self.lbl_vehicule.setWordWrap(True)
        f = self.lbl_vehicule.font()
        f.setBold(True)
        self.lbl_vehicule.setFont(f)
        lay.addWidget(self.lbl_vehicule)
        self.lbl_vehicule2 = QLabel()
        self.lbl_vehicule2.setWordWrap(True)
        lay.addWidget(self.lbl_vehicule2)
        self.silhouette = Silhouette()
        lay.addWidget(self.silhouette)
        b = QPushButton("Changer de véhicule…")
        b.setToolTip("Bibliothèque de véhicules (données des constructeurs et du guide CERTU) — le trajet est conservé")
        b.clicked.connect(self.choisir_vehicule)
        lay.addWidget(b)
        lay.addWidget(self._titre_section("Historique"))
        self.liste_historique = QListWidget()
        self.liste_historique.setToolTip("Cliquez une étape pour y revenir (Ctrl+Z / Ctrl+Y pour annuler ou rétablir)")
        self.liste_historique.itemClicked.connect(self._historique_clique)
        self.liste_historique.setMinimumHeight(170)
        lay.addWidget(self.liste_historique, 1)
        lay.addWidget(self._titre_section("Variantes"))
        self.liste_variantes = QListWidget()
        self.liste_variantes.setToolTip("Chaque essai est une variante : cliquez pour l'activer, les autres restent "
                                        "affichées en contour pour comparer")
        self.liste_variantes.itemClicked.connect(self._variante_cliquee)
        self.liste_variantes.setMaximumHeight(130)
        lay.addWidget(self.liste_variantes)
        h = QHBoxLayout()
        for texte, slot, aide in (("Nouvelle", self.nouvelle_variante, "Copie de la variante active, à modifier"),
                                  ("Renommer", self.renommer_variante, ""),
                                  ("Supprimer", self.supprimer_variante, "")):
            b = QPushButton(texte)
            b.setToolTip(aide)
            b.clicked.connect(slot)
            h.addWidget(b)
        lay.addLayout(h)
        return w

    def _panneau_droit(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        self.verdict.setTextFormat(Qt.RichText)
        self.verdict.setMinimumHeight(60)
        lay.addWidget(self.verdict)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.valeurs = {}
        for cle, libelle, aide in (
                ("emprise", "Emprise max", "Largeur balayée maximale par la carrosserie, perpendiculairement au trajet"),
                ("roues", "Emprise des roues", "Largeur balayée maximale par les roues (« entre trottoirs »)"),
                ("rayon", "Rayon mini", "Plus petit rayon du trajet du milieu de l'essieu avant"),
                ("braquage", "Braquage max", "Braquage maximal atteint, en % du braquage possible du véhicule"),
                ("articulation", "Articulation", "Angle maximal entre deux éléments (ensembles articulés)"),
                ("vitesse", "Vitesse", "Vitesse automatique : entrée → minimum → sortie"),
                ("saisie", "Votre saisie", "Le véhicule suit-il exactement les points cliqués ?"),
                ("chaussee", "Chaussée", "Comparaison avec la chaussée estimée d'après la BD TOPO de l'IGN "
                                         "(et l'îlot du site Girabase)")):
            l = QLabel("—")
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            l.setWordWrap(True)
            lab = QLabel(libelle)
            lab.setToolTip(aide)
            form.addRow(lab, l)
            self.valeurs[cle] = l
        lay.addLayout(form)
        self.graphe = GrapheVitesse()
        lay.addWidget(self.graphe)
        h = QHBoxLayout()
        h.addWidget(QLabel("Vitesse calculée automatiquement ;\nconduite :"))
        self.cb_conduite = QComboBox()
        for code, lib in (("prudent", "prudente"), ("normal", "normale"), ("expérimenté", "expérimentée")):
            self.cb_conduite.addItem(lib, code)
        self.cb_conduite.setToolTip("Prudente : moins d'accélération latérale et volant tourné moins vite ; "
                                    "normale : valeurs du CERTU (15 °/s)")
        self.cb_conduite.activated.connect(self._conduite_changee)
        h.addWidget(self.cb_conduite)
        lay.addLayout(h)
        self.bt_corriger = QPushButton("Corrections du véhicule")
        self.bt_corriger.setToolTip("Élargit les virages trop serrés ; là où la place manque, le véhicule fait au mieux. "
                                    "Aucun point n'est déplacé ; Ctrl+Z rend votre saisie.")
        self.bt_corriger.clicked.connect(self.corrections_vehicule)
        lay.addWidget(self.bt_corriger)
        # point sélectionné
        self.grp_point = QGroupBox("Point sélectionné")
        gl = QVBoxLayout(self.grp_point)
        self.lbl_point = QLabel()
        self.lbl_point.setWordWrap(True)
        gl.addWidget(self.lbl_point)
        h = QHBoxLayout()
        self.ck_auto = QCheckBox("Rayon proposé")
        self.ck_auto.setToolTip("Rayon du cercle passant par le point et ses voisins (entre 1 et 14 m)")
        self.ck_auto.toggled.connect(self._rayon_auto_change)
        h.addWidget(self.ck_auto)
        self.sp_rayon = QDoubleSpinBox()
        self.sp_rayon.setRange(1.0, 500.0)
        self.sp_rayon.setDecimals(1)
        self.sp_rayon.setSingleStep(0.5)
        self.sp_rayon.setSuffix(" m")
        self.sp_rayon.setKeyboardTracking(False)
        self.sp_rayon.valueChanged.connect(self._rayon_saisi)
        h.addWidget(self.sp_rayon)
        gl.addLayout(h)
        b = QPushButton("Supprimer ce point")
        b.clicked.connect(self.supprimer_point)
        gl.addWidget(b)
        lay.addWidget(self.grp_point)
        # position du véhicule
        lay.addWidget(self._titre_section("Position du véhicule"))
        h = QHBoxLayout()
        self.bt_lecture = QToolButton()
        self.bt_lecture.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.bt_lecture.setToolTip("Faire rouler le véhicule à la vitesse calculée")
        self.bt_lecture.clicked.connect(self._lecture)
        h.addWidget(self.bt_lecture)
        self.sl_position = QSlider(Qt.Horizontal)
        self.sl_position.setRange(0, 1000)
        self.sl_position.valueChanged.connect(self._position_changee)
        h.addWidget(self.sl_position, 1)
        lay.addLayout(h)
        lay.addWidget(self._titre_section("Remarques"))
        self.lbl_alertes = QLabel("—")
        self.lbl_alertes.setWordWrap(True)
        self.lbl_alertes.setTextFormat(Qt.RichText)
        self.lbl_alertes.setAlignment(Qt.AlignTop)
        lay.addWidget(self.lbl_alertes, 1)
        return w

    def _assistant(self) -> QWidget:
        f = QFrame()
        f.setObjectName("assistant")
        f.setStyleSheet("QFrame#assistant { border-top: 1px solid palette(mid); background: palette(alternate-base); }")
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 6, 10, 6)
        ic = QLabel()
        ic.setPixmap(self.style().standardIcon(QStyle.SP_MessageBoxInformation).pixmap(22, 22))
        h.addWidget(ic)
        self.lbl_assistant = QLabel()
        self.lbl_assistant.setWordWrap(True)
        self.lbl_assistant.setTextFormat(Qt.RichText)
        h.addWidget(self.lbl_assistant, 1)
        return f

    def _option(self, cle: str, defaut: bool) -> bool:
        v = self.reglages.value(cle, defaut)
        return v in (True, "true", "1", 1) if not isinstance(v, bool) else v

    def _barres_outils(self) -> None:
        tb = QToolBar("Carte")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.saisie = QLineEdit()
        self.saisie.setPlaceholderText("Commune, adresse ou coordonnées (43.8161, 2.1698 · X Y Lambert-93)")
        self.saisie.setClearButtonEnabled(True)
        self.saisie.setMinimumWidth(320)
        self.saisie.returnPressed.connect(self.rechercher)
        tb.addWidget(self.saisie)
        b = QPushButton("Rechercher")
        b.clicked.connect(self.rechercher)
        tb.addWidget(b)
        b = QPushButton("France entière")
        b.clicked.connect(self.carte.voir_france)
        tb.addWidget(b)
        b = QPushButton("Photo 20 cm")
        b.setToolTip("Zoomer à la pleine résolution de la photographie aérienne de l'IGN (20 cm par pixel)")
        b.clicked.connect(lambda: self.carte.centrer(*self.carte.centre_geo(), NIVEAU_PHOTO_20CM))
        tb.addWidget(b)
        tb.addSeparator()
        tb.addWidget(QLabel(" Fond : "))
        self.cb_fond = QComboBox()
        for code, libelle in MODES_FOND.items():
            self.cb_fond.addItem(libelle, code)
        self.cb_fond.setToolTip(f"En automatique, la photographie aérienne remplace le plan à partir du niveau {SEUIL_PHOTO}")
        self.cb_fond.currentIndexChanged.connect(lambda _i: self.carte.set_fond(self.cb_fond.currentData()))
        tb.addWidget(self.cb_fond)
        tb.addWidget(QLabel("  Photo : "))
        self.sl_opacite = QSlider(Qt.Horizontal)
        self.sl_opacite.setRange(20, 100)
        self.sl_opacite.setValue(100)
        self.sl_opacite.setFixedWidth(80)
        self.sl_opacite.setToolTip("Opacité de la photographie aérienne")
        self.sl_opacite.valueChanged.connect(lambda v: self.carte.set_opacite(v / 100))
        tb.addWidget(self.sl_opacite)
        bt = QToolButton()
        bt.setText("Couches ")
        bt.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(bt)
        m.setToolTipsVisible(True)

        def couche(texte, aide, coche, slot):
            a = m.addAction(texte)
            a.setCheckable(True)
            a.setChecked(coche)
            a.setToolTip(aide)
            a.toggled.connect(slot)
            return a
        self.ck_routes_ign = couche("Routes IGN (noms et numéros, sur la photo)", "Couche « Routes » de cartes.gouv.fr",
                                    False, self.carte.set_routes)
        self.ck_chaussee = couche("Chaussée estimée (BD TOPO) et giratoire",
                                  "Axe et largeur de chaussée des tronçons BD TOPO ; îlot, bande franchissable et zone "
                                  "non contrôlée du giratoire", True, lambda _: self._redessiner())
        self.ck_cadastre = couche("Parcelles cadastrales", "Parcellaire Express de l'IGN", False, self.carte.set_cadastre)
        self.ck_bati = couche("Bâtiments (BD TOPO), contrôlés comme obstacles",
                              "Bâti de la BD TOPO : un contact de la caisse donne une réserve (précision de quelques "
                              "mètres ; dessinez l'obstacle avec l'outil Bordure pour un contrôle strict)", True,
                              lambda _: (self._controler_lot2(self.resultat), self._maj_resultats(), self._redessiner()))
        bt.setMenu(m)
        tb.addWidget(bt)

        tb2 = QToolBar("Giration")
        tb2.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(tb2)
        self.act_annuler = QAction("↶ Annuler", self)
        self.act_annuler.setShortcut(QKeySequence.Undo)
        self.act_annuler.setToolTip("Annuler la dernière action (Ctrl+Z) — à tout moment")
        self.act_annuler.triggered.connect(self.annuler)
        self.act_retablir = QAction("↷ Rétablir", self)
        self.act_retablir.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.act_retablir.triggered.connect(self.retablir)
        tb2.addAction(self.act_annuler)
        tb2.addAction(self.act_retablir)
        tb2.addSeparator()
        groupe = QActionGroup(self)
        self.actions_mode = {}
        for mode, texte, aide in (("modifier", "Modifier", "Déplacer la carte et les points (Échap)"),
                                  ("tracer", "Tracer  T", "Cliquer le départ puis les points de passage ; le trajet "
                                                          "passe par vos clics (T)"),
                                  ("giratoire", "Giratoire  G", "Cliquer la branche d'entrée puis la branche de sortie : "
                                                                "le logiciel fait le tour de l'îlot (G)"),
                                  ("mesurer", "Mesurer  M", "Mesurer une largeur sur la photo, d'un bord de chaussée à "
                                                            "l'autre ; corrige la BD TOPO (M)"),
                                  ("anneau", "Anneau  A", "Relever le giratoire sur la photo : 3 clics sur la bordure "
                                                          "extérieure, puis le bord intérieur de la chaussée, puis "
                                                          "l'îlot. Remplace l'anneau de la BD TOPO (A)")):
            a = QAction(texte, self)
            a.setCheckable(True)
            a.setToolTip(aide)
            a.triggered.connect(lambda _=False, m=mode: self.choisir_mode(m))
            groupe.addAction(a)
            tb2.addAction(a)
            self.actions_mode[mode] = a
        self._outil_bordure(tb2, groupe)
        self.actions_mode["modifier"].setChecked(True)
        a = QAction("Terminer  ⏎", self)
        a.setToolTip("Fin de saisie : corrections liées au véhicule (Entrée, double-clic ou clic droit)")
        a.triggered.connect(self.terminer_trace)
        tb2.addAction(a)
        tb2.addSeparator()
        a = QAction("Nouvelle variante", self)
        a.setToolTip("Nouvelle variante (copie de l'actuelle) pour comparer deux essais")
        a.triggered.connect(self.nouvelle_variante)
        tb2.addAction(a)
        a = QAction("Comparer", self)
        a.setToolTip("Comparer les variantes côte à côte (même trajet avec un autre véhicule, ou autre trajet)")
        a.triggered.connect(self.comparer_variantes)
        tb2.addAction(a)
        a = QAction("Centrer", self)
        a.setToolTip("Centrer la carte sur la giration")
        a.triggered.connect(self.centrer_giration)
        tb2.addAction(a)
        tb2.addSeparator()
        bt = QToolButton()
        bt.setText("Exporter : DXF · Note PDF · KML ")
        bt.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(bt)
        for texte, slot in (("DXF (AutoCAD, COVADIS) : Lambert-93 ou CC…", self.exporter_dxf_systeme),
                            ("Note de giration (PDF)…", self.exporter_note),
                            ("KML (Google Earth)…", self.exporter_kml), ("GeoJSON (QGIS)…", self.exporter_geojson),
                            ("JSON du calcul…", self.exporter_json), ("Image de la carte (PNG)…", self.exporter_image)):
            m.addAction(texte).triggered.connect(slot)
        bt.setMenu(m)
        tb2.addWidget(bt)

    def _menus(self) -> None:
        m = self.menuBar().addMenu("&Fichier")
        for texte, slot, rac in (("Nouveau projet", self.nouveau, QKeySequence.New),
                                 ("Ouvrir…", self.ouvrir_dialogue, QKeySequence.Open),
                                 ("Enregistrer", self.enregistrer, QKeySequence.Save),
                                 ("Enregistrer sous…", self.enregistrer_sous, QKeySequence.SaveAs)):
            a = m.addAction(texte)
            a.setShortcut(QKeySequence(rac))
            a.triggered.connect(slot)
        m.addSeparator()
        m.addAction("Ouvrir un site Girabase (.gsite)…").triggered.connect(self.ouvrir_site)
        self.act_exporter_site = m.addAction("Enregistrer l'anneau pour Girabase (.gsite)…")
        self.act_exporter_site.triggered.connect(self.exporter_site)
        self.act_retirer_site = m.addAction("Retirer l'anneau (relevé ou Girabase)")
        self.act_retirer_site.triggered.connect(self.retirer_site)
        m.addSeparator()
        for texte, slot in (("Exporter en KML…", self.exporter_kml), ("Exporter en GeoJSON…", self.exporter_geojson),
                            ("Exporter le calcul en JSON…", self.exporter_json),
                            ("Exporter en DXF (Lambert-93 ou CC)…", self.exporter_dxf_systeme),
                            ("Note de giration (PDF)…", self.exporter_note),
                            ("Image de la carte…", self.exporter_image)):
            m.addAction(texte).triggered.connect(slot)
        m.addSeparator()
        m.addAction("Quitter").triggered.connect(self.close)
        m = self.menuBar().addMenu("&Édition")
        m.addAction(self.act_annuler)
        m.addAction(self.act_retablir)
        m.addSeparator()
        m.addAction("Supprimer le point sélectionné (Suppr)").triggered.connect(self.supprimer_point)
        m.addAction("Effacer le trajet").triggered.connect(self.effacer_trace)
        m.addAction("Effacer les bordures et obstacles").triggered.connect(self.effacer_contraintes)
        m.addSeparator()
        m.addAction("Comparer les variantes…").triggered.connect(self.comparer_variantes)
        m.addAction("Même trajet, autre véhicule…").triggered.connect(self.comparer_autre_vehicule)
        m.addAction("Inverser le sens de marche").triggered.connect(self.inverser)
        m = self.menuBar().addMenu("&Options")
        m.setToolTipsVisible(True)

        def opt(texte, cle, defaut, aide):
            a = m.addAction(texte)
            a.setCheckable(True)
            a.setChecked(self._option(cle, defaut))
            a.setToolTip(aide)
            a.toggled.connect(lambda v, c=cle: (self.reglages.setValue(c, v), self._redessiner()))
            return a
        self.opt_guides = opt("Guides de braquage pendant la saisie", "guides", True,
                              "Pointillé rouge : limite du braquage ; pointillé bleu : virage confortable")
        self.opt_fantome = opt("Fantôme du véhicule pendant la saisie", "fantome", True,
                               "Le véhicule et son enveloppe si l'on cliquait ici")
        self.opt_calage = opt("Caler le départ et l'arrivée au milieu de la voie", "calage", True,
                              "Voie de droite selon le sens de marche (BD TOPO) ; Alt + clic pour ne pas caler")
        m.addSeparator()
        g = QActionGroup(self)
        office = self.reglages.value("corrections", "office") != "proposees"
        self.opt_office = m.addAction("Corrections de fin de saisie : d'office")
        self.opt_office.setToolTip("Appliquées tout de suite ; votre saisie reste en pointillé rouge, Ctrl+Z la rend")
        self.opt_proposees = m.addAction("Corrections de fin de saisie : proposées")
        self.opt_proposees.setToolTip("Affichées en vert, avec « Appliquer » ou « Ignorer »")
        for a, v in ((self.opt_office, "office"), (self.opt_proposees, "proposees")):
            a.setCheckable(True)
            g.addAction(a)
            a.triggered.connect(lambda _=False, v=v: self.reglages.setValue("corrections", v))
        (self.opt_office if office else self.opt_proposees).setChecked(True)
        m.addSeparator()
        self.opt_ignorer = m.addAction("Ne pas tenir compte de la chaussée BD TOPO (pointillé bleu ciel)")
        self.opt_ignorer.setCheckable(True)
        self.opt_ignorer.setToolTip("Quand les routes de l'IGN sont décalées de la photo : plus de calage sur la voie ni "
                                    "d'alerte « roues hors chaussée » ; le giratoire se relève sur la photo (Anneau, A). "
                                    "Enregistré avec le projet.")
        self.opt_ignorer.triggered.connect(self._ignorer_chaussee)
        m.addSeparator()
        self.opt_gauche = m.addAction("Circulation à gauche")
        self.opt_gauche.setCheckable(True)
        self.opt_gauche.setToolTip("En France on roule à droite ; l'option sert à l'étranger (voies et sens des giratoires)")
        self.opt_gauche.triggered.connect(self._circulation_changee)
        m.addAction("Marge au bord extérieur de l'anneau…").triggered.connect(self._marge_giratoire)
        m = self.menuBar().addMenu("&Carte")
        a = m.addAction("France entière")
        a.setShortcut(QKeySequence("Ctrl+F"))
        a.triggered.connect(self.carte.voir_france)
        m.addAction("Centrer sur la giration").triggered.connect(self.centrer_giration)
        m.addAction("Recharger les routes (BD TOPO)").triggered.connect(lambda: self.charger_routes(forcer=True))
        m.addAction("Annuler les corrections de la chaussée").triggered.connect(self.oublier_corrections_chaussee)
        m = self.menuBar().addMenu("&?")
        m.addAction("Mode d'emploi").triggered.connect(self.aide)
        m.addAction("À propos").triggered.connect(self.a_propos)

    # ================================================================== état et historique
    @property
    def variante(self) -> Variante:
        return self.projet.variante

    @property
    def vehicule(self):
        return self.biblio.get(self.variante.vehicule_id)

    @property
    def routes(self) -> Chaussee:
        """Chaussée estimée d'après la BD TOPO, ou rien si l'utilisateur a choisi de ne pas en tenir compte."""
        return Chaussee() if self.projet.ignorer_chaussee else self.chaussee

    def _ignorer_chaussee(self, ignorer: bool) -> None:
        if ignorer != self.projet.ignorer_chaussee:
            self.projet.ignorer_chaussee = ignorer
            self._cache_anneau = (None, None)
            self._changer("Chaussée BD TOPO ignorée (la photo fait foi)" if ignorer else
                          "Chaussée BD TOPO prise en compte")

    def echelle(self) -> float:
        pts = self.variante.points
        return self.geo.echelle(*pts[0]) if (self.geo.systeme and pts) else 1.0

    def rmin(self, var: Optional[Variante] = None) -> float:
        """Rayon minimal du trajet du milieu de l'essieu avant, dans les unités du plan."""
        veh = self.biblio.get((var or self.variante).vehicule_id)
        return veh.rayon_P_min() * self.echelle()

    def _changer(self, libelle: str, calcul: bool = True) -> None:
        """Enregistre l'état courant dans l'historique (annulable) puis rafraîchit."""
        self.variante.normaliser()
        self.historique.ajouter(libelle, self.projet)
        self.modifie = True
        self._tout_rafraichir(calcul)

    def annuler(self) -> None:
        p = self.historique.annuler()
        if p is not None:
            self._restaurer_etat(p)

    def retablir(self) -> None:
        p = self.historique.retablir()
        if p is not None:
            self._restaurer_etat(p)

    def _historique_clique(self, it: QListWidgetItem) -> None:
        self._restaurer_etat(self.historique.aller(self.liste_historique.row(it)))

    def _restaurer_etat(self, p: Projet) -> None:
        self.projet = p
        self.selection = None
        self.modifie = True
        self.avant_correction = None
        self.proposition = None
        self.bandeau.fermer(appeler=False)
        self._tout_rafraichir()

    def _tout_rafraichir(self, calcul: bool = True) -> None:
        self._reconstruire_chaussee()
        v = self.vehicule
        self.lbl_vehicule.setText(v.nom.split(" — ")[0])
        self.lbl_vehicule2.setText((v.nom.split(" — ")[1] if " — " in v.nom else v.constructeur) +
                                   f"\n{_fr(v.longueur_totale, 2)} m × {_fr(v.largeur, 2)} m · rayon mini du trajet "
                                   f"{_fr(v.rayon_P_min(), 1)} m")
        self.silhouette.definir(v)
        self.cb_conduite.setCurrentIndex(max(0, self.cb_conduite.findData(self.variante.conduite)))
        self.opt_gauche.setChecked(self.projet.gauche)
        self.opt_ignorer.setChecked(self.projet.ignorer_chaussee)
        self.act_retirer_site.setEnabled(self.projet.site is not None)
        self.act_exporter_site.setEnabled(self.projet.site is not None)
        # historique
        self.liste_historique.blockSignals(True)
        self.liste_historique.clear()
        for i, lib in enumerate(self.historique.libelles()):
            it = QListWidgetItem(lib)
            if i == self.historique.index:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            elif i > self.historique.index:
                it.setForeground(QBrush(self.palette().color(QPalette.PlaceholderText)))
            self.liste_historique.addItem(it)
        self.liste_historique.setCurrentRow(self.historique.index)
        self.liste_historique.scrollToItem(self.liste_historique.item(self.historique.index))
        self.liste_historique.blockSignals(False)
        # variantes
        self.liste_variantes.clear()
        for i, var in enumerate(self.projet.variantes):
            pm = QPixmap(14, 14)
            pm.fill(QColor(var.couleur))
            veh = self.biblio.get(var.vehicule_id)
            it = QListWidgetItem(QIcon(pm), f"{var.nom} — {veh.nom_court or veh.nom.split(' — ')[0]}"
                                 + (" (active)" if i == self.projet.active else ""))
            if i == self.projet.active:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.liste_variantes.addItem(it)
        self.act_annuler.setEnabled(self.historique.peut_annuler)
        self.act_retablir.setEnabled(self.historique.peut_retablir)
        self._titre()
        self._maj_guide()
        if calcul:
            self.lancer_calcul()
        self._redessiner()
        self._maj_point()
        self._maj_assistant()

    def _titre(self) -> None:
        nom = self.chemin.stem if self.chemin else "Nouveau projet"
        self.setWindowTitle(f"{nom}{' *' if self.modifie else ''} — {TITRE} {__version__}")

    # ================================================================== coordonnées
    def _plan(self, lat: float, lon: float) -> tuple[float, float]:
        return self.geo.depuis_geo(lat, lon)

    def _assurer_geo(self, lat: float, lon: float) -> None:
        """Plan de calcul : Lambert-93 en métropole, plan local orienté au Nord ailleurs (outre-mer)."""
        if not any(v.points for v in self.projet.variantes):
            if en_metropole(lat, lon):
                if self.geo.systeme != "L93":
                    self.geo = Georef("L93")
                self.projet.systeme, self.projet.origine = "L93", None
            else:
                self.geo = Georef(None, (lat, lon))
                self.projet.systeme, self.projet.origine = None, [lat, lon]

    def _repere(self, x: Optional[float] = None, y: Optional[float] = None) -> Optional[Repere]:
        pts = [p for v in self.projet.variantes for p in v.points]
        if pts:
            x, y = pts[0]
        elif x is None:
            if self.repere is not None and self.repere.geo is self.geo:
                return self.repere
            try:
                x, y = self.geo.depuis_geo(*self.carte.centre_geo())
            except (ValueError, ZeroDivisionError):
                return None
        if self.repere is None or not self.repere.proche(x, y) or self.repere.geo is not self.geo:
            self.repere = Repere(self.geo, x, y)
        return self.repere

    def _caler(self, x: float, y: float, cap: Optional[float]) -> Optional[tuple[float, float, str]]:
        """Milieu de la voie (sens de marche), sauf option désactivée ou touche Alt."""
        alt = bool(QGuiApplication.keyboardModifiers() & Qt.AltModifier)
        if not self.opt_calage.isChecked() or alt:
            return None
        return aimanter(self.routes, x, y, cap, self.projet.gauche)

    # ================================================================== outils de la carte
    def choisir_mode(self, mode: str) -> None:
        if mode in ("tracer", "giratoire", "mesurer", "anneau", "bordure") and self.carte.niveau < SEUIL_PHOTO - 0.25:
            self.statusBar().showMessage("Zoomez d'abord jusqu'à la photographie aérienne (molette ou recherche).", 6000)
        self.actions_mode[mode].setChecked(True)
        self.carte.set_mode(mode)
        self.carte.setFocus(Qt.OtherFocusReason)
        self._mesure_a = None
        self._girat_entree = None
        self._releve = {"ext": []} if mode == "anneau" else None
        if mode != "bordure":
            self._contrainte = None
        self.apercu = None
        if mode in ("giratoire", "mesurer") and not self.troncons:
            self.charger_routes(centre=self.carte.centre_geo())
        self.carte.vider("elastique")
        self.carte.vider("apercu")
        self._maj_guide()
        self._redessiner()
        self._maj_assistant()

    def _echap(self) -> None:
        if self.carte.mode != "modifier":
            self.choisir_mode("modifier")
        else:
            self.selection = None
            self.bandeau.fermer(appeler=False)
            self._redessiner()
            self._maj_point()

    def _clic_carte(self, lat: float, lon: float) -> None:
        mode = self.carte.mode
        if mode == "tracer":
            self._clic_tracer(lat, lon)
        elif mode == "giratoire":
            self._clic_giratoire(lat, lon)
        elif mode == "mesurer":
            self._clic_mesurer(lat, lon)
        elif mode == "anneau":
            self._clic_anneau(lat, lon)
        elif mode == "bordure":
            self._clic_bordure(lat, lon)
        elif self.selection is not None:
            self.selection = None
            self._redessiner()
            self._maj_point()

    def _clic_tracer(self, lat: float, lon: float) -> None:
        self._assurer_geo(lat, lon)
        var = self.variante
        pts = var.points
        x, y = self._plan(lat, lon)
        if pts and math.dist(pts[-1], (x, y)) < 0.5:
            return
        pts.append([x, y])
        var.rayons.append(None)
        var.au_mieux = False
        var.giratoire = None
        self.avant_correction = None
        self.proposition = None
        self.apercu = None
        self._gen_apercu += 1
        self.carte.vider("apercu")
        n = len(pts)
        lib = "Départ placé" if n == 1 else f"Point {n} ajouté"
        if n == 2:                                       # le sens de marche est connu : départ au milieu de la voie
            cap = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
            r = self._caler(*pts[0], cap)
            if r and math.dist(r[:2], pts[0]) > 0.05:
                pts[0] = [r[0], r[1]]
                lib += f" · départ sur la {r[2]}"
        self._changer(lib, calcul=n >= 2)
        if n == 1:
            self.charger_routes()

    def terminer_trace(self) -> None:
        mode = self.carte.mode
        if mode == "bordure" and self._contrainte and self._contrainte["points"]:
            self._finir_contrainte()                             # l'outil reste actif pour la suivante
            return
        if mode == "anneau" and self._releve and "r_int" in self._releve:
            self._finir_releve(self._releve["r_int"])          # pas de bande franchissable
            return
        if mode != "tracer":
            if mode != "modifier":
                self.choisir_mode("modifier")
            return
        self.choisir_mode("modifier")
        pts = self.variante.points
        if len(pts) < 2:
            return
        cap = math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
        r = self._caler(*pts[-1], cap)
        if r and math.dist(r[:2], pts[-1]) > 0.05:
            pts[-1] = [r[0], r[1]]
            self._changer(f"Arrivée sur la {r[2]}")
        self.corrections_vehicule(fin_de_saisie=True)

    def _retour_arriere(self) -> None:
        if self.carte.mode == "bordure":
            self._retour_contrainte()
            return
        if self.carte.mode == "anneau" and self._releve:
            r = self._releve
            if "r_int" in r:
                del r["r_int"]
            elif r["ext"]:
                r["ext"].pop()
            self._dessiner_releve()
            self._maj_assistant()
            return
        if self.carte.mode == "tracer" and self.variante.points:
            self.variante.points.pop()
            self.variante.rayons.pop()
            self.apercu = None
            self._changer("Dernier point enlevé")
        elif self.selection is not None:
            self.supprimer_point()

    def _poignee_glissee(self, d, lat: float, lon: float, fini: bool) -> None:
        if d[0] == "anneau":
            self._poignee_anneau(d[1], lat, lon, fini)
            return
        if d[0] != "sommet":
            return
        i = d[1]
        pts = self.variante.points
        if i >= len(pts):
            return
        x, y = self._plan(lat, lon)
        lib = f"Point {i + 1} déplacé"
        if fini and len(pts) >= 2 and i in (0, len(pts) - 1):      # départ et arrivée restent au milieu d'une voie
            j = 1 if i == 0 else len(pts) - 2
            cap = math.atan2(pts[j][1] - y, pts[j][0] - x) if i == 0 else math.atan2(y - pts[j][1], x - pts[j][0])
            r = self._caler(x, y, cap)
            if r:
                x, y = r[0], r[1]
                lib += f" ({r[2]})"
        pts[i] = [x, y]
        self.selection = i
        self.avant_correction = None
        self.proposition = None
        if fini:
            self._changer(lib)
        else:
            self.lancer_calcul(rapide=True)
            self._dessiner_trace()
            self._maj_point()

    def _poignee_cliquee(self, d) -> None:
        if d[0] == "sommet":
            self.selection = d[1]
            self._redessiner()
            self._maj_point()

    def _poignee_molette(self, d, crans: float) -> None:
        if d[0] == "anneau":
            return
        i = d[1]
        if not 0 < i < len(self.variante.points) - 1:
            return
        info = next((s for s in self.infos if s.indice == i), None)
        actuel = self.variante.rayons[i] or (info.rayon_demande if info else 10.0)
        nouveau = max(1.0, round((actuel + 0.5 * crans) * 2) / 2)
        self.variante.rayons[i] = nouveau
        self.selection = i
        self._changer(f"Rayon du point {i + 1} : {_fr(nouveau)} m")

    def _menu_carte(self, d, lat: float, lon: float, pos: QPoint) -> None:
        if self.carte.mode == "tracer" and d is None:
            self.terminer_trace()                         # clic droit : fin de saisie, comme dans AutoCAD
            return
        if self.carte.mode == "bordure" and (d is None or self._contrainte):
            self.terminer_trace()
            return
        m = QMenu(self)
        if d is not None and d[0] == "contrainte":
            self._menu_contrainte(m, d[1])
        elif d is not None and d[0] == "sommet":
            i = d[1]
            self.selection = i
            self._redessiner()
            self._maj_point()
            if 0 < i < len(self.variante.points) - 1:
                m.addAction("Rayon du virage…").triggered.connect(lambda: self._demander_rayon(i))
                m.addAction("Rayon proposé").triggered.connect(lambda: self._fixer_rayon(i, None))
            m.addAction("Insérer un point après").triggered.connect(lambda: self.inserer_point(i))
            m.addAction("Supprimer ce point").triggered.connect(self.supprimer_point)
        else:
            m.addAction("Copier les coordonnées").triggered.connect(lambda: self._copier_coordonnees(lat, lon))
            m.addAction("Centrer ici").triggered.connect(lambda: self.carte.centrer(lat, lon))
        m.addSeparator()
        m.addAction("Annuler (Ctrl+Z)").triggered.connect(self.annuler)
        m.exec(pos)

    def _copier_coordonnees(self, lat: float, lon: float) -> None:
        leg = systeme_legal(lat, lon)
        t = f"{lat:.7f}, {lon:.7f}" + (f"\n{leg.nom} : X {leg.x:.2f}  Y {leg.y:.2f}" if leg else "")
        QGuiApplication.clipboard().setText(t)
        self.statusBar().showMessage("Coordonnées copiées.", 4000)

    def _demander_rayon(self, i: int) -> None:
        info = next((s for s in self.infos if s.indice == i), None)
        r, ok = QInputDialog.getDouble(self, "Rayon du virage", f"Rayon du virage au point {i + 1} (m) :",
                                       self.variante.rayons[i] or (info.rayon if info else 10.0), 1.0, 500.0, 1)
        if ok:
            self._fixer_rayon(i, r)

    def _fixer_rayon(self, i: int, r: Optional[float]) -> None:
        self.variante.rayons[i] = r
        self._changer(f"Rayon du point {i + 1} : " + ("proposé" if r is None else f"{_fr(r)} m"))

    def inserer_point(self, i: int) -> None:
        pts = self.variante.points
        if i >= len(pts) - 1:
            dx, dy = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]) if len(pts) > 1 else (10, 0)
            n = math.hypot(dx, dy) or 1
            pts.append([pts[-1][0] + 10 * dx / n, pts[-1][1] + 10 * dy / n])
            self.variante.rayons.append(None)
        else:
            pts.insert(i + 1, [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2])
            self.variante.rayons.insert(i + 1, None)
        self.selection = i + 1
        self._changer(f"Point inséré après le point {i + 1}")

    def supprimer_point(self) -> None:
        i = self.selection
        if i is None or i >= len(self.variante.points):
            return
        del self.variante.points[i]
        del self.variante.rayons[i]
        self.selection = None
        self._changer(f"Point {i + 1} supprimé")

    def effacer_trace(self) -> None:
        if self.variante.points:
            self.variante.points, self.variante.rayons = [], []
            self.variante.au_mieux, self.variante.giratoire = False, None
            self.selection = None
            self._changer("Trajet effacé")

    def inverser(self) -> None:
        if len(self.variante.points) >= 2:
            self.variante.points.reverse()
            self.variante.rayons.reverse()
            self.variante.giratoire = None
            self._changer("Sens de marche inversé")

    # ================================================================== giratoire en deux clics
    def _clic_giratoire(self, lat: float, lon: float) -> None:
        self._assurer_geo(lat, lon)
        p = self._plan(lat, lon)
        if self._girat_entree is None:
            self._girat_entree = p
            self.charger_routes(centre=(lat, lon))
            self._redessiner()
            rep = self._repere(*p)
            if rep is not None:
                self.carte.vider("elastique")
                h = Poignee(None, JAUNE, QColor("#1b1b1b"), 6, 1.5)
                h.setData(0, None)
                h.setPos(rep.point(*p))
                self.carte.ajouter("elastique", h, 51)
            self._maj_assistant()
            return
        entree, self._girat_entree = self._girat_entree, None
        self._construire_giratoire(entree, p)

    def _anneau_pres(self, p: tuple) -> Optional[Anneau]:
        if self.projet.site:
            an = anneau_site(self.projet.site, self.geo)
            if an is not None and math.dist(an.centre, p) < an.r_ext + 80:
                return an
        return anneau_bdtopo(self.routes, p, portee=70.0)

    def _construire_giratoire(self, entree: tuple, sortie: tuple, libelle: str = "Giratoire") -> bool:
        milieu = ((entree[0] + sortie[0]) / 2, (entree[1] + sortie[1]) / 2)
        an = self._anneau_pres(milieu) or self._anneau_pres(entree)
        if an is None:
            self.bandeau.afficher("<b>Pas de giratoire trouvé ici.</b> Relevez l'anneau sur la photo avec l'outil "
                                  "<b>Anneau</b> (A), ou ouvrez un site Girabase. Les routes de la BD TOPO sont "
                                  "peut-être aussi encore en chargement.",
                                  [("Relever l'anneau", lambda: self.choisir_mode("anneau")), ("Fermer", lambda: None)])
            self._maj_assistant()
            return False
        veh = self.vehicule
        k = self.geo.echelle(*entree) if self.geo.systeme else 1.0
        tg = tour_giratoire(self.routes, an, entree, sortie, (veh.tracteur.LEav or veh.tracteur.LEar) * k,
                            veh.rayon_P_min() * k, self.projet.marge_giratoire, self.projet.gauche)
        if tg is None:
            self.bandeau.afficher("Cliquez au <b>milieu de la voie d'entrée</b>, puis au milieu de la voie de sortie, "
                                  "sur une route qui arrive au giratoire." if an.source == "photo" else
                                  "Cliquez sur une <b>route qui arrive au giratoire</b>, pour l'entrée puis pour la "
                                  "sortie.", duree_ms=6000)
            self._maj_assistant()
            return False
        var = self.variante
        var.points, var.rayons = tg.points, tg.rayons
        var.au_mieux = False
        var.giratoire = [list(entree), list(sortie)]
        self.avant_correction = None
        self.proposition = None
        self.selection = None
        if self.carte.mode != "modifier":
            self.actions_mode["modifier"].setChecked(True)
            self.carte.set_mode("modifier")
        self._changer(f"{libelle} : {tg.de} → {tg.vers}, anneau R {_fr(tg.r_anneau)} m"
                      + {"Girabase": " (site Girabase)", "photo": " (anneau relevé)"}.get(an.source, ""))
        self.corrections_vehicule(fin_de_saisie=True)
        return True

    # ================================================================== anneau relevé sur la photo
    def _clic_anneau(self, lat: float, lon: float) -> None:
        self._assurer_geo(lat, lon)
        p = self._plan(lat, lon)
        r = self._releve if self._releve is not None else {"ext": []}
        self._releve = r
        if len(r["ext"]) < 3:
            r["ext"].append(p)
            if len(r["ext"]) == 3:
                c = cercle_3_points(*r["ext"])
                if c is None or not 3.0 < c[2] < 80.0:
                    r["ext"].pop()
                    self.bandeau.afficher("Ces trois points ne définissent pas un cercle plausible : cliquez-les "
                                          "<b>bien répartis</b> sur la bordure extérieure de l'anneau.", duree_ms=6000)
                else:
                    r["cercle"] = c
        elif "r_int" not in r:
            cx, cy, r_ext = r["cercle"]
            ri = math.dist((cx, cy), p)
            if not 0.5 < ri < r_ext - 2.0:
                self.bandeau.afficher(f"Le bord intérieur de la chaussée doit être à l'intérieur du cercle extérieur "
                                      f"(R {_fr(r_ext)} m), à plus de 2 m de lui.", duree_ms=6000)
            else:
                r["r_int"] = ri
        else:
            cx, cy, _ = r["cercle"]
            self._finir_releve(math.dist((cx, cy), p))
            return
        self._dessiner_releve()
        self._maj_assistant()

    def _finir_releve(self, r_ilot: float) -> None:
        r = self._releve
        cx, cy, r_ext = r["cercle"]
        r_int = r["r_int"]
        if r_ilot > r_int + 0.05:
            self.bandeau.afficher("L'îlot doit être à l'intérieur du bord intérieur de la chaussée.", duree_ms=5000)
            return
        site = site_releve(self.geo, cx, cy, r_ext, r_int, r_ilot)
        self.projet.site = site
        self._releve = None
        self.carte.vider("elastique")
        self.actions_mode["modifier"].setChecked(True)
        self.carte.set_mode("modifier")
        self._changer(f"Anneau relevé sur la photo : rayon extérieur {_fr(r_ext)} m, îlot {_fr(site['R'])} m, bande "
                      f"franchissable {_fr(site['Bf'])} m, anneau {_fr(site['LA'])} m")
        self.bandeau.afficher(f"<b>Anneau relevé</b> : rayon extérieur {_fr(r_ext)} m, largeur de l'anneau "
                              f"{_fr(site['LA'])} m, bande franchissable {_fr(site['Bf'])} m, îlot {_fr(site['R'])} m. "
                              "Il remplace l'anneau de la BD TOPO ; ses poignées permettent de l'ajuster.",
                              [("Faire passer un véhicule (Giratoire)", lambda: self.choisir_mode("giratoire")),
                               ("Fermer", lambda: None)])

    def _dessiner_releve(self) -> None:
        c = self.carte
        c.vider("elastique")
        r = self._releve
        if r is None:
            return
        rep = self._repere(*(r["ext"][0] if r["ext"] else (None, None)))
        if rep is None:
            return
        cur = self._curseur_plan
        for q in r["ext"]:
            h = Poignee(None, JAUNE, QColor("#1b1b1b"), 3.5, 1.0, "carre")
            h.setData(0, None)
            h.setPos(rep.point(*q))
            c.ajouter("elastique", h, 51)
        cercle = r.get("cercle")
        if cercle is None and len(r["ext"]) == 2 and cur is not None:
            cercle = cercle_3_points(*r["ext"], cur)
        if cercle is not None:
            cx, cy, re_ = cercle
            self._cercle(cx, cy, re_, QColor(0, 200, 255, 230), 2.0, Qt.DashLine, rep, "elastique", 50)
            if "cercle" not in r and cur is not None:
                self._bulle(f"bordure extérieure : R {_fr(re_)} m", rep.point(*cur), "ok", "elastique")
            if "cercle" in r:
                if "r_int" in r:
                    self._cercle(cx, cy, r["r_int"], JAUNE, 2.0, Qt.DashLine, rep, "elastique", 50)
                if cur is not None:
                    rc = math.dist((cx, cy), cur)
                    etape = "îlot" if "r_int" in r else "bord intérieur de la chaussée"
                    coul = ROUGE if "r_int" in r else JAUNE
                    self._cercle(cx, cy, rc, coul, 1.6, Qt.DotLine, rep, "elastique", 50)
                    texte = f"{etape} : R {_fr(rc)} m"
                    if "r_int" not in r:
                        texte += f"\nlargeur de l'anneau {_fr(re_ - rc)} m"
                    else:
                        texte += f"\nbande franchissable {_fr(max(0.0, r['r_int'] - rc))} m"
                    self._bulle(texte, rep.point(*cur), "ok", "elastique")

    def _poignee_anneau(self, quoi: str, lat: float, lon: float, fini: bool) -> None:
        site = dict(self._site_edite or self.projet.site or {})
        if not site:
            return
        an = anneau_site(site, self.geo)
        p = self._plan(lat, lon)
        cx, cy = an.centre
        r_ext, r_int, r_ilot = an.r_ext, an.r_int, an.r_ilot
        if quoi == "centre":
            cx, cy = p
        else:
            d = math.dist((cx, cy), p)
            if quoi == "ext":
                r_ext = max(r_int + 2.0, d)
            elif quoi == "int":
                r_int = min(max(r_ilot, d), r_ext - 2.0)
            else:
                r_ilot = min(max(0.5, d), r_int)
        nouveau = site_releve(self.geo, cx, cy, r_ext, r_int, r_ilot, site.get("nom") or "Anneau relevé sur la photo")
        if site.get("source") != "photo":
            nouveau["nom"] = (site.get("nom") or "Site Girabase") + " (ajusté sur la photo)"
        if not fini:
            self._site_edite = nouveau
            self._dessiner_chaussee()
            return
        self._site_edite = None
        self.projet.site = nouveau
        libelle = {"centre": "centre déplacé", "ext": f"rayon extérieur {_fr(r_ext)} m",
                   "int": f"bord intérieur de la chaussée à {_fr(r_int)} m", "ilot": f"îlot {_fr(r_ilot)} m"}[quoi]
        self._changer(f"Anneau ajusté : {libelle}")
        if self.variante.giratoire:
            e, s_ = self.variante.giratoire
            self.bandeau.afficher("L'anneau a changé : le trajet du giratoire a été construit sur l'ancien.",
                                  [("Le reconstruire", lambda: self._construire_giratoire(tuple(e), tuple(s_),
                                                                                         "Giratoire reconstruit")),
                                   ("Laisser tel quel", lambda: None)])

    def exporter_site(self) -> None:
        if not self.projet.site:
            return
        ch = self._fichier_export("Enregistrer l'anneau pour Girabase", "gsite", "Sites Girabase (*.gsite)")
        if ch:
            Path(ch).write_text(json.dumps(gsite(self.projet.site), ensure_ascii=False, indent=2), encoding="utf-8")
            self.bandeau.afficher(f"Anneau enregistré pour Girabase : {Path(ch).name}. Ouvrez-le dans Girabase et "
                                  "ajoutez les branches pour calculer la capacité.", duree_ms=8000)

    # ================================================================== mesure sur la photo
    def _clic_mesurer(self, lat: float, lon: float) -> None:
        p = self._plan(lat, lon)
        if self._mesure_a is None:
            self._mesure_a = p
            self._curseur_plan = p
            if not self.troncons:
                self.charger_routes(centre=(lat, lon))
            self._dessiner_mesure()
            self._maj_assistant()
            return
        a, self._mesure_a = self._mesure_a, None
        self.carte.vider("elastique")
        self._maj_assistant()
        me = mesurer(self.routes, a, p)
        if not me.largeur_plausible:
            self.bandeau.afficher(f"Distance mesurée : <b>{_fr(me.longueur, 2)} m</b>.", duree_ms=6000)
            return
        ax = me.axe
        l1 = round(me.longueur, 1)
        avant = f"{_fr(ax.largeur)} m {'mesurée avant' if ax.corrigee else 'dans la BD TOPO'}"
        texte = f"Largeur mesurée sur la photo : <b>{_fr(me.longueur, 2)} m</b> ({ax.nom} : {avant})."

        def appliquer(axe_aussi: bool):
            self.projet.largeurs[ax.nom] = l1
            lib = f"Largeur de la {ax.nom} : {_fr(l1)} m (mesurée sur la photo)"
            if axe_aussi:
                dx, dy = me.recalage
                prec = self.projet.recalages.get(ax.cle, [0.0, 0.0])
                self.projet.recalages[ax.cle] = [prec[0] + dx, prec[1] + dy]
                lib = f"{ax.nom} : largeur {_fr(l1)} m et axe recalé de {_fr(abs(me.decalage))} m (photo)"
            self._changer(lib)
            self._proposer_remise_sur_voie()
        if me.recaler:
            texte += (f" Le milieu de la chaussée sur la photo est à <b>{_fr(abs(me.decalage))} m</b> de l'axe BD TOPO : "
                      "on peut aussi recaler ce tronçon.")
            actions = [(f"Largeur {_fr(l1)} m et axe recalé", lambda: appliquer(True)),
                       ("Largeur seulement", lambda: appliquer(False)), ("Non", lambda: None)]
        else:
            actions = [(f"Appliquer {_fr(l1)} m à la {ax.nom}", lambda: appliquer(False)), ("Non", lambda: None)]
        self.bandeau.afficher(texte, actions)

    def _proposer_remise_sur_voie(self) -> None:
        """La chaussée a changé : le trajet saisi reste où il est ; on propose de le remettre sur la voie corrigée."""
        var = self.variante
        pts = var.points
        if len(pts) < 2:
            return
        if var.giratoire:
            e, s = var.giratoire
            self.bandeau.afficher("Le trajet du giratoire a été construit sur l'ancienne chaussée.",
                                  [("Le reconstruire sur la voie corrigée",
                                    lambda: self._construire_giratoire(tuple(e), tuple(s), "Giratoire reconstruit")),
                                   ("Laisser tel quel", lambda: None)])
            return
        cap0 = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
        cap1 = math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
        a = aimanter(self.routes, *pts[0], cap0, self.projet.gauche)
        b = aimanter(self.routes, *pts[-1], cap1, self.projet.gauche)
        da = math.dist(a[:2], pts[0]) if a else 0.0
        db = math.dist(b[:2], pts[-1]) if b else 0.0
        if max(da, db) < 0.2:
            return

        def replacer():
            if a:
                var.points[0] = [a[0], a[1]]
            if b:
                var.points[-1] = [b[0], b[1]]
            self._changer("Départ et arrivée replacés sur la voie corrigée")
        self.bandeau.afficher(f"Le départ et l'arrivée ne sont plus au milieu de la voie (écart {_fr(max(da, db), 2)} m).",
                              [("Les replacer sur la voie", replacer), ("Laisser tel quel", lambda: None)])

    def oublier_corrections_chaussee(self) -> None:
        if self.projet.largeurs or self.projet.recalages:
            self.projet.largeurs, self.projet.recalages = {}, {}
            self._changer("Corrections de la chaussée annulées (retour à la BD TOPO)")

    # ================================================================== corrections du véhicule (fin de saisie)
    def corrections_vehicule(self, fin_de_saisie: bool = False) -> None:
        var = self.variante
        pts = var.points
        if len(pts) < 2:
            return
        rmin = self.rmin()
        c = corrections(pts, var.rayons, rmin)
        nom = self.vehicule.nom_court or self.vehicule.nom.split(" — ")[0]
        if c.vide:
            if fin_de_saisie:
                self._quand_calcule(lambda: self._message_fin(f"<b>Fin de saisie</b> : aucun virage à corriger, le "
                                                              f"véhicule ({nom}) suit votre trajet.", [], 4000))
            else:
                self.bandeau.afficher("Rien à corriger : aucun virage n'est plus serré que le minimum du véhicule.",
                                      duree_ms=4000)
            return
        libelle = ", ".join(x for x in (f"{len(c.elargis)} virage(s) élargi(s)" if c.elargis else "",
                                         f"au mieux {_liste_points([i + 1 for i in c.manque])}" if c.manque else "") if x)

        def lignes(ecarts: Optional[list]) -> str:
            l, proches = [], []
            for i, r0, r1 in c.elargis:
                l.append(f"point {i + 1} : virage porté de {_fr(r0)} à {_fr(r1)} m (minimum du véhicule {_fr(rmin)} m)")
            for i in c.manque:
                if ecarts is not None and i < len(ecarts) and ecarts[i] <= ECART_VISIBLE:
                    proches.append(i + 1)
                    continue
                e = f" Il passe à <b>{_fr(ecarts[i], 2)} m</b> de votre point." if ecarts is not None and i < len(ecarts) else ""
                l.append(f"point {i + 1} : pas assez de place pour le rayon du véhicule ({_fr(rmin)} m).{e} "
                         f"Pour qu'il y passe, éloignez le point {i} ou {i + 2}.")
            if proches:
                l.append(f"point{'s' if len(proches) > 1 else ''} {', '.join(map(str, proches))} : un peu serré"
                         f"{'s' if len(proches) > 1 else ''}, il y passe à moins de 10 cm.")
            return "<ul style='margin-left:-20px'>" + "".join(f"<li>{t}</li>" for t in l) + "</ul>"

        def appliquer():
            ts, _, _ = construire_passages(pts, var.rayons, rmin)
            self.avant_correction = [q[:2] for q in ts.echantillons(0.5)] if ts is not None else None
            self.proposition = None
            var.rayons = c.rayons
            var.au_mieux = bool(c.manque)
            self._changer("Corrections du véhicule : " + libelle)

        if self.opt_office.isChecked():
            appliquer()
            self._quand_calcule(lambda: self._message_fin(
                "<b>Corrections du véhicule appliquées</b> (votre saisie reste en pointillé rouge)"
                + lignes(self.au_mieux.ecarts if self.au_mieux else None), [("Revenir à ma saisie", self.annuler)], 15000))
        else:
            tr, infos, tv, am = trajet(pts, c.rayons, rmin, bool(c.manque))
            self.proposition = [q[:2] for q in tv.echantillons(0.4)] if tv is not None else None
            self._redessiner()

            def ignorer():
                self.proposition = None
                self._redessiner()
            self.bandeau.afficher("<b>Corrections proposées</b> (trajet du véhicule en vert pointillé)"
                                  + lignes(am.ecarts if am else None),
                                  [("Appliquer", appliquer), ("Ignorer", ignorer)], au_fermer=ignorer)

    def _quand_calcule(self, f: Callable) -> None:
        """Exécute f après le calcul complet en cours (ou tout de suite s'il n'y en a pas)."""
        en_cours = self._gen > self._gen_affiche or (self.resultat is not None and self.resultat.rapide)
        if en_cours:
            self._apres_calcul = f
        else:
            f()

    def _message_fin(self, html: str, actions: list, duree: int) -> None:
        a_voir = self._a_regarder()
        if a_voir:
            html += "<br>À regarder sur la photo :<ul style='margin-left:-20px'>" + "".join(f"<li>{t}</li>" for t in a_voir) + "</ul>"
            duree = max(duree, 9000)
        if self.controle.hors > 0 and not self.projet.ignorer_chaussee:
            # la chaussée BD TOPO peut être décalée de la photo : un clic pour ne plus en tenir compte
            actions = list(actions) + [("La photo est juste : ignorer la chaussée BD TOPO",
                                        lambda: self._ignorer_chaussee(True))]
        self.bandeau.afficher(html, actions, duree)

    def _a_regarder(self) -> list[str]:
        c = self.controle
        l = []
        if c.ilot > 0.05:
            l.append(f"roues sur l'îlot central infranchissable ({_fr(c.ilot, 2)} m)")
        if c.hors > 0:
            l.append(f"roues hors de la chaussée estimée de {_fr(c.hors, 2)} m (zones rouges)")
        if c.bande > 0.05:
            l.append(f"roues arrière sur la bande franchissable ({_fr(c.bande, 2)} m)" if c.ilot_connu else
                     f"roues à l'intérieur de l'anneau de {_fr(c.bande, 2)} m (bande franchissable ?)")
        return l

    # ================================================================== véhicule, conduite, variantes, options
    def choisir_vehicule(self) -> None:
        d = ChoixVehicule(self.biblio, self.variante.vehicule_id, self)
        if d.exec() and d.choix and d.choix != self.variante.vehicule_id:
            self.variante.vehicule_id = d.choix
            self.reglages.setValue("vehicule", d.choix)
            self._changer(f"Véhicule : {self.biblio.get(d.choix).nom.split(' — ')[0]}")

    def _conduite_changee(self, _i: int) -> None:
        code = self.cb_conduite.currentData()
        if code != self.variante.conduite:
            self.variante.conduite = code
            self._changer(f"Conduite {self.cb_conduite.currentText()}")

    def _circulation_changee(self, gauche: bool) -> None:
        if gauche != self.projet.gauche:
            self.projet.gauche = gauche
            self._changer("Circulation à gauche" if gauche else "Circulation à droite", calcul=False)

    def _marge_giratoire(self) -> None:
        v, ok = QInputDialog.getDouble(self, "Giratoire", "Distance entre la roue avant extérieure et le bord extérieur "
                                       "de l'anneau (m) :", self.projet.marge_giratoire, 0.0, 3.0, 2)
        if ok and abs(v - self.projet.marge_giratoire) > 1e-6:
            self.projet.marge_giratoire = v
            self._changer(f"Marge au bord de l'anneau : {_fr(v, 2)} m", calcul=False)

    def nouvelle_variante(self) -> None:
        v = copy.deepcopy(self.variante)
        v.nom = self.projet.nom_libre()
        v.couleur = self.projet.couleur_libre()
        self.projet.variantes.append(v)
        self.projet.active = len(self.projet.variantes) - 1
        self._changer(f"Variante {v.nom} créée (copie)")

    def _variante_cliquee(self, it: QListWidgetItem) -> None:
        i = self.liste_variantes.row(it)
        if i != self.projet.active:
            self.projet.active = i
            self.selection = None
            self._changer(f"Variante {self.variante.nom} activée")

    def renommer_variante(self) -> None:
        nom, ok = QInputDialog.getText(self, "Renommer la variante", "Nom :", text=self.variante.nom)
        if ok and nom.strip():
            self.variante.nom = nom.strip()
            self._changer(f"Variante renommée « {nom.strip()} »", calcul=False)

    def supprimer_variante(self) -> None:
        if len(self.projet.variantes) <= 1:
            self.effacer_trace()
            return
        nom = self.variante.nom
        del self.projet.variantes[self.projet.active]
        self.projet.active = max(0, self.projet.active - 1)
        self._changer(f"Variante {nom} supprimée")

    def ouvrir_site(self) -> None:
        chemin, _ = QFileDialog.getOpenFileName(self, "Ouvrir un site Girabase", self._dossier(), "Sites Girabase (*.gsite)")
        if chemin:
            self.charger_site(chemin)

    def charger_site(self, chemin: str) -> bool:
        try:
            d = json.loads(Path(chemin).read_text(encoding="utf-8"))
            site = {"lat": float(d["lat"]), "lon": float(d["lon"]), "R": float(d.get("R", 6.0)),
                    "Bf": float(d.get("Bf", 0.0)), "LA": float(d.get("LA", 7.0)), "nom": str(d.get("nom") or Path(chemin).stem)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, TITRE, f"Impossible de lire le site Girabase :\n{exc}")
            return False
        self.projet.site = site
        if not any(v.points for v in self.projet.variantes):
            self._assurer_geo(site["lat"], site["lon"])
            self.carte.centrer(site["lat"], site["lon"], NIVEAU_PHOTO_20CM)
            self.charger_routes(forcer=True, centre=(site["lat"], site["lon"]))
        self._changer(f"Site Girabase « {site['nom']} » : îlot {_fr(site['R'])} m, bande franchissable "
                      f"{_fr(site['Bf'])} m, anneau {_fr(site['LA'])} m", calcul=True)
        return True

    def retirer_site(self) -> None:
        if self.projet.site:
            self.projet.site = None
            self._changer("Anneau retiré (retour à la BD TOPO)")

    # ================================================================== calcul
    def _cle(self, v: Variante) -> str:
        return repr((v.vehicule_id, v.points, v.rayons, v.conduite, v.au_mieux))

    def _fonction_calcul(self, var: Variante, rapide: bool, points: Optional[list] = None,
                         rayons: Optional[list] = None, au_mieux: Optional[bool] = None):
        veh = self.biblio.get(var.vehicule_id)
        pts = copy.deepcopy(points if points is not None else var.points)
        ray = list(rayons if rayons is not None else var.rayons)
        mieux = var.au_mieux if au_mieux is None else au_mieux
        geo = self.geo
        style = var.conduite

        def f():
            k = geo.echelle(*pts[0]) if geo.systeme else 1.0
            rmin = veh.rayon_P_min() * k
            ts, infos, tv, am = trajet(pts, ray, rmin, mieux)
            extra = {"infos": infos, "trace_saisie": ts, "au_mieux": am}
            if tv is None:
                return None, extra
            res = calculer(veh, tv, style=style, echelle=k, systeme=geo.systeme, rapide=rapide, pas_mesure=1.0)
            return res, extra
        return f

    def lancer_calcul(self, rapide: bool = False) -> None:
        self._gen += 1
        if len(self.variante.points) < 2:
            self._gen_affiche = self._gen
            self.resultat, self.infos, self.trace_saisie, self.au_mieux = None, [], None, None
            self.controle = Controle()
            self._maj_resultats()
            return
        demande = (self._gen, self._fonction_calcul(self.variante, rapide), "rapide" if rapide else "complet")
        if self._occupe:
            self._attente = demande                  # seule la dernière demande compte
            return
        self._executer(demande)

    def lancer_apercu(self, curseur: tuple) -> None:
        """Fantôme : le véhicule si l'on cliquait ici (calcul rapide, après les calculs prioritaires)."""
        var = self.variante
        if not self.opt_fantome.isChecked() or not var.points:
            return
        self._gen_apercu += 1
        demande = (self._gen_apercu, self._fonction_calcul(var, True, var.points + [list(curseur)],
                                                           var.rayons + [None], False), "apercu")
        if self._occupe:
            self._attente_apercu = demande
            return
        self._executer(demande)

    def _executer(self, demande) -> None:
        self._occupe = True
        gen, f, nature = demande
        self.pool.start(_Tache(gen, f, self.signaux, nature))

    def _calcul_fini(self, gen: int, res, extra, nature: str) -> None:
        self._occupe = False
        if self._attente is not None:
            d, self._attente = self._attente, None
            self._executer(d)
        elif self._attente_apercu is not None:
            d, self._attente_apercu = self._attente_apercu, None
            self._executer(d)
        if nature == "apercu":
            if gen == self._gen_apercu and self.carte.mode == "tracer" and not isinstance(res, Exception):
                self.apercu = res
                self._dessiner_apercu()
            return
        rapide = nature == "rapide"
        if gen <= self._gen_affiche:            # résultat dépassé (un plus récent est déjà affiché)
            return
        self._gen_affiche = gen
        if isinstance(res, Exception):
            self.resultat, self.infos = None, []
            self.verdict.setStyleSheet("")
            self.verdict.setText(f"<b>Calcul impossible</b><br>{res}")
            return
        self.resultat = res
        self.infos = extra["infos"]
        self.trace_saisie = extra["trace_saisie"]
        self.au_mieux = extra["au_mieux"]
        if res is not None and not rapide:
            self.controle = controler(self.routes, res.enveloppes.roues, self._anneau())
            self._controler_lot2(res)
            if self.position_auto:
                self._placer_position_auto()
        elif res is None:
            self.controle = Controle()
            self.conflits = []
        self._maj_resultats()
        self._redessiner()
        self._maj_point()
        self._maj_assistant()
        if not rapide:
            if gen == self._gen and self._apres_calcul is not None:
                f, self._apres_calcul = self._apres_calcul, None
                f()
            self._calculer_inactifs()

    def _anneau(self) -> Optional[Anneau]:
        """Giratoire traversé par le trajet actif (site Girabase, sinon anneau BD TOPO), gardé en mémoire."""
        pts = self.variante.points
        if len(pts) < 2:
            return None
        cle = (tuple(tuple(round(c, 1) for c in p) for p in pts[::max(1, len(pts) // 6)]), self._cle_chaussee,
               json.dumps(self.projet.site, sort_keys=True))
        if self._cache_anneau[0] == cle:
            return self._cache_anneau[1]
        an = None
        if self.projet.site:
            a = anneau_site(self.projet.site, self.geo)
            if a is not None and min(math.dist(a.centre, p) for p in pts) < a.r_ext + 30:
                an = a
        if an is None and not self.routes.vide:
            for p in pts:
                an = anneau_bdtopo(self.routes, p, portee=8.0)
                if an is not None:
                    break
        self._cache_anneau = (cle, an)
        return an

    def _calculer_inactifs(self) -> None:
        """Résultats des autres variantes (contours de comparaison), calculés une fois puis gardés en mémoire."""
        for i, v in enumerate(self.projet.variantes):
            if i == self.projet.active or len(v.points) < 2:
                continue
            cle = self._cle(v)
            if cle not in self.inactifs:
                try:
                    res, _ = self._fonction_calcul(v, True)()
                    self.inactifs[cle] = res
                except Exception:
                    continue
        self._redessiner()

    def _placer_position_auto(self) -> None:
        sim = self.resultat.sim
        i = int(np.argmax(np.abs(sim.alpha)))
        self.sl_position.blockSignals(True)
        self.sl_position.setValue(int(round(1000 * sim.s[i] / max(sim.s[-1], 1e-9))))
        self.sl_position.blockSignals(False)

    # ================================================================== routes BD TOPO
    def charger_routes(self, forcer: bool = False, centre: Optional[tuple] = None) -> None:
        pts = self.variante.points
        if centre is not None:
            lat, lon = centre
        elif pts:
            lat, lon = self.geo.vers_geo(*pts[0])
        else:
            lat, lon = self.carte.centre_geo()
        if not forcer and self.zone_routes and math.hypot(*vers_local(*self.zone_routes, lat, lon)) < RAYON_ROUTES * 0.6:
            return
        self.zone_routes = (lat, lon)
        self.statusBar().showMessage("Chargement des routes de la BD TOPO…", 4000)

        def recu(donnees, erreur):
            if donnees is None:
                self.statusBar().showMessage(f"Routes BD TOPO indisponibles ({erreur}) : pas de calage sur la voie.", 8000)
                return
            self.troncons = lire_routes(donnees)
            self._cle_chaussee = None
            self._reconstruire_chaussee()
            self.statusBar().showMessage(f"{len(self.chaussee.axes)} tronçons de route chargés (BD TOPO de l'IGN).", 5000)
            if self.resultat is not None and not self.resultat.rapide:
                self.controle = controler(self.routes, self.resultat.enveloppes.roues, self._anneau())
                self._maj_resultats()
            self._redessiner()
            self._maj_assistant()
        self.client.obtenir_json(url_routes(lat, lon, RAYON_ROUTES), recu)
        self.charger_batiments((lat, lon), forcer=forcer)

    def _reconstruire_chaussee(self) -> None:
        cle = (id(self.troncons), len(self.troncons), id(self.geo), json.dumps(self.projet.largeurs, sort_keys=True),
               json.dumps(self.projet.recalages, sort_keys=True), json.dumps(self.projet.site, sort_keys=True))
        if cle == self._cle_chaussee:
            return
        self._cle_chaussee = cle
        anneaux = []
        if self.projet.site and self.projet.site.get("source") == "photo":
            an = anneau_site(self.projet.site, self.geo)
            if an is not None:
                anneaux.append((an.cx, an.cy, an.r_ext, an.r_int))
        self.chaussee = construire_chaussee(self.troncons, self.geo, self.projet.largeurs, self.projet.recalages,
                                            anneaux) if self.troncons else Chaussee()

    # ================================================================== affichage des résultats
    def _maj_resultats(self) -> None:
        res = self.resultat
        if res is None:
            self.verdict.setStyleSheet("")
            self.verdict.setText("<b>Pas encore de trajet</b><br>Tracez-le sur la photo (T), ou utilisez l'outil "
                                 "Giratoire (G).")
            for l in self.valeurs.values():
                l.setText("—")
            self.graphe.definir(None, None)
            self.lbl_alertes.setText("—")
            return
        m = res.mesures()
        c = self.controle
        alertes = [a for a in res.alertes if a.niveau != "info"]
        ecart_max = max(self.au_mieux.ecarts) if self.au_mieux else 0.0
        if not res.faisable:
            titre, detail = "Impossible en l'état", next(a.message for a in res.alertes if a.niveau == "erreur")
            if any(s.rayon < self.rmin() * 0.999 for s in self.infos) and not self.variante.au_mieux:
                detail += " « Terminer » (Entrée) applique les corrections du véhicule."
        elif c.ilot > 0.05:
            titre = "Impossible en l'état"
            detail = (f"Les roues montent sur l'îlot central infranchissable ({_fr(c.ilot, 2)} m) : réduisez la marge "
                      "au bord de l'anneau (Options) ou choisissez un autre trajet.")
        elif ecart_max > ECART_VISIBLE or alertes or c.hors > 0 or c.bande > 0.05:
            titre = "Passe avec réserves"
            if ecart_max > ECART_VISIBLE:
                detail = (f"Le véhicule s'écarte de votre tracé (jusqu'à {_fr(ecart_max, 2)} m, axe rouge) : il ne peut "
                          "pas passer exactement par tous vos points.")
            elif alertes:
                detail = alertes[0].message
            elif c.hors > 0:
                detail = (f"Roues hors de la chaussée estimée de {_fr(c.hors, 2)} m : à vérifier sur la photo (la BD TOPO "
                          "peut être décalée ; Options pour ne pas en tenir compte).")
            else:
                detail = ("Les roues arrière roulent sur la bande franchissable : c'est son rôle, à confirmer."
                          if c.ilot_connu else "Les roues entrent dans l'anneau intérieur (bande franchissable ?) : "
                          "ouvrez le site Girabase pour le vérifier.")
        else:
            titre, detail = "Passe", "Le véhicule suit le trajet sans dépasser son braquage."
        titre, detail = self._verdict_lot2(titre, detail)
        fond, texte = {"Impossible en l'état": ("#FCEBEB", "#791F1F"), "Passe avec réserves": ("#FAEEDA", "#633806"),
                       "Passe": ("#EAF3DE", "#27500A")}[titre]
        self.verdict.setStyleSheet(f"QLabel {{ background: {fond}; color: {texte}; border-radius: 6px; padding: 8px; }}")
        self.verdict.setText(f"<b>{titre}</b><br>{detail}" + ("<br><i>(aperçu)</i>" if res.rapide else ""))
        emp = m["largeur_carrosserie_max"]
        self.valeurs["emprise"].setText("…" if math.isnan(emp) else f"{_fr(emp, 2)} m")
        er = m["largeur_roues_max"]
        self.valeurs["roues"].setText("…" if math.isnan(er) else f"{_fr(er, 2)} m")
        self.valeurs["rayon"].setText(f"{_fr(m['rayon_trace_min'])} m" if math.isfinite(m["rayon_trace_min"]) else "—")
        self.valeurs["braquage"].setText(f"{m['braquage_max_pourcent']:.0f} % ({_fr(m['braquage_max_deg'])}°)")
        self.valeurs["articulation"].setText(f"{m['articulation_max_deg']:.0f}°" if res.vehicule.nb_remorques else "—")
        self.valeurs["vitesse"].setText(f"{m['vitesse_entree_kmh']:.0f} → {m['vitesse_min_kmh']:.0f} → "
                                        f"{m['vitesse_sortie_kmh']:.0f} km/h")
        self.valeurs["saisie"].setText(f"le véhicule fait au mieux : écart jusqu'à {_fr(ecart_max, 2)} m"
                                       if ecart_max > ECART_VISIBLE else "suivie exactement")
        if c.ilot > 0.05:
            ch = f"sur l'îlot de {_fr(c.ilot, 2)} m"
        elif c.hors > 0:
            ch = f"roues dehors de {_fr(c.hors, 2)} m ({len(c.zones_hors)} endroit{'s' if len(c.zones_hors) > 1 else ''})"
        elif c.bande > 0.05:
            ch = f"bande franchissable {_fr(c.bande, 2)} m" if c.ilot_connu else f"dans l'anneau intérieur {_fr(c.bande, 2)} m"
        elif self.projet.ignorer_chaussee:
            ch = "non contrôlée (BD TOPO ignorée) : regardez la photo"
        elif self.chaussee.vide:
            ch = "routes non chargées"
        else:
            ch = "roues sur la chaussée estimée"
        if c.zone_non_controlee:
            ch += " · évasements du giratoire non contrôlés"
        self.valeurs["chaussee"].setText(ch)
        sim = res.sim
        self.graphe.definir(sim.s, sim.vitesse / KMH, self._s_position())
        items = []
        if self.au_mieux:
            for i, e in enumerate(self.au_mieux.ecarts):
                if e > ECART_VISIBLE:
                    items.append(f"<li><b>Point {i + 1} : le véhicule passe à {_fr(e, 2)} m de votre point</b> "
                                 "(pas la place pour son rayon minimal).</li>")
        for a in res.alertes:
            coul = {"erreur": "#C62828", "attention": "#B26A00"}.get(a.niveau)
            items.append(f"<li>{'<b>' if coul else ''}{a.message}{'</b>' if coul else ''}</li>")
        for s in c.zones_hors[:3]:
            items.append(f"<li>Roues hors de la chaussée estimée : {_fr(s.depassement, 2)} m (BD TOPO, à vérifier sur la "
                         "photo ; l'outil Mesurer corrige la largeur et l'axe)</li>")
        if self.projet.ignorer_chaussee:
            items.append("<li>Chaussée BD TOPO ignorée (Options) : pas de contrôle des roues hors chaussée, vérifiez sur "
                         "la photo.</li>")
        if c.zone_non_controlee:
            items.append(f"<li>Chaussée non contrôlée sur {_fr(c.zone_non_controlee[2], 0)} m autour du centre du "
                         "giratoire (évasements des entrées mal décrits par la BD TOPO) : regardez la photo.</li>")
        items = self._remarques_lot2() + items
        self.lbl_alertes.setText("<ul style='margin-left:-20px'>" + "".join(items) + "</ul>" if items else "Aucune.")

    def _s_position(self) -> Optional[float]:
        if self.resultat is None:
            return None
        return self.sl_position.value() / 1000 * float(self.resultat.sim.s[-1])

    def _position_changee(self, _v: int) -> None:
        if self.anim.isActive() is False:
            self.position_auto = False
        self.graphe.position(self._s_position())
        self._dessiner_vehicule()

    def _lecture(self) -> None:
        if self.anim.isActive():
            self.anim.stop()
            self.bt_lecture.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            return
        if self.resultat is None:
            return
        if self.sl_position.value() >= 1000:
            self.sl_position.setValue(0)
        sim = self.resultat.sim
        self._t_anim = float(np.interp(self._s_position(), sim.s, sim.temps))
        self.bt_lecture.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.anim.start()

    def _animer(self) -> None:
        if self.resultat is None:
            self.anim.stop()
            return
        sim = self.resultat.sim
        self._t_anim += self.anim.interval() / 1000
        if self._t_anim >= sim.temps[-1]:
            self._t_anim = float(sim.temps[-1])
            self.anim.stop()
            self.bt_lecture.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        s = float(np.interp(self._t_anim, sim.temps, sim.s))
        self.sl_position.setValue(int(round(1000 * s / max(sim.s[-1], 1e-9))))

    def _maj_point(self) -> None:
        i = self.selection
        n = len(self.variante.points)
        if i is None or i >= n:
            self.grp_point.setVisible(False)
            return
        self.grp_point.setVisible(True)
        self.grp_point.setTitle(f"Point {i + 1}" + (" (départ)" if i == 0 else " (arrivée)" if i == n - 1 and n > 1 else ""))
        info = next((s for s in self.infos if s.indice == i), None)
        interieur = 0 < i < n - 1
        self.ck_auto.setVisible(interieur)
        self.sp_rayon.setVisible(interieur)
        txt = ""
        if interieur and info is not None:
            pct, vit = self._mesures_sommet(info)
            txt = (f"Rayon {_fr(info.rayon)} m" + (" (réduit faute de place)" if info.reduit else
                                                   " (proposé)" if self.variante.rayons[i] is None else "") +
                   (f" · {vit:.0f} km/h · braquage {pct:.0f} %" if pct is not None else ""))
            if self.au_mieux and i < len(self.au_mieux.ecarts) and self.au_mieux.ecarts[i] > ECART_VISIBLE:
                txt += f"\nLe véhicule passe à {_fr(self.au_mieux.ecarts[i], 2)} m de ce point."
        elif not interieur:
            txt = "Départ du trajet." if i == 0 else "Arrivée du trajet."
        self.lbl_point.setText(txt)
        for w in (self.ck_auto, self.sp_rayon):
            w.blockSignals(True)
        self.ck_auto.setChecked(self.variante.rayons[i] is None)
        self.sp_rayon.setEnabled(self.variante.rayons[i] is not None)
        self.sp_rayon.setValue(self.variante.rayons[i] or (info.rayon_demande if info else 10.0))
        for w in (self.ck_auto, self.sp_rayon):
            w.blockSignals(False)

    def _rayon_auto_change(self, auto: bool) -> None:
        i = self.selection
        if i is None:
            return
        self._fixer_rayon(i, None if auto else self.sp_rayon.value())

    def _rayon_saisi(self, r: float) -> None:
        i = self.selection
        if i is not None and not self.ck_auto.isChecked():
            self._fixer_rayon(i, r)

    def _mesures_sommet(self, info: InfoSommet) -> tuple[Optional[float], Optional[float]]:
        res = self.resultat
        if res is None or self.au_mieux is not None:
            return None, None
        sim = res.sim
        m = (sim.s >= info.s_debut - 1e-6) & (sim.s <= info.s_fin + res.vehicule.tracteur.E)
        if not m.any():
            return None, None
        pct = float(np.abs(sim.alpha[m]).max() / res.vehicule.alpha_max * 100)
        m2 = (sim.s >= info.s_debut - 5) & (sim.s <= info.s_fin)
        vit = float(sim.vitesse[m2].min() / KMH) if m2.any() else None
        return pct, vit

    def _maj_assistant(self) -> None:
        c = self.carte
        n = len(self.variante.points)
        cote = "gauche" if self.projet.gauche else "droite"
        if c.mode == "tracer":
            if n == 0:
                t = (f"Cliquez le <b>départ</b> au milieu de la voie de {cote}, sur la route d'arrivée."
                     if self.projet.ignorer_chaussee or not self.opt_calage.isChecked() else
                     f"Cliquez le <b>départ</b> sur la route d'arrivée : il se placera au milieu de la voie de {cote} dès "
                     "que le sens de marche sera connu.")
            elif n == 1:
                t = "Cliquez le <b>point suivant</b> : le trajet passe exactement par vos clics."
            else:
                t = ("Restez <b>au-delà du pointillé rouge</b> : en deçà, le virage au dernier point est plus serré que ne "
                     "le permet le véhicule. Pointillé bleu : virage confortable. <b>Retour arrière</b> : enlever le "
                     "dernier point · <b>Entrée</b>, double-clic ou clic droit : fin de saisie et corrections.")
        elif c.mode == "anneau":
            r = self._releve or {"ext": []}
            if len(r["ext"]) < 3:
                t = (f"Cliquez <b>3 points sur la bordure extérieure</b> de l'anneau, bien répartis "
                     f"({len(r['ext']) + 1}/3). Retour arrière : enlever le dernier clic.")
            elif "r_int" not in r:
                t = ("Cliquez le <b>bord intérieur de la chaussée</b> de l'anneau (limite avec la bande franchissable, "
                     "ou avec l'îlot s'il n'y a pas de bande).")
            else:
                t = ("Cliquez le <b>bord de l'îlot</b> infranchissable, ou <b>Entrée</b> s'il n'y a pas de bande "
                     "franchissable.")
        elif c.mode == "giratoire":
            sens = "dans le sens des aiguilles d'une montre" if self.projet.gauche else "par la gauche de l'îlot (sens inverse des aiguilles d'une montre)"
            an = self._anneau_pres(self._girat_entree or self._plan(*self.carte.centre_geo()))
            releve = an is not None and an.source == "photo"
            if self._girat_entree:
                t = ("Cliquez maintenant " + ("au <b>milieu de la voie de sortie</b>" if releve else "la <b>branche de sortie</b>")
                     + " : le véhicule fera le tour " + sens + ".")
            else:
                t = ("Cliquez au <b>milieu de la voie d'entrée</b>, sur la route d'arrivée." if releve else
                     "Cliquez la <b>branche d'entrée</b> du giratoire, sur la route d'arrivée. Si la BD TOPO est "
                     "décalée de la photo, relevez d'abord l'anneau (A).")
        elif c.mode == "bordure":
            nom = {"bordure": "la bordure", "franchissable": "la bordure franchissable",
                   "obstacle": "l'obstacle"}[self.type_contrainte]
            k = self._contrainte
            if k and k["points"]:
                t = (f"Continuez {nom} point par point · <b>Entrée</b>, double-clic ou clic droit : terminer · clic sur le "
                     "premier point : fermer le contour · Retour arrière : enlever le dernier point.")
            else:
                t = (f"Cliquez {nom} sur la photo, point par point (un seul point puis Entrée : un poteau). Flèche du "
                     "bouton « Bordure » : bordure franchissable ou obstacle. Les roues ne franchissent pas une bordure ; "
                     "la caisse ne touche pas un obstacle.")
        elif c.mode == "mesurer":
            t = ("Cliquez l'<b>autre bord</b> de la chaussée." if self._mesure_a else
                 "Cliquez un <b>bord de la chaussée</b> sur la photo, puis le bord opposé : la largeur mesurée peut "
                 "remplacer celle de la BD TOPO, et l'axe être recalé.")
        elif c.niveau < SEUIL_PHOTO - 0.25 and n == 0:
            t = ("Cherchez une commune ou une adresse en haut, ou zoomez avec la molette jusqu'à la photographie "
                 "aérienne (bouton « Photo 20 cm » pour la pleine résolution).")
        elif n < 2:
            t = ("<b>Tracer</b> (T) : cliquez le trajet du milieu de l'essieu avant sur la photo. <b>Giratoire</b> (G) : "
                 "deux clics, entrée puis sortie. <b>Mesurer</b> (M) : corriger une largeur de route.")
        else:
            t = ("Glissez les points pour ajuster · <b>Ctrl + molette</b> sur un point : rayon du virage · clic droit : "
                 "menu · Ctrl+Z annule à tout moment.")
            res, ctl = self.resultat, self.controle
            if self.au_mieux and max(self.au_mieux.ecarts) > ECART_VISIBLE:
                k = [i + 1 for i, e in enumerate(self.au_mieux.ecarts) if e > ECART_VISIBLE]
                t = (f"Le véhicule <b>fait au mieux</b> : il ne peut pas passer exactement par {_liste_points(k)} (axe rouge "
                     "plein, votre saisie en pointillé). Éloignez un point voisin pour lui donner de la place, ou Ctrl+Z "
                     "pour revenir à votre saisie.")
            elif res is not None and any(s.rayon < self.rmin() * 0.999 for s in self.infos):
                t = ("Un virage est <b>trop serré</b> (en rouge) : « Terminer » (Entrée) ou « Corrections du véhicule » "
                     "applique les corrections, ou glissez le point.")
            elif ctl.ilot > 0.05:
                t = "Les roues montent sur l'<b>îlot central</b> (cercle rouge) : réduisez la marge au bord de l'anneau ou changez de trajet."
            elif ctl.hors > 0:
                t = (f"Les roues sortent de la chaussée estimée de {_fr(ctl.hors, 2)} m (zones rouges). La BD TOPO est une "
                     "estimation : vérifiez sur la photo. Si le véhicule est bien sur la route, <b>Options &gt; Ne pas "
                     "tenir compte de la chaussée BD TOPO</b>, ou corrigez la largeur et l'axe avec <b>Mesurer</b> (M).")
            elif ctl.bande > 0.05:
                t = ("Les roues arrière roulent sur la <b>bande franchissable</b> (cercle jaune) : c'est son rôle."
                     if ctl.ilot_connu else "Les roues entrent dans l'anneau intérieur : ouvrez le site Girabase pour "
                     "connaître l'îlot et la bande franchissable.")
        self.lbl_assistant.setText(t)

    # ================================================================== dessin
    def _redessiner(self) -> None:
        self._dessiner_chaussee()
        self._dessiner_lot2()
        self._dessiner_inactifs()
        self._dessiner_resultat()
        self._dessiner_trace()
        self._dessiner_guides()
        self._dessiner_vehicule()
        if self.carte.mode != "tracer":
            self.carte.vider("apercu")

    def _chemin_poly(self, geom, rep: Repere) -> QPainterPath:
        chemin = QPainterPath()
        for poly in getattr(geom, "geoms", [geom]):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            for anneau in [poly.exterior, *poly.interiors]:
                chemin.addPolygon(QPolygonF(rep.points(list(anneau.coords))))
                chemin.closeSubpath()
        chemin.setFillRule(Qt.OddEvenFill)
        return chemin

    def _chemin_ligne(self, pts, rep: Repere) -> QPainterPath:
        p = QPainterPath()
        if len(pts) >= 2:
            p.addPolygon(QPolygonF(rep.points(pts)))
        return p

    def _cercle(self, cx: float, cy: float, r: float, couleur: QColor, largeur: float, style, rep: Repere,
                calque: str, z: float) -> None:
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 97)]
        it = QGraphicsPathItem(self._chemin_ligne(pts, rep))
        it.setPen(crayon(couleur, largeur, style))
        self.carte.ajouter(calque, it, z)

    def _texte(self, texte: str, p: QPointF, couleur: QColor, calque: str, z: float) -> None:
        it = QGraphicsSimpleTextItem(texte)
        it.setBrush(QBrush(couleur))
        it.setPen(QPen(QColor(0, 0, 0, 160), 0.6))
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        it.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        it.setPos(p)
        self.carte.ajouter(calque, it, z)

    def _dessiner_chaussee(self) -> None:
        c = self.carte
        c.vider("chaussee")
        if not self.ck_chaussee.isChecked():
            return
        rep = self._repere()
        if rep is None:
            return
        if not self.routes.vide and self.routes.surface is not None:
            it = QGraphicsPathItem(self._chemin_poly(self.routes.surface.simplify(0.05), rep))
            it.setPen(crayon(QColor(0, 200, 255, 200), 1.3, Qt.DashLine))
            it.setBrush(QBrush(QColor(0, 200, 255, 22)))
            c.ajouter("chaussee", it, 4)
        site = self._site_edite or self.projet.site
        an = anneau_site(site, self.geo) if site else None
        if an is None:
            an = self._anneau() or (self._anneau_pres(self._girat_entree) if self._girat_entree else None)
        if an is None:
            return
        releve = an.source in ("photo", "Girabase")
        if releve:
            self._cercle(an.cx, an.cy, an.r_ext, QColor(0, 200, 255, 230), 1.4, Qt.SolidLine, rep, "chaussee", 5)
        if an.r_ilot is not None:
            self._cercle(an.cx, an.cy, an.r_ilot, QColor(229, 57, 53, 210), 1.2, Qt.DashLine, rep, "chaussee", 5)
            self._cercle(an.cx, an.cy, an.r_ilot + (an.bf or 0.0), QColor(242, 194, 0, 220), 1.2, Qt.DashLine, rep,
                         "chaussee", 5)
        else:
            self._cercle(an.cx, an.cy, an.r_int, QColor(242, 194, 0, 230), 1.4, Qt.DashLine, rep, "chaussee", 5)
        rz = an.r_ext + ZONE_GIRATOIRE
        self._cercle(an.cx, an.cy, rz, QColor(255, 255, 255, 200), 1.2, Qt.DotLine, rep, "chaussee", 5)
        a = math.radians(55)
        self._texte("chaussée non contrôlée (évasements)", rep.point(an.cx + rz * math.cos(a), an.cy + rz * math.sin(a)),
                    QColor("#ffffff"), "chaussee", 6)
        if releve and self.carte.mode == "modifier" and self.projet.site:
            # poignées : centre (déplacer l'anneau) et rayons (bordure extérieure, bord intérieur, îlot)
            poignees = [("centre", an.cx, an.cy, QColor("#00C8FF"), "Centre de l'anneau : glisser pour le déplacer")]
            for quoi, r, coul, aide in (("ext", an.r_ext, QColor("#00C8FF"), "Bordure extérieure de l'anneau"),
                                        ("int", an.r_int, JAUNE, "Bord intérieur de la chaussée (bande franchissable)"),
                                        ("ilot", an.r_ilot if an.r_ilot is not None else an.r_int, ROUGE,
                                         "Bord de l'îlot infranchissable")):
                t = math.radians(ANGLES_POIGNEES[quoi])
                poignees.append((quoi, an.cx + r * math.cos(t), an.cy + r * math.sin(t), coul, aide + " : glisser pour "
                                 "changer le rayon"))
            for quoi, x, y, coul, aide in poignees:
                h = Poignee(("anneau", quoi), coul, QColor("#1b1b1b"), 3.5, 1.0, "carre") if quoi != "centre" else \
                    Poignee(("anneau", quoi), coul, QColor("#00A6D6"), 6.0, 1.8, "croix")
                h.setPos(rep.point(x, y))
                h.setToolTip(aide)
                c.ajouter("chaussee", h, 39)

    def _dessiner_inactifs(self) -> None:
        c = self.carte
        c.vider("inactifs")
        rep = self._repere()
        if rep is None:
            return
        for i, v in enumerate(self.projet.variantes):
            if i == self.projet.active:
                continue
            res = self.inactifs.get(self._cle(v))
            if res is None:
                continue
            it = QGraphicsPathItem(self._chemin_poly(simplifiee(res.enveloppes.carrosserie, 0.02), rep))
            it.setPen(crayon(QColor(v.couleur), 2.0, Qt.DashLine))
            c.ajouter("inactifs", it, 6)
            p = QPainterPath()
            p.addPolygon(QPolygonF(rep.points(list(zip(res.sim.x[::4], res.sim.y[::4])))))
            it = QGraphicsPathItem(p)
            it.setPen(crayon(QColor(v.couleur), 1.2, Qt.DotLine))
            c.ajouter("inactifs", it, 6)

    def _dessiner_resultat(self) -> None:
        c = self.carte
        c.vider("enveloppe")
        res = self.resultat
        rep = self._repere()
        if res is None or rep is None:
            return
        coul = QColor(self.variante.couleur)
        rempli = QColor(coul)
        rempli.setAlpha(110)
        it = QGraphicsPathItem(self._chemin_poly(simplifiee(res.enveloppes.carrosserie, 0.01), rep))
        it.setPen(crayon(coul, 2.2))
        it.setBrush(QBrush(rempli))
        c.ajouter("enveloppe", it, 10)
        it = QGraphicsPathItem(self._chemin_poly(simplifiee(res.enveloppes.roues, 0.01), rep))
        it.setPen(crayon(QColor("#2E9B3B"), 1.4))
        c.ajouter("enveloppe", it, 11)
        for s in self.controle.zones_hors:
            it = QGraphicsPathItem(self._chemin_poly(s.zone, rep))
            it.setPen(crayon(ROUGE, 2.0))
            it.setBrush(QBrush(QColor(229, 57, 53, 140)))
            c.ajouter("enveloppe", it, 12)
        for a in res.alertes:
            if a.x is None or a.niveau == "info":
                continue
            e = Poignee(None, QColor(0, 0, 0, 0), ROUGE, 16, 3)
            e.setData(0, None)
            e.setPos(rep.point(a.x, a.y))
            e.setCursor(Qt.ArrowCursor)
            c.ajouter("enveloppe", e, 13)

    def _etat_point(self, i: int) -> str:
        if self.au_mieux is not None:
            return "impossible" if i < len(self.au_mieux.ecarts) and self.au_mieux.ecarts[i] > ECART_VISIBLE else "ok"
        info = next((s for s in self.infos if s.indice == i), None)
        return etat_rayon(info.rayon, self.rmin()) if info else "ok"

    def _dessiner_trace(self) -> None:
        c = self.carte
        c.vider("trace")
        rep = self._repere()
        pts = self.variante.points
        if rep is None or not pts:
            return
        rmin = self.rmin()
        ts, _, _ = construire_passages(pts, self.variante.rayons, rmin)
        am = self.au_mieux if (self.au_mieux is not None and len(self.au_mieux.ecarts) == len(pts)) else None
        if self.avant_correction:
            it = QGraphicsPathItem(self._chemin_ligne(self.avant_correction, rep))
            it.setPen(crayon(ROUGE, 1.8, Qt.DashLine))
            c.ajouter("trace", it, 20)
        if ts is not None:
            if am is not None:
                # votre saisie en pointillé rouge ; le trajet du véhicule, en rouge plein là où il la quitte
                it = QGraphicsPathItem(self._chemin_ligne([q[:2] for q in ts.echantillons(0.4)], rep))
                it.setPen(crayon(ROUGE, 1.8, Qt.DashLine))
                c.ajouter("trace", it, 21)
                pv = [q[:2] for q in am.trace_vehicule.echantillons(0.4)]
                for largeur, couleur, style in ((4.5, QColor(255, 255, 255, 200), Qt.SolidLine), (2.4, BLEU, Qt.DashLine)):
                    it = QGraphicsPathItem(self._chemin_ligne(pv, rep))
                    it.setPen(crayon(couleur, largeur, style))
                    c.ajouter("trace", it, 22)
                for seg in am.contraint:
                    it = QGraphicsPathItem(self._chemin_ligne(seg, rep))
                    it.setPen(crayon(ROUGE, 4.0))
                    c.ajouter("trace", it, 23)
            else:
                # trajet saisi, chaque virage dans la couleur de son rayon (bleu, orange, rouge)
                for t in ts.troncons:
                    n = max(1, math.ceil(t.longueur / 0.4)) if t.est_arc else 1
                    q = [t.point(t.longueur * j / n)[:2] for j in range(n + 1)]
                    coul = COULEUR_ETAT[etat_rayon(t.rayon, rmin)] if t.est_arc else BLEU
                    for largeur, couleur, style in ((4.5, QColor(255, 255, 255, 200), Qt.SolidLine), (2.4, coul, Qt.DashLine)):
                        it = QGraphicsPathItem(self._chemin_ligne(q, rep))
                        it.setPen(crayon(couleur, largeur, style))
                        c.ajouter("trace", it, 21)
        if self.proposition:
            for largeur, couleur, style in ((6, QColor(255, 255, 255, 200), Qt.SolidLine), (3, VERT, Qt.DashLine)):
                it = QGraphicsPathItem(self._chemin_ligne(self.proposition, rep))
                it.setPen(crayon(couleur, largeur, style))
                c.ajouter("trace", it, 24)
        # poignées
        auto = self.variante.giratoire is not None          # points construits par l'outil Giratoire
        for i, (x, y) in enumerate(pts):
            trait = COULEUR_ETAT[self._etat_point(i)]
            sel = i == self.selection
            if i == 0:
                h = Poignee(("sommet", i), BLEU, QColor("#ffffff"), 6, 2)
            elif auto and 0 < i < len(pts) - 1 and not sel:
                h = Poignee(("sommet", i), QColor("#ffffff"), trait, 4, 1.6)
            else:
                h = Poignee(("sommet", i), QColor("#ffffff"), trait, 7.5 if sel else 5.5, 3 if sel else 2)
            h.setPos(rep.point(x, y))
            h.setToolTip(f"Point {i + 1} — glisser pour déplacer, Ctrl + molette : rayon, clic droit : menu")
            c.ajouter("trace", h, 40)
        # étiquette du point sélectionné
        i = self.selection
        if i is not None and 0 < i < len(pts) - 1:
            info = next((s for s in self.infos if s.indice == i), None)
            if info is not None:
                ecart = self.au_mieux.ecarts[i] if self.au_mieux and i < len(self.au_mieux.ecarts) else 0.0
                if ecart > ECART_VISIBLE:
                    texte, etat = f"Point {i + 1} · virage trop serré (R {_fr(info.rayon)} m)\nle véhicule passe à {_fr(ecart, 2)} m", "impossible"
                else:
                    pct, vit = self._mesures_sommet(info)
                    texte = f"Point {i + 1} · R = {_fr(info.rayon)} m" + ("" if self.variante.rayons[i] else " (proposé)")
                    if pct is not None:
                        texte += f"\n{vit:.0f} km/h · braquage {pct:.0f} %" if vit is not None else f"\nbraquage {pct:.0f} %"
                    etat = etat_rayon(info.rayon, rmin)
                self._bulle(texte, rep.point(*pts[i]), etat, "trace")

    def _bulle(self, texte: str, p: QPointF, etat: str, calque: str) -> None:
        rouge = etat == "impossible"
        e = Etiquette(texte, QColor("#FCEBEB") if rouge else QColor("#ffffff"),
                      QColor("#791F1F") if rouge else QColor("#0C447C"), COULEUR_ETAT[etat])
        e.setPos(p)
        self.carte.ajouter(calque, e, 45)

    def _maj_guide(self) -> None:
        var = self.variante
        self.guide = guides(var.points, var.rayons, self.rmin()) if (self.carte.mode == "tracer" and
                                                                     len(var.points) >= 2) else None

    def _dessiner_guides(self) -> None:
        c = self.carte
        c.vider("guides")
        g = self.guide
        if g is None or self.carte.mode != "tracer" or not self.opt_guides.isChecked():
            return
        rep = self._repere()
        if rep is None:
            return

        def morceaux(pts):
            seg, cur = [], []
            for q in pts:
                if q is None:
                    if len(cur) >= 2:
                        seg.append(cur)
                    cur = []
                else:
                    cur.append(q)
            if len(cur) >= 2:
                seg.append(cur)
            return seg
        for seg in morceaux(g.limite):
            poly = QGraphicsPolygonItem(QPolygonF(rep.points([g.dernier] + seg)))
            poly.setPen(QPen(Qt.NoPen))
            poly.setBrush(QBrush(QColor(229, 57, 53, 34)))
            c.ajouter("guides", poly, 18)
            it = QGraphicsPathItem(self._chemin_ligne(seg, rep))
            it.setPen(crayon(ROUGE, 2.2, Qt.DotLine))
            c.ajouter("guides", it, 19)
        for seg in morceaux(g.confort):
            it = QGraphicsPathItem(self._chemin_ligne(seg, rep))
            it.setPen(crayon(QColor("#4C8FE0"), 2.0, Qt.DotLine))
            c.ajouter("guides", it, 19)

    def _dessiner_vehicule(self) -> None:
        c = self.carte
        c.vider("vehicule")
        res, rep = self.resultat, self._repere()
        if res is None or rep is None:
            return
        self._dessiner_caisses(res, self._s_position(), rep, "vehicule", QColor(230, 241, 251, 225))

    def _dessiner_caisses(self, res: Resultat, s: Optional[float], rep: Repere, calque: str, fond: QColor) -> None:
        c = self.carte
        sim = res.sim
        i = sim.n - 1 if s is None else int(np.clip(np.searchsorted(sim.s, s), 0, sim.n - 1))
        for k in range(len(res.vehicule.elements)):
            poly = QGraphicsPolygonItem(QPolygonF(rep.points(sim.caisse(k)[i])))
            poly.setPen(crayon(QColor("#0C447C"), 1.6))
            poly.setBrush(QBrush(fond))
            c.ajouter(calque, poly, 30)
        for _nom, centre, n, LE, directeur in sim.essieux():
            cx, cy = centre[i]
            nx, ny = n[i]
            ux, uy = ny, -nx
            a = float(sim.alpha[i]) if directeur else 0.0
            ca, sa = math.cos(a), math.sin(a)
            vx, vy = ux * ca - uy * sa, ux * sa + uy * ca
            wx, wy = -vy, vx
            lg, lp = (0.95, 0.32) if LE > 2.2 else (0.62, 0.2)
            for cote in (1, -1):
                ox = cx + cote * (LE / 2 - lp / 2) * nx
                oy = cy + cote * (LE / 2 - lp / 2) * ny
                q = [(ox + aa * vx + bb * wx, oy + aa * vy + bb * wy) for aa, bb in
                     ((lg / 2, lp / 2), (lg / 2, -lp / 2), (-lg / 2, -lp / 2), (-lg / 2, lp / 2))]
                poly = QGraphicsPolygonItem(QPolygonF(rep.points(q)))
                poly.setPen(QPen(Qt.NoPen))
                poly.setBrush(QBrush(QColor("#1B1B1B")))
                c.ajouter(calque, poly, 31)

    def _dessiner_apercu(self) -> None:
        c = self.carte
        c.vider("apercu")
        res, rep = self.apercu, self._repere()
        if res is None or rep is None or self.carte.mode != "tracer":
            return
        coul = QColor(self.variante.couleur)
        coul.setAlpha(70)
        it = QGraphicsPathItem(self._chemin_poly(simplifiee(res.enveloppes.carrosserie, 0.05), rep))
        it.setPen(QPen(Qt.NoPen))
        it.setBrush(QBrush(coul))
        c.ajouter("apercu", it, 9)
        self._dessiner_caisses(res, None, rep, "apercu", QColor(255, 255, 255, 150))

    def _dessiner_elastique(self, x: float, y: float) -> None:
        c = self.carte
        c.vider("elastique")
        var = self.variante
        rep = self._repere()
        if rep is None or not var.points:
            return
        rmin = self.rmin()
        e = elastique(var.points, var.rayons, (x, y), rmin)
        if e is None:
            return
        coul = COULEUR_ETAT[e.etat]
        for largeur, couleur in ((5, QColor(255, 255, 255, 210)), (3, coul)):
            it = QGraphicsPathItem(self._chemin_ligne(e.morceau, rep))
            it.setPen(crayon(couleur, largeur))
            c.ajouter("elastique", it, 50)
        if e.info is not None:
            veh = self.vehicule
            k = self.echelle()
            pct = pct_braquage(e.info.rayon, veh.tracteur.E * k, veh.alpha_max)
            if e.etat == "impossible":
                texte = f"R {_fr(e.info.rayon)} m · impossible : trop serré\nminimum du véhicule : {_fr(rmin)} m"
            else:
                cd = conduite_pour(veh, var.conduite)
                v = min(cd.v_approche, math.sqrt(cd.a_lat * e.info.rayon)) / KMH
                texte = f"R {_fr(e.info.rayon)} m · {pct:.0f} % du braquage\nvitesse ≈ {v:.0f} km/h"
            self._bulle(texte, rep.point(x, y), e.etat, "elastique")

    def _dessiner_mesure(self) -> None:
        c = self.carte
        c.vider("elastique")
        if self._mesure_a is None or self._curseur_plan is None:
            return
        rep = self._repere(*self._mesure_a)
        if rep is None:
            return
        a, b = self._mesure_a, self._curseur_plan
        it = QGraphicsPathItem(self._chemin_ligne([a, b], rep))
        it.setPen(crayon(JAUNE, 2.6))
        c.ajouter("elastique", it, 50)
        for q in (a, b):
            h = Poignee(None, JAUNE, QColor("#1b1b1b"), 4, 1)
            h.setData(0, None)
            h.setPos(rep.point(*q))
            c.ajouter("elastique", h, 51)
        self._bulle(f"{_fr(math.dist(a, b), 2)} m", rep.point(*b), "ok", "elastique")

    def _curseur(self, lat: float, lon: float) -> None:
        texte = format_dms(lat, lon)
        leg = systeme_legal(lat, lon)
        if leg:
            texte += f"   {'L93' if leg.epsg == 2154 else leg.nom} X {leg.x:,.1f}  Y {leg.y:,.1f}".replace(",", " ")
        self.lbl_curseur.setText(texte)
        mode = self.carte.mode
        if mode == "anneau" and self._releve is not None and self._releve["ext"]:
            self._curseur_plan = self._plan(lat, lon)
            self._dessiner_releve()
        elif mode == "mesurer" and self._mesure_a is not None:
            self._curseur_plan = self._plan(lat, lon)
            self._dessiner_mesure()
        elif mode == "bordure":
            self._curseur_bordure(lat, lon)
        elif mode == "tracer" and self.variante.points:
            x, y = self._plan(lat, lon)
            self._curseur_plan = (x, y)
            self._dessiner_elastique(x, y)
            if math.dist(self.variante.points[-1], (x, y)) > 1.0:
                self.lancer_apercu((x, y))

    # ================================================================== recherche et navigation
    def rechercher(self) -> None:
        texte = self.saisie.text().strip()
        if not texte:
            return
        coord = analyser_saisie(texte)
        if coord:
            self.carte.centrer(coord[0], coord[1], NIVEAU_PHOTO_20CM)
            return
        self.statusBar().showMessage(f"Recherche de « {texte} »…", 0)
        self.client.obtenir_json(url_recherche(texte, 10, *self.carte.centre_geo()), self._resultats_recherche)

    def _resultats_recherche(self, donnees, erreur) -> None:
        if donnees is None:
            self.statusBar().showMessage(f"Recherche impossible : {erreur}. Vérifiez la connexion Internet.", 10000)
            return
        lieux = lire_recherche(donnees)
        if not lieux:
            self.statusBar().showMessage("Aucun résultat.", 6000)
            return
        self.statusBar().clearMessage()
        if len(lieux) == 1:
            self._aller(lieux[0])
            return
        m = QMenu(self)
        for lieu in lieux:
            m.addAction(lieu.libelle + (f"  ({lieu.commune})" if lieu.commune and lieu.commune not in lieu.libelle else "")
                        ).triggered.connect(lambda _=False, l=lieu: self._aller(l))
        m.popup(self.saisie.mapToGlobal(QPoint(0, self.saisie.height())))

    def _aller(self, lieu) -> None:
        self.carte.centrer(lieu.lat, lieu.lon, ZOOM_LIEU.get(lieu.type, 17))
        self.statusBar().showMessage(f"{lieu.libelle} — zoomez puis cliquez « Tracer » ou « Giratoire ».", 8000)

    def centrer_giration(self) -> None:
        pts = [p for v in self.projet.variantes for p in v.points]
        if not pts:
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        lat, lon = self.geo.vers_geo((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        self.carte.ajuster(lat, lon, max(30.0, 0.6 * max(max(xs) - min(xs), max(ys) - min(ys))))

    # ================================================================== fichiers
    def _confirmer_abandon(self) -> bool:
        if not self.modifie or not any(v.points for v in self.projet.variantes):
            return True
        r = QMessageBox.question(self, TITRE, "Enregistrer le projet avant de continuer ?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.enregistrer()
        return r == QMessageBox.Discard

    def nouveau(self) -> None:
        if not self._confirmer_abandon():
            return
        self.projet = projet_vide(self.variante.vehicule_id)
        self.historique = Historique(self.projet)
        self.chemin, self.modifie, self.selection = None, False, None
        self.inactifs.clear()
        self.avant_correction = self.proposition = None
        self._tout_rafraichir()

    def ouvrir_dialogue(self) -> None:
        if not self._confirmer_abandon():
            return
        chemin, _ = QFileDialog.getOpenFileName(self, "Ouvrir un projet", self._dossier(),
                                                "Projets Giration (*.giration);;Sites Girabase (*.gsite)")
        if chemin:
            if chemin.lower().endswith(".gsite"):
                self.charger_site(chemin)
            else:
                self.ouvrir(chemin)

    def ouvrir(self, chemin: str) -> None:
        try:
            p = Projet.ouvrir(chemin)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, TITRE, f"Impossible d'ouvrir le projet :\n{exc}")
            return
        for v in p.variantes:
            if v.vehicule_id not in self.biblio.vehicules:
                v.vehicule_id = VEHICULE_DEFAUT
        self.projet = p
        self.geo = Georef(p.systeme, tuple(p.origine) if p.origine else None) if (p.systeme or p.origine) else Georef("L93")
        self.historique = Historique(self.projet, "Projet ouvert")
        self.chemin, self.modifie, self.selection = Path(chemin), False, None
        self.repere = None
        self.inactifs.clear()
        self.avant_correction = self.proposition = None
        self._cle_chaussee = None
        if p.vue:
            self.carte.centrer(p.vue[0], p.vue[1], p.vue[2])
        self._tout_rafraichir()
        self.charger_routes(forcer=True)

    def _dossier(self) -> str:
        return str(self.reglages.value("dossier", str(Path.home())))

    def enregistrer(self) -> bool:
        if self.chemin is None:
            return self.enregistrer_sous()
        lat, lon = self.carte.centre_geo()
        self.projet.vue = [lat, lon, self.carte.niveau]
        self.projet.enregistrer(self.chemin)
        self.modifie = False
        self._titre()
        self.statusBar().showMessage(f"Projet enregistré : {self.chemin}", 5000)
        return True

    def enregistrer_sous(self) -> bool:
        chemin, _ = QFileDialog.getSaveFileName(self, "Enregistrer le projet", str(Path(self._dossier()) / "giration.giration"),
                                                "Projets Giration (*.giration)")
        if not chemin:
            return False
        if not chemin.lower().endswith(".giration"):
            chemin += ".giration"
        self.chemin = Path(chemin)
        self.reglages.setValue("dossier", str(self.chemin.parent))
        return self.enregistrer()

    # ================================================================== exports
    def _resultats_export(self) -> list[Resultat]:
        res = []
        for v in self.projet.variantes:
            if len(v.points) < 2:
                continue
            r, _ = self._fonction_calcul(v, False)()
            if r is None:
                continue
            veh = r.vehicule.copie(nom=f"Variante {v.nom} — {r.vehicule.nom}",
                                   nom_court=f"{v.nom} {r.vehicule.nom_court or r.vehicule.id}")
            res.append(replace(r, vehicule=veh))
        if not res:
            QMessageBox.information(self, TITRE, "Tracez d'abord un trajet.")
        return res

    def _fichier_export(self, titre: str, ext: str, filtre: str) -> Optional[str]:
        base = self.chemin.stem if self.chemin else "giration"
        chemin, _ = QFileDialog.getSaveFileName(self, titre, str(Path(self._dossier()) / f"{base}.{ext}"), filtre)
        if chemin and not chemin.lower().endswith("." + ext):
            chemin += "." + ext
        if chemin:
            self.reglages.setValue("dossier", str(Path(chemin).parent))
        return chemin or None

    def _apres_export(self, chemin: str) -> None:
        self.statusBar().showMessage(f"Exporté : {chemin}", 8000)

    def exporter_kml(self) -> None:
        from ..exports.kml import exporter_kml
        res = self._resultats_export()
        if res and (ch := self._fichier_export("Exporter en KML", "kml", "KML (*.kml)")):
            exporter_kml(res, ch, self.geo, nom=f"Giration — {Path(ch).stem}")
            self._apres_export(ch)

    def exporter_geojson(self) -> None:
        from ..exports.geojson import exporter_geojson
        res = self._resultats_export()
        if res and (ch := self._fichier_export("Exporter en GeoJSON", "geojson", "GeoJSON (*.geojson)")):
            exporter_geojson(res, ch, self.geo)
            self._apres_export(ch)

    def exporter_json(self) -> None:
        from ..exports.geojson import exporter_projet
        res = self._resultats_export()
        if res and (ch := self._fichier_export("Exporter le calcul en JSON", "json", "JSON (*.json)")):
            exporter_projet(res, ch, self.geo, source=self.chemin.name if self.chemin else "")
            self._apres_export(ch)

    def exporter_dxf(self) -> None:
        from ..exports.dxf import exporter_dxf
        res = self._resultats_export()
        if res and (ch := self._fichier_export("Exporter en DXF (Lambert-93)", "dxf", "DXF (*.dxf)")):
            exporter_dxf(res, ch)
            self._apres_export(ch)

    def exporter_image(self) -> None:
        if ch := self._fichier_export("Image de la carte", "png", "Image PNG (*.png)"):
            self.carte.viewport().grab().save(ch)
            self._apres_export(ch)

    # ================================================================== aide
    def aide(self) -> None:
        QMessageBox.information(self, "Mode d'emploi", (
            "1. Cherchez la commune ou l'adresse, ou zoomez à la molette depuis la carte de France : la photographie "
            "aérienne s'affiche automatiquement (bouton « Photo 20 cm » : pleine résolution).\n\n"
            "2. Choisissez le véhicule (panneau de gauche).\n\n"
            "3. Tracer (T) : cliquez le départ dans la voie d'arrivée, puis les points de passage du milieu de l'essieu "
            "avant ; le trajet passe par vos clics. Restez au-delà du pointillé rouge (limite du braquage) ; le "
            "pointillé bleu montre un virage confortable. Retour arrière enlève le dernier point.\n"
            "   Giratoire (G) : cliquez la branche d'entrée puis la branche de sortie.\n\n"
            "4. Terminer (Entrée, double-clic ou clic droit) : le départ et l'arrivée se placent au milieu de la voie de "
            "droite, puis les corrections du véhicule s'appliquent. Un virage trop serré est élargi ; s'il manque de "
            "place, le véhicule fait au mieux et l'écart à vos points est chiffré. Aucun point n'est déplacé : votre "
            "saisie reste en pointillé rouge et Ctrl+Z la rend. (Options : corrections d'office ou proposées.)\n\n"
            "5. Ajustez : glissez les points ; Ctrl + molette sur un point change le rayon du virage ; clic droit pour "
            "insérer ou supprimer un point.\n\n"
            "6. Mesurer (M) : deux clics d'un bord de la chaussée à l'autre sur la photo corrigent la largeur de la "
            "route et recalent son axe (la BD TOPO est une estimation, la photo fait foi).\n\n"
            "7. Anneau (A) : si l'anneau de la BD TOPO est décalé de la photo, relevez le giratoire vous-même : 3 clics "
            "sur la bordure extérieure, 1 clic sur le bord intérieur de la chaussée, 1 clic sur le bord de l'îlot "
            "(Entrée s'il n'y a pas de bande franchissable). Il remplace l'anneau de la BD TOPO ; ses poignées "
            "l'ajustent ; Fichier > Enregistrer l'anneau pour Girabase l'écrit en .gsite. L'outil Giratoire prend "
            "alors vos clics au milieu des voies.\n\n"
            "8. Bordure (B) : relevez sur la photo les bordures (les roues ne les franchissent pas : ne passe pas), "
            "les bordures franchissables et bateaux (réserve) et les obstacles — murs, poteaux, clôtures (la caisse ne "
            "doit pas les toucher). Clic par clic, Entrée pour finir, clic sur le premier point pour fermer ; la flèche "
            "du bouton choisit le type ; clic droit sur une bordure pour la changer ou la supprimer. Les bâtiments de la "
            "BD TOPO sont contrôlés aussi (réserve, précision de quelques mètres ; menu Couches).\n\n"
            "9. Comparer : les variantes côte à côte (emprises, braquage, vitesse, conflits). « Même trajet, autre "
            "véhicule » copie la variante et change le véhicule.\n\n"
            "10. Exporter : DXF en Lambert-93 ou dans la zone CC du lieu (CC44 dans le Tarn), avec les bordures et "
            "obstacles ; note de giration en PDF (plan, résultats, comparaison, hypothèses, sources).\n\n"
            "Ctrl+Z annule à tout moment ; l'historique permet de revenir à n'importe quelle étape ; une variante "
            "garde un essai pour le comparer à un autre. Fichier > Ouvrir un site Girabase donne l'îlot et la bande "
            "franchissable exacts d'un giratoire."))

    def a_propos(self) -> None:
        QMessageBox.about(self, "À propos", f"<b>Giration {__version__}</b><br>{DEVISE}<br><br>Épures de giration "
                          "des véhicules : modèle cinématique du CERTU (Giration 3.3), bibliothèque de véhicules sourcée, "
                          "fonds et données de l'IGN (Licence Ouverte 2.0).<br>Licence GNU GPL v3. Ce logiciel n'est ni "
                          "édité ni validé par le CEREMA.")

    # ================================================================== réglages
    def _restaurer(self) -> None:
        g = self.reglages.value("geometrie")
        if g is not None:
            self.restoreGeometry(g)
        vue = self.reglages.value("vue")
        if vue:
            try:
                lat, lon, niv = (float(v) for v in vue)
                self.carte.centrer(lat, lon, niv)
                return
            except (TypeError, ValueError):
                pass
        self.carte.voir_france()

    def closeEvent(self, ev):
        if not self._confirmer_abandon():
            ev.ignore()
            return
        self.reglages.setValue("geometrie", self.saveGeometry())
        lat, lon = self.carte.centre_geo()
        self.reglages.setValue("vue", [lat, lon, self.carte.niveau])
        self.pool.waitForDone(3000)
        super().closeEvent(ev)
