"""Couleurs du panneau, en thème clair et en thème sombre (réglage « Mode » de Windows).

Chaque élément stylé reçoit un couple texte / fond complet : on ne mélange jamais une couleur imposée
et une couleur du thème, ce qui rendait des boutons illisibles (texte blanc du thème sombre sur fond
clair imposé). Les contrastes sont vérifiés par les tests (WCAG 2.1).
"""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QPalette

THEMES = {
    "clair": {
        "cadre_fond": "#ffffff", "cadre_bord": "#d6d6d6",
        "titre": "#C24E08", "titre_survol": "#7F0541",
        "resume": "#4a4a4a", "resume_survol": "#7F0541",
        "lien": "#2F6EA5",
        "outil_texte": "#1e1e1e", "outil_fond": "#f2f2f2", "outil_bord": "#b9b9b9",
        "outil_survol_fond": "#fbe4d6", "outil_survol_bord": "#DD590A",
        "actif_texte": "#ffffff", "actif_fond": "#C24E08", "actif_bord": "#9E3F06",
        "effacer_texte": "#7F0541", "effacer_fond": "#f9eef2", "effacer_bord": "#d9a7bb",
        "effacer_survol_fond": "#f1d9e3",
        "inactif_texte": "#6e6e6e", "inactif_fond": "#ececec", "inactif_bord": "#d6d6d6",
    },
    "sombre": {
        "cadre_fond": "#2b2b2b", "cadre_bord": "#4a4a4a",
        "titre": "#F28C4E", "titre_survol": "#F7B489",
        "resume": "#d4d4d4", "resume_survol": "#F7B489",
        "lien": "#8EC0EA",
        "outil_texte": "#f2f2f2", "outil_fond": "#3a3a3a", "outil_bord": "#5e5e5e",
        "outil_survol_fond": "#4a3528", "outil_survol_bord": "#F28C4E",
        "actif_texte": "#ffffff", "actif_fond": "#B34806", "actif_bord": "#F28C4E",
        "effacer_texte": "#F6B6CC", "effacer_fond": "#3d2730", "effacer_bord": "#8a3a5a",
        "effacer_survol_fond": "#4d2f3b",
        "inactif_texte": "#9a9a9a", "inactif_fond": "#323232", "inactif_bord": "#444444",
    },
}

# Couples (texte, fond) à contrôler : (clé du texte, clé du fond, contraste minimal)
COUPLES = [
    ("titre", "cadre_fond", 4.5), ("titre_survol", "cadre_fond", 4.5), ("resume", "cadre_fond", 4.5),
    ("lien", "cadre_fond", 4.5), ("outil_texte", "outil_fond", 4.5), ("outil_texte", "outil_survol_fond", 4.5),
    ("actif_texte", "actif_fond", 4.5), ("effacer_texte", "effacer_fond", 4.5),
    ("effacer_texte", "effacer_survol_fond", 4.5), ("inactif_texte", "inactif_fond", 3.0),
]

GABARIT = """
QFrame#encart {{ border: 1px solid {cadre_bord}; border-radius: 6px; background: {cadre_fond}; }}
QPushButton#entete {{ color: {titre}; font-weight: 600; border: none; padding: 3px 0px; text-align: left;
                     background: transparent; }}
QPushButton#entete:checked, QPushButton#entete:pressed {{ background: transparent; }}
QPushButton#entete:hover {{ color: {titre_survol}; }}
QLabel#titre_outils {{ color: {titre}; font-weight: 600; padding: 3px 0px; background: transparent; }}
QLabel#resume {{ color: {resume}; padding-left: 16px; background: transparent; }}
QLabel#resume:hover {{ color: {resume_survol}; }}
QToolButton#lien {{ color: {lien}; border: none; padding: 0px 3px; background: transparent; }}
QToolButton#lien:hover {{ text-decoration: underline; }}
QPushButton#outil {{ text-align: left; padding: 6px 10px; font-weight: 600; color: {outil_texte};
                    background: {outil_fond}; border: 1px solid {outil_bord}; border-radius: 4px; }}
QPushButton#outil:hover {{ background: {outil_survol_fond}; border-color: {outil_survol_bord}; }}
QPushButton#outil:checked {{ color: {actif_texte}; background: {actif_fond}; border-color: {actif_bord}; }}
QPushButton#envoyer {{ text-align: center; padding: 6px 10px; font-weight: 600; color: {actif_texte};
                      background: {actif_fond}; border: 1px solid {actif_bord}; border-radius: 4px; }}
QPushButton#envoyer:hover {{ border-color: {outil_survol_bord}; }}
QPushButton#effacer {{ padding: 5px 10px; color: {effacer_texte}; background: {effacer_fond};
                      border: 1px solid {effacer_bord}; border-radius: 4px; }}
QPushButton#effacer:hover {{ background: {effacer_survol_fond}; }}
QPushButton#effacer:disabled {{ color: {inactif_texte}; background: {inactif_fond}; border-color: {inactif_bord}; }}
"""


def theme_sombre() -> bool:
    """Thème sombre si la palette de l'application (réglée par Windows) a un fond foncé."""
    return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def feuille_de_style(sombre: bool) -> str:
    return GABARIT.format(**THEMES["sombre" if sombre else "clair"])


def luminance(couleur: str) -> float:
    r, g, b = (int(couleur[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contraste(texte: str, fond: str) -> float:
    """Rapport de contraste WCAG 2.1 (de 1 à 21)."""
    a, b = sorted((luminance(texte), luminance(fond)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# Couleurs de texte sur le fond des tableaux et des listes (palette « Base » : blanc ou gris très foncé)
TEXTES = {
    "clair": {"fond": "#ffffff", "sature": "#7F0541", "faible": "#B34806", "correct": "#11705F",
              "surdim": "#2F6EA5", "erreur": "#7F0541", "alerte": "#9A4A05", "ok": "#11705F"},
    "sombre": {"fond": "#2b2b2b", "sature": "#FF8FB4", "faible": "#F28C4E", "correct": "#5FD3B5",
               "surdim": "#8EC0EA", "erreur": "#FF8FB4", "alerte": "#F7B489", "ok": "#5FD3B5"},
}


def couleur_texte(cle: str) -> str:
    """Couleur de texte lisible sur le fond des tableaux, selon le thème de Windows."""
    return TEXTES["sombre" if theme_sombre() else "clair"][cle]
