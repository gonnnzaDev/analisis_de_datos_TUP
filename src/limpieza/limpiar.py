"""
Limpieza y transformación con pandas: convierte los datos crudos de la API de Steam
en una tabla ordenada de juegos, lista para analizar.

Qué hace:
- Toma las fichas crudas de cada app (data/crudo/steam_api/lote_XX_appdetails.json).
- Descarta todo lo que NO sea un juego (DLC, software, demos, videos...).
- Normaliza precios (centavos -> pesos), descuentos, fechas de lanzamiento,
  géneros, categorías y plataformas.
- Cruza jugadores conectados, ranking de más jugados y reseñas de usuarios.
- Guarda el resultado en data/procesado/lote_XX/ y los descartes en data/descartado/.
- Consolida todos los lotes en un único dataset.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

import pandas as pd

from .. import config
from ..almacenamiento.guardar import guardar_csv, guardar_json, leer_json

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "january": 1, "may": 5,
}


def parsear_fecha_lanzamiento(texto: str | None) -> tuple[str | None, str]:
    """
    Convierte la fecha que devuelve Steam ("17 Oct, 2007", "OCT 2024", "2025",
    "Próximamente") en (fecha_iso, precision).

    precision: "dia", "mes", "anio", "trimestre", "sin_fecha" o "desconocida".
    """
    if not texto or not isinstance(texto, str):
        return None, "sin_fecha"
    limpio = texto.lower().replace(",", " ").strip()
    limpio = re.sub(r"\s+", " ", limpio)
    if any(palabra in limpio for palabra in ("pronto", "soon", "tba", "to be announced", "anunciar")):
        return None, "sin_fecha"
    if re.match(r"^q[1-4]\s*\d{4}$", limpio) or "quarter" in limpio or "trimestre" in limpio:
        return None, "trimestre"

    # 17 oct 2007
    coincidencia = re.match(r"^(\d{1,2})\s+([a-záéíóúñ]+)\.?\s+(\d{4})$", limpio)
    if coincidencia and coincidencia.group(2)[:3] in MESES:
        mes = MESES[coincidencia.group(2)[:3]]
        try:
            return date(int(coincidencia.group(3)), mes, int(coincidencia.group(1))).isoformat(), "dia"
        except ValueError:
            return None, "desconocida"

    # oct 2007
    coincidencia = re.match(r"^([a-záéíóúñ]+)\.?\s+(\d{4})$", limpio)
    if coincidencia and coincidencia.group(1)[:3] in MESES:
        return f"{int(coincidencia.group(2)):04d}-{MESES[coincidencia.group(1)[:3]]:02d}", "mes"

    # 2007
    coincidencia = re.fullmatch(r"(\d{4})", limpio)
    if coincidencia:
        return coincidencia.group(1), "anio"

    return None, "desconocida"


def _generos(datos: dict) -> str:
    generos = datos.get("genres") or []
    return " | ".join(genero.get("description", "") for genero in generos if genero.get("description"))


def _categorias(datos: dict) -> str:
    categorias = datos.get("categories") or []
    return " | ".join(categoria.get("description", "") for categoria in categorias if categoria.get("description"))


def _tiene_categoria(texto_categorias: str, palabra: str) -> bool:
    return palabra.lower() in texto_categorias.lower()


def _precio(datos: dict) -> tuple[float | None, float | None, int | None, str, bool]:
    """Devuelve (precio_inicial, precio_final, descuento, moneda, gratis) en unidades, no centavos."""
    if datos.get("is_free"):
        return 0.0, 0.0, 0, "", True
    precio = datos.get("price_overview")
    if not precio:
        return None, None, None, "", False
    return (
        precio.get("initial", 0) / 100,
        precio.get("final", 0) / 100,
        precio.get("discount_percent", 0),
        precio.get("currency", ""),
        False,
    )


def construir_fila(
    appid: int,
    datos: dict,
    resenas: dict,
    jugadores: dict,
    mas_jugados: dict,
) -> dict:
    """Convierte la ficha cruda de una app en una fila limpia del dataset."""
    texto_categorias = _categorias(datos)
    precio_inicial, precio_final, descuento, moneda, gratis = _precio(datos)
    fecha_texto = (datos.get("release_date") or {}).get("date") or ""
    fecha_iso, precision = parsear_fecha_lanzamiento(fecha_texto)

    resumen_resenas = resenas.get(str(appid)) or {}
    total_resenas = resumen_resenas.get("total_reviews")
    positivas = resumen_resenas.get("total_positive")
    pct_positivo = None
    if total_resenas:
        pct_positivo = round(100 * (positivas or 0) / total_resenas, 1)

    info_mas_jugados = mas_jugados.get(appid) or {}

    anio = int(fecha_iso[:4]) if fecha_iso else None
    mes = int(fecha_iso[5:7]) if fecha_iso and len(fecha_iso) >= 7 else None
    edad_anios = round((date.today() - date(anio, 1, 1)).days / 365.25, 2) if anio else None

    return {
        "appid": appid,
        "nombre": datos.get("name", ""),
        "es_gratis": gratis,
        "precio_inicial": precio_inicial,
        "precio_final": precio_final,
        "descuento_pct": descuento,
        "moneda": moneda,
        "fecha_texto": fecha_texto,
        "fecha_lanzamiento": fecha_iso,
        "precision_fecha": precision,
        "anio": anio,
        "mes": mes,
        "edad_anios": edad_anios,
        "proximo_lanzamiento": bool((datos.get("release_date") or {}).get("coming_soon")),
        "desarrolladores": " | ".join(datos.get("developers") or []),
        "editores": " | ".join(datos.get("publishers") or []),
        "generos": _generos(datos),
        "cantidad_generos": len(datos.get("genres") or []),
        "categorias": texto_categorias,
        "cantidad_categorias": len(datos.get("categories") or []),
        "tiene_un_jugador": _tiene_categoria(texto_categorias, "un jugador") or _tiene_categoria(texto_categorias, "single-player"),
        "tiene_multijugador": _tiene_categoria(texto_categorias, "multijugador") or _tiene_categoria(texto_categorias, "multi-player"),
        "tiene_cooperativo": _tiene_categoria(texto_categorias, "cooperativo") or _tiene_categoria(texto_categorias, "co-op"),
        "tiene_logros": bool((datos.get("achievements") or {}).get("total")),
        "cantidad_logros": (datos.get("achievements") or {}).get("total"),
        "windows": bool((datos.get("platforms") or {}).get("windows")),
        "mac": bool((datos.get("platforms") or {}).get("mac")),
        "linux": bool((datos.get("platforms") or {}).get("linux")),
        "metacritic": (datos.get("metacritic") or {}).get("score"),
        "recomendaciones_steam": (datos.get("recommendations") or {}).get("total"),
        "cantidad_dlc": len(datos.get("dlc") or []),
        "descripcion_corta": (datos.get("short_description") or "")[:300],
        "jugadores_actuales": jugadores.get(str(appid)),
        "rank_mas_jugados": info_mas_jugados.get("rank"),
        "pico_en_juego": info_mas_jugados.get("pico_en_juego"),
        "resenas_totales": total_resenas,
        "resenas_positivas": positivas,
        "resenas_negativas": resumen_resenas.get("total_negative"),
        "pct_resenas_positivas": pct_positivo,
        "nota_resenas": resumen_resenas.get("review_score_desc"),
    }


def limpiar_lote(lote: int) -> pd.DataFrame:
    """Limpia un lote completo: crudo de la API -> tabla de juegos + descartes."""
    dos_digitos = f"{lote:02d}"
    ruta_detalles = config.CRUDO_API / f"lote_{dos_digitos}_appdetails.json"
    if not ruta_detalles.exists():
        raise FileNotFoundError(
            f"No existe {ruta_detalles}. Corré primero: python pipeline.py extraer --lote {lote}"
        )

    detalles: dict = leer_json(ruta_detalles, {}) or {}
    resenas: dict = leer_json(config.CRUDO_API / f"lote_{dos_digitos}_resenas.json", {}) or {}
    jugadores: dict = leer_json(config.CRUDO_API / f"lote_{dos_digitos}_jugadores.json", {}) or {}
    mas_jugados_crudo = leer_json(config.CRUDO / "mas_jugados.json", {}) or {}
    mas_jugados: dict = {}
    for clave, valor in (mas_jugados_crudo.get("ranks") or {}).items():
        try:
            mas_jugados[int(clave)] = valor
        except (TypeError, ValueError):
            continue

    filas, descartes = [], []
    for clave, datos in detalles.items():
        try:
            appid = int(clave)
        except (TypeError, ValueError):
            continue
        tipo = (datos.get("type") or "desconocido").lower()
        if tipo != "game":
            descartes.append(
                {
                    "appid": appid,
                    "nombre": datos.get("name", ""),
                    "tipo": tipo,
                    "motivo_descarte": f"tipo={tipo} (no es un juego)",
                    "fuente": "api_steam",
                }
            )
            continue
        filas.append(construir_fila(appid, datos, resenas, jugadores, mas_jugados))

    tabla = pd.DataFrame(filas)
    if not tabla.empty:
        tabla = tabla.drop_duplicates(subset=["appid"], keep="first").sort_values("appid").reset_index(drop=True)

    carpeta_lote = config.PROCESADO / f"lote_{dos_digitos}"
    ruta_csv = guardar_csv(tabla, carpeta_lote / f"juegos_lote_{dos_digitos}.csv")
    guardar_json(
        {
            "lote": lote,
            "cantidad_juegos": int(len(tabla)),
            "cantidad_descartados": len(descartes),
            "columnas": list(tabla.columns),
            "filas": tabla.to_dict(orient="records") if not tabla.empty else [],
        },
        carpeta_lote / f"juegos_lote_{dos_digitos}.json",
    )
    if descartes:
        guardar_csv(
            pd.DataFrame(descartes),
            config.DESCARTADO / f"no_juegos_lote_{dos_digitos}_api.csv",
        )

    print(
        f"[limpiar] Lote {lote}: {len(tabla)} juegos limpios, {len(descartes)} no-juegos descartados. "
        f"Guardado en {ruta_csv}"
    )
    if not tabla.empty and tabla["precio_final"].notna().any():
        precio_mediano = tabla["precio_final"].median()
        monedas = tabla["moneda"].dropna().astype(str)
        monedas = monedas[monedas.str.len() > 0]
        etiqueta_moneda = monedas.mode().iloc[0] if not monedas.empty else "moneda de la tienda"
        print(f"[limpiar] Precio mediano del lote: {precio_mediano:,.2f} {etiqueta_moneda}")
    return tabla


def cargar_consolidado() -> pd.DataFrame | None:
    """Lee el dataset consolidado. Devuelve None si todavía no existe."""
    ruta = config.PROCESADO / "consolidado" / "juegos_consolidado.csv"
    if not ruta.exists():
        return None
    return pd.read_csv(ruta)


def _enriquecer_con_ia(consolidado: pd.DataFrame) -> pd.DataFrame:
    """Cruza por appid las filas que extrajo la IA de SteamDB (rank y picos de jugadores)."""
    filas = []
    for ruta in sorted(config.INTERMEDIO.glob("ia_lote_*.json")):
        datos = leer_json(ruta, {}) or {}
        for item in datos.get("items", []):
            appid = item.get("appid")
            if not appid or not item.get("appid_validado"):
                continue
            filas.append(
                {
                    "appid": int(appid),
                    "steamdb_jugadores": item.get("jugadores_actuales"),
                    "steamdb_pico_24h": item.get("pico_hoy"),
                    "steamdb_pico_historico": item.get("pico_historico"),
                    "steamdb_rank": item.get("rank_steamdb"),
                }
            )
    if not filas:
        return consolidado
    tabla_ia = pd.DataFrame(filas).drop_duplicates(subset=["appid"])
    enriquecido = consolidado.merge(tabla_ia, on="appid", how="left")
    cantidad = int(enriquecido["steamdb_rank"].notna().sum())
    print(f"[consolidar] {cantidad} juegos enriquecidos con datos de SteamDB (IA).")
    return enriquecido


def _clave_nombre_externa(nombre: str) -> str:
    """Clave para cruzar nombres de juegos externos con el dataset de Steam."""
    texto = unicodedata.normalize("NFKD", str(nombre or "").lower())
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[®™©]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _cargar_ia_externa() -> tuple[list[dict], list[dict]]:
    """Lee la salida de la tercera fuente (estructuración de datos externos)."""
    datos = leer_json(config.INTERMEDIO / "ia_externa.json", {}) or {}
    if not isinstance(datos, dict):
        return [], []
    items = [item for item in datos.get("items", []) if isinstance(item, dict)]
    hechos = [dato for dato in datos.get("datos", []) if isinstance(dato, dict)]
    return items, hechos


def _enriquecer_con_externa(consolidado: pd.DataFrame) -> pd.DataFrame:
    """
    Cruza los juegos externos con el dataset de Steam y guarda los datos que no
    encajan en una fila de juego.

    No agrega filas nuevas a `juegos_consolidado.csv`: los juegos externos que no
    coinciden quedan en `juegos_externos.csv` para revisarlos por separado.
    """
    items, hechos = _cargar_ia_externa()
    ruta_juegos = config.PROCESADO / "consolidado" / "juegos_externos.csv"
    ruta_datos = config.PROCESADO / "consolidado" / "datos_externos.csv"

    if not items and not hechos:
        for ruta in (ruta_juegos, ruta_datos):
            if ruta.exists():
                ruta.unlink()
        return consolidado

    indice_appid: dict[int, object] = {}
    indice_nombre: dict[str, object] = {}
    for posicion, fila in consolidado.iterrows():
        try:
            indice_appid[int(fila.get("appid"))] = posicion
        except (TypeError, ValueError):
            pass
        nombre = _clave_nombre_externa(fila.get("nombre", ""))
        if nombre:
            indice_nombre.setdefault(nombre, posicion)

    columnas_ext = [
        "ext_precio",
        "ext_descuento_pct",
        "ext_fecha_lanzamiento",
        "ext_jugadores_actuales",
        "ext_resenas_totales",
        "ext_positivas_pct",
        "ext_generos",
        "ext_notas",
        "ext_fuente",
        "ext_documento",
    ]
    coincidencias: list[tuple[object, dict]] = []
    externos: list[dict] = []
    vistos: set[tuple] = set()

    for item in items:
        appid = item.get("appid")
        try:
            appid = int(appid) if appid is not None else None
        except (TypeError, ValueError):
            appid = None
        nombre = str(item.get("nombre") or "").strip()
        if not nombre:
            continue

        posicion = indice_appid.get(appid) if appid is not None else None
        if posicion is None:
            posicion = indice_nombre.get(_clave_nombre_externa(nombre))
        if posicion is not None:
            coincidencias.append((posicion, item))
            continue

        clave = ("appid", appid) if appid is not None else ("nombre", _clave_nombre_externa(nombre))
        if clave in vistos:
            continue
        vistos.add(clave)
        externos.append(
            {
                "nombre": nombre,
                "appid": appid,
                "tipo": item.get("tipo", ""),
                "generos": " | ".join(item.get("generos") or []),
                "precio": item.get("precio"),
                "descuento_pct": item.get("descuento_pct"),
                "fecha_lanzamiento": item.get("fecha_lanzamiento"),
                "plataformas": " | ".join(item.get("plataformas") or []),
                "jugadores_actuales": item.get("jugadores_actuales"),
                "resenas_totales": item.get("resenas_totales"),
                "positivas_pct": item.get("positivas_pct"),
                "notas": item.get("notas", ""),
                "fuente": item.get("fuente", ""),
                "documento": item.get("documento", ""),
            }
        )

    if coincidencias:
        for columna in columnas_ext:
            if columna not in consolidado.columns:
                consolidado[columna] = None
        for posicion, item in coincidencias:
            valores = {
                "ext_precio": item.get("precio"),
                "ext_descuento_pct": item.get("descuento_pct"),
                "ext_fecha_lanzamiento": item.get("fecha_lanzamiento"),
                "ext_jugadores_actuales": item.get("jugadores_actuales"),
                "ext_resenas_totales": item.get("resenas_totales"),
                "ext_positivas_pct": item.get("positivas_pct"),
                "ext_generos": " | ".join(item.get("generos") or []),
                "ext_notas": item.get("notas", ""),
                "ext_fuente": item.get("fuente", ""),
                "ext_documento": item.get("documento", ""),
            }
            for columna, valor in valores.items():
                if valor is None or valor == "":
                    continue
                actual = consolidado.at[posicion, columna]
                if pd.isna(actual) or actual == "":
                    consolidado.at[posicion, columna] = valor

    if externos:
        guardar_csv(pd.DataFrame(externos), ruta_juegos)
    elif ruta_juegos.exists():
        ruta_juegos.unlink()

    if hechos:
        tabla = pd.DataFrame(hechos)
        columnas = [
            "tipo", "juego", "appid", "metrica", "valor", "unidad",
            "fecha", "detalle", "documento", "documento_id", "fuente",
        ]
        columnas = [columna for columna in columnas if columna in tabla.columns]
        guardar_csv(tabla[columnas], ruta_datos)
    elif ruta_datos.exists():
        ruta_datos.unlink()

    print(
        f"[consolidar] Datos externos: {len(coincidencias)} juego(s) enriquecidos, "
        f"{len(externos)} juego(s) sin coincidencia y {len(hechos)} dato(s) de mercado."
    )
    return consolidado


def consolidar() -> pd.DataFrame:
    """Junta todos los lotes limpios en un único dataset (sin repetir appid)."""
    tablas = []
    for carpeta in sorted(config.PROCESADO.glob("lote_*")):
        for archivo in carpeta.glob("juegos_lote_*.csv"):
            tabla = pd.read_csv(archivo)
            tabla["lote_origen"] = int(re.search(r"lote_(\d+)", archivo.stem).group(1))
            tablas.append(tabla)

    if not tablas:
        raise FileNotFoundError(
            "No hay lotes limpios en data/procesado/. Corré primero: "
            "python pipeline.py limpiar --lote N (o python pipeline.py todo --lote N)"
        )

    consolidado = pd.concat(tablas, ignore_index=True)
    consolidado = (
        consolidado.sort_values(["resenas_totales", "appid"], ascending=[False, True])
        .drop_duplicates(subset=["appid"], keep="first")
        .sort_values("appid")
        .reset_index(drop=True)
    )
    consolidado = _enriquecer_con_ia(consolidado)
    consolidado = _enriquecer_con_externa(consolidado)

    # Tabla "explotada": una fila por combinación juego-género (para analizar géneros)
    generos = (
        consolidado[["appid", "nombre", "generos", "jugadores_actuales", "resenas_totales",
                     "precio_final", "anio"]]
        .assign(genero=lambda df: df["generos"].fillna("").str.split(r"\s*\|\s*"))
        .explode("genero")
        .query("genero != ''")
    )

    carpeta = config.PROCESADO / "consolidado"
    ruta_csv = guardar_csv(consolidado, carpeta / "juegos_consolidado.csv")
    guardar_csv(generos, carpeta / "generos_explotado.csv")
    guardar_json(
        {
            "cantidad_juegos": int(len(consolidado)),
            "lotes": sorted(consolidado["lote_origen"].unique().tolist()) if "lote_origen" in consolidado else [],
            "columnas": list(consolidado.columns),
        },
        carpeta / "resumen_consolidado.json",
    )
    print(
        f"[consolidar] {len(consolidado)} juegos únicos de {len(tablas)} lote(s) "
        f"-> {ruta_csv}"
    )
    return consolidado
