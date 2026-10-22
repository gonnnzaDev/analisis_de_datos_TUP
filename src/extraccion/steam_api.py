"""
Extracción de datos desde las interfaces públicas de Steam (no requieren API key).

Fuentes usadas:
1. Buscador de la tienda (store.steampowered.com/search/results):
   lista de appids de la categoría "juegos" (category1=998), paginada de a 100.
2. Ficha de cada app (store.steampowered.com/api/appdetails):
   nombre, tipo (game/dlc/software/...), precio, descuento, géneros, categorías,
   plataformas, fecha de lanzamiento, desarrolladores, logros, etc.
3. Jugadores conectados en este momento
   (api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers).
4. Ranking semanal de más jugados (ISteamChartsService/GetMostPlayedGames).
5. Resumen de reseñas de usuarios (store.steampowered.com/appreviews/<appid>).

Todo se guarda crudo en data/crudo/steam_api/ para poder reprocesar sin volver a pedir.
"""

from __future__ import annotations

import math
import re

import requests
from tqdm import tqdm

from .. import config
from ..almacenamiento.guardar import ahora_iso, guardar_json, guardar_texto
from ..http_util import LIMITADOR_API, LIMITADOR_STORE, crear_sesion, descargar

BUSCADOR = "https://store.steampowered.com/search/results/"
APPDETAILS = "https://store.steampowered.com/api/appdetails"
APPRESENAS = "https://store.steampowered.com/appreviews/{appid}"
JUGADORES = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
MAS_JUGADOS = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"

PATRON_APPID = re.compile(r'data-ds-appid="(\d+)')


def buscar_appids(
    sesion: requests.Session,
    cantidad: int,
    inicio: int = 0,
    orden: str = "Released_DESC",
    categoria: str = "998",
) -> tuple[list[int], list[str]]:
    """
    Devuelve hasta `cantidad` appids de JUEGOS desde el buscador de la tienda.

    El filtro category1=998 deja únicamente juegos (excluye DLC, software,
    demos, videos, bandas sonoras, etc.). Devuelve también el HTML de cada página
    de resultados para guardarlo como evidencia cruda y para que lo procese la IA.
    """
    appids: list[int] = []
    paginas_html: list[str] = []
    paginas = max(1, math.ceil(cantidad / 100))

    for pagina in range(paginas):
        parametros = {
            "query": "",
            "start": inicio + pagina * 100,
            "count": 100,
            "infinite": 1,
            "category1": categoria,
            "sort_by": orden,
            "cc": config.PAIS,
            "l": config.IDIOMA,
        }
        respuesta = descargar(sesion, BUSCADOR, params=parametros, limitador=LIMITADOR_STORE)
        respuesta.raise_for_status()
        datos = respuesta.json()
        if datos.get("success") != 1:
            raise RuntimeError("El buscador de Steam no devolvió resultados válidos.")
        html = datos.get("results_html", "")
        paginas_html.append(html)
        for coincidencia in PATRON_APPID.findall(html):
            appids.append(int(coincidencia))

    # Sacar repetidos preservando el orden original
    vistos: set[int] = set()
    unicos = [appid for appid in appids if not (appid in vistos or vistos.add(appid))]
    return unicos[:cantidad], paginas_html


def obtener_appdetails(sesion: requests.Session, appid: int) -> dict | None:
    """Pide la ficha completa de una app. Devuelve None si Steam no la tiene."""
    parametros = {"appids": appid, "cc": config.PAIS, "l": config.IDIOMA}
    respuesta = descargar(sesion, APPDETAILS, params=parametros, limitador=LIMITADOR_STORE)
    if respuesta.status_code != 200:
        return None
    try:
        contenido = respuesta.json()
    except ValueError:
        return None
    bloque = contenido.get(str(appid)) or {}
    if not bloque.get("success"):
        return None
    return bloque.get("data")


def obtener_jugadores_actuales(sesion: requests.Session, appid: int) -> int | None:
    """Cantidad de personas jugando en este momento."""
    parametros = {"appid": appid}
    respuesta = descargar(sesion, JUGADORES, params=parametros, limitador=LIMITADOR_API)
    if respuesta.status_code != 200:
        return None
    try:
        respuesta_json = respuesta.json()
    except ValueError:
        return None
    return (respuesta_json.get("response") or {}).get("player_count")


def obtener_resumen_resenas(sesion: requests.Session, appid: int) -> dict | None:
    """Resumen de reseñas de usuarios (total, positivas, negativas)."""
    parametros = {
        "json": 1,
        "num_per_page": 0,
        "language": "all",
        "purchase_type": "all",
    }
    url = APPRESENAS.format(appid=appid)
    respuesta = descargar(sesion, url, params=parametros, limitador=LIMITADOR_STORE)
    if respuesta.status_code != 200:
        return None
    try:
        contenido = respuesta.json()
    except ValueError:
        return None
    if contenido.get("success") != 1:
        return None
    return contenido.get("query_summary")


def total_juegos_en_tienda() -> int:
    """Cantidad total de juegos (categoría 998) que reporta el buscador de la tienda."""
    sesion = crear_sesion()
    parametros = {
        "query": "",
        "start": 0,
        "count": 100,
        "infinite": 1,
        "category1": "998",
        "sort_by": "Released_DESC",
        "cc": config.PAIS,
        "l": config.IDIOMA,
    }
    respuesta = descargar(sesion, BUSCADOR, params=parametros, limitador=LIMITADOR_STORE)
    respuesta.raise_for_status()
    return int(respuesta.json().get("total_count") or 0)


def obtener_mas_jugados(sesion: requests.Session) -> dict[int, dict]:
    """Ranking semanal de más jugados: appid -> {rank, pico}". Se pide una vez por corrida."""
    respuesta = descargar(sesion, MAS_JUGADOS, limitador=LIMITADOR_API)
    if respuesta.status_code != 200:
        return {}
    try:
        ranks = (respuesta.json().get("response") or {}).get("ranks") or []
    except ValueError:
        return {}
    resultado = {}
    for fila in ranks:
        appid = fila.get("appid")
        if appid:
            resultado[int(appid)] = {
                "rank": fila.get("rank"),
                "pico_en_juego": fila.get("peak_in_game"),
            }
    return resultado


def extraer_lote(
    lote: int,
    cantidad: int | None = None,
    con_resenas: bool = True,
    con_jugadores: bool = True,
    orden: str = "Released_DESC",
) -> dict:
    """
    Extrae un lote completo de juegos desde la API pública de Steam.

    El lote N empieza en la posición (N-1)*cantidad del listado del buscador,
    así los lotes no se pisan entre sí. Guarda todo crudo en data/crudo/steam_api/.
    """
    cantidad = cantidad or config.LOTE_TAMANO
    inicio = (lote - 1) * cantidad
    dos_digitos = f"{lote:02d}"
    sesion = crear_sesion()
    resumen: dict = {
        "lote": lote,
        "cantidad_solicitada": cantidad,
        "inicio_en_buscador": inicio,
        "orden_buscador": orden,
        "con_resenas": con_resenas,
        "fecha_inicio": ahora_iso(),
    }

    print(f"[extraer] Lote {lote}: buscando {cantidad} juegos (inicio={inicio}, orden={orden})...")
    appids, paginas_html = buscar_appids(sesion, cantidad, inicio=inicio, orden=orden)
    for numero, html in enumerate(paginas_html, start=1):
        guardar_texto(html, config.CRUDO_API / f"lote_{dos_digitos}_busqueda_pagina_{numero}.html")
    guardar_json(
        {
            "lote": lote,
            "inicio": inicio,
            "cantidad": cantidad,
            "orden": orden,
            "appids": appids,
            "generado": ahora_iso(),
        },
        config.CRUDO_API / f"lote_{dos_digitos}_pool.json",
    )
    print(f"[extraer] {len(appids)} appids encontrados. Descargando fichas, jugadores y reseñas...")

    detalles: dict = {}
    jugadores: dict = {}
    resenas: dict = {}
    fallos: list[dict] = []
    tiempo_inicio = ahora_iso()

    for appid in tqdm(appids, desc=f"lote {lote}", unit="juego"):
        datos = obtener_appdetails(sesion, appid)
        if datos is None:
            fallos.append({"appid": appid, "motivo": "Steam no devolvió ficha (app retirada o sin página)"})
            continue
        detalles[str(appid)] = datos
        jugadores[str(appid)] = obtener_jugadores_actuales(sesion, appid) if con_jugadores else None
        if con_resenas:
            resenas[str(appid)] = obtener_resumen_resenas(sesion, appid)

    mas_jugados = obtener_mas_jugados(sesion)
    guardar_json(
        {"fecha_registro": ahora_iso(), "ranks": mas_jugados},
        config.CRUDO / "mas_jugados.json",
    )
    guardar_json(detalles, config.CRUDO_API / f"lote_{dos_digitos}_appdetails.json")
    guardar_json(jugadores, config.CRUDO_API / f"lote_{dos_digitos}_jugadores.json")
    if con_resenas:
        guardar_json(resenas, config.CRUDO_API / f"lote_{dos_digitos}_resenas.json")

    tipos = {}
    for datos in detalles.values():
        tipo = (datos.get("type") or "desconocido").lower()
        tipos[tipo] = tipos.get(tipo, 0) + 1

    resumen.update(
        {
            "fecha_fin": ahora_iso(),
            "appids_pedidos": len(appids),
            "fichas_ok": len(detalles),
            "fallos": len(fallos),
            "detalle_fallos": fallos,
            "tipos_encontrados": tipos,
            "con_jugadores": sum(1 for valor in jugadores.values() if valor is not None),
            "con_resenas": sum(1 for valor in resenas.values() if valor),
            "ranks_mas_jugados": len(mas_jugados),
        }
    )
    guardar_json(resumen, config.CRUDO_API / f"lote_{dos_digitos}_meta.json")

    print(
        f"[extraer] Lote {lote} listo: {len(detalles)} fichas OK, {len(fallos)} fallos, "
        f"tipos={tipos} (inicio {tiempo_inicio}, fin {ahora_iso()})"
    )
    return resumen
