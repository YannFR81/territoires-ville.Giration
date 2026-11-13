"""Moteur cinématique : modèle « bicyclette » sans glissement du CERTU, tracteur + 0 à 2 remorques.

Deux façons de conduire le véhicule, avec les mêmes équations :

- **tracé imposé** (``simuler_trace``) : le milieu de l'essieu avant suit exactement un tracé fait de droites
  et d'arcs ; on en déduit à chaque instant le braquage α, puis la position des remorques ;
- **volant imposé** (``simuler_volant``, grille du CERTU) : on donne le braquage α le long du parcours
  (LIGNE, CERCLE, RACCORD, VIRAGE) et le tracé en découle.

Pour une longueur parcourue ds par le milieu P de l'essieu avant (vitesses « par mètre ») :

    tracteur   : dβ/ds = sin α / E0              (α = direction de P − cap β du tracteur)
                 vitesse de l'essieu arrière A0 : cos α · u0
    attelage k : vH = vA(k−1) − D(k−1) · ω(k−1) · n(k−1)
    remorque k : ωk = (vH · nk) / Ek ,  vAk = (vH · uk) · uk

u = (cos θ, sin θ) est l'axe d'un élément, n = (−sin θ, cos θ) sa normale à gauche. Ces équations sont
celles de TRAJECT.FRM (Giration 3.3) écrites sous forme vectorielle ; elles sont intégrées par Runge-Kutta
d'ordre 4, avec des pas alignés sur les changements de tronçon, indépendamment de tout affichage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .trace import Trace, norm_angle
from .vehicule import Vehicule

PAS_DEFAUT = 0.05             # m : erreur d'intégration inférieure au dixième de millimètre (voir validation)


def _derivees(veh: Vehicule, beta: float, thetas: Sequence[float], alpha: float) -> list[float]:
    """[dβ/ds, dθ1/ds, dθ2/ds] pour un braquage α donné."""
    els = veh.elements
    e0 = els[0].E
    w = math.sin(alpha) / e0
    res = [w]
    cb, sb = math.cos(beta), math.sin(beta)
    ca = math.cos(alpha)
    vax, vay = ca * cb, ca * sb                       # vitesse de l'essieu arrière du tracteur
    th_prec, w_prec = beta, w
    for k in range(1, len(els)):
        d = els[k - 1].D
        nx, ny = -math.sin(th_prec), math.cos(th_prec)
        vhx, vhy = vax - d * w_prec * nx, vay - d * w_prec * ny
        th = thetas[k - 1]
        ux, uy = math.cos(th), math.sin(th)
        wk = (-vhx * uy + vhy * ux) / els[k].E          # (vH · nk) / Ek
        p = vhx * ux + vhy * uy
        vax, vay = p * ux, p * uy
        res.append(wk)
        th_prec, w_prec = th, wk
    return res


@dataclass
class Simulation:
    """Résultat d'un calcul : états échantillonnés le long de l'abscisse s du milieu de l'essieu avant."""
    vehicule: Vehicule
    mode: str                       # « tracé » ou « volant »
    s: np.ndarray
    x: np.ndarray                   # milieu de l'essieu avant
    y: np.ndarray
    thP: np.ndarray                 # direction de déplacement de P
    beta: np.ndarray                # cap du tracteur
    alpha: np.ndarray               # braquage équivalent
    theta: np.ndarray               # caps de tous les éléments, forme (n_elements, N) ; theta[0] = beta
    k_av: np.ndarray                # courbure du tracé de P avant / après l'échantillon (saut aux raccords)
    k_ap: np.ndarray
    dalpha_av: np.ndarray           # dα/ds avant / après l'échantillon (rad/m)
    dalpha_ap: np.ndarray
    trace: Optional[Trace] = None
    pas: float = PAS_DEFAUT
    vitesse: Optional[np.ndarray] = None     # m/s, rempli par vitesse.profil_vitesse
    temps: Optional[np.ndarray] = None       # s
    infos: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.s)

    # ---------------------------------------------------------- géométrie des éléments
    def axes(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        th = self.theta[k]
        return np.stack([np.cos(th), np.sin(th)], 1), np.stack([-np.sin(th), np.cos(th)], 1)

    def essieu_arriere(self, k: int) -> np.ndarray:
        """Milieu de l'essieu arrière (équivalent) de l'élément k, forme (N, 2)."""
        els = self.vehicule.elements
        u, _ = self.axes(0)
        A = np.stack([self.x, self.y], 1) - els[0].E * u
        for j in range(1, k + 1):
            up, _ = self.axes(j - 1)
            H = A - els[j - 1].D * up
            uj, _ = self.axes(j)
            A = H - els[j].E * uj
        return A

    def attelage(self, k: int) -> np.ndarray:
        """Point d'attelage de l'élément k ≥ 1 (pivot de sellette ou anneau)."""
        els = self.vehicule.elements
        up, _ = self.axes(k - 1)
        return self.essieu_arriere(k - 1) - els[k - 1].D * up

    def caisse(self, k: int) -> np.ndarray:
        """Coins de la caisse de l'élément k, forme (N, 4, 2) : avant gauche, avant droit, arrière droit, arrière gauche."""
        el = self.vehicule.elements[k]
        u, nrm = self.axes(k)
        if k == 0:
            av = np.stack([self.x, self.y], 1) + el.PAFav * u
            ar = np.stack([self.x, self.y], 1) - (el.E + el.PAFar) * u
        else:
            av = self.attelage(k) + el.PAFav * u
            ar = self.essieu_arriere(k) - el.PAFar * u
        h = el.L / 2 * nrm
        return np.stack([av + h, av - h, ar - h, ar + h], 1)

    def essieux(self) -> list[tuple[str, np.ndarray, np.ndarray, float, bool]]:
        """Essieux équivalents : (nom, centre (N,2), axe transversal unitaire (N,2), largeur pneus compris, directeur)."""
        res = []
        els = self.vehicule.elements
        u0, n0 = self.axes(0)
        P = np.stack([self.x, self.y], 1)
        res.append(("tracteur avant", P, n0, els[0].LEav or els[0].LEar, True))
        for k in range(len(els)):
            _, nk = self.axes(k)
            nom = "tracteur arrière" if k == 0 else f"remorque {k}"
            res.append((nom, self.essieu_arriere(k), nk, els[k].LEar, False))
        return res

    def articulations(self) -> np.ndarray:
        """Angles entre éléments successifs (rad), forme (n_remorques, N)."""
        if self.theta.shape[0] < 2:
            return np.zeros((0, self.n))
        d = self.theta[1:] - self.theta[:-1]
        return (d + np.pi) % (2 * np.pi) - np.pi

    def indice(self, s: float) -> int:
        return int(np.clip(np.searchsorted(self.s, s), 0, self.n - 1))


# ---------------------------------------------------------------------- tracé imposé
def simuler_trace(veh: Vehicule, trace: Trace, pas: float = PAS_DEFAUT, caps_initiaux: Optional[Sequence[float]] = None) -> Simulation:
    """Le milieu de l'essieu avant suit exactement ``trace``. Par défaut le véhicule part aligné sur la direction
    de départ du tracé (convention de Giration : remorques alignées)."""
    x0, y0, th0 = trace.depart
    n_rem = veh.nb_remorques
    beta = th0 if caps_initiaux is None else caps_initiaux[0]
    thetas = [th0] * n_rem if caps_initiaux is None else list(caps_initiaux[1:])
    e0 = veh.tracteur.E

    S, X, Y, TH, B, TT, KAV, KAP = [], [], [], [], [], [], [], []
    s_cum = 0.0
    S.append(0.0); X.append(x0); Y.append(y0); TH.append(th0); B.append(beta); TT.append(list(thetas))
    KAV.append(trace.troncons[0].k); KAP.append(trace.troncons[0].k)

    for it, tr in enumerate(trace.troncons):
        n = max(1, math.ceil(tr.longueur / pas - 1e-9))
        h = tr.longueur / n
        KAP[-1] = tr.k                                 # courbure en sortie de l'échantillon de jonction
        for i in range(n):
            sl = i * h
            th_a = tr.th0 + tr.k * sl
            th_m = th_a + tr.k * h / 2
            th_b = th_a + tr.k * h

            def f(b, th, thP):
                return _derivees(veh, b, th, norm_angle(thP - b))

            k1 = f(beta, thetas, th_a)
            k2 = f(beta + h / 2 * k1[0], [t + h / 2 * d for t, d in zip(thetas, k1[1:])], th_m)
            k3 = f(beta + h / 2 * k2[0], [t + h / 2 * d for t, d in zip(thetas, k2[1:])], th_m)
            k4 = f(beta + h * k3[0], [t + h * d for t, d in zip(thetas, k3[1:])], th_b)
            beta += h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            thetas = [t + h / 6 * (a + 2 * b + 2 * c + d) for t, a, b, c, d in zip(thetas, k1[1:], k2[1:], k3[1:], k4[1:])]
            s_cum += h
            px, py, pth = tr.point(sl + h)
            S.append(s_cum); X.append(px); Y.append(py); TH.append(pth); B.append(beta); TT.append(list(thetas))
            KAV.append(tr.k); KAP.append(tr.k)

    s = np.array(S)
    beta_a = np.array(B)
    thP = np.array(TH)
    alpha = (thP - beta_a + np.pi) % (2 * np.pi) - np.pi
    theta = np.vstack([beta_a] + ([np.array(TT)[:, j] for j in range(n_rem)] if n_rem else []))
    k_av, k_ap = np.array(KAV), np.array(KAP)
    w = np.sin(alpha) / e0
    return Simulation(veh, "tracé", s, np.array(X), np.array(Y), thP, beta_a, alpha, theta, k_av, k_ap,
                      k_av - w, k_ap - w, trace=trace, pas=pas)


# ---------------------------------------------------------------------- volant imposé (grille CERTU)
@dataclass
class Commande:
    """Ligne de la grille de Giration.

    - ``DEBUT`` : braquage initial (``rayon`` CERTU au pneu intérieur arrière, signé ; None ou 0 = roues droites) ;
    - ``LIGNE`` : roues droites sur ``longueur`` ;
    - ``CERCLE`` : braquage constant sur ``longueur`` ;
    - ``RACCORD`` : volant tourné à ``vitesse_braquage`` (°/s, signée) sur ``longueur`` ;
    - ``VIRAGE`` : raccord + cercle + raccord symétriques tournant de ``deviation`` (°) sur ``longueur``.
    """
    type: str
    longueur: float = 0.0
    rayon: Optional[float] = None
    vitesse_braquage: Optional[float] = None       # °/s, signée
    deviation: Optional[float] = None              # ° (VIRAGE)


def alpha_depuis_rayon(veh: Vehicule, R: Optional[float]) -> float:
    """Braquage correspondant au rayon CERTU R (pneu intérieur de l'essieu arrière du tracteur, signé)."""
    if not R or abs(R) >= 10000:
        return 0.0
    return math.atan(veh.tracteur.E / (R + math.copysign(veh.tracteur.LEar / 2, R)))


def rayon_depuis_alpha(veh: Vehicule, alpha: float) -> float:
    if abs(alpha) < 1e-12:
        return math.inf
    return veh.tracteur.E / math.tan(alpha) - math.copysign(veh.tracteur.LEar / 2, alpha)


def decomposer_virage(veh: Vehicule, deviation_deg: float, longueur: float, vitesse_braquage_deg_s: float,
                      v_kmh: float) -> tuple[float, float, float]:
    """VIRAGE de Giration (CalculElemVirage) : retourne (α du cercle, longueur de chaque raccord, longueur du cercle).

    Lève ValueError si le virage est impossible (le message donne la longueur minimale)."""
    v = v_kmh / 3.6
    a = math.radians(abs(vitesse_braquage_deg_s))
    e0 = veh.tracteur.E
    db = math.radians(deviation_deg)
    sg = 1.0 if db >= 0 else -1.0
    l = abs(longueur)

    def somme(al):
        L1 = v * al / a
        L2 = l - 2 * L1
        return 2 * v / (e0 * a) * (1 - math.cos(al)) + L2 / e0 * math.sin(al), L1, L2

    amax = min(a / 2 * l / v, veh.alpha_max)
    tot = somme(amax)[0]
    if tot < abs(db) - 1e-12:
        k = 2 * v / a
        def lg(al):
            return (e0 * abs(db) - k * (1 - math.cos(al))) / math.sin(al) + k * al
        lmin = lg(veh.alpha_max) if e0 * abs(db) - k * (1 - math.cos(veh.alpha_max)) > 0 else lg(math.acos(1 - e0 * abs(db) / k))
        raise ValueError(f"Virage de {deviation_deg:.1f}° impossible sur {l:.2f} m : longueur minimale {lmin:.3f} m "
                         f"(ou augmentez la vitesse de braquage, ou réduisez la vitesse).")
    lo, hi = 0.0, amax
    al = amax
    for _ in range(200):
        if abs(tot - abs(db)) <= 1e-12:
            break
        if abs(db) < tot:
            hi = al
        else:
            lo = al
        al = (lo + hi) / 2
        tot = somme(al)[0]
    _, L1, L2 = somme(al)
    return sg * al, L1, L2


def simuler_volant(veh: Vehicule, commandes: Sequence[Commande], x0: float = 0.0, y0: float = 0.0, cap0_deg: float = 0.0,
                   v_kmh: float = 5.0, pas: float = PAS_DEFAUT) -> Simulation:
    """Grille de Giration : le braquage est imposé, la trajectoire en découle. Vitesse constante ``v_kmh``
    (elle ne sert qu'à convertir les vitesses de braquage en longueurs de raccord)."""
    v = v_kmh / 3.6
    e0 = veh.tracteur.E
    n_rem = veh.nb_remorques
    # 1) braquage par morceaux : liste de (longueur, alpha_debut, dalpha_ds)
    morceaux: list[tuple[float, float, float]] = []
    alpha = 0.0
    for c in commandes:
        t = c.type.upper()
        if t == "DEBUT":
            alpha = alpha_depuis_rayon(veh, c.rayon)
        elif t == "LIGNE":
            if abs(alpha) > 1e-9:
                raise ValueError("LIGNE demande des roues droites : insérez un RACCORD avant.")
            morceaux.append((c.longueur, 0.0, 0.0))
        elif t == "CERCLE":
            morceaux.append((c.longueur, alpha, 0.0))
        elif t == "RACCORD":
            da = math.radians(c.vitesse_braquage) / v
            morceaux.append((c.longueur, alpha, da))
            alpha += da * c.longueur
        elif t == "VIRAGE":
            al, L1, L2 = decomposer_virage(veh, c.deviation, c.longueur, c.vitesse_braquage, v_kmh)
            a = math.copysign(math.radians(abs(c.vitesse_braquage)) / v, al)
            morceaux += [(L1, alpha, a), (L2, alpha + al, 0.0), (L1, alpha + al, -a)]
        else:
            raise ValueError(f"Commande inconnue : {c.type}")
        if abs(alpha) > veh.alpha_max + 1e-9:
            raise ValueError(f"Braquage {math.degrees(alpha):.2f}° supérieur au maximum {math.degrees(veh.alpha_max):.2f}°.")

    # 2) intégration de [x, y, β, θ1, θ2]
    beta = math.radians(cap0_deg)
    thetas = [beta] * n_rem
    px, py = x0, y0
    a0 = morceaux[0][1] if morceaux else 0.0
    S, X, Y, B, A, TT, DAV, DAP = [0.0], [px], [py], [beta], [a0], [list(thetas)], [0.0], [0.0]
    s_cum = 0.0
    for L, a_deb, da in morceaux:
        if L <= 1e-12:
            continue
        n = max(1, math.ceil(L / pas - 1e-9))
        h = L / n
        DAP[-1] = da
        for i in range(n):
            al_a = a_deb + da * i * h

            def f(state, al):
                b = state[2]
                d = _derivees(veh, b, state[3:], al)
                return [math.cos(b + al), math.sin(b + al)] + d

            y = [px, py, beta] + thetas
            k1 = f(y, al_a)
            k2 = f([u + h / 2 * d for u, d in zip(y, k1)], al_a + da * h / 2)
            k3 = f([u + h / 2 * d for u, d in zip(y, k2)], al_a + da * h / 2)
            k4 = f([u + h * d for u, d in zip(y, k3)], al_a + da * h)
            y = [u + h / 6 * (p + 2 * q + 2 * r + w) for u, p, q, r, w in zip(y, k1, k2, k3, k4)]
            px, py, beta, thetas = y[0], y[1], y[2], y[3:]
            s_cum += h
            S.append(s_cum); X.append(px); Y.append(py); B.append(beta); A.append(al_a + da * h)
            TT.append(list(thetas)); DAV.append(da); DAP.append(da)
    s = np.array(S)
    beta_a = np.array(B)
    alpha_a = np.array(A)
    theta = np.vstack([beta_a] + ([np.array(TT)[:, j] for j in range(n_rem)] if n_rem else []))
    thP = beta_a + alpha_a
    dav, dap = np.array(DAV), np.array(DAP)
    w = np.sin(alpha_a) / e0
    return Simulation(veh, "volant", s, np.array(X), np.array(Y), thP, beta_a, alpha_a, theta,
                      w + dav, w + dap, dav, dap, pas=pas, infos={"v_kmh": v_kmh})
