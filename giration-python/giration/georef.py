"""Géoréférencement d'un plan : du système du plan (Lambert-93, CC42 à CC50, ou plan local) vers WGS84."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .projections import SYSTEMES, depuis_local, facteur_echelle, vers_local


@dataclass
class Georef:
    systeme: Optional[str] = None                      # « L93 », « CC44 »… ; None = plan local
    origine: Optional[tuple[float, float]] = None      # (lat, lon) du point (0, 0) d'un plan local orienté au Nord

    @property
    def georeference(self) -> bool:
        return self.systeme is not None or self.origine is not None

    def nom(self) -> str:
        if self.systeme == "L93":
            return "RGF93 / Lambert-93 (EPSG:2154)"
        if self.systeme:
            return f"RGF93 / {self.systeme} (EPSG:{SYSTEMES[self.systeme].epsg})"
        if self.origine:
            return f"plan local orienté au Nord, origine {self.origine[0]:.7f}° N, {self.origine[1]:.7f}° E"
        return "plan local non géoréférencé"

    def vers_geo(self, x: float, y: float) -> tuple[float, float]:
        """(lat, lon) d'un point du plan."""
        if self.systeme:
            return SYSTEMES[self.systeme].vers_geo(x, y)
        if self.origine:
            return depuis_local(self.origine[0], self.origine[1], x, y)
        raise ValueError("Plan non géoréférencé : indiquez le système (L93, CC42…CC50) ou une origine.")

    def depuis_geo(self, lat: float, lon: float) -> tuple[float, float]:
        if self.systeme:
            return SYSTEMES[self.systeme].depuis_geo(lat, lon)
        if self.origine:
            return vers_local(self.origine[0], self.origine[1], lat, lon)
        raise ValueError("Plan non géoréférencé.")

    def lonlat(self, pts) -> list[tuple[float, float]]:
        res = []
        for p in pts:
            lat, lon = self.vers_geo(float(p[0]), float(p[1]))
            res.append((lon, lat))
        return res

    def echelle(self, x: float, y: float) -> float:
        """Facteur d'échelle de la projection au point (1 pour un plan local)."""
        if not self.systeme:
            return 1.0
        lat, _ = self.vers_geo(x, y)
        return facteur_echelle(SYSTEMES[self.systeme], lat)
