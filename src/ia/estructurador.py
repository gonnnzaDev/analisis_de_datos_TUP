"""
Módulo de IA: convierte texto crudo en datos estructurados usando el modelo
local de Ollama. Trabaja con tres fuentes:
1. páginas de SteamDB;
2. HTML del buscador de la tienda de Steam;
3. datos externos aceptados por la ingesta (PDFs, imágenes, URLs, texto...).

Reglas del flujo:
- La IA recibe fragmentos de texto de la fuente y devuelve JSON con items y,
  para los datos externos, también cifras de mercado que no son filas de juegos.
- Cada item se clasifica por tipo (juego, dlc, demo, software, banda sonora, video...).
- SOLO los items de tipo "juego" pasan al dataset final.
- Los no-juegos se guardan en data/descartado/ con el motivo, para el informe.
- Cada fragmento procesado se guarda en un archivo parcial: si se corta la corrida,
  al volver a ejecutar se retoma donde quedó (importante con CPU, que es lento).
- Los datos externos se procesan una sola vez y se guardan en ia_externa.json;
  la consolidación los usa para enriquecer juegos y para armar datos_externos.csv.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .. import config
from ..almacenamiento.guardar import (
    ahora_iso,
    guardar_csv,
    guardar_json,
    leer_json,
    registrar_en_jsonl,
)
from .ollama_cliente import ErrorIA, pedir_json

# ---------------------------------------------------------------------------
# Prompt del sistema: esquema fijo + regla de "solo juegos"
# ---------------------------------------------------------------------------
PROMPT_SISTEMA = """\
Sos un asistente que convierte texto de páginas web sobre Steam (SteamDB y la tienda de Steam)
en datos estructurados. Trabajás en español.

Devolvés SIEMPRE un único objeto JSON válido, sin explicaciones ni bloques de código.

Forma exacta del JSON:
{"items": [ ... ]}

Cada item representa UNA aplicación de Steam y tiene estos campos:
- "nombre": texto (obligatorio)
- "appid": número entero o null si no aparece en el texto
- "tipo": uno de: "juego", "dlc", "demo", "software", "banda_sonora", "video", "herramienta", "desconocido"
- "generos": lista de textos (puede ser vacía)
- "precio": número con el precio final en la moneda que muestre la página (ojo: Steam Argentina muestra dólares), o null
- "descuento_pct": número entero de 0 a 100, o null
- "fecha_lanzamiento": "AAAA-MM-DD", "AAAA-MM" o "AAAA" según la precisión del texto, o null
- "plataformas": lista con "windows", "mac" y/o "linux" (puede ser vacía)
- "jugadores_actuales": número o null (jugadores conectados en este momento)
- "pico_hoy": número con el pico de las últimas 24 horas o null
- "pico_historico": número con el pico histórico (all-time) o null
- "rank_steamdb": número entero con la posición en la tabla de más jugados, o null
- "resenas_totales": número o null
- "positivas_pct": número de 0 a 100 o null
- "notas": texto corto o ""

Reglas:
1. Clasificá "tipo" con cuidado: los DLC, demos, programas, videos, bandas sonoras y
   herramientas NO son juegos. Solo las aplicaciones de juego llevan "juego".
2. No inventes datos: si un campo no está en el texto, usá null o lista vacía.
3. No repitas items: si la misma aplicación aparece dos veces, devolvela una sola vez.
4. No agregues campos fuera de la lista.
"""


PROMPT_EXTERNA = """\
Sos un asistente que incorpora datos externos al análisis del mercado de
videojuegos de Steam. Los datos pueden venir de PDFs, artículos, tablas,
capturas, páginas web o texto pegado. Trabajás en español.

Devolvés SIEMPRE un único objeto JSON válido, sin explicaciones ni bloques de código.

Forma exacta del JSON:
{"items": [ ... ], "datos": [ ... ]}

"items" contiene SOLO aplicaciones de Steam que aparezcan con datos concretos.
Cada item tiene los mismos campos que usa el pipeline:
- "nombre": texto (obligatorio)
- "appid": número entero o null si no aparece
- "tipo": "juego", "dlc", "demo", "software", "banda_sonora", "video", "herramienta" o "desconocido"
- "generos": lista de textos
- "precio": número o null
- "descuento_pct": entero de 0 a 100 o null
- "fecha_lanzamiento": "AAAA-MM-DD", "AAAA-MM", "AAAA" o null
- "plataformas": lista con "windows", "mac" y/o "linux"
- "jugadores_actuales": número o null
- "pico_hoy": número o null
- "pico_historico": número o null
- "rank_steamdb": número o null
- "resenas_totales": número o null
- "positivas_pct": número de 0 a 100 o null
- "notas": texto corto

"datos" contiene cifras o hechos que no encajan en un item, por ejemplo totales
del mercado, evolución anual, promedios o conclusiones con números:
- "tipo": "precios" | "resenas" | "jugadores" | "generos" | "fechas" | "mercado" | "mixto" | "otro"
- "juego": nombre del juego si el dato es de un juego, o ""
- "appid": número entero o null
- "metrica": nombre corto de la métrica (ej. "juegos_lanzados", "precio_promedio")
- "valor": número o texto corto
- "unidad": "USD", "%", "jugadores", "juegos", etc., o ""
- "fecha": "AAAA-MM-DD", "AAAA-MM" o "AAAA" si corresponde, o null
- "detalle": texto breve con el contexto exacto

Reglas:
1. No inventes datos: solo lo que esté en el texto.
2. No repitas items ni datos.
3. Si el texto no contiene juegos ni cifras de mercado, devolvé {"items": [], "datos": []}.
4. No agregues campos fuera de los indicados.
"""


# ---------------------------------------------------------------------------
# Utilidades de normalización
# ---------------------------------------------------------------------------

TIPOS_VALIDOS = {
    "juego", "dlc", "demo", "software", "banda_sonora", "video", "herramienta", "desconocido",
}

TIPOS_DATO_EXTERNO = {
    "precios", "resenas", "jugadores", "generos", "fechas", "mercado", "mixto", "otro",
}

SINONIMOS_DATO_EXTERNO = {
    "precio": "precios",
    "resena": "resenas",
    "reseña": "resenas",
    "jugador": "jugadores",
    "genero": "generos",
    "fecha": "fechas",
}

SINONIMOS_TIPO = {
    "game": "juego", "games": "juego", "juego": "juego", "juegos": "juego",
    "dlc": "dlc", "expansión": "dlc", "expansion": "dlc", "contenido descargable": "dlc",
    "demo": "demo",
    "software": "software", "programa": "software", "aplicación": "software", "aplicacion": "software",
    "soundtrack": "banda_sonora", "banda sonora": "banda_sonora", "música": "banda_sonora", "musica": "banda_sonora",
    "video": "video", "vídeo": "video",
    "tool": "herramienta", "herramienta": "herramienta", "utilidad": "herramienta",
}


def _sin_acentos(texto: str) -> str:
    return "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )


def _normalizar_tipo(valor) -> str:
    if not isinstance(valor, str):
        return "desconocido"
    clave = _sin_acentos(valor.strip().lower())
    return SINONIMOS_TIPO.get(clave, clave if clave in TIPOS_VALIDOS else "desconocido")


def _a_numero(valor) -> float | None:
    """Convierte '1.234,56', '1,234.56', '1.2M', '3 mil' a número. None si no se puede."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if not isinstance(valor, str):
        return None
    limpio = valor.strip().lower().replace("ars$", "").replace("$", "").replace("ars", "").strip()
    multiplicador = 1.0
    if limpio.endswith(("k", "mil")):
        multiplicador = 1_000.0
        limpio = limpio.rstrip("k ").replace("mil", "").strip()
    elif limpio.endswith(("m", "millon", "millón")):
        multiplicador = 1_000_000.0
        limpio = limpio.rstrip("m ").replace("millon", "").replace("millón", "").strip()
    # Decidir separadores: el último separador es decimal si deja 1-2 dígitos
    limpio = re.sub(r"[^\d.,]", "", limpio)
    if not limpio:
        return None
    if "," in limpio and "." in limpio:
        if limpio.rfind(",") > limpio.rfind("."):
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    elif "," in limpio:
        partes = limpio.split(",")
        if len(partes[-1]) in (1, 2):
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    try:
        return float(limpio) * multiplicador
    except ValueError:
        return None


MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _normalizar_fecha(valor) -> str | None:
    """Normaliza fechas al formato AAAA[-MM[-DD]]; None si no se puede interpretar."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        valor = str(valor)
    texto = _sin_acentos(valor.lower().replace(",", " ")).strip()
    if not texto or any(palabra in texto for palabra in ("pronto", "soon", "tba", "anunciar", "unknown")):
        return None

    # AAAA-MM-DD ya normalizado
    coincidencia = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if coincidencia:
        return texto
    # dd mmm aaaa
    coincidencia = re.match(r"^(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})$", texto)
    if coincidencia and coincidencia.group(2)[:3] in MESES:
        mes = MESES[coincidencia.group(2)[:3]]
        return f"{int(coincidencia.group(3)):04d}-{mes:02d}-{int(coincidencia.group(1)):02d}"
    # mmm aaaa
    coincidencia = re.match(r"^([a-z]+)\.?\s+(\d{4})$", texto)
    if coincidencia and coincidencia.group(1)[:3] in MESES:
        mes = MESES[coincidencia.group(1)[:3]]
        return f"{int(coincidencia.group(2)):04d}-{mes:02d}"
    # dd/mm/aaaa
    coincidencia = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    if coincidencia:
        return f"{int(coincidencia.group(3)):04d}-{int(coincidencia.group(2)):02d}-{int(coincidencia.group(1)):02d}"
    # solo aaaa
    coincidencia = re.fullmatch(r"(\d{4})", texto)
    if coincidencia:
        return coincidencia.group(1)
    return None


def _limpiar_lista(valor) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        partes = re.split(r"[|,;/]", valor)
    elif isinstance(valor, list):
        partes = [str(elemento) for elemento in valor]
    else:
        return []
    return [parte.strip() for parte in partes if parte and parte.strip()]


def _normalizar_plataformas(valor) -> list[str]:
    equivalencias = {
        "win": "windows", "pc": "windows", "windows": "windows",
        "mac": "mac", "macos": "mac", "osx": "mac", "os x": "mac",
        "linux": "linux", "steam deck": "linux", "steamos": "linux",
    }
    resultado = []
    for elemento in _limpiar_lista(valor):
        clave = _sin_acentos(elemento.strip().lower())
        if clave in equivalencias and equivalencias[clave] not in resultado:
            resultado.append(equivalencias[clave])
    return resultado


def normalizar_item(bruto: dict, fuente: str) -> dict | None:
    """Valida y limpia un item devuelto por la IA. None si no tiene nombre."""
    if not isinstance(bruto, dict):
        return None
    nombre = str(bruto.get("nombre") or "").strip()
    if not nombre:
        return None

    appid = bruto.get("appid")
    try:
        appid = int(appid) if appid is not None else None
    except (TypeError, ValueError):
        appid = None

    descuento = _a_numero(bruto.get("descuento_pct"))
    if descuento is not None:
        descuento = int(max(0, min(100, descuento)))

    positivas = _a_numero(bruto.get("positivas_pct"))
    if positivas is not None:
        positivas = round(max(0.0, min(100.0, positivas)), 1)

    def entero(valor):
        numero = _a_numero(valor)
        return int(numero) if numero is not None else None

    return {
        "nombre": nombre[:200],
        "appid": appid,
        "tipo": _normalizar_tipo(bruto.get("tipo")),
        "generos": _limpiar_lista(bruto.get("generos"))[:10],
        "precio": _a_numero(bruto.get("precio")),
        "descuento_pct": descuento,
        "fecha_lanzamiento": _normalizar_fecha(bruto.get("fecha_lanzamiento")),
        "plataformas": _normalizar_plataformas(bruto.get("plataformas")),
        "jugadores_actuales": entero(bruto.get("jugadores_actuales")),
        "pico_hoy": entero(bruto.get("pico_hoy")),
        "pico_historico": entero(bruto.get("pico_historico")),
        "rank_steamdb": entero(bruto.get("rank_steamdb")),
        "resenas_totales": entero(bruto.get("resenas_totales")),
        "positivas_pct": positivas,
        "notas": str(bruto.get("notas") or "")[:300],
        "fuente": fuente,
    }


def _a_numero_dato(valor: str) -> float | None:
    """Convierte cifras externas respetando separadores de miles (10.000 -> 10000)."""
    texto = valor.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", texto):
        texto = texto.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", texto):
        texto = texto.replace(",", "")
    return _a_numero(texto)


def normalizar_dato_externo(bruto: dict, fuente: str, documento: str = "") -> dict | None:
    """Valida una cifra/hecho externo devuelto por la IA. None si no tiene métrica ni valor."""
    if not isinstance(bruto, dict):
        return None

    metrica = str(bruto.get("metrica") or "").strip()[:80]
    valor = bruto.get("valor")
    if isinstance(valor, str):
        texto_valor = valor.strip()
        if not texto_valor:
            return None
        # Solo convertir a número si el texto es puramente numérico (con moneda/símbolos).
        if re.fullmatch(r"[\s\d.,%$€£+\-]+", texto_valor):
            numero = _a_numero_dato(texto_valor)
            valor = numero if numero is not None else texto_valor[:300]
        else:
            valor = texto_valor[:300]
    if valor is None or valor == "":
        return None
    if not metrica:
        metrica = "dato"

    tipo = _sin_acentos(str(bruto.get("tipo") or "otro").strip().lower())
    tipo = SINONIMOS_DATO_EXTERNO.get(tipo, tipo)
    if tipo not in TIPOS_DATO_EXTERNO:
        tipo = "otro"

    appid = bruto.get("appid")
    try:
        appid = int(appid) if appid is not None else None
    except (TypeError, ValueError):
        appid = None

    return {
        "tipo": tipo,
        "juego": str(bruto.get("juego") or "").strip()[:200],
        "appid": appid,
        "metrica": metrica,
        "valor": valor,
        "unidad": str(bruto.get("unidad") or "").strip()[:40],
        "fecha": _normalizar_fecha(bruto.get("fecha")),
        "detalle": str(bruto.get("detalle") or "").strip()[:500],
        "fuente": fuente,
        "documento": documento,
    }


def _clave_nombre(nombre: str) -> str:
    """Clave para comparar nombres: sin acentos, sin ®/™/©, en minúsculas y sin espacios dobles."""
    limpio = _sin_acentos(nombre.lower())
    limpio = re.sub(r"[®™©]", "", limpio)
    return re.sub(r"\s+", " ", limpio).strip()


def _clave_item(item: dict) -> str:
    if item.get("appid"):
        return f"appid:{item['appid']}"
    return "nombre:" + _clave_nombre(item["nombre"])


def deduplicar(items: list[dict]) -> list[dict]:
    """Une items repetidos, quedándose con el que tenga más datos completos."""
    mejores: dict[str, dict] = {}
    for item in items:
        clave = _clave_item(item)
        candidato = mejores.get(clave)
        if candidato is None:
            mejores[clave] = item
            continue
        campos = ("precio", "descuento_pct", "fecha_lanzamiento", "jugadores_actuales",
                  "resenas_totales", "positivas_pct")
        puntaje_nuevo = sum(1 for campo in campos if item.get(campo) is not None) + len(item.get("generos") or [])
        puntaje_viejo = sum(1 for campo in campos if candidato.get(campo) is not None) + len(candidato.get("generos") or [])
        if puntaje_nuevo > puntaje_viejo:
            mejores[clave] = item
    return list(mejores.values())


# ---------------------------------------------------------------------------
# Fragmentado de texto
# ---------------------------------------------------------------------------

def fragmentar(texto: str, tamano: int | None = None, solape: int | None = None) -> list[str]:
    """Corta un texto largo en fragmentos con un poco de solape para no perder filas."""
    tamano = tamano or config.IA_FRAGMENTO_CARACTERES
    solape = solape or config.IA_FRAGMENTO_SOLAPE
    texto = re.sub(r"\n{3,}", "\n\n", texto.strip())
    if len(texto) <= tamano:
        return [texto] if texto else []
    fragmentos = []
    inicio = 0
    while inicio < len(texto):
        fin = min(inicio + tamano, len(texto))
        if fin < len(texto):
            corte = texto.rfind("\n", inicio + int(tamano * 0.6), fin)
            if corte > inicio:
                fin = corte
        fragmentos.append(texto[inicio:fin].strip())
        if fin >= len(texto):
            break
        inicio = max(fin - solape, inicio + 1)
    return [fragmento for fragmento in fragmentos if fragmento]


# ---------------------------------------------------------------------------
# Llamada a la IA por fragmento (con reanudación)
# ---------------------------------------------------------------------------

def _procesar_fragmentos(
    fragmentos: list[str],
    fuente: str,
    contexto: str,
    lote: int,
    max_fragmentos: int | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """
    Manda cada fragmento a la IA y junta los items. Guarda resultados parciales
    en data/intermedio/ para poder reanudar si se corta.
    """
    parcial = config.INTERMEDIO / f"ia_lote_{lote:02d}_parcial.jsonl"
    hechos: dict[str, dict] = {}
    if parcial.exists():
        for linea in parcial.read_text(encoding="utf-8").splitlines():
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                continue
            hechos[f"{registro['fuente']}|{registro['fragmento']}"] = registro

    items: list[dict] = []
    descartados: list[dict] = []
    procesados = 0
    errores = 0
    pendientes = [
        (indice, fragmento)
        for indice, fragmento in enumerate(fragmentos)
        if f"{fuente}|{indice}" not in hechos
    ]
    if max_fragmentos:
        pendientes = pendientes[:max_fragmentos]

    if pendientes:
        print(f"[ia] {fuente}: {len(pendientes)} fragmentos pendientes de {len(fragmentos)} "
              f"({len(hechos)} ya procesados antes).")

    for indice, fragmento in pendientes:
        usuario = (
            f"Fuente: {contexto}\n\n"
            f"Texto de la página (puede estar recortado al principio o al final):\n"
            f"-----\n{fragmento}\n-----\n\n"
            f"Extraé TODAS las aplicaciones que aparezcan en el texto, una por item."
        )
        momento = time.time()
        try:
            datos = pedir_json(PROMPT_SISTEMA, usuario, fuente=fuente)
        except ErrorIA as error:
            errores += 1
            print(f"[ia] {fuente} fragmento {indice + 1}: error -> {error}")
            registrar_en_jsonl(
                {"fecha": ahora_iso(), "fuente": fuente, "lote": lote, "fragmento": indice,
                 "ok": False, "error": str(error)[:300]},
                config.INTERMEDIO / "registro_ia.jsonl",
            )
            continue

        items_brutos = datos.get("items") if isinstance(datos, dict) else None
        limpios = [normalizar_item(bruto, fuente) for bruto in (items_brutos or [])]
        limpios = [item for item in limpios if item]
        juegos = [item for item in limpios if item["tipo"] == "juego"]
        no_juegos = [item for item in limpios if item["tipo"] != "juego"]

        registro = {
            "fecha": ahora_iso(),
            "fuente": fuente,
            "lote": lote,
            "fragmento": indice,
            "duracion_seg": round(time.time() - momento, 1),
            "items": limpios,
        }
        with parcial.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

        items.extend(juegos)
        descartados.extend(
            [{**item, "motivo_descarte": f"tipo={item['tipo']} (no es un juego)"} for item in no_juegos]
        )
        procesados += 1
        print(
            f"[ia] {fuente} fragmento {indice + 1}/{len(fragmentos)}: "
            f"{len(limpios)} items ({len(juegos)} juegos, {len(no_juegos)} descartados) "
            f"en {round(time.time() - momento, 1)}s"
        )

    # Recuperar los resultados de fragmentos procesados en corridas anteriores
    if hechos:
        for clave, registro in hechos.items():
            fuente_registro = registro.get("fuente")
            if fuente_registro != fuente or clave in {f"{fuente}|{i}" for i, _ in pendientes}:
                continue
            for item in registro.get("items", []):
                if item.get("tipo") == "juego":
                    items.append(item)
                else:
                    descartados.append(
                        {**item, "motivo_descarte": f"tipo={item.get('tipo')} (no es un juego)"}
                    )

    resumen = {
        "fuente": fuente,
        "fragmentos_totales": len(fragmentos),
        "fragmentos_procesados_ahora": procesados,
        "errores": errores,
    }
    return items, descartados, resumen


# ---------------------------------------------------------------------------
# Fuente 1: texto de páginas de SteamDB
# ---------------------------------------------------------------------------

def _mapa_appids_de_steamdb(ruta_html: Path) -> dict[str, int]:
    """Arma nombre -> appid usando las filas con data-appid del HTML de SteamDB."""
    if not ruta_html.exists():
        return {}
    sopa = BeautifulSoup(ruta_html.read_text(encoding="utf-8", errors="replace"), "html.parser")
    mapa: dict[str, int] = {}
    for fila in sopa.select("tr.app[data-appid]"):
        appid = fila.get("data-appid")
        celdas = fila.find_all("td")
        if not appid or len(celdas) < 3:
            continue
        nombre = celdas[2].get_text(" ", strip=True)
        if nombre:
            mapa[_clave_nombre(nombre)] = int(appid)
    return mapa


def estructurar_steamdb(lote: int, max_fragmentos: int | None = None):
    """Procesa los .txt de SteamDB guardados para el lote (o el HTML si no hay texto extraído)."""
    dos_digitos = f"{lote:02d}"
    archivos = sorted(config.CRUDO_STEAMDB.glob(f"lote_{dos_digitos}_steamdb*.txt"))

    if not archivos:
        # Sin texto visible extraído: convertimos a texto el HTML guardado (evitando las páginas de bloqueo)
        htmls = [
            archivo
            for archivo in sorted(config.CRUDO_STEAMDB.glob(f"lote_{dos_digitos}_steamdb*.html"))
            if "bloqueo_cloudflare" not in archivo.name
        ]
        if not htmls:
            return None
        for archivo_html in htmls:
            sopa = BeautifulSoup(archivo_html.read_text(encoding="utf-8", errors="replace"), "html.parser")
            for etiqueta in sopa(["script", "style", "noscript"]):
                etiqueta.decompose()
            texto = re.sub(r"\n{3,}", "\n\n", sopa.get_text("\n", strip=True))
            archivo_txt = archivo_html.with_suffix(".txt")
            archivo_txt.write_text(texto, encoding="utf-8")
        archivos = sorted(config.CRUDO_STEAMDB.glob(f"lote_{dos_digitos}_steamdb*.txt"))

    if not archivos:
        return None

    items, descartados, resumenes = [], [], []
    for archivo in archivos:
        texto = archivo.read_text(encoding="utf-8", errors="replace")
        print(f"[ia] SteamDB: procesando {archivo.name} ({len(texto)} caracteres)...")
        fragmentos = fragmentar(texto)
        nuevos, nuevos_descartes, resumen = _procesar_fragmentos(
            fragmentos, f"steamdb:{archivo.stem}", f"SteamDB ({archivo.stem})", lote, max_fragmentos
        )
        # Validación con el HTML original: nombre -> appid real de cada fila
        mapa_appids = _mapa_appids_de_steamdb(archivo.with_suffix(".html"))
        for item in nuevos + nuevos_descartes:
            appid_real = mapa_appids.get(_clave_nombre(item["nombre"]))
            if appid_real is not None:
                item["appid"] = appid_real
                item["appid_validado"] = True
            else:
                item["appid_validado"] = False
        resumen["filas_con_appid_en_html"] = len(mapa_appids)
        resumen["appids_que_la_ia_devolvio_bien"] = sum(
            1 for item in nuevos + nuevos_descartes if item.get("appid_validado")
        )
        items.extend(nuevos)
        descartados.extend(nuevos_descartes)
        resumenes.append(resumen)
    return items, descartados, resumenes


# ---------------------------------------------------------------------------
# Fuente 2: HTML del buscador de la tienda de Steam
# ---------------------------------------------------------------------------

PATRON_FILA = re.compile(r'data-ds-appid="(\d+)')


def _filas_del_buscador(html: str) -> list[tuple[int | None, str]]:
    """Extrae de cada fila del buscador: (appid, texto visible limpio)."""
    sopa = BeautifulSoup(html, "html.parser")
    filas = []
    for fila in sopa.select("a.search_result_row"):
        appid = None
        atributo = fila.get("data-ds-appid") or ""
        coincidencia = re.match(r"(\d+)", atributo)
        if coincidencia:
            appid = int(coincidencia.group(1))
        texto = re.sub(r"\s+", " ", fila.get_text(" ", strip=True))[:1200]
        filas.append((appid, texto))
    return filas


def estructurar_tienda(lote: int, max_fragmentos: int | None = None):
    """
    Procesa el HTML del buscador de la tienda guardado para el lote.
    Le agrega el appid a cada fila (dato técnico que está en el HTML) para que la IA
    no tenga que adivinarlo, y después valida los appids que devuelve el modelo.
    """
    dos_digitos = f"{lote:02d}"
    archivos = sorted(config.CRUDO_API.glob(f"lote_{dos_digitos}_busqueda_pagina_*.html"))
    if not archivos:
        return None

    items, descartados, resumenes = [], [], []
    for archivo in archivos:
        html = archivo.read_text(encoding="utf-8", errors="replace")
        filas = _filas_del_buscador(html)
        if not filas:
            continue
        verdaderos = {appid for appid, _ in filas if appid is not None}

        # Bloques de 15 filas: mantiene la respuesta del modelo en un tamaño razonable
        fragmentos = []
        for inicio in range(0, len(filas), 15):
            bloque = filas[inicio : inicio + 15]
            lineas = [
                f"[appid={appid if appid is not None else 'desconocido'}] {texto}"
                for appid, texto in bloque
            ]
            fragmentos.append("\n".join(lineas))

        print(f"[ia] Tienda: procesando {archivo.name} ({len(filas)} filas en {len(fragmentos)} fragmentos)...")
        nuevos, nuevos_descartes, resumen = _procesar_fragmentos(
            fragmentos, f"tienda:{archivo.stem}", "Buscador de la tienda de Steam", lote, max_fragmentos
        )
        # Validación: ¿los appids que devolvió la IA existen realmente en la página?
        for item in nuevos:
            item["appid_validado"] = bool(item.get("appid") and item["appid"] in verdaderos)
        for item in nuevos_descartes:
            item["appid_validado"] = bool(item.get("appid") and item["appid"] in verdaderos)
        resumen["appids_reales_en_pagina"] = len(verdaderos)
        resumen["appids_que_la_ia_devolvio_bien"] = sum(
            1 for item in nuevos + nuevos_descartes if item.get("appid_validado")
        )
        items.extend(nuevos)
        descartados.extend(nuevos_descartes)
        resumenes.append(resumen)
    return items, descartados, resumenes


# ---------------------------------------------------------------------------
# Fuente 3: datos externos aceptados por la ingesta
# ---------------------------------------------------------------------------

def _documentos_externos_aceptados() -> list[dict]:
    """Lee los datos externos que la ingesta ya aceptó y tienen texto disponible."""
    documentos: list[dict] = []
    for ruta_decision in sorted(config.INTERMEDIO_INGESTA.glob("*/decision.json")):
        try:
            decision = leer_json(ruta_decision, None)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(decision, dict):
            continue
        verificacion = decision.get("verificacion") or {}
        if decision.get("estado") != "procesado" or not verificacion.get("sirve"):
            continue

        ruta_texto = decision.get("texto") or ""
        if ruta_texto:
            ruta_texto = Path(str(ruta_texto)).expanduser()
            if not ruta_texto.is_absolute():
                ruta_texto = config.RAIZ / ruta_texto
        if not ruta_texto or not Path(ruta_texto).exists():
            identificador = str(decision.get("id") or ruta_decision.parent.name)
            ruta_texto = config.INTERMEDIO_INGESTA / identificador / "texto.txt"
        if not Path(ruta_texto).exists():
            continue
        try:
            texto = Path(ruta_texto).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not texto:
            continue

        documentos.append(
            {
                "id": str(decision.get("id") or ruta_decision.parent.name),
                "archivo": str(decision.get("archivo") or ruta_decision.parent.name),
                "texto": texto,
                "hash_texto": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                "ruta_texto": str(ruta_texto),
            }
        )
    return documentos


def _procesar_fragmentos_externa(
    documentos: list[dict],
    max_fragmentos: int | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Manda cada fragmento de cada dato externo a la IA y junta items y datos.
    Usa un archivo parcial para no repetir llamadas si se corta la corrida.
    """
    parcial = config.INTERMEDIO / "ia_externa_parcial.jsonl"
    parcial.parent.mkdir(parents=True, exist_ok=True)
    hechos: dict[str, dict] = {}
    if parcial.exists():
        for linea in parcial.read_text(encoding="utf-8").splitlines():
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                continue
            clave = registro.get("clave")
            if clave:
                hechos[str(clave)] = registro

    fragmentos_por_documento: dict[str, list[str]] = {}
    pendientes: list[tuple[str, dict, int, str]] = []
    for documento in documentos:
        fragmentos = fragmentar(documento["texto"])
        fragmentos_por_documento[documento["id"]] = fragmentos
        for indice, fragmento in enumerate(fragmentos):
            clave = f"{documento['id']}|{documento['hash_texto'][:16]}|{indice}"
            if clave not in hechos:
                pendientes.append((clave, documento, indice, fragmento))

    if max_fragmentos:
        pendientes = pendientes[:max_fragmentos]

    if pendientes:
        print(
            f"[ia] Externa: {len(pendientes)} fragmento(s) pendientes "
            f"de {sum(len(f) for f in fragmentos_por_documento.values())}."
        )

    procesados_ahora: dict[str, int] = {}
    errores = 0
    for clave, documento, indice, fragmento in pendientes:
        usuario = (
            f"Fuente externa: {documento['archivo']}.\n\n"
            f"Contenido (puede estar recortado al principio o al final):\n"
            f"-----\n{fragmento}\n-----\n\n"
            "Extraé las aplicaciones y los datos de mercado que aparezcan. "
            "Devolvé el JSON pedido, sin inventar nada."
        )
        momento = time.time()
        try:
            datos = pedir_json(
                PROMPT_EXTERNA,
                usuario,
                fuente=f"externa:{documento['id']}",
            )
        except ErrorIA as error:
            errores += 1
            print(f"[ia] Externa {documento['archivo']} fragmento {indice + 1}: error -> {error}")
            registrar_en_jsonl(
                {
                    "fecha": ahora_iso(),
                    "fuente": f"externa:{documento['id']}",
                    "documento": documento["archivo"],
                    "fragmento": indice,
                    "ok": False,
                    "error": str(error)[:300],
                },
                config.INTERMEDIO / "registro_ia.jsonl",
            )
            continue

        items_brutos = datos.get("items") if isinstance(datos, dict) else None
        datos_brutos = datos.get("datos") if isinstance(datos, dict) else None
        limpios = [
            item
            for item in (
                normalizar_item(bruto, f"externa:{documento['id']}")
                for bruto in (items_brutos or [])
            )
            if item
        ]
        juegos = []
        no_juegos = []
        for item in limpios:
            item["documento"] = documento["archivo"]
            item["documento_id"] = documento["id"]
            if item["tipo"] == "juego":
                juegos.append(item)
            else:
                no_juegos.append(
                    {
                        **item,
                        "motivo_descarte": f"tipo={item['tipo']} (no es un juego)",
                    }
                )

        hechos_externos = [
            dato
            for dato in (
                normalizar_dato_externo(
                    bruto,
                    f"externa:{documento['id']}",
                    documento=documento["archivo"],
                )
                for bruto in (datos_brutos or [])
            )
            if dato
        ]
        for dato in hechos_externos:
            dato["documento_id"] = documento["id"]

        registro = {
            "fecha": ahora_iso(),
            "clave": clave,
            "documento_id": documento["id"],
            "archivo": documento["archivo"],
            "hash_texto": documento["hash_texto"],
            "fragmento": indice,
            "duracion_seg": round(time.time() - momento, 1),
            "items": juegos,
            "descartados": no_juegos,
            "datos": hechos_externos,
        }
        with parcial.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
        hechos[clave] = registro
        procesados_ahora[documento["id"]] = procesados_ahora.get(documento["id"], 0) + 1

        print(
            f"[ia] Externa {documento['archivo']} fragmento {indice + 1}: "
            f"{len(juegos)} juego(s), {len(hechos_externos)} dato(s) "
            f"en {round(time.time() - momento, 1)}s"
        )

    items: list[dict] = []
    datos_externos: list[dict] = []
    descartados: list[dict] = []
    resumenes: list[dict] = []
    vistos: set[tuple] = set()

    for documento in documentos:
        items_documento = 0
        datos_documento = 0
        for indice, _fragmento in enumerate(fragmentos_por_documento.get(documento["id"], [])):
            clave = f"{documento['id']}|{documento['hash_texto'][:16]}|{indice}"
            registro = hechos.get(clave)
            if not registro:
                continue
            for item in registro.get("items", []):
                item["documento"] = documento["archivo"]
                item["documento_id"] = documento["id"]
                items.append(item)
                items_documento += 1
            descartados.extend(registro.get("descartados", []))
            for dato in registro.get("datos", []):
                dato["documento"] = documento["archivo"]
                dato["documento_id"] = documento["id"]
                clave_dato = (
                    dato.get("documento_id"),
                    dato.get("tipo"),
                    dato.get("juego"),
                    dato.get("metrica"),
                    str(dato.get("valor")),
                    dato.get("fecha"),
                )
                if clave_dato in vistos:
                    continue
                vistos.add(clave_dato)
                datos_externos.append(dato)
                datos_documento += 1

        resumenes.append(
            {
                "fuente": f"externa:{documento['id']}",
                "archivo": documento["archivo"],
                "fragmentos_totales": len(
                    fragmentos_por_documento.get(documento["id"], [])
                ),
                "fragmentos_procesados_ahora": procesados_ahora.get(documento["id"], 0),
                "items": items_documento,
                "datos": datos_documento,
            }
        )

    if errores:
        print(f"[ia] Externa: {errores} fragmento(s) con error; se puede volver a intentar.")
    return items, datos_externos, descartados, resumenes


def estructurar_externa(max_fragmentos: int | None = None) -> dict | None:
    """
    Procesa como tercera fuente todos los datos externos aceptados por `ingerir`.

    Los items de juego se guardan aparte de los lotes de Steam para no duplicar
    filas; la consolidación los usa para enriquecer el dataset. Las cifras que no
    son de un juego quedan en `datos_externos.csv`.
    """
    documentos = _documentos_externos_aceptados()
    if not documentos:
        print("[ia] Externa: no hay datos externos aceptados para estructurar.")
        return None

    print(f"[ia] Externa: procesando {len(documentos)} dato(s) aceptado(s)...")
    items, datos_externos, descartados, resumenes = _procesar_fragmentos_externa(
        documentos, max_fragmentos=max_fragmentos
    )
    items = deduplicar(items) if items else []
    resultado = {
        "fecha": ahora_iso(),
        "documentos": len(documentos),
        "items": items,
        "datos": datos_externos,
        "descartados": descartados,
        "resumenes": resumenes,
    }
    guardar_json(resultado, config.INTERMEDIO / "ia_externa.json")
    print(
        f"[ia] Externa lista: {len(items)} juego(s) externo(s) y "
        f"{len(datos_externos)} dato(s) de mercado."
    )
    return resultado


# ---------------------------------------------------------------------------
# Orquestador del lote
# ---------------------------------------------------------------------------

def estructurar_lote(lote: int, fuente: str = "auto", max_fragmentos: int | None = None) -> dict:
    """
    Estructura con la IA todo lo disponible para el lote.
    fuente: "auto" (SteamDB + tienda + datos externos), "steamdb", "tienda" o "externa".

    La fuente externa es global (no pertenece a un lote): se procesa una sola vez
    y se guarda en `data/intermedio/ia_externa.json`. La consolidación la usa
    después para enriquecer los juegos y para armar `datos_externos.csv`.
    """
    from .ollama_cliente import asegurar_modelo

    asegurar_modelo()
    momento = time.time()

    if fuente == "externa":
        resultado_externa = estructurar_externa(max_fragmentos)
        descartados_externa = (resultado_externa or {}).get("descartados", [])
        return {
            "lote": lote,
            "fecha": ahora_iso(),
            "duracion_seg": round(time.time() - momento, 1),
            "fuentes": (resultado_externa or {}).get("resumenes", []),
            "cantidad_juegos": 0,
            "cantidad_juegos_externos": len((resultado_externa or {}).get("items", [])),
            "cantidad_datos_externos": len((resultado_externa or {}).get("datos", [])),
            "cantidad_descartados": len(descartados_externa),
            "items": [],
            "descartados": descartados_externa,
            "solo_externa": True,
        }

    items: list[dict] = []
    descartados: list[dict] = []
    resumenes: list[dict] = []

    if fuente in ("auto", "steamdb"):
        resultado = estructurar_steamdb(lote, max_fragmentos)
        if resultado:
            nuevos, nuevos_descartes, resumenes_steamdb = resultado
            items.extend(nuevos)
            descartados.extend(nuevos_descartes)
            resumenes.extend(resumenes_steamdb)
        elif fuente == "steamdb":
            print("[ia] No hay archivos de texto de SteamDB para este lote (¿corriste 'steamdb' primero?)")

    if fuente in ("auto", "tienda"):
        resultado = estructurar_tienda(lote, max_fragmentos)
        if resultado:
            nuevos, nuevos_descartes, resumenes_tienda = resultado
            items.extend(nuevos)
            descartados.extend(nuevos_descartes)
            resumenes.extend(resumenes_tienda)

    resultado_externa = None
    if fuente == "auto":
        resultado_externa = estructurar_externa(max_fragmentos)
        if resultado_externa:
            resumenes.extend(resultado_externa.get("resumenes", []))

    items = deduplicar(items)
    descartados = deduplicar(descartados) if descartados else []

    dos_digitos = f"{lote:02d}"
    resultado_lote = {
        "lote": lote,
        "fecha": ahora_iso(),
        "duracion_seg": round(time.time() - momento, 1),
        "fuentes": resumenes,
        "cantidad_juegos": len(items),
        "cantidad_juegos_externos": len((resultado_externa or {}).get("items", [])),
        "cantidad_datos_externos": len((resultado_externa or {}).get("datos", [])),
        "cantidad_descartados": len(descartados),
        "items": items,
        "descartados": descartados,
    }
    if items or descartados:
        guardar_json(resultado_lote, config.INTERMEDIO / f"ia_lote_{dos_digitos}.json")
    else:
        print(
            f"[ia] Lote {lote}: no había datos de SteamDB/tienda; "
            "no se escribe un resultado vacío para el lote."
        )

    if descartados:
        tabla = pd.DataFrame(descartados)
        columnas = ["nombre", "appid", "tipo", "motivo_descarte", "fuente", "appid_validado"]
        columnas = [columna for columna in columnas if columna in tabla.columns]
        guardar_csv(tabla[columnas], config.DESCARTADO / f"no_juegos_lote_{dos_digitos}_ia.csv")

    print(
        f"[ia] Lote {lote} listo: {len(items)} juegos estructurados, "
        f"{resultado_lote['cantidad_juegos_externos']} juegos externos, "
        f"{resultado_lote['cantidad_datos_externos']} datos externos, "
        f"{len(descartados)} no-juegos descartados, "
        f"{round(time.time() - momento, 1)}s en total."
    )
    hay_fuentes_steam = any(
        not str(resumen.get("fuente", "")).startswith("externa:")
        for resumen in resumenes
    )
    if fuente == "auto" and not hay_fuentes_steam:
        print(
            "[ia] Ojo: no había texto de SteamDB ni HTML del buscador para este lote. "
            "Corré primero 'python pipeline.py extraer' y/o 'python pipeline.py steamdb'."
        )
    return resultado_lote
