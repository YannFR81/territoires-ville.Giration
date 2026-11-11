"""Lot 2 de Giration, greffé sur la fenêtre principale (``class FenetreGiration(OutilsLot2, QMainWindow)``).

- **Outil Bordure (B)** : bordures, bordures franchissables et obstacles relevés sur la photo (la photo fait foi).
  Clic par clic ; Entrée, double-clic ou clic droit pour finir ; clic sur le premier point pour fermer un contour ;
  un seul clic puis Entrée pour un poteau. Clic droit sur une contrainte : changer son type ou la supprimer.
- **Bâtiments de la BD TOPO**, chargés avec les routes, contrôlés comme obstacles (réserve, précision de quelques
  mètres).
- **Conflits** reportés dans le verdict, les remarques et sur la carte.
- **Comparer** : variantes côte à côte (même trajet avec un autre véhicule, ou autre trajet).
- **Note de giration** en PDF.
- **DXF** en Lambert-93 ou dans la zone CC du lieu (CC44 dans le Tarn), avec les bordures et obstacles.

Les points d'accroche dans ``fenetre.py`` sont décrits dans ``docs/integration-lot2.md``.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGraphicsEllipseItem, QGraphicsPathItem,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QPushButton, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout)

from ..carto.geoservices import _wfs
from ..projections import format_dms, systeme_legal
from .bilan import COULEURS as COULEURS_VERDICT
from .bilan import Bilan, bilan_depuis_mesures, completer, resume, tableau
from .carte import crayon
from .contraintes import (TYPES, controler_bati, controler_contraintes, libelle, lire_batiments, nouvelle, valide)

RAYON_BATI = 200.0                                   # m autour du trajet
STYLE = {"bordure": (QColor("#FFFFFF"), Qt.SolidLine), "franchissable": (QColor("#F2C200"), Qt.DashLine),
         "obstacle": (QColor("#E53935"), Qt.SolidLine)}
NOMS_OUTIL = {"bordure": "Bordure", "franchissable": "Bordure franchissable", "obstacle": "Obstacle"}


def url_batiments(lat: float, lon: float, rayon: float = RAYON_BATI) -> str:
    return _wfs("BDTOPO_V3:batiment", lat, lon, rayon, 500)


class OutilsLot2:
    # ================================================================== état et interface
    def _init_lot2(self) -> None:
        """À appeler au début de ``FenetreGiration.__init__`` (avant la construction des barres d'outils)."""
        self.type_contrainte = "bordure"
        self._contrainte: Optional[dict] = None          # contrainte en cours de dessin
        self.batiments: list = []
        self.zone_bati: Optional[tuple] = None
        self.conflits: list = []                         # contraintes.Conflit de la variante active
        self._complets: dict = {}                        # clé de variante → (résultat complet, écart au mieux)

    def _outil_bordure(self, tb, groupe: QActionGroup) -> None:
        """Bouton « Bordure B » de la barre Giration, avec le choix du type (flèche)."""
        a = QAction("Bordure  B", self)
        a.setCheckable(True)
        a.setToolTip("Dessiner sur la photo une bordure (les roues ne la franchissent pas), une bordure franchissable "
                     "ou un obstacle (mur, poteau) : clic par clic, Entrée pour finir, clic sur le premier point pour "
                     "fermer (B)")
        a.triggered.connect(lambda _=False: self.choisir_mode("bordure"))
        groupe.addAction(a)
        self.actions_mode["bordure"] = a
        bt = QToolButton()
        bt.setDefaultAction(a)
        bt.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(bt)
        g = QActionGroup(m)
        self.actions_type = {}
        for code, texte in TYPES.items():
            t = m.addAction(texte)
            t.setCheckable(True)
            t.setChecked(code == self.type_contrainte)
            g.addAction(t)
            t.triggered.connect(lambda _=False, c=code: self._choisir_type(c))
            self.actions_type[code] = t
        m.addSeparator()
        m.addAction("Effacer toutes les bordures et obstacles").triggered.connect(self.effacer_contraintes)
        bt.setMenu(m)
        tb.addWidget(bt)

    def _choisir_type(self, code: str) -> None:
        self.type_contrainte = code
        self.actions_type[code].setChecked(True)
        self.actions_mode["bordure"].setText(f"{NOMS_OUTIL[code]}  B")
        if self._contrainte is not None:
            self._contrainte["type"] = code
        self.choisir_mode("bordure")

    # ================================================================== outil Bordure
    def _clic_bordure(self, lat: float, lon: float) -> None:
        self._assurer_geo(lat, lon)
        p = list(self._plan(lat, lon))
        c = self._contrainte
        if c is None:
            c = self._contrainte = {"type": self.type_contrainte, "points": []}
        pts = c["points"]
        tol = max(0.3, 8 * self.carte.metres_par_pixel())
        if len(pts) >= 3 and math.dist(pts[0], p) < tol:
            self._finir_contrainte(ferme=True)
            return
        if pts and math.dist(pts[-1], p) < 0.05:
            return
        pts.append(p)
        self._dessiner_contrainte_en_cours()
        self._maj_assistant()

    def _finir_contrainte(self, ferme: bool = False) -> bool:
        """Entrée, double-clic ou clic droit : la contrainte en cours rejoint le projet (annulable)."""
        c = self._contrainte
        if c is None or not c["points"]:
            return False
        n = nouvelle(c["type"], c["points"], ferme)
        if not valide(n):
            self.bandeau.afficher("Une bordure demande au moins <b>deux points</b> (un seul point : choisissez "
                                  "<b>Obstacle</b> pour un poteau).", duree_ms=5000)
            return False
        self._contrainte = None
        self.carte.vider("elastique")
        self.projet.contraintes.append(n)
        self._changer(f"{libelle(n).capitalize()} dessinée")
        return True

    def _retour_contrainte(self) -> bool:
        if self._contrainte and self._contrainte["points"]:
            self._contrainte["points"].pop()
            self._dessiner_contrainte_en_cours()
            return True
        return False

    def _curseur_bordure(self, lat: float, lon: float) -> None:
        if self._contrainte and self._contrainte["points"]:
            self._curseur_plan = self._plan(lat, lon)
            self._dessiner_contrainte_en_cours()

    def _dessiner_contrainte_en_cours(self) -> None:
        c = self.carte
        c.vider("elastique")
        k = self._contrainte
        if not k or not k["points"]:
            return
        rep = self._repere(*k["points"][0])
        if rep is None:
            return
        pts = list(k["points"])
        if self._curseur_plan is not None:
            pts.append(list(self._curseur_plan))
        coul, style = STYLE[k["type"]]
        for largeur, couleur, st in ((6, QColor(0, 0, 0, 150), Qt.SolidLine), (3, coul, style)):
            it = QGraphicsPathItem(self._chemin_ligne(pts, rep))
            it.setPen(crayon(couleur, largeur, st))
            c.ajouter("elastique", it, 50)
        if self._curseur_plan is not None and len(k["points"]) >= 1:
            L = math.dist(k["points"][-1], self._curseur_plan)
            self._bulle(f"{NOMS_OUTIL[k['type']].lower()} · {L:.2f} m".replace(".", ","),
                        rep.point(*self._curseur_plan), "ok", "elastique")

    def _menu_contrainte(self, m: QMenu, i: int) -> None:
        """Entrées du menu contextuel d'une contrainte (clic droit dessus)."""
        cs = self.projet.contraintes
        if not 0 <= i < len(cs):
            return
        c = cs[i]
        for code, texte in NOMS_OUTIL.items():
            if code != c["type"] and (len(c["points"]) > 1 or code == "obstacle"):
                m.addAction(f"Changer en {texte.lower()}").triggered.connect(
                    lambda _=False, k=code: self._changer_type_contrainte(i, k))
        m.addAction(f"Supprimer ({libelle(c)})").triggered.connect(lambda: self._supprimer_contrainte(i))

    def _changer_type_contrainte(self, i: int, code: str) -> None:
        self.projet.contraintes[i]["type"] = code
        self._changer(f"{libelle(self.projet.contraintes[i]).capitalize()} (type changé)")

    def _supprimer_contrainte(self, i: int) -> None:
        c = self.projet.contraintes.pop(i)
        self._changer(f"{libelle(c).capitalize()} supprimée")

    def effacer_contraintes(self) -> None:
        if self.projet.contraintes:
            self.projet.contraintes = []
            self._changer("Bordures et obstacles effacés")

    # ================================================================== bâtiments de la BD TOPO
    def charger_batiments(self, centre: tuple, forcer: bool = False) -> None:
        lat, lon = centre
        if not forcer and self.zone_bati and math.dist(self.zone_bati, (lat, lon)) < 0.0007:
            return
        self.zone_bati = (lat, lon)

        def recu(donnees, erreur):
            if donnees is None:
                self.statusBar().showMessage(f"Bâtiments BD TOPO indisponibles ({erreur}).", 6000)
                return
            self.batiments = lire_batiments(donnees, self.geo)
            if self.resultat is not None and not self.resultat.rapide:
                self._controler_lot2(self.resultat)
                self._maj_resultats()
            self._redessiner()
        self.client.obtenir_json(url_batiments(lat, lon), recu)

    def _bati_actif(self) -> bool:
        ck = getattr(self, "ck_bati", None)
        return ck is None or ck.isChecked()

    # ================================================================== contrôles et verdict
    def _controler_lot2(self, res) -> list:
        """Conflits de la variante active : contraintes dessinées (roues et caisse), puis bâti BD TOPO (caisse)."""
        if res is None or getattr(res, "rapide", False):
            return self.conflits
        axe = np.column_stack([res.sim.x, res.sim.y])
        k = controler_contraintes(self.projet.contraintes, res.enveloppes.roues, res.enveloppes.carrosserie, axe)
        if self._bati_actif():
            k += controler_bati(self.batiments, res.enveloppes.carrosserie)
        self.conflits = k
        return k

    def _verdict_lot2(self, titre: str, detail: str) -> tuple[str, str]:
        """Durcit le verdict du panneau : bordure ou obstacle touché → impossible ; bordure franchissable ou bâti →
        réserve."""
        k = self.conflits
        if not k:
            return titre, detail
        graves = [c for c in k if c.grave]
        if graves and titre != "Impossible en l'état":
            return "Impossible en l'état", (graves[0].message() + ". Changez le trajet, élargissez le virage ou "
                                            "choisissez un autre véhicule.")
        if not graves and titre == "Passe":
            return "Passe avec réserves", k[0].message() + "."
        return titre, detail

    def _remarques_lot2(self) -> list[str]:
        items = []
        for c in self.conflits[:6]:
            coul = "#C62828" if c.grave else "#B26A00"
            items.append(f"<li><font color='{coul}'><b>{c.message()}</b></font></li>")
        if len(self.conflits) > 6:
            items.append(f"<li>… et {len(self.conflits) - 6} autres contacts.</li>")
        return items

    # ================================================================== dessin
    def _dessiner_lot2(self) -> None:
        """À appeler dans ``_redessiner``."""
        c = self.carte
        c.vider("contraintes")
        rep = self._repere()
        if rep is None:
            return
        if self._bati_actif():
            for b in self.batiments:
                it = QGraphicsPathItem(self._chemin_poly(b.polygone, rep))
                it.setPen(crayon(QColor(200, 120, 255, 200), 1.0))
                it.setBrush(QBrush(QColor(200, 120, 255, 40)))
                it.setToolTip(f"Bâtiment BD TOPO ({b.usage or 'usage inconnu'}) : précision de quelques mètres, la "
                              "photo fait foi")
                c.ajouter("contraintes", it, 3)
        for i, k in enumerate(self.projet.contraintes):
            if not valide(k):
                continue
            coul, style = STYLE[k["type"]]
            aide = f"{libelle(k).capitalize()} — clic droit : changer le type ou supprimer"
            if len(k["points"]) == 1:
                x, y = k["points"][0]
                p = rep.point(x, y)
                it = QGraphicsEllipseItem(-5, -5, 10, 10)
                it.setFlag(it.GraphicsItemFlag.ItemIgnoresTransformations, True)
                it.setPos(p)
                it.setPen(QPen(QColor("#1b1b1b"), 1.5))
                it.setBrush(QBrush(coul))
                it.setData(0, ("contrainte", i))
                it.setToolTip(aide)
                c.ajouter("contraintes", it, 26)
                continue
            pts = [tuple(q) for q in k["points"]] + ([tuple(k["points"][0])] if k.get("ferme") else [])
            for largeur, couleur, st in ((6, QColor(0, 0, 0, 150), Qt.SolidLine), (3, coul, style)):
                it = QGraphicsPathItem(self._chemin_ligne(pts, rep))
                it.setPen(crayon(couleur, largeur, st))
                it.setData(0, ("contrainte", i))
                it.setToolTip(aide)
                c.ajouter("contraintes", it, 25)
        for k in self.conflits:
            if k.zone is None or k.zone.is_empty:
                continue
            it = QGraphicsPathItem(self._chemin_poly(k.zone, rep))
            rouge = k.grave
            it.setPen(crayon(QColor("#E53935") if rouge else QColor("#E8730C"), 1.6))
            it.setBrush(QBrush(QColor(229, 57, 53, 150) if rouge else QColor(232, 115, 12, 120)))
            c.ajouter("contraintes", it, 27)

    # ================================================================== comparaison
    def _resultat_complet(self, v):
        """Résultat complet d'une variante inactive (les contours de comparaison sont des calculs rapides)."""
        cle = self._cle(v)
        if cle not in self._complets:
            if len(self._complets) > 20:
                self._complets.clear()
            try:
                res, extra = self._fonction_calcul(v, False)()
            except Exception:                             # variante incalculable : absente de la comparaison
                res, extra = None, {}
            am = (extra or {}).get("au_mieux")
            self._complets[cle] = (res, max(am.ecarts) if am is not None and am.ecarts else 0.0)
        return self._complets[cle]

    def _bilan(self, i: int) -> Optional[Bilan]:
        v = self.projet.variantes[i]
        if len(v.points) < 2:
            return None
        actif = i == self.projet.active
        if actif:
            res = self.resultat
            ecart = max(self.au_mieux.ecarts) if self.au_mieux and self.au_mieux.ecarts else 0.0
        else:
            res, ecart = self._resultat_complet(v)
        if res is None or getattr(res, "rapide", False):
            return None
        from .saisie import controler
        veh = res.vehicule
        nom_veh = veh.nom_court or veh.nom.split(" — ")[0]
        ctl = self.controle if actif else controler(self.routes, res.enveloppes.roues, self._anneau())
        alertes = tuple(a.message for a in res.alertes if a.niveau != "info")
        b = bilan_depuis_mesures(f"{v.nom} — {nom_veh}", veh.nom.split(" — ")[0], res.mesures(),
                                 faisable=res.faisable, couleur=v.couleur, remorques=bool(veh.nb_remorques),
                                 surface=float(res.enveloppes.carrosserie.area), ecart_saisie=ecart, alertes=alertes,
                                 hors=ctl.hors, ilot=ctl.ilot, bande=ctl.bande)
        if actif:
            conflits = self.conflits
        else:
            axe = np.column_stack([res.sim.x, res.sim.y])
            conflits = controler_contraintes(self.projet.contraintes, res.enveloppes.roues,
                                             res.enveloppes.carrosserie, axe)
            if self._bati_actif():
                conflits += controler_bati(self.batiments, res.enveloppes.carrosserie)
        return completer(b, conflits)

    def bilans(self) -> list[Bilan]:
        return [b for b in (self._bilan(i) for i in range(len(self.projet.variantes))) if b is not None]

    def comparer_autre_vehicule(self) -> None:
        """Même trajet, autre véhicule : nouvelle variante puis choix du véhicule."""
        self.nouvelle_variante()
        self.choisir_vehicule()

    def comparer_variantes(self) -> None:
        bs = self.bilans()
        if len(bs) < 2:
            self.bandeau.afficher("Pour comparer, il faut au moins <b>deux variantes calculées</b> : « Nouvelle "
                                  "variante » copie le trajet ; changez ensuite le véhicule ou les points.",
                                  [("Même trajet, autre véhicule", self.comparer_autre_vehicule),
                                   ("Fermer", lambda: None)])
            return
        ref = next((j for j, b in enumerate(bs) if b.variante.startswith(self.variante.nom + " ")), 0)
        entetes, lignes = tableau(bs, ref)
        d = QDialog(self)
        d.setWindowTitle("Comparer les variantes")
        lay = QVBoxLayout(d)
        lbl = QLabel(resume(bs))
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        t = QTableWidget(len(lignes), len(entetes) - 1)
        t.setHorizontalHeaderLabels(entetes[1:])
        t.setVerticalHeaderLabels([l[0] for l in lignes])
        for r, ligne in enumerate(lignes):
            for k, v in enumerate(ligne[1:]):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if ligne[0] == "Verdict":
                    it.setForeground(QBrush(QColor(COULEURS_VERDICT.get(bs[k].verdict, "#333333"))))
                t.setItem(r, k, it)
        for k, b in enumerate(bs):
            t.horizontalHeaderItem(k).setForeground(QBrush(QColor(b.couleur)))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setMinimumSize(560, 420)
        lay.addWidget(t)
        note = QLabel(f"Entre parenthèses : écart avec la variante active ({bs[ref].variante}).")
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)
        h = QHBoxLayout()
        b1 = QPushButton("Même trajet, autre véhicule…")
        b1.clicked.connect(lambda: (d.accept(), self.comparer_autre_vehicule()))
        b2 = QPushButton("Note de giration (PDF)…")
        b2.clicked.connect(lambda: (d.accept(), self.exporter_note()))
        h.addWidget(b1)
        h.addWidget(b2)
        h.addStretch(1)
        boutons = QDialogButtonBox(QDialogButtonBox.Close)
        boutons.rejected.connect(d.reject)
        h.addWidget(boutons)
        lay.addLayout(h)
        d.exec()

    # ================================================================== note PDF
    def _image_carte(self):
        vis = self.bandeau.isVisible()
        self.bandeau.hide()
        try:
            return self.carte.viewport().grab().toImage()
        finally:
            if vis:
                self.bandeau.show()

    def _coordonnees_lieu(self) -> tuple[str, str]:
        pts = self.variante.points
        if not pts:
            return "", self.geo.nom()
        try:
            lat, lon = self.geo.vers_geo(*pts[0])
        except ValueError:
            return "", self.geo.nom()
        leg = systeme_legal(lat, lon)

        def xy(x, y):
            return f"X {x:,.1f} Y {y:,.1f}".replace(",", " ").replace(".", ",")
        t = format_dms(lat, lon) + (f" — {leg.nom} {xy(leg.x, leg.y)}" if leg else "")
        if leg is not None and leg.epsg == 2154:                 # métropole : aussi la conique conforme du lieu
            from ..exports.reprojection import projection, zone_cc
            z = zone_cc(lat)
            t += f" — CC{z} {xy(*projection(f'CC{z}').depuis_geo(lat, lon))}"
        return t, self.geo.nom()

    def _hypotheses(self) -> list[str]:
        p = self.projet
        h = [f"Circulation à {'gauche' if p.gauche else 'droite'}.",
             "Marge entre la roue avant extérieure et le bord de l'anneau : "
             + f"{p.marge_giratoire:.2f}".replace(".", ",") + " m.",
             "Corrections de fin de saisie " + ("appliquées d'office." if self.opt_office.isChecked() else "proposées.")]
        if p.ignorer_chaussee:
            h.append("Chaussée BD TOPO ignorée : la photo fait foi, roues hors chaussée non contrôlées.")
        if p.site:
            h.append(f"Anneau : {p.site.get('nom') or 'site Girabase'} (R {p.site.get('R')} m, Bf {p.site.get('Bf')} m, "
                     f"LA {p.site.get('LA')} m).")
        if p.contraintes:
            h.append(f"{len(p.contraintes)} bordure(s) ou obstacle(s) relevés sur la photo.")
        if self.batiments and self._bati_actif():
            h.append("Bâtiments de la BD TOPO contrôlés comme obstacles (réserve).")
        return h

    def exporter_note(self) -> None:
        from .. import __version__
        from .note import DonneesNote, EnteteNote, exporter_pdf
        bs = self.bilans()
        if not bs:
            self.bandeau.afficher("Pas encore d'épure calculée à mettre dans la note.", duree_ms=4000)
            return
        ch = self._fichier_export("Note de giration (PDF)", "pdf", "Documents PDF (*.pdf)")
        if not ch:
            return
        actif = next((j for j, b in enumerate(bs) if b.variante.startswith(self.variante.nom + " ")), 0)
        coord, systeme = self._coordonnees_lieu()
        titre = self.projet.nom or (self.chemin.stem if self.chemin else "Épure de giration")
        d = DonneesNote(titre, bs, actif, lieu="", coordonnees=coord, systeme=systeme, carte=self._image_carte(),
                        remarques=[a.message for a in self.resultat.alertes if a.niveau != "info"] if self.resultat else [],
                        hypotheses=self._hypotheses(),
                        sources=["Photographies aériennes, Plan IGN, BD TOPO (routes, bâtiments) © IGN — Géoplateforme, "
                                 "Licence Ouverte Etalab 2.0",
                                 "Véhicules : données des constructeurs et guide CERTU (bibliothèque de Giration)"],
                        version=__version__)
        entete = EnteteNote(auteur=str(self.reglages.value("note/auteur", "")),
                            organisme=str(self.reglages.value("note/organisme", "Giration — épures de giration")),
                            pied=str(self.reglages.value("note/pied", "")))
        nb = exporter_pdf(d, ch, entete)
        self.statusBar().showMessage(f"Note de giration enregistrée ({nb} page{'s' if nb > 1 else ''}).", 6000)
        self._apres_export(ch)

    # ================================================================== DXF Lambert-93 ou CC
    def exporter_dxf_systeme(self) -> None:
        from ..exports.dxf import exporter_dxf as ecrire_dxf
        from ..exports.dxf_contraintes import ajouter_au_fichier
        from ..exports.reprojection import reprojeter_fichier, suffixe, systemes_proposes
        source = self.projet.systeme or ""
        if source not in ("L93",) and not source.startswith("CC"):
            self.bandeau.afficher("Hors métropole, le DXF reste dans le plan local du projet.", duree_ms=5000)
            self.exporter_dxf()
            return
        pts = self.variante.points
        lat = self.geo.vers_geo(*pts[0])[0] if pts else self.carte.centre_geo()[0]
        choix = systemes_proposes(lat)
        libelles = [l for _, l in choix]
        defaut = str(self.reglages.value("dxf/systeme", choix[1][0]))
        idx = next((i for i, (c, _) in enumerate(choix) if c == defaut), 1)
        lib, ok = QInputDialog.getItem(self, "Exporter en DXF", "Coordonnées du dessin :", libelles, idx, False)
        if not ok:
            return
        cible = choix[libelles.index(lib)][0]
        self.reglages.setValue("dxf/systeme", cible)
        res = self._resultats_export()
        if not res:
            return
        ch = self._fichier_export(f"Exporter en DXF ({cible})", "dxf", "Dessins DXF (*.dxf)")
        if not ch:
            return
        if cible == source:
            ecrire_dxf(res, ch)
            ajouter_au_fichier(ch, self.projet.contraintes)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                brut = str(Path(tmp) / "epure.dxf")
                ecrire_dxf(res, brut)
                ajouter_au_fichier(brut, self.projet.contraintes)
                reprojeter_fichier(brut, ch, cible, source=source)
        self.statusBar().showMessage(f"DXF enregistré en {cible} ({suffixe(cible).lstrip('-')}).", 6000)
        self._apres_export(ch)
