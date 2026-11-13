"""Aperçu image (PNG) d'une épure, pour les notes et les comptes rendus."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .calcul import Resultat
from .enveloppe import simplifiee
from .exports.dxf import positions_silhouettes

ORANGE, VERT, BLEU, GRIS, ROUGE = "#e8730c", "#2e8b3b", "#1d5fa8", "#555555", "#c62828"


def _remplir(ax, geom, **kw):
    for poly in getattr(geom, "geoms", [geom]):
        if poly.is_empty:
            continue
        x, y = poly.exterior.xy
        ax.fill(x, y, **kw)
        for a in poly.interiors:
            xi, yi = a.xy
            ax.fill(xi, yi, color="white")


def _contour(ax, geom, **kw):
    for poly in getattr(geom, "geoms", [geom]):
        if poly.is_empty:
            continue
        for a in [poly.exterior, *poly.interiors]:
            x, y = a.xy
            ax.plot(x, y, **kw)


def dessiner(ax, res: Resultat, silhouettes: bool = True, traces: bool = False, couleur: str = ORANGE):
    sim, veh = res.sim, res.vehicule
    car, roues = simplifiee(res.enveloppes.carrosserie), simplifiee(res.enveloppes.roues)
    _remplir(ax, car, color=couleur, alpha=0.22, lw=0)
    _contour(ax, car, color=couleur, lw=1.3)
    _contour(ax, roues, color=VERT, lw=1.0)
    if traces:
        for pts in res.enveloppes.traces_roues.values():
            ax.plot(pts[:, 0], pts[:, 1], color=GRIS, lw=0.4)
    if silhouettes:
        caisses = [sim.caisse(k) for k in range(len(veh.elements))]
        for i in positions_silhouettes(res):
            for c in caisses:
                q = np.vstack([c[i], c[i][:1]])
                ax.plot(q[:, 0], q[:, 1], color=GRIS, lw=0.6)
    ax.plot(sim.x, sim.y, color=BLEU, lw=1.2, ls="--")
    ax.plot(sim.x[0], sim.y[0], "o", color=BLEU, ms=4)
    for a in res.alertes:
        if a.x is not None and a.niveau != "info":
            ax.plot(a.x, a.y, "o", mfc="none", mec=ROUGE, ms=14, mew=1.5)


def enregistrer_apercu(resultats: Sequence[Resultat] | Resultat, chemin, titre: str = "", taille=(7.5, 5.5),
                       annotations=None, dpi: int = 160, cadre: Optional[tuple] = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if isinstance(resultats, Resultat):
        resultats = [resultats]
    fig, ax = plt.subplots(figsize=taille)
    couleurs = [ORANGE, "#7b3fb5", "#0f7c8c"]
    for j, r in enumerate(resultats):
        dessiner(ax, r, couleur=couleurs[j % len(couleurs)])
    if annotations:
        annotations(ax)
    ax.set_aspect("equal")
    if cadre:
        ax.set_xlim(cadre[0], cadre[2])
        ax.set_ylim(cadre[1], cadre[3])
    ax.grid(True, lw=0.3, alpha=0.5)
    ax.tick_params(labelsize=7)
    if titre:
        ax.set_title(titre, fontsize=10)
    fig.tight_layout()
    chemin = Path(chemin)
    fig.savefig(chemin, dpi=dpi)
    plt.close(fig)
    return chemin
