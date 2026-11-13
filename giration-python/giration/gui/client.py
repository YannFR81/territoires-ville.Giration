"""Accès réseau (QtNetwork) : tuiles et services IGN, avec cache disque et nouvelles tentatives."""
from __future__ import annotations

import json
from typing import Callable, Optional

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkDiskCache, QNetworkProxyFactory, QNetworkReply,
                               QNetworkRequest)

from ..carto.geoservices import AGENT

ESSAIS = 3


def creer_gestionnaire(parent: Optional[QObject] = None) -> QNetworkAccessManager:
    """Gestionnaire réseau : proxy du système (réseau de la collectivité), cache disque de 300 Mo."""
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    nam = QNetworkAccessManager(parent)
    cache = QNetworkDiskCache(nam)
    dossier = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
    cache.setCacheDirectory((dossier or ".") + "/giration_carto")
    cache.setMaximumCacheSize(300 * 1024 * 1024)
    nam.setCache(cache)
    return nam


def requete(url: str) -> QNetworkRequest:
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", AGENT.encode("ascii", "replace"))
    req.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.PreferCache)
    req.setTransferTimeout(20000)
    return req


def delai_reessai(rep: QNetworkReply) -> float:
    """Durée d'attente (s) demandée par le serveur après un refus HTTP 429 (5 s par défaut)."""
    try:
        return min(30.0, max(1.0, float(bytes(rep.rawHeader(b"Retry-After")).decode() or 5)))
    except ValueError:
        return 5.0


class ClientIGN(QObject):
    """Requêtes JSON asynchrones ; le rappel reçoit (données, message d'erreur)."""

    def __init__(self, nam: QNetworkAccessManager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.nam = nam

    def obtenir_json(self, url: str, rappel: Callable[[Optional[dict], str], None], essai: int = 1) -> None:
        rep = self.nam.get(requete(url))

        def fin():
            erreur = rep.error()
            statut = rep.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            donnees = bytes(rep.readAll())
            rep.deleteLater()
            if erreur != QNetworkReply.NoError or (statut and int(statut) >= 400):
                if essai < ESSAIS and statut is not None and int(statut) == 429:
                    # Limite de débit de la Géoplateforme : blocage de 5 s, durée restante dans Retry-After
                    attente = delai_reessai(rep)
                    QTimer.singleShot(int(attente * 1000), lambda: self.obtenir_json(url, rappel, essai + 1))
                    return
                if essai < ESSAIS and (statut is None or int(statut) >= 500):
                    # Panne passagère : nouvel essai après 1 s, puis 2 s (sans insister auprès du serveur)
                    QTimer.singleShot(1000 * essai, lambda: self.obtenir_json(url, rappel, essai + 1))
                    return
                rappel(None, rep.errorString() if erreur != QNetworkReply.NoError else f"HTTP {statut}")
                return
            try:
                rappel(json.loads(donnees.decode("utf-8")), "")
            except (ValueError, UnicodeDecodeError) as exc:
                rappel(None, f"réponse illisible ({exc})")

        rep.finished.connect(fin)
