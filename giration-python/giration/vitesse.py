"""Vitesse automatique : le logiciel conduit comme un chauffeur au lieu de demander une vitesse.

En mode « tracé imposé », la trajectoire ne dépend pas de la vitesse : le milieu de l'essieu avant suit
exactement le tracé. La vitesse dit seulement *comment* un chauffeur peut le faire :

1. en virage, il ne dépasse pas une accélération latérale de confort : v ≤ √(a_lat · R) ;
2. il doit avoir le temps de tourner le volant : |dα/dt| = v · |dα/ds| ≤ vitesse de braquage,
   donc v ≤ vitesse de braquage / |dα/ds| ; quand la place manque, il ralentit, jusqu'au pas d'homme
   et, à la limite, braque presque à l'arrêt (c'est ce que font les chauffeurs en manœuvre serrée) ;
3. il freine avant et réaccélère après, sans à-coups (accélération et décélération limitées) ;
4. il ne dépasse pas la vitesse d'approche.

Les valeurs par famille de véhicules sont des hypothèses de départ, à confronter au terrain.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from .cinematique import Simulation

KMH = 1 / 3.6
V_PAS = 5 * KMH              # pas d'homme
V_ARRET = 1 * KMH            # en dessous : braquage presque à l'arrêt
V_PLANCHER = 0.05            # m/s, pour le calcul des temps


@dataclass(frozen=True)
class Conduite:
    nom: str
    v_approche: float        # m/s
    a_lat: float             # m/s² (confort latéral)
    a_acc: float             # m/s²
    a_dec: float             # m/s² (valeur positive)
    facteur_braquage: float = 1.0   # multiplie la vitesse de braquage du véhicule

    def decrire(self) -> str:
        return (f"conduite {self.nom} : approche {self.v_approche / KMH:.0f} km/h, accélération latérale "
                f"{self.a_lat:.1f} m/s², accélération {self.a_acc:.1f} m/s², freinage {self.a_dec:.1f} m/s²")


_VL = Conduite("véhicule léger", 30 * KMH, 2.0, 1.5, 2.0)
_UL = Conduite("utilitaire léger", 30 * KMH, 1.8, 1.2, 1.8)
_PL = Conduite("poids lourd", 30 * KMH, 1.5, 0.8, 1.5)
_ART = Conduite("ensemble articulé", 30 * KMH, 1.5, 0.6, 1.5)
_BUS = Conduite("bus avec voyageurs debout", 30 * KMH, 1.2, 1.0, 1.3)
_CAR = Conduite("autocar", 30 * KMH, 1.5, 0.8, 1.5)
_AGR = Conduite("engin agricole", 25 * KMH, 1.2, 0.6, 1.2)
_CONVOI = Conduite("convoi exceptionnel", 20 * KMH, 1.0, 0.4, 1.0)
_CYCLE = Conduite("cycle", 15 * KMH, 1.5, 0.8, 1.5, facteur_braquage=4.0)

PAR_FAMILLE = {
    "Véhicules légers": _VL, "Utilitaires légers": _UL, "Camping-cars": _UL, "Poids lourds porteurs": _PL,
    "Ensembles articulés": _ART, "Trains routiers": _ART, "Bus urbains": _BUS, "Autocars": _CAR,
    "Services techniques": _PL, "Secours (SDIS)": _PL, "Agricole": _AGR, "Cycles": _CYCLE,
}

STYLES = {"prudent": (0.8, 0.8), "normal": (1.0, 1.0), "expérimenté": (1.2, 1.33)}


def conduite_pour(veh, style: str = "normal") -> Conduite:
    c = PAR_FAMILLE.get(veh.categorie)
    if c is None:                                     # véhicules du guide CERTU et bibliothèques .veh
        n = veh.nom.lower()
        if "convoi" in n:
            c = _CONVOI
        elif "bus" in n:
            c = _BUS
        elif "autocar" in n:
            c = _CAR
        elif any(m in n for m in ("citadine", "berline", "monospace", "4x4", "voiture")):
            c = _VL
        elif "utilitaire" in n or "camping" in n:
            c = _UL
        elif veh.nb_remorques:
            c = _ART
        else:
            c = _PL
    style = style if style in STYLES else "normal"
    fl, fb = STYLES[style]
    return replace(c, nom=f"{c.nom}, {style}", a_lat=c.a_lat * fl, facteur_braquage=c.facteur_braquage * fb)


@dataclass
class ProfilVitesse:
    v: np.ndarray                 # m/s à chaque échantillon
    t: np.ndarray                 # s
    v_lat: np.ndarray             # limites (m/s)
    v_braquage: np.ndarray
    conduite: Conduite
    vitesse_braquage: float       # rad/s effectivement retenue

    def zones(self, seuil: float, s: np.ndarray) -> list[tuple[float, float]]:
        """Intervalles d'abscisse où la vitesse est inférieure au seuil."""
        res, debut = [], None
        for i, ok in enumerate(self.v < seuil - 1e-9):
            if ok and debut is None:
                debut = s[i]
            elif not ok and debut is not None:
                res.append((debut, s[i]))
                debut = None
        if debut is not None:
            res.append((debut, s[-1]))
        return res


def profil_vitesse(sim: Simulation, conduite: Conduite, depart_arrete: bool = False,
                   v_approche: float | None = None) -> ProfilVitesse:
    veh = sim.vehicule
    s = sim.s
    vb = veh.vitesse_braquage * conduite.facteur_braquage
    va = conduite.v_approche if v_approche is None else v_approche
    kmax = np.maximum(np.abs(sim.k_av), np.abs(sim.k_ap))
    with np.errstate(divide="ignore"):
        v_lat = np.where(kmax > 1e-9, np.sqrt(conduite.a_lat / np.maximum(kmax, 1e-12)), np.inf)
        da = np.maximum(np.abs(sim.dalpha_av), np.abs(sim.dalpha_ap))
        v_br = np.where(da > 1e-12, vb / np.maximum(da, 1e-12), np.inf)
    v = np.minimum(np.minimum(v_lat, v_br), va)
    if depart_arrete:
        v[0] = 0.0
    else:
        v[0] = min(v[0], va)
    ds = np.diff(s)
    for i in range(1, len(v)):                       # accélération limitée
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * conduite.a_acc * ds[i - 1]))
    for i in range(len(v) - 2, -1, -1):              # freinage limité
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * conduite.a_dec * ds[i]))
    # temps de parcours : un pas ne dure jamais moins que le temps de tourner le volant
    vm = np.maximum((v[1:] + v[:-1]) / 2, V_PLANCHER)
    dt = np.maximum(ds / vm, np.abs(np.diff(sim.alpha)) / vb)
    t = np.concatenate([[0.0], np.cumsum(dt)])
    return ProfilVitesse(v, t, v_lat, v_br, conduite, vb)
