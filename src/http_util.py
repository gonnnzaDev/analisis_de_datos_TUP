"""
Utilidades HTTP compartidas por los módulos de extracción:
sesión con cabeceras de navegador y limitador de velocidad entre pedidos.
"""

from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}


def crear_sesion() -> requests.Session:
    """Sesión de requests con reintentos automáticos y cabeceras de navegador."""
    sesion = requests.Session()
    sesion.headers.update(CABECERAS)
    reintentos = Retry(
        total=config.HTTP_REINTENTOS,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    adaptador = HTTPAdapter(max_retries=reintentos, pool_connections=8, pool_maxsize=8)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    return sesion


class Limitador:
    """Fuerza una pausa mínima entre pedidos para no pasarse de los límites de Steam."""

    def __init__(self, intervalo: float) -> None:
        self.intervalo = max(intervalo, 0.0)
        self._ultimo = 0.0

    def esperar(self) -> None:
        espera = self.intervalo - (time.monotonic() - self._ultimo)
        if espera > 0:
            time.sleep(espera)
        self._ultimo = time.monotonic()


# Un limitador por host: la tienda es más restrictiva que api.steampowered.com
LIMITADOR_STORE = Limitador(config.PAUSA_STORE)
LIMITADOR_API = Limitador(config.PAUSA_API)


def es_bloqueo_cloudflare(respuesta: requests.Response) -> bool:
    """Detecta si Cloudflare cortó el pedido (pasa con SteamDB)."""
    if respuesta.status_code in (403, 429, 503):
        return True
    cuerpo = (respuesta.text or "").lower()
    señales = ("just a moment", "cf-chl", "challenge-platform", "__cf_chl", "cf-mitigated")
    return any(señal in cuerpo for señal in señales)


def descargar(
    sesion: requests.Session,
    url: str,
    params: dict | None = None,
    limitador: Limitador = LIMITADOR_STORE,
    timeout: int | None = None,
) -> requests.Response:
    """GET con pausa previa. Devuelve la respuesta completa; el llamador decide qué hacer."""
    limitador.esperar()
    return sesion.get(url, params=params, timeout=timeout or config.HTTP_TIMEOUT)
