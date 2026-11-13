"""Contrôles de précision du moteur (commande « giration valider » et tests automatiques).

1. Exemples chiffrés du guide Giration 3.3 (CERTU, 2007) : rayons des VIRAGE et longueurs minimales.
2. Écart avec le prototype fidèle au code VB6 de Giration 3.3 (exemple 4.1).
3. Régime permanent : pour chaque véhicule de la bibliothèque, rayons calculés pas à pas comparés aux
   formules fermées du CERTU.
4. Convergence de l'intégration : résultat au pas par défaut comparé au pas de 1 cm.
5. Cohérence des deux modes de conduite (tracé imposé et volant imposé) sur un même cercle.
6. Rayons de braquage publiés par les constructeurs comparés au modèle.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from .cinematique import PAS_DEFAUT, Commande, decomposer_virage, rayon_depuis_alpha, simuler_trace, simuler_volant
from .trace import Trace
from .vehicule import Bibliotheque, Vehicule, rayons_permanents

# (véhicule, déviation °, longueur m, vitesse de braquage °/s, rayon R publié, tolérance)
CAS_GUIDE = [
    ("certu-grande-berline", -90, 12.0, -15, -4.299, 0.0015, "exemple 4.1"),
    ("certu-grande-berline", 120, 50.0, 10, 22.311, 0.0015, "tutoriel 3.2.1.1"),
    ("certu-grande-berline", 120, 25.0, 10, 9.62, 0.005, "tutoriel 3.2.1.1"),
    ("certu-grande-berline", 120, 16.0, 10, 3.925, 0.0015, "tutoriel 3.2.1.1"),
    ("certu-bus", 20, 8.0, 15, 15.228, 0.0015, "exemple 4.2"),
    ("certu-bus", -130, 23.8, -15, -5.058, 0.0015, "exemple 4.2"),
    ("certu-camion-porteur", -120, 40.0, -5, -13.0, 0.0015, "exemple 4.5"),
    ("certu-camion-porteur", 310, 95.0, 5, 13.592, 0.0015, "exemple 4.5"),
]
# position finale de l'exemple 4.1 calculée par le prototype fidèle au VB6 (Euler/trapèzes, pas de 1 cm)
PROTOTYPE_41 = (8.737690165850731, -14.537787589632613)


class _Journal:
    def __init__(self):
        self.lignes: list[str] = []
        self.ok = True

    def titre(self, t: str):
        self.lignes += ["", t, "-" * len(t)]

    def test(self, libelle: str, ecart: float, tol: float, unite: str = "m"):
        bon = abs(ecart) <= tol
        self.ok &= bon
        e = f"{ecart:+.6f}" if abs(ecart) >= 1e-5 or ecart == 0 else f"{ecart:+.1e}"
        self.lignes.append(f"  [{'OK' if bon else 'ÉCHEC'}] {libelle} : écart {e} {unite} (tolérance {tol:g})")
        return bon

    def info(self, t: str):
        self.lignes.append("  " + t)


def _rayon_numerique(sim, centre, pts: np.ndarray, dernier: int = 200) -> tuple[float, float]:
    d = np.hypot(pts[-dernier:, 0] - centre[0], pts[-dernier:, 1] - centre[1])
    return float(d.min()), float(d.max())


def regime_permanent(veh: Vehicule, distance: float | None = None, pas: float = PAS_DEFAUT) -> dict:
    """Cercle à 90 % du braquage maximal (réduit si besoin) parcouru longtemps : rayons numériques et théoriques.

    À 100 %, certains ensembles articulés sont réglés pour que la remorque tourne presque sur place : leur régime
    permanent n'est alors atteint qu'au bout de centaines de mètres ; 90 % donne un état d'équilibre bien défini."""
    a = 0.9 * (veh.alpha_max_permanent or veh.alpha_max)
    theo = rayons_permanents(veh, a)
    if any(r.R_essieu <= 0 for r in theo[1:]):
        return {}
    L = distance or max(250.0, 25 * veh.longueur_totale)
    sim = simuler_volant(veh, [Commande("DEBUT", rayon=rayon_depuis_alpha(veh, a)), Commande("CERCLE", L)], pas=pas)
    t = veh.tracteur
    Rt = t.E / math.tan(a)
    A0 = sim.essieu_arriere(0)
    b = sim.beta[-1]
    centre = (A0[-1, 0] - Rt * math.sin(b), A0[-1, 1] + Rt * math.cos(b))
    res = {}
    for k in range(len(veh.elements)):
        A = sim.essieu_arriere(k)
        res[f"R_essieu_{k}"] = (_rayon_numerique(sim, centre, A)[1], abs(theo[k].R_essieu))
        coins = sim.caisse(k)
        res[f"ext_murs_{k}"] = (_rayon_numerique(sim, centre, coins[:, 1])[1], theo[k].ext_murs)
    ess = sim.essieux()
    int_tr = min(_rayon_numerique(sim, centre, c + LE / 2 * n)[0] for _, c, n, LE, _ in ess[1:])
    res["int_trottoirs"] = (int_tr, min(abs(r.int_trottoirs) for r in theo))
    return res


def executer(biblio: Bibliotheque | None = None, rapide: bool = False) -> tuple[bool, str]:
    b = biblio or Bibliotheque()
    J = _Journal()
    J.lignes.append("Contrôles de précision du moteur de Giration")

    J.titre("1. Exemples du guide Giration 3.3 (VIRAGE à 5 km/h, rayon R au pneu intérieur arrière)")
    for vid, db, L, a, R_pub, tol, ref in CAS_GUIDE:
        v = b.get(vid)
        al, _, _ = decomposer_virage(v, db, L, a, 5.0)
        R = rayon_depuis_alpha(v, al)
        J.test(f"{ref}, {v.nom.split(' (')[0]}, VIRAGE {db:+}° sur {L} m à {a} °/s → R = {R:.4f} (guide {R_pub})",
               R - R_pub, tol)
    berline = b.get("certu-grande-berline")
    for a, lmin_pub in ((10, 15.723), (12, 14.995)):
        try:
            decomposer_virage(berline, 120, lmin_pub - 0.002, a, 5.0)
            J.test(f"longueur minimale d'un VIRAGE de 120° à {a} °/s", 1.0, 0.0)
        except ValueError as exc:
            lmin = float(str(exc).split("longueur minimale ")[1].split(" m")[0])
            J.test(f"longueur minimale d'un VIRAGE de 120° à {a} °/s = {lmin:.3f} m (guide {lmin_pub})", lmin - lmin_pub, 0.0015)

    J.titre("2. Comparaison avec le prototype fidèle au code VB6 (exemple 4.1, position finale)")
    sim = simuler_volant(berline, [Commande("LIGNE", 5), Commande("VIRAGE", 12, vitesse_braquage=-15, deviation=-90),
                                   Commande("LIGNE", 5)], v_kmh=5.0)
    # la berline de la bibliothèque a RVehMin = 3,672 (au lieu de 3,671) : sans effet sur cet exemple (braquage < max)
    d = math.hypot(sim.x[-1] - PROTOTYPE_41[0], sim.y[-1] - PROTOTYPE_41[1])
    J.test("écart de position du milieu de l'essieu avant après 22 m (le VB intègre par trapèzes)", d, 0.0002)
    J.test("cap final (−90° attendu)", math.degrees(sim.beta[-1]) + 90, 1e-6, "°")

    J.titre("3. Régime permanent à 90 % du braquage maximal : rayons pas à pas / formules du CERTU (toute la bibliothèque)")
    pire = (0.0, "")
    n = 0
    for v in b:
        if rapide and n >= 12:
            break
        r = regime_permanent(v)
        if not r:
            J.info(f"{v.id} : régime permanent inaccessible au braquage maximal (remorque « sur place »), ignoré")
            continue
        n += 1
        for cle, (num, theo) in r.items():
            e = num - theo
            if abs(e) > abs(pire[0]):
                pire = (e, f"{v.id} {cle}")
            if abs(e) > 0.001:
                J.test(f"{v.id} {cle} : {num:.4f} / {theo:.4f}", e, 0.001)
    J.test(f"{n} véhicules, plus grand écart ({pire[1]})", pire[0], 0.001)

    J.titre("4. Convergence de l'intégration (semi-remorque CERTU, virage à 90° de 12 m, pas de référence 1 cm)")
    semi = b.get("certu-camion-semi-remorque")
    tr = Trace.depuis_morceaux(0, 0, 0, [(30, None), ("angle", -12, -90), (30, None)])
    ref = simuler_trace(semi, tr, pas=0.01)
    c_ref = ref.caisse(1)[-1]
    for pas in (0.20, 0.10, PAS_DEFAUT):
        s = simuler_trace(semi, tr, pas=pas)
        e = float(np.max(np.hypot(*(s.caisse(1)[-1] - c_ref).T)))
        J.test(f"pas de {pas * 100:.0f} cm : coins arrière de la semi en fin de virage", e, 0.0001 if pas == PAS_DEFAUT else 0.01)

    J.titre("5. Cohérence des deux modes de conduite (cercle parcouru par tracé imposé et par volant imposé)")
    for v in (semi, b.get("train-routier-remorque-timon-18m75"), b.get("certu-bus-bi-articul")):
        a = 0.8 * (v.alpha_max_permanent or v.alpha_max)
        Rp = v.tracteur.E / math.sin(a)
        sv = simuler_volant(v, [Commande("DEBUT", rayon=rayon_depuis_alpha(v, a)), Commande("CERCLE", 60)])
        st = simuler_trace(v, Trace.depuis_morceaux(0, 0, math.degrees(a), [(60, Rp)]),
                           caps_initiaux=[0.0] * len(v.elements))
        e = float(np.max(np.hypot(*(sv.caisse(len(v.elements) - 1)[-1] - st.caisse(len(v.elements) - 1)[-1]).T)))
        J.test(f"{v.id} : dernier élément après 60 m", e, 0.0001)

    J.titre("6. Rayon de braquage « entre murs » publié par les constructeurs / modèle (bibliothèque)")
    ecarts = []
    for vid, brut in b.brut.items():
        c = (brut.get("giration") or {}).get("comparaison_constructeur") or {}
        if c.get("ecart_m") is not None:
            ecarts.append((c["ecart_m"], vid))
    if ecarts:
        ecarts.sort()
        moy = sum(e[0] for e in ecarts) / len(ecarts)
        J.info(f"{len(ecarts)} véhicules : le modèle dépasse le rayon publié de {moy:.2f} m en moyenne "
               f"(de {ecarts[0][0]:.2f} à {ecarts[-1][0]:.2f} m, jamais en dessous).")
        J.info("Le braquage est réglé sur le rayon « entre trottoirs » publié ; l'écart restant vient de la caisse")
        J.info("rectangulaire du modèle, alors que les caisses réelles ont des angles arrondis : l'épure est")
        J.info("légèrement du côté de la sécurité.")
    J.lignes += ["", "RÉSULTAT : " + ("tous les contrôles sont satisfaits." if J.ok else "AU MOINS UN CONTRÔLE ÉCHOUE.")]
    return J.ok, "\n".join(J.lignes)
