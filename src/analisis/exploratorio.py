"""
Análisis exploratorio del dataset consolidado de juegos de Steam.

Responde las preguntas preliminares del trabajo práctico:
1. ¿Cómo evolucionó la cantidad de juegos disponibles a lo largo del tiempo?
2. ¿Qué géneros tienen mayor presencia y popularidad?
3. ¿Qué relación hay entre las características de los juegos y la interacción de usuarios?
4. ¿Cómo se relacionan precios y descuentos con la actividad/popularidad?
5. ¿Qué patrones se ven en el comportamiento de los usuarios?

Cada respuesta genera:
- una tabla en reportes/tablas/*.csv (para el informe),
- un gráfico en reportes/figuras/*.png,
- y párrafos en reportes/resumen_analisis.md con los números calculados.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from ..almacenamiento.guardar import guardar_csv, guardar_texto
from ..limpieza import limpiar
from .. import config

plt.rcParams.update(
    {
        "figure.dpi": 140,
        "savefig.dpi": 140,
        "font.size": 10,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

COLUMNAS_NUMERICAS = [
    "precio_final",
    "descuento_pct",
    "edad_anios",
    "cantidad_generos",
    "cantidad_categorias",
    "cantidad_logros",
    "metacritic",
    "recomendaciones_steam",
    "jugadores_actuales",
    "resenas_totales",
    "pct_resenas_positivas",
]

NOMBRES_NUMERICAS = {
    "precio_final": "Precio final",
    "descuento_pct": "Descuento",
    "edad_anios": "Antigüedad (años)",
    "cantidad_generos": "Cantidad de géneros",
    "cantidad_categorias": "Cantidad de categorías",
    "cantidad_logros": "Logros",
    "metacritic": "Metacritic",
    "recomendaciones_steam": "Recomendaciones en Steam",
    "jugadores_actuales": "Jugadores actuales",
    "resenas_totales": "Reseñas totales",
    "pct_resenas_positivas": "% reseñas positivas",
}


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _guardar_figura(figura, nombre: str) -> str:
    ruta = config.FIGURAS / nombre
    figura.tight_layout()
    figura.savefig(ruta, bbox_inches="tight")
    plt.close(figura)
    print(f"[analisis] Figura guardada: reportes/figuras/{nombre}")
    return nombre


def _tabla_a_markdown(tabla: pd.DataFrame, max_filas: int = 12) -> str:
    """Convierte una tabla chica a Markdown para el resumen."""
    vista = tabla.head(max_filas)
    encabezado = "| " + " | ".join(str(columna) for columna in vista.columns) + " |"
    separador = "|" + "|".join(["---"] * len(vista.columns)) + "|"
    lineas = [encabezado, separador]
    for _, fila in vista.iterrows():
        celdas = []
        for valor in fila:
            if pd.isna(valor):
                celdas.append("")
            elif isinstance(valor, float):
                if float(valor).is_integer():
                    celdas.append(f"{int(valor):,}".replace(",", "."))
                else:
                    formateado = f"{valor:,.2f}"
                    celdas.append(formateado.replace(",", "@").replace(".", ",").replace("@", "."))
            else:
                celdas.append(str(valor))
        lineas.append("| " + " | ".join(celdas) + " |")
    return "\n".join(lineas)


def _moneda(tabla: pd.DataFrame) -> str:
    """Detecta en qué moneda viene el lote (Steam Argentina factura en USD, por ejemplo)."""
    if "moneda" in tabla.columns:
        valores = tabla["moneda"].dropna().astype(str)
        valores = valores[valores.str.len() > 0]
        if not valores.empty:
            return valores.mode().iloc[0]
    return "moneda de la tienda"


def _formatear_moneda(valor, moneda: str = "") -> str:
    if valor is None or pd.isna(valor):
        return "sin dato"
    etiqueta = f" {moneda}" if moneda else ""
    return f"{valor:,.2f}{etiqueta}".replace(",", "@").replace(".", ",").replace("@", ".")


def _preparar_tabla_cruda() -> pd.DataFrame:
    tabla = limpiar.cargar_consolidado()
    if tabla is None:
        tabla = limpiar.consolidar()
    for columna in COLUMNAS_NUMERICAS:
        if columna in tabla.columns:
            tabla[columna] = pd.to_numeric(tabla[columna], errors="coerce")
    if "es_gratis" in tabla.columns:
        tabla["es_gratis"] = tabla["es_gratis"].astype(str).str.lower().isin(["true", "1", "si", "sí"])
    return tabla


def _analizar_datos_externos(hallazgos: list[str], figuras: list[str]) -> pd.DataFrame | None:
    """Incorpora al informe las cifras externas que no son filas de un juego."""
    ruta = config.PROCESADO / "consolidado" / "datos_externos.csv"
    if not ruta.exists():
        return None
    try:
        tabla = pd.read_csv(ruta)
    except (pd.errors.EmptyDataError, OSError):
        return None
    if tabla.empty:
        return None

    guardar_csv(tabla, config.TABLAS / "07_datos_externos.csv")
    documentos = int(tabla["documento"].nunique()) if "documento" in tabla.columns else 0
    if "tipo" in tabla.columns:
        conteo = tabla["tipo"].fillna("otro").value_counts()
        detalle_tipos = ", ".join(f"{tipo} ({cantidad})" for tipo, cantidad in conteo.items())
    else:
        conteo = pd.Series(dtype=int)
        detalle_tipos = "sin clasificar"

    hallazgos.append(
        f"Se incorporaron **{len(tabla)} datos externos** de **{documentos} documento(s)**; "
        f"tipos: {detalle_tipos}."
    )

    ruta_juegos = config.PROCESADO / "consolidado" / "juegos_externos.csv"
    if ruta_juegos.exists():
        try:
            juegos_externos = pd.read_csv(ruta_juegos)
            if not juegos_externos.empty:
                hallazgos.append(
                    f"Hay **{len(juegos_externos)} juegos externos** que no coincidieron con "
                    "el dataset de Steam; quedaron en `juegos_externos.csv` para revisarlos."
                )
        except (pd.errors.EmptyDataError, OSError):
            pass

    if len(conteo) > 1:
        figura, eje = plt.subplots(figsize=(7, 4))
        conteo.plot(kind="bar", ax=eje, color="#4c72b0")
        eje.set_title("Datos externos por tipo")
        eje.set_xlabel("Tipo de dato")
        eje.set_ylabel("Cantidad de datos")
        eje.tick_params(axis="x", rotation=30)
        figuras.append(_guardar_figura(figura, "07_datos_externos_por_tipo.png"))
    return tabla


# ---------------------------------------------------------------------------
# Pregunta 1: evolución en el tiempo
# ---------------------------------------------------------------------------

def _analizar_evolucion(tabla: pd.DataFrame, hallazgos: list[str]) -> list[str]:
    figuras: list[str] = []
    moneda = _moneda(tabla)
    por_anio = (
        tabla.dropna(subset=["anio"])
        .query("anio >= 1997")
        .groupby("anio")
        .agg(
            juegos=("appid", "count"),
            precio_mediano=("precio_final", "median"),
            resenas_medianas=("resenas_totales", "median"),
            jugadores_totales=("jugadores_actuales", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "anio": "Año",
                "juegos": "Juegos en la muestra",
                "precio_mediano": f"Precio mediano ({moneda})",
                "resenas_medianas": "Reseñas medianas",
                "jugadores_totales": "Jugadores actuales (suma)",
            }
        )
        .sort_values("Año")
    )
    if por_anio.empty:
        return figuras

    guardar_csv(por_anio, config.TABLAS / "01_lanzamientos_por_anio.csv")
    acumulado = por_anio.set_index("Año")["Juegos en la muestra"].cumsum()

    figura, eje = plt.subplots(figsize=(9, 4.5))
    eje.bar(por_anio["Año"], por_anio["Juegos en la muestra"], color="#4c72b0", label="Juegos por año")
    eje.set_xlabel("Año de lanzamiento")
    eje.set_ylabel("Juegos en la muestra")
    eje.set_title("Lanzamientos por año de la muestra extraída")
    eje2 = eje.twinx()
    eje2.plot(acumulado.index, acumulado.values, color="#dd8452", marker="o", markersize=3, label="Acumulado")
    eje2.set_ylabel("Acumulado")
    eje2.grid(False)
    lineas_1, etiquetas_1 = eje.get_legend_handles_labels()
    lineas_2, etiquetas_2 = eje2.get_legend_handles_labels()
    eje.legend(lineas_1 + lineas_2, etiquetas_1 + etiquetas_2, loc="upper left")
    figuras.append(_guardar_figura(figura, "01_evolucion_por_anio.png"))

    anio_min, anio_max = int(por_anio["Año"].min()), int(por_anio["Año"].max())
    anio_top = por_anio.sort_values("Juegos en la muestra", ascending=False).iloc[0]
    hallazgos.append(
        f"**Evolución temporal.** La muestra contiene juegos lanzados entre {anio_min} y {anio_max}. "
        f"El año con más lanzamientos dentro de la muestra es {int(anio_top['Año'])} "
        f"con {int(anio_top['Juegos en la muestra'])} juegos."
    )

    # Detalle de los últimos meses (si hay datos de día/mes)
    recientes = tabla.dropna(subset=["fecha_lanzamiento"])
    if not recientes.empty:
        fechas = pd.to_datetime(recientes["fecha_lanzamiento"], errors="coerce")
        recientes = recientes.assign(mes_lanzamiento=fechas.dt.to_period("M"))
        recientes = recientes.dropna(subset=["mes_lanzamiento"])
    if not recientes.empty:
        limite = recientes["mes_lanzamiento"].max() - 17
        recientes = recientes[recientes["mes_lanzamiento"] >= limite]
        por_mes = (
            recientes.groupby("mes_lanzamiento")
            .agg(juegos=("appid", "count"), precio_mediano=("precio_final", "median"))
            .reset_index()
        )
        if len(por_mes) >= 2:
            por_mes["mes_lanzamiento"] = por_mes["mes_lanzamiento"].astype(str)
            por_mes = por_mes.rename(
                columns={"mes_lanzamiento": "Mes", "juegos": "Juegos lanzados",
                         "precio_mediano": f"Precio mediano ({moneda})"}
            )
            guardar_csv(por_mes, config.TABLAS / "01b_lanzamientos_ultimos_meses.csv")
    return figuras


# ---------------------------------------------------------------------------
# Pregunta 2: géneros
# ---------------------------------------------------------------------------

def _analizar_generos(tabla: pd.DataFrame, hallazgos: list[str]) -> list[str]:
    figuras: list[str] = []
    ruta_generos = config.PROCESADO / "consolidado" / "generos_explotado.csv"
    if not ruta_generos.exists():
        # La tabla explotada se genera en la consolidación; si falta, la regeneramos.
        try:
            limpiar.consolidar()
        except FileNotFoundError:
            return figuras
    if not ruta_generos.exists():
        return figuras
    generos = pd.read_csv(ruta_generos)
    if generos.empty:
        return figuras
    moneda = _moneda(tabla)

    resumen = (
        generos.groupby("genero")
        .agg(
            juegos=("appid", "nunique"),
            resenas_totales=("resenas_totales", "sum"),
            resenas_medianas=("resenas_totales", "median"),
            precio_mediano=("precio_final", "median"),
            jugadores_totales=("jugadores_actuales", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "genero": "Género",
                "juegos": "Juegos",
                "resenas_totales": "Reseñas totales",
                "resenas_medianas": "Reseñas medianas",
                "precio_mediano": f"Precio mediano ({moneda})",
                "jugadores_totales": "Jugadores actuales (suma)",
            }
        )
        .sort_values("Juegos", ascending=False)
    )
    guardar_csv(resumen, config.TABLAS / "02_generos.csv")

    top_presencia = resumen.head(15)
    top_popularidad = resumen.sort_values("Jugadores actuales (suma)", ascending=False).head(15)

    figura, (eje_a, eje_b) = plt.subplots(1, 2, figsize=(12, 5))
    eje_a.barh(top_presencia["Género"][::-1], top_presencia["Juegos"][::-1], color="#4c72b0")
    eje_a.set_title("Top 15 géneros por cantidad de juegos")
    eje_a.set_xlabel("Juegos en la muestra")
    eje_b.barh(top_popularidad["Género"][::-1], top_popularidad["Jugadores actuales (suma)"][::-1], color="#55a868")
    eje_b.set_title("Top 15 géneros por jugadores actuales (suma)")
    eje_b.set_xlabel("Jugadores actuales")
    figuras.append(_guardar_figura(figura, "02_generos.png"))

    genero_top = resumen.iloc[0]
    genero_popular = top_popularidad.iloc[0]
    hallazgos.append(
        f"**Géneros.** El género más presente en la muestra es {genero_top['Género']} "
        f"({int(genero_top['Juegos'])} juegos). El género con más jugadores actuales sumados "
        f"es {genero_popular['Género']} ({int(genero_popular['Jugadores actuales (suma)'] or 0):,} jugadores).".replace(",", ".")
    )
    return figuras


# ---------------------------------------------------------------------------
# Pregunta 3: características vs interacción de usuarios
# ---------------------------------------------------------------------------

def _analizar_caracteristicas(tabla: pd.DataFrame, hallazgos: list[str]) -> list[str]:
    figuras: list[str] = []
    presentes = [columna for columna in COLUMNAS_NUMERICAS if columna in tabla.columns]
    # Spearman = Pearson sobre los rangos (evita depender de scipy)
    correlaciones = tabla[presentes].rank().corr(method="pearson", min_periods=5)
    correlaciones = correlaciones.rename(index=NOMBRES_NUMERICAS, columns=NOMBRES_NUMERICAS)
    guardar_csv(correlaciones.round(3).reset_index(names="Variable"), config.TABLAS / "03_correlaciones.csv")

    figura, eje = plt.subplots(figsize=(8.5, 7))
    datos = correlaciones.values
    imagen = eje.imshow(datos, cmap="RdBu_r", vmin=-1, vmax=1)
    eje.set_xticks(range(len(correlaciones.columns)), correlaciones.columns, rotation=45, ha="right")
    eje.set_yticks(range(len(correlaciones.index)), correlaciones.index)
    for fila in range(len(correlaciones.index)):
        for columna in range(len(correlaciones.columns)):
            valor = datos[fila, columna]
            if pd.notna(valor):
                eje.text(columna, fila, f"{valor:.2f}", ha="center", va="center", fontsize=7)
    figura.colorbar(imagen, shrink=0.8, label="Correlación de Spearman")
    eje.set_title("Relación entre características y actividad de usuarios (Spearman)")
    eje.grid(False)
    figuras.append(_guardar_figura(figura, "03_correlaciones.png"))

    # Comparación gratis vs pago
    if "es_gratis" in tabla.columns and tabla["es_gratis"].any():
        comparacion = (
            tabla.groupby("es_gratis")
            .agg(
                juegos=("appid", "count"),
                resenas_medianas=("resenas_totales", "median"),
                jugadores_medianos=("jugadores_actuales", "median"),
                positivas_medianas=("pct_resenas_positivas", "median"),
            )
            .reset_index()
            .assign(
                Tipo=lambda df: df["es_gratis"].map({True: "Gratis", False: "Pago"}),
            )
            .rename(
                columns={
                    "juegos": "Juegos",
                    "resenas_medianas": "Reseñas medianas",
                    "jugadores_medianos": "Jugadores medianos",
                    "positivas_medianas": "% positivas mediano",
                }
            )[["Tipo", "Juegos", "Reseñas medianas", "Jugadores medianos", "% positivas mediano"]]
        )
        guardar_csv(comparacion, config.TABLAS / "03b_gratis_vs_pago.csv")

        figura, (eje_a, eje_b) = plt.subplots(1, 2, figsize=(9, 4))
        eje_a.bar(comparacion["Tipo"], comparacion["Reseñas medianas"], color=["#55a868", "#4c72b0"])
        eje_a.set_title("Reseñas medianas por tipo de juego")
        eje_b.bar(comparacion["Tipo"], comparacion["Jugadores medianos"], color=["#55a868", "#4c72b0"])
        eje_b.set_title("Jugadores actuales medianos")
        figuras.append(_guardar_figura(figura, "04_gratis_vs_pago.png"))

        gratis = comparacion.set_index("Tipo")
        if {"Gratis", "Pago"}.issubset(gratis.index):
            hallazgos.append(
                f"**Gratis vs pago.** Los juegos gratuitos de la muestra tienen una mediana de "
                f"{int(gratis.loc['Gratis', 'Reseñas medianas'] or 0):,} reseñas frente a "
                f"{int(gratis.loc['Pago', 'Reseñas medianas'] or 0):,} de los pagos.".replace(",", ".")
            )

    # Precio vs cantidad de reseñas
    consulta = tabla.dropna(subset=["precio_final", "resenas_totales"])
    consulta = consulta[consulta["resenas_totales"] > 0]
    if len(consulta) >= 5:
        figura, eje = plt.subplots(figsize=(8, 4.5))
        eje.scatter(consulta["precio_final"], consulta["resenas_totales"], alpha=0.5, color="#4c72b0")
        eje.set_yscale("log")
        eje.set_xlabel("Precio final (ARS)")
        eje.set_ylabel("Reseñas totales (escala logarítmica)")
        eje.set_title("Precio vs cantidad de reseñas de usuarios")
        figuras.append(_guardar_figura(figura, "05_precio_vs_resenas.png"))

        correlacion = (
            consulta["precio_final"].rank().corr(consulta["resenas_totales"].rank())
        )
        if pd.notna(correlacion):
            direccion = "positiva" if correlacion > 0 else "negativa"
            fuerza = "débil" if abs(correlacion) < 0.3 else ("moderada" if abs(correlacion) < 0.6 else "fuerte")
            hallazgos.append(
                f"**Precio y reseñas.** La correlación de Spearman entre precio final y cantidad de "
                f"reseñas es {correlacion:.2f} ({direccion}, {fuerza})."
            )

    fuerte = correlaciones.stack().reset_index()
    fuerte.columns = ["Variable 1", "Variable 2", "Spearman"]
    fuerte = fuerte.dropna(subset=["Spearman"])
    fuertes = fuerte[
        (fuerte["Variable 1"] < fuerte["Variable 2"]) & (fuerte["Spearman"].abs() >= 0.4)
    ].sort_values("Spearman", key=lambda serie: serie.abs(), ascending=False)
    if not fuertes.empty:
        par = fuertes.iloc[0]
        hallazgos.append(
            f"**Relación más fuerte.** La asociación más marcada es entre {par['Variable 1']} y "
            f"{par['Variable 2']} (Spearman = {par['Spearman']:.2f})."
        )
    return figuras


# ---------------------------------------------------------------------------
# Pregunta 4: precios y descuentos
# ---------------------------------------------------------------------------

def _analizar_precios(tabla: pd.DataFrame, hallazgos: list[str]) -> list[str]:
    figuras: list[str] = []
    con_precio = tabla.dropna(subset=["precio_final"]).copy()
    if con_precio.empty:
        return figuras
    moneda = _moneda(tabla)

    # Rangos de precio por cuantiles (se adaptan a precios argentinos sin asumir valores fijos)
    etiquetas = ["Más barato", "Barato", "Medio", "Caro", "Más caro"]
    try:
        con_precio["rango_precio"] = pd.qcut(
            con_precio["precio_final"], q=5, labels=etiquetas, duplicates="drop"
        )
    except ValueError:
        con_precio["rango_precio"] = pd.cut(con_precio["precio_final"], bins=5, labels=etiquetas)

    rangos = (
        con_precio.groupby("rango_precio", observed=True)
        .agg(
            juegos=("appid", "count"),
            precio_min=("precio_final", "min"),
            precio_max=("precio_final", "max"),
            resenas_medianas=("resenas_totales", "median"),
            jugadores_medianos=("jugadores_actuales", "median"),
            descuento_promedio=("descuento_pct", "mean"),
            pct_positivas=("pct_resenas_positivas", "median"),
        )
        .reset_index()
        .rename(
            columns={
                "rango_precio": "Rango de precio",
                "juegos": "Juegos",
                "precio_min": f"Precio mínimo ({moneda})",
                "precio_max": f"Precio máximo ({moneda})",
                "resenas_medianas": "Reseñas medianas",
                "jugadores_medianos": "Jugadores medianos",
                "descuento_promedio": "Descuento promedio",
                "pct_positivas": "% positivas mediano",
            }
        )
    )
    guardar_csv(rangos, config.TABLAS / "04_rangos_precio.csv")

    figura, (eje_a, eje_b) = plt.subplots(1, 2, figsize=(11, 4.5))
    eje_a.bar(rangos["Rango de precio"].astype(str), rangos["Juegos"], color="#4c72b0")
    eje_a.set_title("Cantidad de juegos por rango de precio")
    eje_a.set_ylabel("Juegos")
    eje_b.bar(rangos["Rango de precio"].astype(str), rangos["Reseñas medianas"], color="#c44e52")
    eje_b.set_title("Reseñas medianas por rango de precio")
    eje_b.set_ylabel("Reseñas (mediana)")
    figuras.append(_guardar_figura(figura, "06_rangos_precio.png"))

    # Descuentos
    con_precio["tramo_descuento"] = pd.cut(
        con_precio["descuento_pct"].fillna(0),
        bins=[-1, 0, 25, 50, 75, 100],
        labels=["Sin descuento", "1-25%", "26-50%", "51-75%", "76-100%"],
    )
    tramos = (
        con_precio.groupby("tramo_descuento", observed=True)
        .agg(
            juegos=("appid", "count"),
            resenas_medianas=("resenas_totales", "median"),
            jugadores_medianos=("jugadores_actuales", "median"),
            pct_positivas=("pct_resenas_positivas", "median"),
        )
        .reset_index()
        .rename(
            columns={
                "tramo_descuento": "Tramo de descuento",
                "juegos": "Juegos",
                "resenas_medianas": "Reseñas medianas",
                "jugadores_medianos": "Jugadores medianos",
                "pct_positivas": "% positivas mediano",
            }
        )
    )
    guardar_csv(tramos, config.TABLAS / "05_descuentos.csv")

    figura, (eje_a, eje_b) = plt.subplots(1, 2, figsize=(11, 4.5))
    eje_a.bar(tramos["Tramo de descuento"].astype(str), tramos["Juegos"], color="#8172b3")
    eje_a.set_title("Juegos por tramo de descuento")
    eje_a.set_ylabel("Juegos")
    eje_b.bar(tramos["Tramo de descuento"].astype(str), tramos["Jugadores medianos"], color="#55a868")
    eje_b.set_title("Jugadores actuales medianos por tramo de descuento")
    eje_b.set_ylabel("Jugadores (mediana)")
    figuras.append(_guardar_figura(figura, "07_descuentos.png"))

    gratis = int(tabla["es_gratis"].sum()) if "es_gratis" in tabla.columns else 0
    mediana = tabla["precio_final"].median()
    hallazgos.append(
        f"**Precios y descuentos.** Precio mediano de la muestra: {_formatear_moneda(mediana, moneda)} "
        f"(los juegos gratuitos tienen precio 0 y son {gratis} de {len(tabla)}). "
        f"En este recorte, {int((tabla['descuento_pct'].fillna(0) > 0).sum())} juegos tienen descuento activo."
    )
    return figuras


# ---------------------------------------------------------------------------
# Pregunta 5: comportamiento de los usuarios
# ---------------------------------------------------------------------------

def _analizar_usuarios(tabla: pd.DataFrame, hallazgos: list[str]) -> list[str]:
    figuras: list[str] = []
    moneda = _moneda(tabla)

    top = (
        tabla.dropna(subset=["jugadores_actuales"])
        .sort_values("jugadores_actuales", ascending=False)
        .head(20)[["nombre", "jugadores_actuales", "pico_en_juego", "rank_mas_jugados",
                   "resenas_totales", "pct_resenas_positivas", "precio_final"]]
        .rename(
            columns={
                "nombre": "Juego",
                "jugadores_actuales": "Jugadores actuales",
                "pico_en_juego": "Pico semanal",
                "rank_mas_jugados": "Rank de más jugados",
                "resenas_totales": "Reseñas totales",
                "pct_resenas_positivas": "% positivas",
                "precio_final": f"Precio final ({moneda})",
            }
        )
        .reset_index(drop=True)
    )
    if top.empty:
        return figuras

    guardar_csv(top, config.TABLAS / "06_top_jugadores.csv")

    figura, eje = plt.subplots(figsize=(8, 6))
    nombres = top["Juego"].str.slice(0, 28)[::-1]
    eje.barh(nombres, top["Jugadores actuales"][::-1], color="#4c72b0")
    eje.set_title("Top 20 juegos de la muestra por jugadores conectados")
    eje.set_xlabel("Jugadores actuales")
    figuras.append(_guardar_figura(figura, "08_top_jugadores.png"))

    primero = top.iloc[0]
    hallazgos.append(
        f"**Uso de la plataforma.** El juego con más jugadores conectados al momento de la extracción "
        f"es {primero['Juego']} con {int(primero['Jugadores actuales']):,} jugadores.".replace(",", ".")
    )

    # Valoración vs volumen de reseñas: ¿los juegos menos masivos tienen mejor valoración?
    consulta = tabla.dropna(subset=["resenas_totales", "pct_resenas_positivas"])
    consulta = consulta[consulta["resenas_totales"] >= 10]
    if len(consulta) >= 10:
        consulta = consulta.copy()
        consulta["decil_resenas"] = pd.qcut(consulta["resenas_totales"], 5, duplicates="drop")
        por_decil = (
            consulta.groupby("decil_resenas", observed=True)
            .agg(
                juegos=("appid", "count"),
                resenas_medianas=("resenas_totales", "median"),
                pct_positivas_mediano=("pct_resenas_positivas", "median"),
            )
            .reset_index()
            .rename(
                columns={
                    "decil_resenas": "Grupo por cantidad de reseñas",
                    "juegos": "Juegos",
                    "resenas_medianas": "Reseñas medianas del grupo",
                    "pct_positivas_mediano": "% positivas mediano",
                }
            )
        )
        guardar_csv(por_decil, config.TABLAS / "07_valoracion_por_volumen.csv")

        figura, eje = plt.subplots(figsize=(8, 4.5))
        eje.bar(
            por_decil["Grupo por cantidad de reseñas"].astype(str),
            por_decil["% positivas mediano"],
            color="#dd8452",
        )
        eje.set_title("Valoración mediana según el volumen de reseñas")
        eje.set_ylabel("% de reseñas positivas (mediana)")
        eje.set_ylim(0, 100)
        figura.autofmt_xdate(rotation=20)
        figuras.append(_guardar_figura(figura, "09_valoracion_por_volumen.png"))

    # Antigüedad vs jugadores
    consulta = tabla.dropna(subset=["edad_anios", "jugadores_actuales"])
    if len(consulta) >= 5:
        figura, eje = plt.subplots(figsize=(8, 4.5))
        eje.scatter(consulta["edad_anios"], consulta["jugadores_actuales"], alpha=0.5, color="#8172b3")
        eje.set_yscale("log")
        eje.set_xlabel("Antigüedad del juego (años)")
        eje.set_ylabel("Jugadores actuales (escala logarítmica)")
        eje.set_title("Antigüedad vs jugadores conectados")
        figuras.append(_guardar_figura(figura, "10_antiguedad_vs_jugadores.png"))
    return figuras


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def analizar_todo() -> dict:
    """Corre todas las preguntas del análisis y genera tablas, figuras y resumen."""
    tabla = _preparar_tabla_cruda()
    if tabla is None or tabla.empty:
        print("[analisis] No hay datos para analizar. Corré primero el pipeline de extracción.")
        return {}

    print(f"[analisis] Analizando {len(tabla)} juegos...")
    hallazgos: list[str] = [
        f"El dataset consolidado tiene **{len(tabla)} juegos** (de los lotes "
        f"{sorted(int(l) for l in tabla['lote_origen'].unique()) if 'lote_origen' in tabla.columns else 'sin dato'}). "
        "Es una muestra del catálogo de Steam ordenada por lanzamiento reciente, "
        "no el catálogo completo.",
    ]
    figuras: list[str] = []
    figuras += _analizar_evolucion(tabla, hallazgos)
    figuras += _analizar_generos(tabla, hallazgos)
    figuras += _analizar_caracteristicas(tabla, hallazgos)
    figuras += _analizar_precios(tabla, hallazgos)
    figuras += _analizar_usuarios(tabla, hallazgos)
    tabla_externa = _analizar_datos_externos(hallazgos, figuras)

    lineas = [
        "# Resumen del análisis exploratorio",
        "",
        "Generado automáticamente por `src/analisis/exploratorio.py`.",
        "Las tablas completas están en `reportes/tablas/` y los gráficos en `reportes/figuras/`.",
        "",
        "## Hallazgos principales",
        "",
    ]
    lineas += [f"- {hallazgo}" for hallazgo in hallazgos]

    # Tablas clave copiadas al resumen para leerlas sin abrir los CSV
    tablas_incluidas = [
        ("01_lanzamientos_por_anio.csv", "Lanzamientos por año (últimos 12 años de la muestra)", 12),
        ("02_generos.csv", "Géneros con más presencia", 10),
        ("04_rangos_precio.csv", "Rangos de precio", 10),
        ("05_descuentos.csv", "Tramos de descuento", 10),
        ("06_top_jugadores.csv", "Top de jugadores conectados", 10),
    ]
    for nombre_archivo, titulo, filas in tablas_incluidas:
        ruta = config.TABLAS / nombre_archivo
        if not ruta.exists():
            continue
        tabla = pd.read_csv(ruta)
        if nombre_archivo == "01_lanzamientos_por_anio.csv" and len(tabla) > filas:
            tabla = tabla.tail(filas)
        lineas += ["", f"### {titulo}", "", _tabla_a_markdown(tabla, max_filas=filas)]

    if tabla_externa is not None and not tabla_externa.empty:
        lineas += [
            "",
            "### Datos externos incorporados",
            "",
            _tabla_a_markdown(tabla_externa, max_filas=15),
        ]

    lineas += ["", "## Gráficos generados", ""]
    lineas += [f"- `reportes/figuras/{nombre}`" for nombre in figuras]
    guardar_texto("\n".join(lineas) + "\n", config.REPORTES / "resumen_analisis.md")
    print(f"[analisis] Resumen guardado en reportes/resumen_analisis.md ({len(figuras)} figuras).")
    return {"juegos": len(tabla), "figuras": figuras, "hallazgos": hallazgos}
