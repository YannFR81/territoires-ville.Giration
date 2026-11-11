# Lot 2 — branchement dans Giration 1.2.1

Écrit le 11/11/2026 sans les sources 1.2.1 (restées sur le PC de Yann), d'après des extraits de `fenetre.py`,
`carte.py`, `modele.py`, `calcul.py`, `geoservices.py` relus le 05/11. À vérifier ligne à ligne sur les vraies sources.

## Fichiers nouveaux (à copier tels quels)

| Fichier | Rôle | Tests |
|---|---|---|
| `giration/gui/contraintes.py` | bordures, franchissables, obstacles ; bâti BD TOPO ; contrôle roues / caisse | `tests/test_contraintes.py` (8) |
| `giration/gui/bilan.py` | verdict (mêmes règles que le panneau), bilan d'une variante, tableau comparatif | `tests/test_bilan.py` (7) |
| `giration/gui/note.py` | note de giration PDF (mise en page de la note Girabase) | `tests/test_note.py` (2) |
| `giration/gui/outils_lot2.py` | classe `OutilsLot2` greffée sur la fenêtre | essai hors écran à écrire après fusion |
| `giration/exports/reprojection.py` | DXF Lambert-93 → CC42…CC50 | `tests/test_reprojection.py` (9) |
| `giration/exports/dxf_contraintes.py` | bordures et obstacles dans le DXF (calques GIR_BORDURES…) | `tests/test_dxf_contraintes.py` (2) |
| `giration/carto/donnees/bdtopo_lombers_batiments.json` | 12 bâtiments autour du giratoire de Lombers (essais hors ligne) | — |

Ne pas reprendre : `giration/__init__.py`, `giration/projections.py`, `__init__.py` vides (bouchons, voir BOUCHONS.txt).

## Modifications des fichiers existants

### `gui/modele.py`
- `Projet` : champ `contraintes: list = field(default_factory=list)` (après `ignorer_chaussee`).
- `Projet.depuis_dict` : passer `list(d.get("contraintes") or [])` en dernier argument.

### `gui/carte.py`
- `set_mode` : ajouter `"bordure"` aux modes à curseur en croix.
- `mouseDoubleClickEvent` : `if self.mode in ("tracer", "anneau", "bordure")`.

### `gui/fenetre.py`
1. `class FenetreGiration(OutilsLot2, QMainWindow)` ; import `from .outils_lot2 import OutilsLot2`.
2. `__init__` : `self._init_lot2()` tout au début (avant `_barres_outils`).
3. `MODES` : ajouter `"bordure": "Bordure"`.
4. Raccourci : `(Qt.Key_B, lambda: self.choisir_mode("bordure"))`.
5. `_barres_outils` :
   - après la boucle des modes : `self._outil_bordure(tb2, groupe)` ;
   - menu Couches : `self.ck_bati = couche("Bâtiments (BD TOPO), contrôlés comme obstacles", "Bâti de la BD TOPO : un contact de la caisse donne une réserve (précision de quelques mètres)", True, lambda _: (self._controler_lot2(self.resultat), self._maj_resultats(), self._redessiner()))` ;
   - après « Nouvelle variante » : action « Comparer » → `self.comparer_variantes` ;
   - menu Exporter : remplacer « DXF Lambert-93 (AutoCAD, COVADIS)… » par « DXF (AutoCAD, COVADIS) : Lambert-93 ou CC… » → `self.exporter_dxf_systeme` ; ajouter « Note de giration (PDF)… » → `self.exporter_note`.
6. `_menus` (Fichier) : mêmes entrées DXF et Note PDF ; menu Édition : « Comparer les variantes… », « Même trajet, autre véhicule… ».
7. `choisir_mode` : ajouter `"bordure"` à la liste qui exige la photo ; `self._contrainte = None` si l'on quitte le mode.
8. `_clic_carte` : `elif mode == "bordure": self._clic_bordure(lat, lon)`.
9. `terminer_trace` : en tête, `if mode == "bordure": self._finir_contrainte(); return`.
10. `_retour_arriere` : en tête, `if self.carte.mode == "bordure" and self._retour_contrainte(): return`.
11. `_menu_carte` : en mode bordure sans donnée sous la souris → `self._finir_contrainte(); return` ; si `d[0] == "contrainte"` → `self._menu_contrainte(m, d[1])`.
12. `_curseur` : `elif mode == "bordure": self._curseur_bordure(lat, lon)`.
13. `charger_routes` (fin) : `self.charger_batiments((lat, lon))`.
14. Après chaque calcul complet (là où `self.controle = controler(...)`, dans `_calcul_fini` et dans `charger_routes.recu`) : `self._controler_lot2(self.resultat)`.
15. `_maj_resultats` : juste avant le choix des couleurs du verdict, `titre, detail = self._verdict_lot2(titre, detail)` ; dans les remarques, `items += self._remarques_lot2()` ; ligne Chaussée : ajouter « · N conflit(s) avec les bordures » si `self.conflits`.
16. `_redessiner` : `self._dessiner_lot2()` après `_dessiner_chaussee()`.
17. `_maj_assistant` : texte du mode bordure (« Cliquez la bordure point par point sur la photo… Entrée pour finir, clic sur le premier point pour fermer ; flèche du bouton : bordure franchissable ou obstacle »).
18. `aide()` : paragraphe « Bordures et obstacles (B) », « Comparer », « Note PDF », « DXF en CC ».

### `gui/app.py` — `_autotest`
Ajouter : contrôle d'une bordure franchie (`controler_contraintes`), note PDF (`exporter_pdf` avec un `Bilan`), DXF reprojeté en CC44 ; ligne de résultat « ok … note … cc44 … ».

## Version
**1.3.0** (choix de Yann, 11/11/2026) : nouvelles fonctions, Girabase reste en 1.2.1 jusqu'à sa prochaine version.
À changer : `giration/__init__.py`, `pyproject.toml`, `version_windows*.txt` (1,3,0,0), NOUVEAUTES.md, README,
LISEZMOI, motifs des zips (`Giration-1.3.0-Windows-…`).

## Compilation Windows (Wine 9)
numpy **2.1.3** : à partir de numpy 2.2, `import numpy` plante sous Wine 9 (fonction `ucrtbase.crealf` absente).
PySide6-Essentials 6.9.3, shapely 2.0.7, ezdxf 1.4.5, PyInstaller 6.22.0 ; roues téléchargées par
`pip download --platform win_amd64 --python-version 3.12 --only-binary=:all:` (+ pefile, pywin32-ctypes).
