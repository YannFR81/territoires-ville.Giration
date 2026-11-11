"""Modèle de véhicule de Giration (CERTU) : un tracteur à deux essieux équivalents suivi de 0, 1 ou 2 remorques.

Conventions (guide Giration 3.3, annexe A), en mètres, indice 0 = tracteur :

- ``PAFav`` : porte-à-faux avant. Pour une remorque, compté depuis le point d'attelage, positif vers l'avant.
- ``E`` : empattement ; pour une remorque, distance du point d'attelage au centre du groupe d'essieux.
- ``PAFar`` : porte-à-faux arrière, depuis l'essieu (équivalent) arrière.
- ``L`` : largeur de caisse sans rétroviseurs.
- ``LEav`` (tracteur), ``LEar`` : largeur d'essieu pneus compris.
- ``D`` : position de l'attelage de l'élément suivant par rapport à l'essieu arrière de l'élément,
  positive derrière l'essieu (crochet), négative devant (sellette).

L'angle de braquage α est celui d'une roue avant « équivalente » placée au milieu de l'essieu avant.
Le rayon minimal du tracteur se déduit du rayon intérieur entre trottoirs ``RVehMin`` :
``α_max = atan(E0 / (RVehMin + LEar0 / 2))``.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Optional, Union

VITESSE_BRAQUAGE_DEFAUT = math.radians(15.0)       # rad/s, valeur par défaut de Giration (CERTU)
ARTICULATION_MAX_DEFAUT = math.radians(90.0)       # au-delà : mise en portefeuille (hypothèse, à régler)


@dataclass
class Element:
    role: str                 # « tracteur », « motrice », « semi-remorque », « remorque », « timon »…
    PAFav: float
    E: float
    PAFar: float
    L: float
    LEar: float
    LEav: Optional[float] = None       # tracteur seulement
    D: Optional[float] = None          # attelage de l'élément suivant (None pour le dernier)
    designation: str = ""

    @property
    def longueur(self) -> float:
        return self.PAFav + self.E + self.PAFar


@dataclass
class Vehicule:
    id: str
    nom: str
    elements: list[Element]
    alpha_max: float                                  # rad, braquage maximal du tracteur
    vitesse_braquage: float = VITESSE_BRAQUAGE_DEFAUT  # rad/s
    articulation_max: float = ARTICULATION_MAX_DEFAUT  # rad, angle maximal entre deux éléments
    nom_court: str = ""
    categorie: str = ""
    arborescence: list[str] = field(default_factory=list)
    constructeur: str = ""
    modele: str = ""
    fiabilite: str = ""
    alpha_max_permanent: Optional[float] = None       # braquage réduit (régime permanent faisable)
    origine: str = ""
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ propriétés
    @property
    def tracteur(self) -> Element:
        return self.elements[0]

    @property
    def nb_remorques(self) -> int:
        return len(self.elements) - 1

    @property
    def longueur_totale(self) -> float:
        """Longueur hors tout en ligne droite (attelages compris)."""
        t = self.tracteur
        avant = t.PAFav
        arriere = -(t.E + t.PAFar)                    # abscisses le long de l'axe, origine au milieu de l'essieu avant
        x_essieu = -t.E
        for k in range(1, len(self.elements)):
            prec, el = self.elements[k - 1], self.elements[k]
            x_att = x_essieu - prec.D
            avant = max(avant, x_att + el.PAFav)
            x_essieu = x_att - el.E
            arriere = min(arriere, x_essieu - el.PAFar)
        return avant - arriere

    @property
    def largeur(self) -> float:
        return max(max(e.L, e.LEar, e.LEav or 0) for e in self.elements)

    @property
    def RVehMin(self) -> float:
        """Rayon intérieur entre trottoirs du tracteur au braquage maximal (définition CERTU)."""
        t = self.tracteur
        return t.E / math.tan(self.alpha_max) - t.LEar / 2

    def rayon_P_min(self) -> float:
        """Rayon minimal de la trajectoire du milieu de l'essieu avant (point guidé)."""
        return self.tracteur.E / math.sin(self.alpha_max)

    def copie(self, **modifs) -> "Vehicule":
        return replace(self, elements=[replace(e) for e in self.elements], **modifs)

    def mis_a_l_echelle(self, k: float) -> "Vehicule":
        """Dimensions multipliées par k (altération linéaire de la projection du plan)."""
        if abs(k - 1) < 1e-12:
            return self
        els = []
        for e in self.elements:
            els.append(replace(e, PAFav=e.PAFav * k, E=e.E * k, PAFar=e.PAFar * k, L=e.L * k, LEar=e.LEar * k,
                               LEav=None if e.LEav is None else e.LEav * k, D=None if e.D is None else e.D * k))
        return replace(self, elements=els)

    def description(self) -> str:
        t = self.tracteur
        lignes = [f"{self.nom} [{self.id}]",
                  f"  longueur {self.longueur_totale:.2f} m, largeur {self.largeur:.2f} m, "
                  f"{self.nb_remorques} remorque(s), braquage max {math.degrees(self.alpha_max):.2f}°, "
                  f"vitesse de braquage {math.degrees(self.vitesse_braquage):.0f} °/s"]
        for k, e in enumerate(self.elements):
            s = (f"  {k}. {e.role:14s} PAFav {e.PAFav:6.3f}  E {e.E:6.3f}  PAFar {e.PAFar:6.3f}  "
                 f"L {e.L:5.3f}  LEar {e.LEar:5.3f}")
            if e.LEav is not None:
                s += f"  LEav {e.LEav:5.3f}"
            if e.D is not None:
                s += f"  D {e.D:+6.3f}"
            lignes.append(s)
        return "\n".join(lignes)

    def en_dict(self) -> dict:
        d = asdict(self)
        d["alpha_max_deg"] = math.degrees(self.alpha_max)
        d["vitesse_braquage_deg_s"] = math.degrees(self.vitesse_braquage)
        d["articulation_max_deg"] = math.degrees(self.articulation_max)
        if self.alpha_max_permanent is not None:
            d["alpha_max_permanent_deg"] = math.degrees(self.alpha_max_permanent)
        for k in ("alpha_max", "vitesse_braquage", "articulation_max", "alpha_max_permanent"):
            d.pop(k, None)
        return d

    @classmethod
    def depuis_dict(cls, d: dict) -> "Vehicule":
        d = dict(d)
        els = [Element(**e) for e in d.pop("elements")]
        am = math.radians(d.pop("alpha_max_deg"))
        vb = math.radians(d.pop("vitesse_braquage_deg_s", math.degrees(VITESSE_BRAQUAGE_DEFAUT)))
        art = math.radians(d.pop("articulation_max_deg", math.degrees(ARTICULATION_MAX_DEFAUT)))
        amp = d.pop("alpha_max_permanent_deg", None)
        return cls(elements=els, alpha_max=am, vitesse_braquage=vb, articulation_max=art,
                   alpha_max_permanent=None if amp is None else math.radians(amp), **d)


# ---------------------------------------------------------------------- régime permanent
@dataclass
class RayonsElement:
    R_essieu: float          # rayon du milieu de l'essieu arrière (équivalent)
    int_trottoirs: float     # roue intérieure de l'essieu arrière
    int_murs: float          # flanc intérieur de la caisse
    ext_trottoirs: float     # roue extérieure la plus éloignée (essieu avant pour le tracteur)
    ext_murs: float          # coin avant extérieur de la caisse


def rayons_permanents(veh: Vehicule, alpha: Optional[float] = None) -> list[RayonsElement]:
    """Rayons en régime permanent (virage de rayon constant parcouru assez longtemps), formules du CERTU.

    Ils servent à la validation du moteur. Un rayon de remorque négatif signifie qu'elle ne peut pas
    atteindre ce régime (elle tournerait « sur place » puis se mettrait en portefeuille).
    """
    a = veh.alpha_max if alpha is None else alpha
    t = veh.tracteur
    Rt = t.E / math.tan(abs(a))                     # milieu de l'essieu arrière du tracteur
    res = []
    ext_tr = math.hypot(Rt + t.LEav / 2, t.E) if t.LEav else math.hypot(Rt, t.E)
    res.append(RayonsElement(Rt, Rt - t.LEar / 2, _int_murs(Rt, t.L, -t.PAFar, t.E + t.PAFav),
                             max(ext_tr, Rt + t.LEar / 2), math.hypot(Rt + t.L / 2, t.E + t.PAFav)))
    R = Rt
    for k in range(1, len(veh.elements)):
        prec, el = veh.elements[k - 1], veh.elements[k]
        carre = R * R + prec.D ** 2 - el.E ** 2
        Rk = math.sqrt(carre) if carre > 0 else -math.sqrt(-carre)
        Ra = abs(Rk)
        res.append(RayonsElement(Rk, Rk - el.LEar / 2, _int_murs(Ra, el.L, -el.PAFar, el.E + el.PAFav),
                                 Rk + el.LEar / 2, math.hypot(Ra + el.L / 2, el.E + el.PAFav)))
        R = Ra
    return res


def _int_murs(Ra: float, L: float, x_arr: float, x_av: float) -> float:
    """Distance du centre de rotation au flanc intérieur d'une caisse [x_arr, x_av] autour de l'essieu."""
    d = Ra - L / 2
    if x_arr <= 0 <= x_av:
        return d
    x = min(abs(x_arr), abs(x_av))
    return math.copysign(math.hypot(d, x), d)


# ---------------------------------------------------------------------- bibliothèque JSON
CHEMIN_BIBLIOTHEQUE = Path(__file__).with_name("ressources") / "bibliotheque-vehicules.json"


def _vehicule_bibliotheque(v: dict) -> Optional[Vehicule]:
    g = v.get("giration", {})
    els_src = g.get("elements") or []
    if not els_src or not g.get("convertible", True) or g.get("alpha_max_deg") is None:
        return None
    els = []
    for k, e in enumerate(els_src):
        if any(e.get(c) is None for c in ("PAFav", "E", "PAFar", "L", "LEar")):
            return None
        D = e.get("D")
        if k < len(els_src) - 1 and D is None:
            return None
        els.append(Element(role=e.get("role", "tracteur" if k == 0 else "remorque"), PAFav=e["PAFav"], E=e["E"],
                           PAFar=e["PAFar"], L=e["L"], LEar=e["LEar"],
                           LEav=e.get("LEav") if k == 0 else None,
                           D=D if k < len(els_src) - 1 else None))
    if els[0].LEav is None:
        els[0].LEav = els[0].LEar
    am_perm = math.radians(g["alpha_max_deg"])
    am = am_perm
    ts = g.get("tracteur_seul") or {}
    if len(els) > 1 and ts.get("alpha_max_deg"):
        am = math.radians(ts["alpha_max_deg"])         # braquage mécanique du tracteur
    arbo = v.get("arborescence") or []
    return Vehicule(id=v["id"], nom=v.get("nom", v["id"]), elements=els, alpha_max=am,
                    vitesse_braquage=math.radians(g.get("vitesse_braquage_deg_s") or 15.0),
                    nom_court=v.get("nom_court", ""), categorie=arbo[0] if arbo else "", arborescence=arbo,
                    constructeur=v.get("constructeur", ""), modele=v.get("modele", ""),
                    fiabilite=v.get("fiabilite", ""),
                    alpha_max_permanent=am_perm if abs(am_perm - am) > 1e-9 else None,
                    origine=v.get("origine", ""), notes=list(g.get("notes") or []))


class Bibliotheque:
    """Bibliothèque de véhicules (JSON sourcé, voir docs/bibliotheque.md)."""

    def __init__(self, chemin: Union[str, Path, None] = None):
        self.chemin = Path(chemin) if chemin else CHEMIN_BIBLIOTHEQUE
        donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
        self.titre = donnees.get("titre", "")
        self.version = donnees.get("version", "")
        self.brut = {v["id"]: v for v in donnees["vehicules"]}
        self.vehicules: dict[str, Vehicule] = {}
        self.ecartes: dict[str, str] = {}
        for v in donnees["vehicules"]:
            veh = _vehicule_bibliotheque(v)
            if veh is None:
                self.ecartes[v["id"]] = "données de giration incomplètes"
            else:
                self.vehicules[veh.id] = veh

    def __iter__(self):
        return iter(self.vehicules.values())

    def __len__(self):
        return len(self.vehicules)

    def get(self, cle: str) -> Vehicule:
        """Par identifiant exact, sinon par recherche unique dans l'identifiant ou le nom."""
        if cle in self.vehicules:
            return self.vehicules[cle]
        trouves = self.chercher(cle)
        if len(trouves) == 1:
            return trouves[0]
        if not trouves:
            raise KeyError(f"Aucun véhicule ne correspond à « {cle} ».")
        raise KeyError(f"« {cle} » désigne plusieurs véhicules : " + ", ".join(v.id for v in trouves[:12]))

    def chercher(self, texte: str) -> list[Vehicule]:
        mots = _sans_accents(texte.lower()).split()
        res = []
        for v in self.vehicules.values():
            cible = _sans_accents(" ".join([v.id, v.nom, v.nom_court, *v.arborescence]).lower())
            if all(m in cible for m in mots):
                res.append(v)
        return res

    def familles(self) -> dict[str, list[Vehicule]]:
        f: dict[str, list[Vehicule]] = {}
        for v in self.vehicules.values():
            f.setdefault(v.categorie or "Autres", []).append(v)
        return f


def _sans_accents(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------- fichiers .veh de Giration 3.3
_REC = struct.Struct("<20s h h 3f 3f 3f 3f f 3f 2f f f f")


def lire_veh(chemin: Union[str, Path]) -> list[Vehicule]:
    """Lit une bibliothèque binaire de Giration 3.3 (enregistrements VB6 de 108 octets)."""
    data = Path(chemin).read_bytes()
    res = []
    for i in range(1, len(data) // _REC.size):            # l'enregistrement 0 est l'en-tête de version
        f = _REC.unpack_from(data, i * _REC.size)
        nom = f[0].decode("cp1252").rstrip(" \x00")
        n = f[2]
        if not nom or not 0 <= n <= 2:
            continue
        PAFav, E, PAFar, L = f[3:6], f[6:9], f[9:12], f[12:15]
        LEav, LEar, Dp = f[15], f[16:19], f[19:21]
        aVeh, RVehMin = f[22], f[23]
        els = []
        for k in range(n + 1):
            els.append(Element(role="tracteur" if k == 0 else f"remorque {k}", PAFav=_r(PAFav[k]), E=_r(E[k]),
                               PAFar=_r(PAFar[k]), L=_r(L[k]), LEar=_r(LEar[k]),
                               LEav=_r(LEav) if k == 0 else None, D=_r(Dp[k]) if k < n else None))
        am = math.atan(els[0].E / (_r(RVehMin) + els[0].LEar / 2))
        ident = "veh-" + _sans_accents(nom.lower()).replace(" ", "-")
        res.append(Vehicule(id=ident, nom=nom, elements=els, alpha_max=am, vitesse_braquage=float(aVeh) or
                            VITESSE_BRAQUAGE_DEFAUT, nom_court=nom, categorie="Bibliothèque Giration 3.3",
                            origine=Path(chemin).name))
    return res


def _r(x: float) -> float:
    """Les Single de VB6 portent des résidus binaires : arrondi au dixième de millimètre."""
    return round(float(x), 4)
