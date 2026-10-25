# Diccionario de datos y ficha de evaluación

> Documento de la **Parte 1** del trabajo práctico: evaluación del conjunto de datos elegido,
> su diccionario de variables y las decisiones de limpieza. Complementa al `README.md`.

---

## 1. Ficha de evaluación del dataset

### 1.1 Identificación

| Campo | Detalle |
|---|---|
| **Nombre del dataset** | Mercado de videojuegos de Steam (muestra ordenada por lanzamiento reciente) |
| **Proyecto** | Steam Market Analysis — pipeline de extracción, IA local y análisis exploratorio |
| **Fuente 1** | API pública y tienda de Steam (`store.steampowered.com`): buscador con filtro `category1=998`, `api/appdetails`, `ISteamUserStats/GetNumberOfCurrentPlayers`, `ISteamChartsService/GetMostPlayedGames`, `appreviews` |
| **Fuente 2** | SteamDB (`steamdb.info`): tabla de más jugados y páginas de apps (con fallback Playwright + Firefox frente a Cloudflare) |
| **Fuente 3** | Datos externos aceptados: PDF, imagen, CSV, JSON, TXT, HTML, DOCX, XLSX, URL o texto pegado (procesados por IA portero + OCR autorizado) |
| **Quién lo produce** | Valve Corporation (Steam) para las fuentes 1 y 2; la fuente 3 depende del documento aportado por el equipo |
| **Finalidad original** | Catálogo comercial y estadísticas públicas de la plataforma Steam; no fue creado para investigación |
| **Actualización** | Extracción en vivo al momento de correr el pipeline (reportes generados el 2026-10-17). No es un snapshot estático: cada corrida re-extrae |
| **Formato** | CSV, JSON y PNG (tablas, dataset consolidado y figuras) |
| **Licencia / uso** | Datos públicos de Steam; el proyecto se distribuye bajo MIT |

### 1.2 Qué representa cada registro

- **`juegos_consolidado.csv`**: **una fila por juego** (identificado por `appid` de Steam).
- **`generos_explotado.csv`**: **una fila por par juego–género** (explosión de la lista de géneros).
- **`datos_externos.csv`**: **una fila por cifra de mercado externa** aceptada por la IA portero.
- **`juegos_externos.csv`**: juegos mencionados en la fuente externa que no coincidieron con el dataset principal.

### 1.3 Período y territorio

| Aspecto | Detalle |
|---|---|
| **Período** | Muestra de 100 juegos ordenados por lanzamiento reciente (año 2026 en el recorte analizado). No es el catálogo completo de Steam |
| **Territorio** | Global: Steam es una plataforma internacional; los precios se expresan en USD y las reseñas son agregadas de todos los idiomas/países |
| **Cobertura** | Catálogo completo disponible por lotes no superpuestos (`--lote`, `--cantidad`); la muestra analizada es un recorte de 1 lote de 100 juegos |

### 1.4 Documentación disponible

- `README.md`: instalación, flujo del pipeline, comandos, fuentes, salidas y esquema del dataset.
- `.env.example`: variables de configuración.
- `tests/`: 12 pruebas unitarias (sin dependencia de Ollama).
- `reportes/resumen_analisis.md`: hallazgos generados automáticamente por `src/analisis/exploratorio.py`.
- Este documento: diccionario de variables y ficha de evaluación.

### 1.5 Calidad de los datos

| Aspecto | Situación y tratamiento |
|---|---|
| **Datos faltantes** | `metacritic` suele estar vacío; `steamdb_*` solo se completa cuando el juego aparece en la tabla de SteamDB; `ext_*` solo cuando hay coincidencia con datos externos; `pico semanal`/`rank` vacíos en juegos fuera del top. Se conservan como nulos y se excluyen de las agregaciones que los requieran |
| **Duplicados** | Consolidación por `appid` (clave única). La columna `lote_origen` permite rastrear de qué lote vino cada fila. No se duplican appids en el consolidado |
| **Categorías inconsistentes** | Los géneros llegan como listas separadas por coma; se normalizan y se explotan en `generos_explotado.csv`. Las categorías de plataforma (windows/mac/linux) se booleanizan |
| **No-juegos** | DLC, software, demos, bandas sonoras y videos se descartan y se guardan en `data/descartado/` con motivo. La IA estructuradora valida cada `appid` contra el HTML original (anti-invención) |
| **Unidades de medida** | Precios en USD (`moneda`), fechas en ISO 8601, porcentajes en escala 0–100, conteos enteros (jugadores, reseñas, logros) |
| **Valores atípicos** | Rango de precios 0–27,59 USD; se segmentan en 5 tiers para el análisis. Los juegos gratuitos se tratan como un grupo aparte (`es_gratis`) |
| **Sesgo de la muestra** | La muestra está ordenada por lanzamiento reciente (2026), por lo que sobrerrepresenta juegos nuevos y subrepresenta títulos antiguos/populares de SteamDB |

### 1.6 Justificación de la elección

Se priorizó este conjunto por su **potencial analítico**: permite estudiar evoluciones temporales, comparar géneros, analizar relaciones entre precio/descuentos/reseñas/jugadores y segmentar por rangos. Es **real, con fuente identificable** (Steam/SteamDB) y en **formatos reutilizables** (CSV/JSON), aptos para importar en Looker Studio. No es una tabla ya resumida ni datos ficticios.

---

## 2. Diccionario de variables — `juegos_consolidado.csv`

### 2.1 Identificación

| Columna | Tipo | Descripción |
|---|---|---|
| `appid` | int | Identificador único del juego en Steam (clave primaria) |
| `nombre` | string | Nombre comercial del juego |
| `lote_origen` | int | Número de lote del que proviene la fila |

### 2.2 Precio

| Columna | Tipo | Descripción |
|---|---|---|
| `es_gratis` | bool | `True` si el juego es gratuito |
| `precio_inicial` | float (USD) | Precio de lista original |
| `precio_final` | float (USD) | Precio actual (con descuento aplicado) |
| `descuento_pct` | int (%) | Porcentaje de descuento activo |
| `moneda` | string | Moneda del precio (USD) |

### 2.3 Fechas

| Columna | Tipo | Descripción |
|---|---|---|
| `fecha_texto` | string | Fecha de lanzamiento tal como aparece en Steam |
| `fecha_lanzamiento` | date (ISO) | Fecha de lanzamiento parseada |
| `precision_fecha` | string | Precisión de la fecha (`day`, `month`, `year`) |
| `anio` | int | Año de lanzamiento |
| `mes` | int | Mes de lanzamiento |
| `edad_anios` | float | Antigüedad del juego en años |
| `proximo_lanzamiento` | bool | `True` si aún no se lanzó |

### 2.4 Clasificación

| Columna | Tipo | Descripción |
|---|---|---|
| `desarrolladores` | string | Estudio(s) desarrollador(es) |
| `editores` | string | Editor(es) |
| `generos` | string (lista) | Géneros separados por coma |
| `cantidad_generos` | int | Cantidad de géneros asignados |
| `categorias` | string (lista) | Categorías de Steam (multijugador, cooperativo, etc.) |
| `cantidad_categorias` | int | Cantidad de categorías |

### 2.5 Características

| Columna | Tipo | Descripción |
|---|---|---|
| `tiene_un_jugador` | bool | Soporta un jugador |
| `tiene_multijugador` | bool | Soporta multijugador |
| `tiene_cooperativo` | bool | Soporta cooperativo |
| `tiene_logros` | bool | Tiene logros de Steam |
| `cantidad_logros` | int | Cantidad de logros |
| `windows` / `mac` / `linux` | bool | Plataformas soportadas |
| `metacritic` | int (0–100) | Puntaje Metacritic (cuando existe) |
| `recomendaciones_steam` | int | Recomendaciones en Steam |
| `cantidad_dlc` | int | Cantidad de DLC |
| `descripcion_corta` | string | Descripción corta del juego |

### 2.6 Actividad

| Columna | Tipo | Descripción |
|---|---|---|
| `jugadores_actuales` | int | Jugadores conectados en el momento de la extracción |
| `rank_mas_jugados` | int | Posición en el ranking de más jugados |
| `pico_en_juego` | int | Pico histórico de jugadores concurrentes |
| `resenas_totales` | int | Cantidad total de reseñas |
| `resenas_positivas` | int | Reseñas positivas |
| `resenas_negativas` | int | Reseñas negativas |
| `pct_resenas_positivas` | float (%) | Porcentaje de reseñas positivas |
| `nota_resenas` | string | Etiqueta de Steam (Overwhelmingly Positive, Mixed, etc.) |

### 2.7 SteamDB (cuando hay coincidencia)

| Columna | Tipo | Descripción |
|---|---|---|
| `steamdb_jugadores` | int | Jugadores según SteamDB |
| `steamdb_pico_24h` | int | Pico de las últimas 24 h |
| `steamdb_pico_historico` | int | Pico histórico según SteamDB |
| `steamdb_rank` | int | Ranking en SteamDB |

### 2.8 Datos externos (cuando hay coincidencia)

| Columna | Tipo | Descripción |
|---|---|---|
| `ext_precio` | float | Precio externo |
| `ext_descuento_pct` | int (%) | Descuento externo |
| `ext_fecha_lanzamiento` | date | Fecha externa |
| `ext_jugadores_actuales` | int | Jugadores externos |
| `ext_resenas_totales` | int | Reseñas externas |
| `ext_positivas_pct` | float (%) | % positivas externo |
| `ext_generos` | string (lista) | Géneros externos |
| `ext_notas` | string | Notas del dato externo |
| `ext_fuente` | string | Origen del dato externo |
| `ext_documento` | string | Documento del que proviene |

---

## 3. Diccionario — `generos_explotado.csv`

| Columna | Tipo | Descripción |
|---|---|---|
| `appid` | int | Identificador del juego (FK a `juegos_consolidado`) |
| `nombre` | string | Nombre del juego |
| `genero` | string | Un género por fila |

---

## 4. Diccionario — tablas de reporte

Las tablas de `reportes/tablas/*.csv` son agregaciones del dataset consolidado:

| Archivo | Granularidad | Métricas principales |
|---|---|---|
| `01_lanzamientos_por_anio.csv` | por año | juegos, precio mediano, reseñas medianas, jugadores (suma) |
| `02_generos.csv` | por género | juegos, reseñas totales/medianas, precio mediano, jugadores (suma) |
| `03_correlaciones.csv` | matriz Spearman | correlaciones entre variables numéricas |
| `03b_gratis_vs_pago.csv` | por tipo (gratis/pago) | juegos, reseñas medianas, jugadores medianos, % positivas mediano |
| `04_rangos_precio.csv` | por tier de precio | juegos, min/max precio, reseñas medianas, jugadores medianos, descuento promedio, % positivas mediano |
| `05_descuentos.csv` | por tramo de descuento | juegos, reseñas medianas, jugadores medianos, % positivas mediano |
| `06_top_jugadores.csv` | por juego (top) | jugadores actuales, pico semanal, rank, reseñas, % positivas, precio |
| `07_valoracion_por_volumen.csv` | por grupo de reseñas | juegos, reseñas medianas del grupo, % positivas mediano |
| `07_datos_externos.csv` | por tipo de dato externo | cifras de mercado externas (si hay) |

---

## 5. Notas para Looker Studio

- **Fuente de datos recomendada:** `data/procesado/consolidado/juegos_consolidado.csv` como tabla principal; `generos_explotado.csv` para análisis por género.
- **Dimensiones sugeridas:** `generos`, `anio`, `moneda`, `nota_resenas`, `plataforma` (derivada de windows/mac/linux).
- **Métricas sugeridas:** `precio_final`, `descuento_pct`, `jugadores_actuales`, `resenas_totales`, `pct_resenas_positivas`, `cantidad_logros`.
- **Filtros útiles:** `es_gratis`, rango de precio, rango de fechas, presencia de SteamDB (`steamdb_rank` no nulo).
- Los valores nulos (Metacritic, SteamDB, externos) deben tratarse como "sin dato" y no como cero.
