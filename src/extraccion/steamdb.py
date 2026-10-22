"""
Extracción desde SteamDB (https://steamdb.info).

SteamDB está protegido por Cloudflare, así que este módulo intenta dos caminos:
1. Pedido directo con requests (muy rápido). Si Cloudflare lo bloquea,
2. Navegador Firefox automatizado con Playwright (más lento, pero suele pasar el control).

El HTML y el texto visible que se obtengan se guardan en data/crudo/steamdb/
y después el módulo de IA (src/ia/estructurador.py) los convierte en filas
estructuradas, dejando únicamente los juegos.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .. import config
from ..almacenamiento.guardar import ahora_iso, guardar_json, guardar_texto
from ..http_util import LIMITADOR_STORE, crear_sesion, descargar, es_bloqueo_cloudflare

URIS_POR_DEFECTO = [
    "https://steamdb.info/charts/",  # juegos con más jugadores
]


def _slug(uri: str) -> str:
    """Convierte una URL en un nombre de archivo simple."""
    limpio = re.sub(r"^https?://", "", uri)
    limpio = re.sub(r"[^A-Za-z0-9]+", "_", limpio).strip("_").lower()
    return limpio[:80] or "pagina"


def descargar_con_requests(uri: str) -> dict:
    """Intento rápido: pedido directo. Devuelve dict con estado y HTML (o motivo del bloqueo)."""
    sesion = crear_sesion()
    try:
        respuesta = descargar(sesion, uri, limitador=LIMITADOR_STORE)
    except Exception as error:  # noqa: BLE001 - queremos seguir con el otro camino
        return {"ok": False, "html": "", "motivo": f"error de red: {error}"}
    if es_bloqueo_cloudflare(respuesta):
        return {"ok": False, "html": respuesta.text, "motivo": f"Cloudflare bloqueó el pedido (HTTP {respuesta.status_code})"}
    if respuesta.status_code != 200:
        return {"ok": False, "html": respuesta.text, "motivo": f"HTTP {respuesta.status_code}"}
    return {"ok": True, "html": respuesta.text, "motivo": ""}


def descargar_con_navegador(uri: str) -> dict:
    """
    Intento con navegador real (Playwright + Firefox). Si Cloudflare muestra el
    control "Un momento...", espera hasta STEAMDB_ESPERA_NAVEGADOR segundos a que pase.
    Devuelve HTML renderizado y texto visible.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "html": "",
            "texto": "",
            "motivo": "Playwright no está instalado (uv pip install playwright && playwright install firefox)",
        }

    try:
        with sync_playwright() as pw:
            navegador = pw.firefox.launch(headless=not config.STEAMDB_NAVEGADOR_VISIBLE)
            contexto = navegador.new_context(
                locale="es-AR",
                viewport={"width": 1366, "height": 900},
            )
            pagina = contexto.new_page()
            pagina.goto(uri, wait_until="domcontentloaded", timeout=90_000)

            html = ""
            for _ in range(config.STEAMDB_ESPERA_NAVEGADOR):
                time.sleep(1)
                html = pagina.content()
                titulo = (pagina.title() or "").lower()
                bloqueado = "just a moment" in titulo or "attention required" in titulo
                if not bloqueado and len(html) > 5_000:
                    break

            html = pagina.content()
            texto = pagina.inner_text("body")
            navegador.close()
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "html": "", "texto": "", "motivo": f"falló el navegador: {error}"}

    titulo = ""
    coincidencia = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if coincidencia:
        titulo = coincidencia.group(1).strip().lower()
    if "just a moment" in titulo or "attention required" in titulo:
        return {"ok": False, "html": html, "texto": texto, "motivo": "Cloudflare no dejó pasar ni con navegador"}
    if len(html) < 2_000:
        return {"ok": False, "html": html, "texto": texto, "motivo": "la página llegó vacía"}

    return {"ok": True, "html": html, "texto": texto, "motivo": ""}


def descargar_uri(lote: int, uri: str, indice: int = 1) -> dict:
    """
    Descarga una URI de SteamDB probando requests y, si hace falta, el navegador.
    Guarda el resultado en data/crudo/steamdb/.
    """
    dos_digitos = f"{lote:02d}"
    nombre = f"lote_{dos_digitos}_steamdb{'' if indice == 1 else f'_{indice}'}"
    registro: dict = {"lote": lote, "uri": uri, "fecha": ahora_iso()}

    print(f"[steamdb] Probando pedido directo: {uri}")
    intento = descargar_con_requests(uri)
    metodo = "requests"

    if not intento["ok"]:
        print(f"[steamdb] Bloqueado ({intento['motivo']}).", end=" ")
        guardar_texto(
            intento.get("html", ""),
            config.CRUDO_STEAMDB / f"{nombre}_bloqueo_cloudflare.html",
        )
        if not config.STEAMDB_USAR_NAVEGADOR:
            registro.update({"metodo": metodo, "ok": False, "motivo": intento["motivo"]})
            guardar_json(registro, config.CRUDO_STEAMDB / f"{nombre}_meta.json")
            return registro
        print("Probando con navegador Firefox automatizado (puede abrirse una ventana; es normal)...")
        intento = descargar_con_navegador(uri)
        metodo = "navegador"

    if intento["ok"]:
        guardar_texto(intento["html"], config.CRUDO_STEAMDB / f"{nombre}.html")
        texto = intento.get("texto") or ""
        if not texto:
            # Si vino por requests, no hay texto visible: aceptamos el HTML igual,
            # el módulo de IA trabaja sobre texto, así que solo lo guardamos si hay texto.
            pass
        if texto:
            guardar_texto(texto, config.CRUDO_STEAMDB / f"{nombre}.txt")
        registro.update({"metodo": metodo, "ok": True, "archivo_html": f"{nombre}.html",
                         "archivo_texto": f"{nombre}.txt" if texto else None,
                         "caracteres_html": len(intento["html"]), "caracteres_texto": len(texto)})
        print(f"[steamdb] OK por {metodo}: {len(intento['html'])} caracteres de HTML, "
              f"{len(texto)} de texto visible.")
    else:
        registro.update({"metodo": metodo, "ok": False, "motivo": intento["motivo"]})
        print(f"[steamdb] No se pudo obtener {uri}: {intento['motivo']}")

    guardar_json(registro, config.CRUDO_STEAMDB / f"{nombre}_meta.json")
    return registro


def descargar_lote(lote: int, uris: list[str] | None = None) -> list[dict]:
    """Descarga varias páginas de SteamDB para un lote y resume el resultado."""
    uris = uris or URIS_POR_DEFECTO
    resultados = [descargar_uri(lote, uri, indice) for indice, uri in enumerate(uris, start=1)]
    exitos = sum(1 for resultado in resultados if resultado.get("ok"))
    print(f"[steamdb] Lote {lote}: {exitos}/{len(resultados)} páginas obtenidas.")
    if exitos == 0:
        print(
            "[steamdb] Aviso: SteamDB no entregó ninguna página (bloqueo de Cloudflare). "
            "El pipeline sigue funcionando con los datos de la API de Steam; "
            "dejá la ventana del navegador abierta y volvé a intentar más tarde."
        )
    return resultados
