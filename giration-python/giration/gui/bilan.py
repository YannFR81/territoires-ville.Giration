"""Bilan chiffré d'une variante et comparaison côte à côte (même trajet, autre véhicule ; ou autre trajet).

Sans dépendance à Qt : testable seul. Sert au panneau « Comparer », à la note PDF et à l'assistant d'IA. Les
valeurs viennent du calcul (emprises, rayon, braquage, vitesse), du contrôle de la chaussée et des contraintes
dessinées (bordures, obstacles, bâti).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PASSE, RESERVE, IMPOSSIBLE = "passe", "reserve", "impossible"
ORDRE = {PASSE: 0, RESERVE: 1, IMPOSSIBLE: 2}
LIBELLES = {PASSE: "Passe", RESERVE: "Passe avec réserves", IMPOSSIBLE: "Ne passe pas"}
COULEURS = {PASSE: "#2E7D32", RESERVE: "#B26A00", IMPOSSIBLE: "#C62828"}


def fr(v: Optional[float], d: int = 2, unite: str = "") -> str:
    if v is None:
        return "—"
    t = f"{v:.{d}f}".replace(".", ",").replace("-", "−")
    return f"{t} {unite}".rstrip()


def _ecart(v: Optional[float], ref: Optional[float], d: int = 2) -> str:
    if v is None or ref is None:
        return ""
    e = round(v - ref, d)
    if e == 0:
        return " (=)"
    return f" ({'+' if e > 0 else '−'}{fr(abs(e), d)})"


@dataclass
class Bilan:
    variante: str                              # « A — Car 13 m »
    vehicule: str
    verdict: str = PASSE                       # passe, reserve, impossible
    couleur: str = "#E8730C"
    emprise_max: Optional[float] = None        # m : largeur balayée par la caisse
    emprise_roues: Optional[float] = None      # m : largeur balayée par les roues
    surface: Optional[float] = None            # m² : surface balayée par la caisse
    rayon_mini: Optional[float] = None         # m : rayon mini du trajet
    braquage_pct: Optional[float] = None       # % du braquage maximal
    braquage_deg: Optional[float] = None
    articulation_deg: Optional[float] = None   # véhicules articulés
    vitesses: tuple = ()                       # km/h : (entrée, mini, sortie)
    longueur: Optional[float] = None           # m : longueur du trajet
    ecart_saisie: float = 0.0                  # m : écart du véhicule à la saisie (trajet au mieux)
    hors_chaussee: float = 0.0                 # m : roues hors de la chaussée estimée
    ilot: float = 0.0                          # m : empiètement sur l'îlot infranchissable
    bande: float = 0.0                         # m : roues sur la bande franchissable
    conflits: list = field(default_factory=list)   # messages (bordures, obstacles, bâti)
    raisons: list = field(default_factory=list)    # pourquoi réserve ou impossible
    indice: int = -1                           # rang de la variante dans le projet
    remarques: list = field(default_factory=list)  # alertes du calcul (attention, erreur)

    @property
    def libelle(self) -> str:
        return LIBELLES.get(self.verdict, self.verdict)


ECART_VISIBLE = 0.10                           # m (saisie.ECART_VISIBLE) : écart du véhicule à la saisie signalé


def verdict_resultat(faisable: bool, ilot: float = 0.0, ecart_saisie: float = 0.0, alertes: tuple = (),
                     hors: float = 0.0, bande: float = 0.0) -> str:
    """Verdict d'une épure, selon les mêmes règles que le panneau des résultats : calcul impossible ou îlot
    infranchissable touché → ne passe pas ; écart à la saisie, alerte, roues hors chaussée ou sur la bande
    franchissable → réserve ; sinon passe. ``alertes`` : messages des alertes « attention » ou « erreur »."""
    if not faisable or ilot > 0.05:
        return IMPOSSIBLE
    if ecart_saisie > ECART_VISIBLE or alertes or hors > 0 or bande > 0.05:
        return RESERVE
    return PASSE


def bilan_depuis_mesures(variante: str, vehicule: str, m: dict, *, faisable: bool = True, couleur: str = "#E8730C",
                         remorques: bool = False, surface: Optional[float] = None, ecart_saisie: float = 0.0,
                         alertes: tuple = (), hors: float = 0.0, ilot: float = 0.0, bande: float = 0.0,
                         raison: str = "") -> Bilan:
    """Bilan d'après ``Resultat.mesures()`` (clés largeur_carrosserie_max, largeur_roues_max, rayon_trace_min,
    braquage_max_pourcent, braquage_max_deg, articulation_max_deg, vitesse_entree_kmh, vitesse_min_kmh,
    vitesse_sortie_kmh) et le contrôle de la chaussée."""
    import math

    def v(cle):
        x = m.get(cle)
        return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)
    b = Bilan(variante, vehicule, verdict_resultat(faisable, ilot, ecart_saisie, alertes, hors, bande), couleur,
              emprise_max=v("largeur_carrosserie_max"), emprise_roues=v("largeur_roues_max"), surface=surface,
              rayon_mini=v("rayon_trace_min"), braquage_pct=v("braquage_max_pourcent"),
              braquage_deg=v("braquage_max_deg"), articulation_deg=v("articulation_max_deg") if remorques else None,
              vitesses=(v("vitesse_entree_kmh"), v("vitesse_min_kmh"), v("vitesse_sortie_kmh")),
              ecart_saisie=ecart_saisie, hors_chaussee=hors, ilot=ilot, bande=bande)
    if raison:
        b.raisons.append(raison)
    elif b.verdict != PASSE:
        if not faisable:
            b.raisons.append(alertes[0] if alertes else "Le calcul n'aboutit pas.")
        elif ilot > 0.05:
            b.raisons.append(f"Les roues montent sur l'îlot central infranchissable ({fr(ilot)} m)")
        elif ecart_saisie > ECART_VISIBLE:
            b.raisons.append(f"Le véhicule s'écarte de la saisie (jusqu'à {fr(ecart_saisie)} m)")
        elif alertes:
            b.raisons.append(alertes[0])
        elif hors > 0:
            b.raisons.append(f"Roues hors de la chaussée estimée de {fr(hors)} m")
        else:
            b.raisons.append(f"Roues sur la bande franchissable ({fr(bande)} m)")
    return b


def pire(*verdicts: str) -> str:
    return max(verdicts, key=lambda v: ORDRE.get(v, 0)) if verdicts else PASSE


def verdict_contraintes(conflits) -> str:
    """Verdict dû aux bordures, obstacles et bâtiments (objets ``contraintes.Conflit``)."""
    if any(getattr(k, "grave", False) for k in conflits):
        return IMPOSSIBLE
    return RESERVE if conflits else PASSE


def completer(b: Bilan, conflits) -> Bilan:
    """Ajoute au bilan les conflits avec les contraintes dessinées et le bâti, et durcit le verdict."""
    b.conflits = [k.message() for k in conflits]
    v = verdict_contraintes(conflits)
    if ORDRE[v] > 0:
        b.raisons += [k.message() for k in conflits if (k.grave if v == IMPOSSIBLE else True)][:3]
    b.verdict = pire(b.verdict, v)
    return b


def plus_favorable(bilans: list[Bilan]) -> Optional[int]:
    """Indice de la variante la plus favorable : meilleur verdict, puis plus petite emprise des roues, puis plus
    petite surface balayée."""
    if not bilans:
        return None
    big = 1e9
    return min(range(len(bilans)), key=lambda i: (ORDRE.get(bilans[i].verdict, 0),
                                                  bilans[i].emprise_roues if bilans[i].emprise_roues is not None else big,
                                                  bilans[i].surface if bilans[i].surface is not None else big))


def _vitesses(b: Bilan) -> str:
    v = [x for x in b.vitesses if x is not None]
    return " → ".join(fr(x, 0) for x in v) + " km/h" if v else "—"


def tableau(bilans: list[Bilan], reference: int = 0) -> tuple[list[str], list[list[str]]]:
    """Tableau côte à côte : une colonne par variante, une ligne par critère ; écarts à la colonne de référence."""
    entetes = ["Critère"] + [b.variante for b in bilans]
    ref = bilans[reference] if bilans else None

    def ligne(titre, valeur, d=2, unite="m", ecart=True):
        cells = [titre]
        for i, b in enumerate(bilans):
            v = valeur(b)
            cells.append(fr(v, d, unite) + (_ecart(v, valeur(ref), d) if ecart and i != reference else ""))
        return cells

    lignes = [["Véhicule"] + [b.vehicule for b in bilans],
              ["Verdict"] + [b.libelle for b in bilans],
              ligne("Emprise de la caisse", lambda b: b.emprise_max),
              ligne("Emprise des roues", lambda b: b.emprise_roues),
              ligne("Surface balayée", lambda b: b.surface, 0, "m²"),
              ligne("Rayon mini du trajet", lambda b: b.rayon_mini, 1),
              ["Braquage maxi"] + [("—" if b.braquage_pct is None else fr(b.braquage_pct, 0, "%")
                                    + ("" if b.braquage_deg is None else f" ({fr(b.braquage_deg, 1)}°)"))
                                   for b in bilans],
              ["Articulation maxi"] + [("—" if b.articulation_deg is None else fr(b.articulation_deg, 1) + "°")
                                       for b in bilans],
              ["Vitesse"] + [_vitesses(b) for b in bilans],
              ligne("Écart à votre saisie", lambda b: b.ecart_saisie),
              ligne("Roues hors chaussée", lambda b: b.hors_chaussee),
              ligne("Îlot / bande franchissable", lambda b: max(b.ilot, b.bande), ecart=False),
              ["Bordures et obstacles"] + [(f"{len(b.conflits)} conflit{'s' if len(b.conflits) > 1 else ''}"
                                            if b.conflits else "aucun conflit") for b in bilans]]
    return entetes, lignes


def resume(bilans: list[Bilan]) -> str:
    """Phrase de synthèse pour le bandeau, la note et l'assistant."""
    if not bilans:
        return ""
    if len(bilans) == 1:
        b = bilans[0]
        return f"{b.variante} : {b.libelle.lower()}" + (f" ({b.raisons[0]})" if b.raisons else "") + "."
    i = plus_favorable(bilans)
    meilleur = bilans[i]
    autres = []
    for j, b in enumerate(bilans):
        if j == i:
            continue
        t = f"{b.variante} : {b.libelle.lower()}"
        if b.emprise_roues is not None and meilleur.emprise_roues is not None:
            d = b.emprise_roues - meilleur.emprise_roues
            if abs(d) >= 0.05:
                t += f", emprise des roues {'+' if d > 0 else '−'}{fr(abs(d))} m"
        autres.append(t)
    return (f"Variante la plus favorable : {meilleur.variante} ({meilleur.libelle.lower()}"
            + (f", emprise des roues {fr(meilleur.emprise_roues)} m" if meilleur.emprise_roues is not None else "")
            + "). " + " ; ".join(autres) + ".")
