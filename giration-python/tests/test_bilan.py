"""Bilan d'une variante et comparaison côte à côte."""
from dataclasses import dataclass

from giration.gui.bilan import (IMPOSSIBLE, PASSE, RESERVE, Bilan, bilan_depuis_mesures, completer, fr, pire,
                                plus_favorable, resume, tableau, verdict_contraintes, verdict_resultat)


@dataclass
class FauxConflit:
    grave: bool
    texte: str

    def message(self):
        return self.texte


def _bilans():
    a = Bilan("A — Car 13 m", "Car interurbain 13 m", RESERVE, emprise_max=6.56, emprise_roues=4.49, surface=512.3,
              rayon_mini=9.4, braquage_pct=83, braquage_deg=40.9, vitesses=(30, 6, 27), ecart_saisie=0.18,
              bande=0.3)
    b = Bilan("B — Semi 16,5 m", "Semi-remorque 13,60 m", PASSE, emprise_max=7.10, emprise_roues=4.20, surface=540.0,
              rayon_mini=9.8, braquage_pct=76, braquage_deg=35.2, articulation_deg=48.5, vitesses=(30, 7, 25))
    return [a, b]


def test_format_francais():
    assert fr(6.56) == "6,56" and fr(-0.4, 1, "m") == "−0,4 m" and fr(None) == "—"


def test_verdicts():
    assert pire(PASSE, RESERVE) == RESERVE and pire(RESERVE, IMPOSSIBLE, PASSE) == IMPOSSIBLE and pire() == PASSE
    assert verdict_contraintes([]) == PASSE
    assert verdict_contraintes([FauxConflit(False, "bâti")]) == RESERVE
    assert verdict_contraintes([FauxConflit(False, "bâti"), FauxConflit(True, "bordure")]) == IMPOSSIBLE


def test_completer():
    b = _bilans()[1]
    completer(b, [FauxConflit(True, "Les roues franchissent une bordure de 0,42 m")])
    assert b.verdict == IMPOSSIBLE and b.conflits and "bordure" in b.raisons[0]
    c = _bilans()[0]
    completer(c, [])
    assert c.verdict == RESERVE and c.conflits == []


def test_tableau_cote_a_cote():
    entetes, lignes = tableau(_bilans())
    assert entetes == ["Critère", "A — Car 13 m", "B — Semi 16,5 m"]
    d = {l[0]: l[1:] for l in lignes}
    assert d["Verdict"] == ["Passe avec réserves", "Passe"]
    assert d["Emprise de la caisse"] == ["6,56 m", "7,10 m (+0,54)"]
    assert d["Emprise des roues"] == ["4,49 m", "4,20 m (−0,29)"]
    assert d["Braquage maxi"][0] == "83 % (40,9°)"
    assert d["Articulation maxi"] == ["—", "48,5°"]
    assert d["Vitesse"] == ["30 → 6 → 27 km/h", "30 → 7 → 25 km/h"]
    assert all(len(l) == 3 for l in lignes)


def test_plus_favorable_et_resume():
    bilans = _bilans()
    assert plus_favorable(bilans) == 1
    r = resume(bilans)
    assert r.startswith("Variante la plus favorable : B — Semi 16,5 m (passe")
    assert "A — Car 13 m : passe avec réserves, emprise des roues +0,29 m" in r
    assert resume([bilans[0]]).startswith("A — Car 13 m : passe avec réserves")
    assert plus_favorable([]) is None


def test_verdict_resultat_regles_du_panneau():
    assert verdict_resultat(True) == PASSE
    assert verdict_resultat(False) == IMPOSSIBLE
    assert verdict_resultat(True, ilot=0.06) == IMPOSSIBLE
    assert verdict_resultat(True, ilot=0.04) == PASSE
    assert verdict_resultat(True, ecart_saisie=0.18) == RESERVE
    assert verdict_resultat(True, ecart_saisie=0.08) == PASSE
    assert verdict_resultat(True, alertes=("Vitesse très faible",)) == RESERVE
    assert verdict_resultat(True, hors=0.6) == RESERVE
    assert verdict_resultat(True, bande=0.3) == RESERVE


def test_bilan_depuis_mesures():
    m = {"largeur_carrosserie_max": 6.56, "largeur_roues_max": 4.49, "rayon_trace_min": 9.4,
         "braquage_max_pourcent": 83.0, "braquage_max_deg": 40.9, "articulation_max_deg": 0.0,
         "vitesse_entree_kmh": 30.0, "vitesse_min_kmh": 6.0, "vitesse_sortie_kmh": 27.0}
    b = bilan_depuis_mesures("A", "Car 13 m", m, ecart_saisie=0.18)
    assert b.verdict == RESERVE and b.articulation_deg is None and b.vitesses == (30.0, 6.0, 27.0)
    assert "0,18" in b.raisons[0]
    m2 = dict(m, rayon_trace_min=float("inf"))
    b2 = bilan_depuis_mesures("B", "Semi", m2, remorques=True, bande=0.3)
    assert b2.rayon_mini is None and b2.articulation_deg == 0.0 and "bande" in b2.raisons[0]
