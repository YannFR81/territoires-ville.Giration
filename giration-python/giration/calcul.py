"""Calcul complet d'une épure : simulation, vitesse automatique, enveloppes, contrôles, et compte rendu."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .cinematique import PAS_DEFAUT, Simulation, simuler_trace
from .enveloppe import Enveloppes, calculer_enveloppes
from .trace import Trace
from .vehicule import Vehicule
from .vitesse import KMH, V_ARRET, V_PAS, Conduite, ProfilVitesse, conduite_pour, profil_vitesse

ARTICULATION_ATTENTION = math.radians(80.0)


@dataclass
class Alerte:
    niveau: str              # « erreur », « attention », « info »
    message: str
    s: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None


@dataclass
class Resultat:
    vehicule: Vehicule
    trace: Optional[Trace]
    sim: Simulation
    vitesse: ProfilVitesse
    enveloppes: Enveloppes
    alertes: list[Alerte] = field(default_factory=list)
    echelle: float = 1.0                    # facteur d'échelle de la projection appliqué au véhicule
    systeme: Optional[str] = None           # « L93 », « CC44 », None = plan local
    rapide: bool = False                    # aperçu (sans mesure de largeur)

    # ------------------------------------------------------------------ synthèse
    @property
    def faisable(self) -> bool:
        return not any(a.niveau == "erreur" for a in self.alertes)

    def verdict(self) -> str:
        if not self.faisable:
            return "IMPOSSIBLE"
        if any(a.niveau == "attention" for a in self.alertes):
            return "POSSIBLE AVEC RÉSERVES"
        return "POSSIBLE"

    def mesures(self) -> dict:
        sim, veh = self.sim, self.vehicule
        i_a = int(np.argmax(np.abs(sim.alpha)))
        kmax = np.maximum(np.abs(sim.k_av), np.abs(sim.k_ap))
        art = np.abs(sim.articulations())
        v = self.vitesse.v
        i_v = int(np.argmin(v))
        m = {
            "longueur_trace": float(sim.s[-1]),
            "braquage_max_deg": float(math.degrees(abs(sim.alpha[i_a]))),
            "braquage_max_pourcent": float(100 * abs(sim.alpha[i_a]) / veh.alpha_max),
            "braquage_max_abscisse": float(sim.s[i_a]),
            "rayon_trace_min": float(1 / kmax.max()) if kmax.max() > 1e-9 else math.inf,
            "articulation_max_deg": float(math.degrees(art.max())) if art.size else 0.0,
            "vitesse_min_kmh": float(v[i_v] / KMH),
            "vitesse_min_abscisse": float(sim.s[i_v]),
            "vitesse_entree_kmh": float(v[0] / KMH),
            "vitesse_sortie_kmh": float(v[-1] / KMH),
            "duree_s": float(self.vitesse.t[-1]),
            "surface_carrosserie_m2": float(self.enveloppes.carrosserie.area),
            "surface_roues_m2": float(self.enveloppes.roues.area),
        }
        for nom in ("carrosserie", "roues"):
            d = self.enveloppes.largeur.get(nom) or {"largeur_max": math.nan, "a_gauche": math.nan,
                                                    "a_droite": math.nan, "abscisse": math.nan}
            m[f"largeur_{nom}_max"] = float(d["largeur_max"])
            m[f"largeur_{nom}_gauche"] = float(d["a_gauche"])
            m[f"largeur_{nom}_droite"] = float(d["a_droite"])
            m[f"largeur_{nom}_abscisse"] = float(d["abscisse"])
        return m

    def rapport(self) -> str:
        veh, m = self.vehicule, self.mesures()
        L = []
        L.append(f"Véhicule : {veh.nom}")
        L.append(f"  longueur {veh.longueur_totale:.2f} m, largeur {veh.largeur:.2f} m, braquage maximal "
                 f"{math.degrees(veh.alpha_max):.1f}°, vitesse de braquage {math.degrees(veh.vitesse_braquage):.0f} °/s")
        if self.echelle != 1.0:
            L.append(f"  dimensions corrigées de l'altération linéaire du {self.systeme} : × {self.echelle:.6f}")
        if self.trace is not None:
            L.append(f"Tracé : {m['longueur_trace']:.2f} m, rayon minimal {m['rayon_trace_min']:.2f} m "
                     "(milieu de l'essieu avant)")
        L.append(f"Résultat : {self.verdict()}")
        L.append(f"  braquage maximal atteint {m['braquage_max_deg']:.1f}° ({m['braquage_max_pourcent']:.0f} % du maximum) "
                 f"à {m['braquage_max_abscisse']:.1f} m")
        if veh.nb_remorques:
            L.append(f"  articulation maximale {m['articulation_max_deg']:.1f}°")
        L.append(f"  largeur balayée maximale : carrosserie {m['largeur_carrosserie_max']:.2f} m "
                 f"(dont {m['largeur_carrosserie_gauche']:.2f} à gauche et {m['largeur_carrosserie_droite']:.2f} à droite "
                 f"de l'axe, à {m['largeur_carrosserie_abscisse']:.1f} m), roues {m['largeur_roues_max']:.2f} m")
        L.append(f"  vitesse automatique ({self.vitesse.conduite.nom}) : entrée {m['vitesse_entree_kmh']:.0f} km/h, "
                 f"minimum {m['vitesse_min_kmh']:.1f} km/h à {m['vitesse_min_abscisse']:.1f} m, "
                 f"sortie {m['vitesse_sortie_kmh']:.0f} km/h ; durée {m['duree_s']:.1f} s")
        for a in self.alertes:
            ou = f" (à {a.s:.1f} m)" if a.s is not None else ""
            L.append(f"  [{a.niveau}] {a.message}{ou}")
        return "\n".join(L)


def controler(res: Resultat) -> list[Alerte]:
    sim, veh = res.sim, res.vehicule
    al: list[Alerte] = []
    # braquage
    trop = np.abs(sim.alpha) > veh.alpha_max + math.radians(0.01)
    for d, f in _intervalles(trop):
        i = d + int(np.argmax(np.abs(sim.alpha[d:f + 1])))
        kmax = max(abs(sim.k_av[i]), abs(sim.k_ap[i]))
        rayon = f"{1 / kmax:.2f} m" if kmax > 1e-9 else "—"
        al.append(Alerte("erreur", f"Braquage impossible : {math.degrees(abs(sim.alpha[i])):.1f}° demandés pour "
                                   f"{math.degrees(veh.alpha_max):.1f}° possibles (rayon du tracé {rayon}, minimum "
                                   f"{veh.rayon_P_min():.2f} m pour le milieu de l'essieu avant) de {sim.s[d]:.1f} à "
                                   f"{sim.s[f]:.1f} m.", sim.s[i], sim.x[i], sim.y[i]))
    # braquage réduit du CERTU (régime permanent) dépassé : possible en passage, pas en virage prolongé
    if veh.alpha_max_permanent and not trop.any():
        au_dela = np.abs(sim.alpha) > veh.alpha_max_permanent + math.radians(0.01)
        if au_dela.any():
            d, f = _intervalles(au_dela)[0]
            al.append(Alerte("info", f"Braquage supérieur au braquage « permanent » du CERTU "
                                     f"({math.degrees(veh.alpha_max_permanent):.1f}°) de {sim.s[d]:.1f} à {sim.s[f]:.1f} m : "
                                     "possible en passage, mais la remorque se mettrait en portefeuille si on le "
                                     "maintenait (voir l'articulation).", sim.s[d], sim.x[d], sim.y[d]))
    # articulation
    if veh.nb_remorques:
        art = np.abs(sim.articulations()).max(axis=0)
        for niveau, seuil, texte in (("erreur", veh.articulation_max, "mise en portefeuille"),
                                     ("attention", ARTICULATION_ATTENTION, "vérifier le dégagement entre éléments")):
            masque = art > seuil
            if masque.any():
                d, f = _intervalles(masque)[0]
                i = d + int(np.argmax(art[d:f + 1]))
                al.append(Alerte(niveau, f"Articulation de {math.degrees(art[i]):.0f}° : {texte}.", sim.s[i], sim.x[i], sim.y[i]))
                break
    # vitesse
    v, s = res.vitesse.v, sim.s
    for d, f in res.vitesse.zones(V_ARRET, s):
        i = sim.indice((d + f) / 2)
        al.append(Alerte("attention", f"Braquage presque à l'arrêt de {d:.1f} à {f:.1f} m : le chauffeur doit s'arrêter "
                                      "pour tourner le volant.", d, sim.x[i], sim.y[i]))
    for d, f in res.vitesse.zones(V_PAS, s):
        if not any(a.s is not None and d <= a.s <= f and "arrêt" in a.message for a in al):
            i = sim.indice((d + f) / 2)
            al.append(Alerte("info", f"Passage au pas (moins de 5 km/h) de {d:.1f} à {f:.1f} m.", d, sim.x[i], sim.y[i]))
    # tracé
    if res.trace is not None:
        t0 = res.trace.troncons[0]
        if t0.est_arc or t0.longueur < veh.longueur_totale:
            al.append(Alerte("info", f"Le tracé commence par {'un arc' if t0.est_arc else f'une droite de {t0.longueur:.1f} m'} : "
                                     f"le véhicule est supposé aligné au départ. Une droite d'approche d'au moins "
                                     f"{veh.longueur_totale:.1f} m rend le calcul plus réaliste.", 0.0))
        for r in res.trace.remarques:
            al.append(Alerte("info", r))
    return al


def _intervalles(masque: np.ndarray) -> list[tuple[int, int]]:
    res, d = [], None
    for i, m in enumerate(masque):
        if m and d is None:
            d = i
        elif not m and d is not None:
            res.append((d, i - 1))
            d = None
    if d is not None:
        res.append((d, len(masque) - 1))
    return res


def calculer(veh: Vehicule, trace: Trace, style: str = "normal", pas: float = PAS_DEFAUT, echelle: float = 1.0,
             systeme: Optional[str] = None, conduite: Optional[Conduite] = None, depart_arrete: bool = False,
             v_approche_kmh: Optional[float] = None, rapide: bool = False, pas_mesure: float = 0.5) -> Resultat:
    """Épure complète d'un véhicule suivant un tracé (milieu de l'essieu avant).

    ``rapide`` : aperçu pendant un déplacement à la souris (pas de 10 cm, enveloppe au pas de 30 cm, sans mesure
    de largeur), une dizaine de fois plus rapide ; le calcul complet suit au relâchement."""
    v = veh.mis_a_l_echelle(echelle)
    sim = simuler_trace(v, trace, pas=0.10 if rapide else pas)
    c = conduite or conduite_pour(veh, style)
    pv = profil_vitesse(sim, c, depart_arrete=depart_arrete,
                        v_approche=None if v_approche_kmh is None else v_approche_kmh * KMH)
    sim.vitesse, sim.temps = pv.v, pv.t
    if rapide:
        env = calculer_enveloppes(sim, pas=0.30, mesures=False)
    else:
        env = calculer_enveloppes(sim, mesures=False)
        from .enveloppe import mesurer_largeurs
        env.largeur = mesurer_largeurs(sim, env, pas=pas_mesure)
    res = Resultat(v, trace, sim, pv, env, echelle=echelle, systeme=systeme)
    res.rapide = rapide
    res.alertes = controler(res)
    return res
