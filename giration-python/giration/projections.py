"""Conversions de coordonnées : WGS84 / RGF93, Lambert-93, coniques conformes CC42 à CC50,
pseudo-Mercator (tuiles web) et plan local en mètres autour d'un point.

Formules de la projection conique conforme de Lambert sur l'ellipsoïde GRS80 (IGN, notes
techniques NT/G 71 ; Snyder, Map Projections, 1987). RGF93 et WGS84 sont confondus ici :
l'écart, inférieur au mètre, est sans effet à l'échelle d'un schéma de giratoire.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

A = 6378137.0                         # demi-grand axe GRS80 / WGS84
F = 1 / 298.257222101                 # aplatissement GRS80
E2 = F * (2 - F)
E = math.sqrt(E2)
R_MERCATOR = 6378137.0


@dataclass(frozen=True)
class Lambert:
    nom: str
    epsg: int
    phi0: float
    phi1: float
    phi2: float
    lam0: float
    x0: float
    y0: float

    def _constantes(self) -> tuple[float, float, float]:
        def m(p):
            return math.cos(p) / math.sqrt(1 - E2 * math.sin(p) ** 2)

        def t(p):
            s = math.sin(p)
            return math.tan(math.pi / 4 - p / 2) / ((1 - E * s) / (1 + E * s)) ** (E / 2)

        p0, p1, p2 = (math.radians(v) for v in (self.phi0, self.phi1, self.phi2))
        n = (math.log(m(p1)) - math.log(m(p2))) / (math.log(t(p1)) - math.log(t(p2)))
        f = m(p1) / (n * t(p1) ** n)
        rho0 = A * f * t(p0) ** n
        return n, f, rho0

    def depuis_geo(self, lat: float, lon: float) -> tuple[float, float]:
        n, f, rho0 = _CONST[self.epsg]
        p = math.radians(lat)
        s = math.sin(p)
        t = math.tan(math.pi / 4 - p / 2) / ((1 - E * s) / (1 + E * s)) ** (E / 2)
        rho = A * f * t ** n
        theta = n * math.radians(lon - self.lam0)
        return self.x0 + rho * math.sin(theta), self.y0 + rho0 - rho * math.cos(theta)

    def vers_geo(self, x: float, y: float) -> tuple[float, float]:
        n, f, rho0 = _CONST[self.epsg]
        dx, dy = x - self.x0, rho0 - (y - self.y0)
        rho = math.copysign(math.hypot(dx, dy), n)
        theta = math.atan2(dx, dy)
        t = (rho / (A * f)) ** (1 / n)
        lon = math.degrees(theta / n) + self.lam0
        p = math.pi / 2 - 2 * math.atan(t)
        for _ in range(20):
            s = math.sin(p)
            p_nouv = math.pi / 2 - 2 * math.atan(t * ((1 - E * s) / (1 + E * s)) ** (E / 2))
            if abs(p_nouv - p) < 1e-14:
                p = p_nouv
                break
            p = p_nouv
        return math.degrees(p), lon


LAMBERT93 = Lambert("Lambert-93", 2154, 46.5, 44.0, 49.0, 3.0, 700000.0, 6600000.0)
CC = {k: Lambert(f"CC{k}", 3900 + k, float(k), k - 0.75, k + 0.75, 3.0, 1700000.0, (k - 41) * 1000000.0 + 200000.0)
      for k in range(42, 51)}
_CONST = {p.epsg: p._constantes() for p in [LAMBERT93, *CC.values()]}


def zone_cc(lat: float) -> int:
    """Zone CC adaptée à une latitude (CC42 à CC50)."""
    return min(50, max(42, int(round(lat))))


# ---------------------------------------------------------------------------
# UTM (Mercator transverse, séries de Krüger à l'ordre 4 — Karney, 2011) pour l'outre-mer
# ---------------------------------------------------------------------------
_N = F / (2 - F)
_A_RECT = A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_ALPHA = (_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180,
          13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140,
          49561 * _N ** 4 / 161280)
_BETA = (_N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360,
         _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840,
         4397 * _N ** 4 / 161280)
_DELTA = (2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3 + 116 * _N ** 4 / 45,
          7 * _N ** 2 / 3 - 8 * _N ** 3 / 5 - 227 * _N ** 4 / 45,
          56 * _N ** 3 / 15 - 136 * _N ** 4 / 35,
          4279 * _N ** 4 / 630)
K0_UTM = 0.9996


@dataclass(frozen=True)
class UTM:
    nom: str
    epsg: int
    zone: int
    sud: bool = False

    @property
    def lam0(self) -> float:
        return math.radians(6 * self.zone - 183)

    def depuis_geo(self, lat: float, lon: float) -> tuple[float, float]:
        phi, dl = math.radians(lat), math.radians(lon) - self.lam0
        c = 2 * math.sqrt(_N) / (1 + _N)
        t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
        xi, eta = math.atan2(t, math.cos(dl)), math.atanh(math.sin(dl) / math.sqrt(1 + t * t))
        x = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
        y = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
        return 500000.0 + K0_UTM * _A_RECT * x, (10000000.0 if self.sud else 0.0) + K0_UTM * _A_RECT * y

    def vers_geo(self, x: float, y: float) -> tuple[float, float]:
        xi = (y - (10000000.0 if self.sud else 0.0)) / (K0_UTM * _A_RECT)
        eta = (x - 500000.0) / (K0_UTM * _A_RECT)
        xi1 = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
        eta1 = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
        chi = math.asin(math.sin(xi1) / math.cosh(eta1))
        phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
        return math.degrees(phi), math.degrees(self.lam0 + math.atan2(math.sinh(eta1), math.cos(xi1)))


# Systèmes légaux (décret n° 2000-1276 modifié) : Lambert-93 en métropole, UTM outre-mer.
TERRITOIRES = [
    # (nom, lat_min, lat_max, lon_min, lon_max, projection)
    ("Guadeloupe, Martinique, Saint-Martin, Saint-Barthélemy", 14.2, 18.2, -63.3, -60.7,
     UTM("RGAF09 / UTM 20N", 5490, 20)),
    ("Guyane", 2.0, 6.0, -54.8, -51.4, UTM("RGFG95 / UTM 22N", 2972, 22)),
    ("La Réunion", -21.5, -20.8, 55.1, 56.0, UTM("RGR92 / UTM 40S", 2975, 40, True)),
    ("Mayotte", -13.1, -12.5, 44.9, 45.4, UTM("RGM04 / UTM 38S", 4471, 38, True)),
    ("Saint-Pierre-et-Miquelon", 46.7, 47.2, -56.5, -56.0, UTM("RGSPM06 / UTM 21N", 4467, 21)),
]


def en_metropole(lat: float, lon: float) -> bool:
    """France métropolitaine et Corse (emprise de Lambert-93)."""
    return 41.3 <= lat <= 51.15 and -5.3 <= lon <= 9.65


@dataclass(frozen=True)
class CoordonneesLegales:
    nom: str             # « RGF93 Lambert-93 », « RGR92 / UTM 40S »…
    epsg: int
    x: float
    y: float


def systeme_legal(lat: float, lon: float) -> Optional[CoordonneesLegales]:
    """Coordonnées dans le système légal du territoire (None hors de France)."""
    if en_metropole(lat, lon):
        x, y = LAMBERT93.depuis_geo(lat, lon)
        return CoordonneesLegales("RGF93 Lambert-93", 2154, x, y)
    for _, la0, la1, lo0, lo1, proj in TERRITOIRES:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            x, y = proj.depuis_geo(lat, lon)
            return CoordonneesLegales(proj.nom, proj.epsg, x, y)
    return None


# ---------------------------------------------------------------------------
# Plan local (Est, Nord) en mètres autour d'un point
# ---------------------------------------------------------------------------
def rayons_courbure(lat: float) -> tuple[float, float]:
    """Rayons de courbure méridien M et grande normale N (m)."""
    s2 = math.sin(math.radians(lat)) ** 2
    w = math.sqrt(1 - E2 * s2)
    return A * (1 - E2) / w ** 3, A / w


def vers_local(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    m, n = rayons_courbure((lat0 + lat) / 2)
    return (math.radians(lon - lon0) * n * math.cos(math.radians((lat0 + lat) / 2)),
            math.radians(lat - lat0) * m)


def depuis_local(lat0: float, lon0: float, est: float, nord: float) -> tuple[float, float]:
    lat = lat0
    for _ in range(3):         # rayons évalués à la latitude moyenne
        m, n = rayons_courbure((lat0 + lat) / 2)
        lat = lat0 + math.degrees(nord / m)
    lon = lon0 + math.degrees(est / (n * math.cos(math.radians((lat0 + lat) / 2))))
    return lat, lon


def azimut(lat0: float, lon0: float, lat: float, lon: float) -> float:
    """Azimut géographique (degrés, sens horaire depuis le Nord) du point vu depuis (lat0, lon0)."""
    e, n = vers_local(lat0, lon0, lat, lon)
    return math.degrees(math.atan2(e, n)) % 360


def point_azimut(lat0: float, lon0: float, az: float, distance: float) -> tuple[float, float]:
    a = math.radians(az)
    return depuis_local(lat0, lon0, distance * math.sin(a), distance * math.cos(a))


# ---------------------------------------------------------------------------
# Pseudo-Mercator (EPSG:3857) et tuiles
# ---------------------------------------------------------------------------
def vers_mercator(lat: float, lon: float) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    return (R_MERCATOR * math.radians(lon),
            R_MERCATOR * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def depuis_mercator(x: float, y: float) -> tuple[float, float]:
    return (math.degrees(2 * math.atan(math.exp(y / R_MERCATOR)) - math.pi / 2),
            math.degrees(x / R_MERCATOR))


DEMI_MONDE = math.pi * R_MERCATOR


def taille_tuile(z: int) -> float:
    """Côté d'une tuile en mètres Mercator au niveau z."""
    return 2 * DEMI_MONDE / 2 ** z


def tuile_de(x: float, y: float, z: int) -> tuple[int, int]:
    t = taille_tuile(z)
    return int((x + DEMI_MONDE) // t), int((DEMI_MONDE - y) // t)


def coin_tuile(tx: int, ty: int, z: int) -> tuple[float, float]:
    """Coin nord-ouest de la tuile en mètres Mercator."""
    t = taille_tuile(z)
    return tx * t - DEMI_MONDE, DEMI_MONDE - ty * t


# ---------------------------------------------------------------------------
# Formats d'affichage et saisie
# ---------------------------------------------------------------------------
def dms(valeur: float, positif: str, negatif: str, decimales: int = 1) -> str:
    h = positif if valeur >= 0 else negatif
    v = abs(valeur)
    d = int(v)
    m_tot = (v - d) * 60
    m = int(m_tot)
    s = round((m_tot - m) * 60, decimales)
    if s >= 60:
        s -= 60
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return f"{d}°{m:02d}'{s:0{3 + decimales}.{decimales}f}\"{h}"


def format_dms(lat: float, lon: float) -> str:
    """Format de Google Maps : 43°48'58.1"N 2°10'11.2"E."""
    return f"{dms(lat, 'N', 'S')} {dms(lon, 'E', 'W')}"


_RE_DMS = re.compile(r"""(\d+(?:[.,]\d+)?)\s*[°º]\s*(?:(\d+(?:[.,]\d+)?)\s*['’′]\s*)?(?:(\d+(?:[.,]\d+)?)\s*(?:"|''|”|″)\s*)?
                         \s*([NSEOW])""", re.X | re.I)


def _f(t: Optional[str]) -> float:
    return float(t.replace(",", ".")) if t else 0.0


_RE_UTM = re.compile(r"^(?:UTM\s*(\d{1,2})\s*([NS])|EPSG\s*:?\s*(\d{4,5}))\b\s*[:;,]?\s*(.+)$", re.I)


def _saisie_utm(t: str) -> Optional[tuple[float, float, str]]:
    m = _RE_UTM.match(t)
    if m is None:
        return None
    if m.group(3):
        proj = next((p for *_, p in TERRITOIRES if p.epsg == int(m.group(3))), None)
        if proj is None:
            return None
    else:
        zone, sud = int(m.group(1)), m.group(2).upper() == "S"
        if not 1 <= zone <= 60:
            return None
        proj = next((p for *_, p in TERRITOIRES if p.zone == zone and p.sud == sud),
                    UTM(f"UTM {zone}{'S' if sud else 'N'}", 0, zone, sud))
    nombres = re.findall(r"\d+(?:[.,]\d+)?", m.group(4))
    if len(nombres) != 2:
        return None
    x, y = (float(v.replace(",", ".")) for v in nombres)
    if not 100_000 <= x <= 900_000 or not 0 <= y <= 10_000_000:
        return None
    lat, lon = proj.vers_geo(x, y)
    return lat, lon, proj.nom


def analyser_saisie(texte: str) -> Optional[tuple[float, float, str]]:
    """Reconnaît des coordonnées saisies ou collées. Retourne (lat, lon, système reconnu) ou None.

    Formats acceptés : degrés-minutes-secondes (43°48'58.1"N 2°10'11.2"E), degrés décimaux
    (43.8161, 2.1698), Lambert-93 (X Y en mètres), coniques conformes CC42 à CC50 et, outre-mer,
    UTM avec la zone ou le code EPSG en tête (« UTM 40S 340000 7650000 », « EPSG:2975 340000 7650000 »).
    """
    t = texte.strip()
    if _RE_UTM.match(t):                  # zone UTM ou code EPSG annoncé : pas d'autre lecture possible
        return _saisie_utm(t)
    m = list(_RE_DMS.finditer(t))
    if len(m) >= 2:
        vals = {}
        for g in m[:2]:
            v = _f(g.group(1)) + _f(g.group(2)) / 60 + _f(g.group(3)) / 3600
            h = g.group(4).upper()
            if h in "SOW":
                v = -v
            vals["lat" if h in "NS" else "lon"] = v
        if "lat" in vals and "lon" in vals:
            return vals["lat"], vals["lon"], "WGS84 (DMS)"
    nombres = re.findall(r"-?\d+(?:[.,]\d+)?", t.replace(" ", " "))
    if len(nombres) == 4 and "," in t and "." not in t:            # 43,8161 2,1698
        nombres = [f"{nombres[0]}.{nombres[1]}", f"{nombres[2]}.{nombres[3]}"]
    if len(nombres) != 2:
        return None
    a, b = (float(v.replace(",", ".")) for v in nombres)
    if abs(a) <= 90 and abs(b) <= 180:
        return a, b, "WGS84 (degrés décimaux)"
    if 50_000 < a < 1_400_000 and 6_000_000 < b < 7_200_000:
        lat, lon = LAMBERT93.vers_geo(a, b)
        return lat, lon, "Lambert-93"
    if 1_000_000 < a < 2_500_000 and 1_000_000 < b < 9_300_000:
        zone = int(round((b - 200_000) / 1_000_000)) + 41
        if zone in CC:
            lat, lon = CC[zone].vers_geo(a, b)
            return lat, lon, f"CC{zone}"
    return None


# ---------------------------------------------------------------------------
# Ajouts Giration : systèmes de coordonnées des plans, détection et altération linéaire
# ---------------------------------------------------------------------------
SYSTEMES = {"L93": LAMBERT93, **{f"CC{k}": p for k, p in CC.items()}}


def facteur_echelle(proj: Lambert, lat: float) -> float:
    """Facteur d'échelle ponctuel k de la projection conique conforme à la latitude lat.

    Une longueur vraie de 1 m vaut k mètres en coordonnées projetées (altération linéaire k − 1).
    """
    n, f, _ = _CONST[proj.epsg]
    p = math.radians(lat)
    s = math.sin(p)
    t = math.tan(math.pi / 4 - p / 2) / ((1 - E * s) / (1 + E * s)) ** (E / 2)
    rho = A * f * t ** n
    m = math.cos(p) / math.sqrt(1 - E2 * s * s)
    return n * rho / (A * m)


def detecter_systeme(x: float, y: float) -> Optional[str]:
    """Système probable d'un plan d'après ses coordonnées : « L93 », « CC42 »… « CC50 », ou None (plan local)."""
    if 50_000 < x < 1_400_000 and 6_000_000 < y < 7_200_000:
        return "L93"
    if 1_000_000 < x < 2_500_000 and 1_000_000 < y < 9_300_000:
        zone = int(round((y - 200_000) / 1_000_000)) + 41
        if zone in CC:
            return f"CC{zone}"
    return None
