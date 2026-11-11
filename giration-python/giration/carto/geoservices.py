"""Services de l'IGN (Géoplateforme, data.geopf.fr) : adresses des requêtes et lecture des réponses.

Ce module ne fait aucun accès réseau : il construit les URL et interprète le JSON reçu, ce qui
permet de le tester hors ligne. Les appels sont faits par l'interface (QtNetwork).

Données IGN sous Licence Ouverte Etalab 2.0 ; tuiles OpenStreetMap © contributeurs OSM (ODbL).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urlencode

GEOPF = "https://data.geopf.fr"
DEPOT = "https://github.com/YannFR81/territoires-ville.Giration"


def _agent() -> str:
    from .. import __version__
    # Politique des tuiles OSM et bonnes pratiques Géoplateforme : identifiant stable de l'application et contact
    return f"Giration-Python/{__version__} (+{DEPOT})"


AGENT = _agent()


@dataclass(frozen=True)
class Fond:
    code: str
    libelle: str
    modele: str
    zoom_max: int
    attribution: str
    format_image: str = "jpg"
    url_licence: str = "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"


_WMTS = (GEOPF + "/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&TILEMATRIXSET=PM"
         "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}")
FONDS = {
    "ortho": Fond("ortho", "Photographie aérienne (IGN)",
                  _WMTS + "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&FORMAT=image/jpeg", 19,
                  "© IGN – Géoplateforme, orthophotographies"),
    "plan": Fond("plan", "Plan IGN", _WMTS + "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png",
                 19, "© IGN – Géoplateforme, Plan IGN", "png"),
    "osm": Fond("osm", "OpenStreetMap", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19,
                "© contributeurs OpenStreetMap", "png", "https://www.openstreetmap.org/copyright"),
}
CADASTRE = Fond("cadastre", "Parcelles cadastrales (IGN)",
                _WMTS + "&LAYER=CADASTRALPARCELS.PARCELLAIRE_EXPRESS&STYLE=PCI%20vecteur&FORMAT=image/png", 19,
                "© IGN – Parcellaire Express", "png")


# Couche « Routes » de cartes.gouv.fr (réseau routier IGN avec numéros), superposée à la photographie
ROUTES = Fond("routes", "Routes (IGN)",
              _WMTS + "&LAYER=TRANSPORTNETWORKS.ROADS&STYLE=normal&FORMAT=image/png", 18, "© IGN – Routes", "png")

# Fonds automatiques : plan pour naviguer sur la France, photographie aérienne une fois zoomé
SEUIL_PHOTO = 16                      # niveau de zoom à partir duquel la photo s'affiche (bascule dès 15,75,
                                      # soit environ 1/7 500 à l'écran en métropole)
MODES_FOND = {
    "auto": "Automatique : Plan IGN, puis photo aérienne",
    "auto_osm": "Automatique : OpenStreetMap, puis photo aérienne",
    "ortho": FONDS["ortho"].libelle,
    "plan": FONDS["plan"].libelle,
    "osm": FONDS["osm"].libelle,
}


def fond_effectif(mode: str, niveau: float) -> str:
    """Code du fond affiché pour un mode et un niveau de zoom."""
    if mode in ("auto", "auto_osm"):
        if niveau >= SEUIL_PHOTO - 0.25:
            return "ortho"
        return "plan" if mode == "auto" else "osm"
    return mode if mode in FONDS else "ortho"


def url_tuile(fond: Fond, x: int, y: int, z: int) -> str:
    return fond.modele.format(x=x, y=y, z=z)


# ---------------------------------------------------------------------------
# Recherche d'adresse (géocodage IGN)
# ---------------------------------------------------------------------------
@dataclass
class Lieu:
    libelle: str
    lat: float
    lon: float
    type: str = ""
    commune: str = ""
    insee: str = ""


def url_recherche(texte: str, limite: int = 10, lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    """Géocodage IGN ; (lat, lon) favorise les résultats proches de la vue affichée."""
    params = {"q": texte, "limit": limite}
    if lat is not None and lon is not None:
        params.update(lat=f"{lat:.5f}", lon=f"{lon:.5f}")
    return f"{GEOPF}/geocodage/search?" + urlencode(params)


def lire_recherche(donnees: dict) -> list[Lieu]:
    out = []
    for f in donnees.get("features", []):
        p = f.get("properties", {})
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append(Lieu(p.get("label", ""), lat, lon, p.get("type", ""), p.get("city", ""), p.get("citycode", "")))
    return out


# ---------------------------------------------------------------------------
# Commune (Admin Express)
# ---------------------------------------------------------------------------
@dataclass
class Commune:
    nom: str = ""
    insee: str = ""
    code_postal: str = ""
    departement: str = ""


def _bbox(lat: float, lon: float, rayon: float) -> str:
    dlat = rayon / 111132.0
    dlon = rayon / (111320.0 * math.cos(math.radians(lat)))
    return f"{lat - dlat:.7f},{lon - dlon:.7f},{lat + dlat:.7f},{lon + dlon:.7f},urn:ogc:def:crs:EPSG::4326"


def _wfs(couche: str, lat: float, lon: float, rayon: float, nombre: int) -> str:
    return (f"{GEOPF}/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES={quote(couche, safe=':')}"
            f"&outputFormat=application/json&COUNT={nombre}&BBOX={_bbox(lat, lon, rayon)}")


def url_commune(lat: float, lon: float) -> str:
    return _wfs("ADMINEXPRESS-COG.LATEST:commune", lat, lon, 2.0, 1)


def lire_commune(donnees: dict) -> Optional[Commune]:
    feats = donnees.get("features", [])
    if not feats:
        return None
    p = feats[0].get("properties", {})
    return Commune(p.get("nom_officiel") or p.get("nom", ""), p.get("code_insee", ""), p.get("code_postal", "") or "",
                   p.get("code_insee_du_departement", ""))


# ---------------------------------------------------------------------------
# Tronçons de route (BD TOPO)
# ---------------------------------------------------------------------------
@dataclass
class Troncon:
    numero: str                       # D71, N88, A68… (vide pour une voie communale sans numéro)
    nom: str                          # nom de voie (route nommée ou nom collaboratif)
    nature: str                       # Route à 1 chaussée, Rond-point, Chemin…
    importance: int                   # 1 (réseau principal) à 6 (chemins)
    gestionnaire: str = ""
    largeur: Optional[float] = None
    voies: Optional[int] = None
    insee: str = ""
    points: list[tuple[float, float]] = field(default_factory=list)   # (lat, lon)
    sens: str = ""                    # Double sens, Sens direct, Sens inverse, Sans objet
    date_maj: str = ""                # date de dernière modification du tronçon dans la BD TOPO (AAAA-MM-JJ)

    @property
    def sens_unique(self) -> bool:
        return self.sens in ("Sens direct", "Sens inverse")

    @property
    def anneau(self) -> bool:
        return (self.nature or "").lower().startswith("rond-point")


def url_routes(lat: float, lon: float, rayon: float = 120.0) -> str:
    return _wfs("BDTOPO_V3:troncon_de_route", lat, lon, rayon, 300)


ABREVIATIONS = {"RTE": "Route", "CHE": "Chemin", "AV": "Avenue", "BD": "Boulevard", "IMP": "Impasse",
                "PL": "Place", "ALL": "Allée", "CHEM": "Chemin", "RUE": "Rue", "R": "Rue", "LOT": "Lotissement",
                "ZA": "ZA", "ZI": "ZI", "ST": "Saint", "STE": "Sainte", "SQ": "Square", "QU": "Quai", "QUAI": "Quai",
                "CRS": "Cours", "FG": "Faubourg", "PROM": "Promenade", "PASS": "Passage", "HAM": "Hameau",
                "RPT": "Rond-point", "CHS": "Chaussée", "ESP": "Esplanade", "SEN": "Sente", "VOIE": "Voie"}
PETITS_MOTS = {"de", "du", "des", "la", "le", "les", "et", "à", "a", "aux", "au", "sur", "sous", "en"}


def titre_fr(texte: str) -> str:
    """« RTE VIEILLE D'ALBI » -> « Route vieille d'Albi » (casse française des noms de voies)."""
    mots = []
    for i, m in enumerate(str(texte).split()):
        u = m.upper()
        if u in ABREVIATIONS and i == 0:
            mots.append(ABREVIATIONS[u])
            continue
        if u in ("ZA", "ZI", "ZAC", "RD", "RN"):
            mots.append(u)
            continue
        if "'" in m:
            pre, _, suite = m.partition("'")
            mots.append(pre.lower() + "'" + suite.capitalize())
            continue
        bas = m.lower()
        mots.append(bas if (i > 0 and bas in PETITS_MOTS) else bas.capitalize() if i == 0 else bas.capitalize())
    return " ".join(mots)


def _nom_voie(p: dict) -> str:
    for cle in ("cpx_toponyme_route_nommee", "nom_collaboratif_gauche", "nom_collaboratif_droite"):
        v = p.get(cle)
        if v:
            return titre_fr(v)
    return ""


def lire_routes(donnees: dict) -> list[Troncon]:
    out = []
    for f in donnees.get("features", []):
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        if g.get("type") == "MultiLineString":
            coords = [c for partie in coords for c in partie]
        if len(coords) < 2:
            continue
        p = f.get("properties", {})
        try:
            importance = int(p.get("importance") or 6)
        except (TypeError, ValueError):
            importance = 6
        out.append(Troncon(numero=(p.get("cpx_numero") or "").replace(" ", ""), nom=_nom_voie(p),
                           nature=p.get("nature") or "", importance=importance,
                           gestionnaire=p.get("cpx_gestionnaire") or "",
                           largeur=p.get("largeur_de_chaussee"), voies=p.get("nombre_de_voies"),
                           insee=p.get("insee_commune_gauche") or p.get("insee_commune_droite") or "",
                           points=[(c[1], c[0]) for c in coords], sens=p.get("sens_de_circulation") or "",
                           date_maj=str(p.get("date_modification") or p.get("date_creation") or "")[:10]))
    return out


# ---------------------------------------------------------------------------
# Sources des données et licences (affichées dans l'aide, reprises dans les exports)
# ---------------------------------------------------------------------------
ETALAB = "Licence Ouverte / Open Licence Etalab 2.0"
URL_ETALAB = "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"
URL_CGU_GEOPF = "https://cartes.gouv.fr/cgu/"
URL_LIMITES_GEOPF = ("https://cartes.gouv.fr/aide/fr/guides-utilisateur/utiliser-les-services-de-la-geoplateforme/"
                     "limites-d-usage/")


@dataclass(frozen=True)
class Source:
    usage: str
    donnees: str
    producteur: str
    service: str
    licence: str
    url_licence: str


SOURCES = [
    Source("Fond de carte : photographie aérienne", "Orthophotographies (BD ORTHO®)", "IGN",
           "WMTS ORTHOIMAGERY.ORTHOPHOTOS", ETALAB, URL_ETALAB),
    Source("Fond de carte : plan", "Plan IGN", "IGN", "WMTS GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", ETALAB, URL_ETALAB),
    Source("Couche « Routes »", "Réseau routier (BD TOPO®)", "IGN", "WMTS TRANSPORTNETWORKS.ROADS", ETALAB,
           URL_ETALAB),
    Source("Couche cadastre", "Parcellaire Express (PCI)", "IGN d'après DGFiP",
           "WMTS CADASTRALPARCELS.PARCELLAIRE_EXPRESS", ETALAB, URL_ETALAB),
    Source("Analyse du carrefour (routes, numéros, noms de voies, anneau)", "BD TOPO® — tronçons de route", "IGN",
           "WFS BDTOPO_V3:troncon_de_route", ETALAB, URL_ETALAB),
    Source("Commune et code INSEE", "ADMIN EXPRESS COG", "IGN", "WFS ADMINEXPRESS-COG.LATEST:commune", ETALAB,
           URL_ETALAB),
    Source("Recherche d'adresses et de lieux", "Base Adresse Nationale, BD TOPO®", "IGN, BAN",
           "Géocodage de la Géoplateforme", ETALAB, URL_ETALAB),
    Source("Fond de carte : OpenStreetMap (option)", "Carte OpenStreetMap", "Contributeurs OpenStreetMap",
           "tile.openstreetmap.org", "ODbL (données), CC BY-SA 2.0 (tuiles)", "https://www.openstreetmap.org/copyright"),
]


def mention_fond(fond: str = "auto", quand: Optional[str] = None) -> str:
    """Mention de paternité d'un site dessiné à la main (sans analyse de la BD TOPO) : le fond consulté."""
    import datetime as _dt
    quand = quand or _dt.date.today().strftime("%d/%m/%Y")
    if fond == "osm":
        return f"Fond de carte : © contributeurs OpenStreetMap (ODbL), consulté le {quand}"
    osm = " et © contributeurs OpenStreetMap (ODbL)" if fond == "auto_osm" else ""
    return (f"Sources : IGN – Géoplateforme, photographies aériennes et Plan IGN (consultés le {quand}){osm}, "
            f"{ETALAB}")


def mention_sources(troncons: Optional[list] = None, quand: Optional[str] = None) -> str:
    """Mention de paternité exigée par la Licence Ouverte 2.0 : producteur, données, date de mise à jour."""
    import datetime as _dt
    quand = quand or _dt.date.today().strftime("%d/%m/%Y")
    maj = ""
    dates = sorted({t.date_maj for t in (troncons or []) if getattr(t, "date_maj", "")})
    if dates:
        try:
            maj = f", tronçons mis à jour jusqu'au {_dt.date.fromisoformat(dates[-1]).strftime('%d/%m/%Y')}"
        except ValueError:
            maj = ""
    return (f"Sources : IGN — BD TOPO®{maj}, ADMIN EXPRESS (Géoplateforme, consultée le {quand}), "
            f"{ETALAB}")
