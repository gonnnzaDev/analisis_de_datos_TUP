# Análisis del mercado de videojuegos en Steam

Trabajo práctico final – Parte 1 (Introducción al Análisis de Datos).

El pipeline extrae información de videojuegos de Steam, usa una **IA local (Ollama)** para
estructurar el texto de las páginas y clasificar qué es un juego y qué no, limpia los datos
con pandas y genera tablas y gráficos que responden a las preguntas preliminares del trabajo.
La IA trabaja con **tres fuentes**: la API/tienda de Steam, SteamDB y los datos externos
aceptados.

Además, acepta **datos externos de cualquier formato** (PDF, imágenes, CSV, JSON, TXT, HTML,
DOCX, XLSX, URLs o texto pegado): una IA "portero" decide si aportan al análisis y, si el
contenido es una imagen o un escaneo, otra IA hace el OCR **solo después de que la persona
lo autorice**. Lo aceptado se convierte en la tercera fuente de la IA estructuradora.

Integrantes: Maximo Luzkevich Marino, Giovanni Morro Bai, Gonzalo Leonel Lopez, Eloy Roldan Quevedo.

---

## Arquitectura por módulos

| Módulo | Archivo | Qué hace |
|---|---|---|
| Configuración | `src/config.py` | Rutas, parámetros y lectura del archivo `.env` |
| HTTP | `src/http_util.py` | Sesión con cabeceras de navegador y pausas para no saturar a Steam |
| Extracción Steam | `src/extraccion/steam_api.py` | Buscador de la tienda + ficha de cada app + jugadores + reseñas |
| Extracción SteamDB | `src/extraccion/steamdb.py` | Páginas de SteamDB por requests y, si Cloudflare bloquea, con navegador Firefox |
| IA local | `src/ia/ollama_cliente.py` | Cliente HTTP contra Ollama (JSON estricto, reintentos, bitácora) |
| IA estructuradora | `src/ia/estructurador.py` | Convierte texto de SteamDB, la tienda y los datos externos en filas JSON; **solo deja juegos** y descarta DLC, software, demos, etc. |
| Ingesta: archivos | `src/ingesta/archivos.py` | Detecta y guarda cualquier dato (archivo, carpeta, URL, texto) y extrae el texto cuando se puede |
| Ingesta: portero IA | `src/ingesta/verificador.py` | Decide si el dato aporta al análisis (`sirve`, relevancia, datos aprovechables, si necesita OCR) |
| Ingesta: OCR | `src/ingesta/ocr.py` | Transcribe imágenes y PDFs escaneados con una IA de visión (o Tesseract), solo si se autoriza |
| Ingesta: orquestador | `src/ingesta/gestor.py` | Encadena guardado, verificación, autorización, OCR y resumen |
| Limpieza | `src/limpieza/limpiar.py` | Normaliza precios, fechas, géneros, plataformas; cruza reseñas y jugadores; consolida lotes |
| Almacenamiento | `src/almacenamiento/guardar.py` | Guarda/lee JSON y CSV y reporta el estado de los lotes |
| Análisis | `src/analisis/exploratorio.py` | Responde las 5 preguntas del TP con tablas CSV, gráficos PNG y un resumen en Markdown |
| Orquestador | `pipeline.py` | Comandos para correr todo por lotes y para ingerir datos externos |

Flujo de datos:

```
tienda/API de Steam ──┐
                      │
SteamDB (navegador) ──┼─► data/crudo/ ──► IA local (Ollama) ──► data/intermedio/
                      │                          │
datos externos ───────┘                          ▼
(archivos/PDFs/URLs/texto)            limpieza (pandas) ──► data/procesado/
                                                 │
                                                 ▼
                          reportes/tablas/ + reportes/figuras/ + resumen_analisis.md

archivos / imágenes / PDFs / URLs / texto ──► IA portero ──┐
                                                          ├─► (si autorizás) IA de OCR
data/crudo/ingesta/ ◄─────────────────────────────────────┘
        │
        └─► data/intermedio/ingesta/ (texto + decisión + resumen)
                        │
                        └─► tercera fuente de la IA estructuradora
```

## Fuentes de datos

1. **API pública de Steam (sin API key)**
   - Buscador de la tienda con filtro `category1=998` (solo juegos): arma la lista de appids.
   - `api/appdetails`: ficha completa (tipo, precio, descuento, géneros, fecha, logros...).
   - `ISteamUserStats/GetNumberOfCurrentPlayers`: jugadores conectados ahora.
   - `ISteamChartsService/GetMostPlayedGames`: ranking semanal de más jugados.
   - `appreviews`: resumen de reseñas de usuarios (positivas/negativas).
2. **SteamDB** (`steamdb.info`): tabla de más jugados y páginas de apps. Está protegido por
   Cloudflare: el módulo primero prueba un pedido directo y, si lo bloquean, abre Firefox
   automatizado (Playwright). Si tampoco pasa, se registra el bloqueo y el pipeline continúa
   con los datos de la API (quedan guardadas las evidencias en `data/crudo/steamdb/`).
3. **Datos externos aceptados** (`ingerir`): cualquier archivo, PDF, imagen, URL o texto que
   la IA portero haya aceptado. Es la tercera fuente de la IA estructuradora: se procesa una
   sola vez, se guarda en `data/intermedio/ia_externa.json` y después se usa para enriquecer
   los juegos y para sumar cifras de mercado al informe.

La IA **no inventa** filas: el HTML del buscador se le entrega con el appid ya marcado y después
se valida que cada appid devuelto exista realmente en la página original
(las filas que no validan quedan registradas para el informe). Con SteamDB pasa lo mismo:
cada nombre que devuelve la IA se cruza contra los `data-appid` de la tabla del HTML.

Cuando hay datos de SteamDB para un juego, la consolidación agrega las columnas
`steamdb_jugadores`, `steamdb_pico_24h`, `steamdb_pico_historico` y `steamdb_rank`.
Para que el cruce dé resultados conviene extraer algún lote con `--orden Reviews_DESC`
o `Name_ASC`: la tabla de SteamDB es el top de juegos con más jugadores (títulos antiguos
y muy populares), que no aparecen en los lotes ordenados por lanzamiento reciente.

## Instalación

Requisitos: Linux, Python 3.11+, conexión a internet, unos 8 GB libres para los modelos de IA
(el de texto y, si usás OCR, el de visión).

```bash
# 1) Entorno de Python (sin sudo, con uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv venv .venv --python /usr/bin/python3
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt

# 2) Navegador para SteamDB (opcional pero recomendado)
.venv/bin/python -m playwright install firefox

# 3) IA local: Ollama + modelo (sin sudo)
curl -fL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o ollama.tar.zst
mkdir -p ~/.local/ollama && tar -I zstd -xf ollama.tar.zst -C ~/.local/ollama && rm ollama.tar.zst
./iniciar_ollama.sh

# 4) OCR (opcional): modelo de visión + herramientas de PDF
ollama pull qwen2.5vl:3b                 # IA que transcribe imágenes/escaneos
sudo apt install poppler-utils           # pdftoppm y pdftotext (PDF -> imagen/texto)
# Alternativa sin IA de visión: sudo apt install tesseract-ocr tesseract-ocr-spa
```

## Uso por lotes

Cada lote son 100 juegos (configurable con `--cantidad`). Los lotes no se superponen:
el lote N arranca donde termina el N-1.

```bash
source .venv/bin/activate

python pipeline.py verificar              # chequea IA, navegador y datos
python pipeline.py todo --lote 1          # extrae + SteamDB + IA + limpieza
python pipeline.py todo --lote 2
python pipeline.py todo --lote 3
python pipeline.py estado                 # muestra qué etapas están hechas

python pipeline.py consolidar             # junta todos los lotes
python pipeline.py analizar               # tablas + figuras + resumen
```

**Sugerencia para que la muestra cubra épocas distintas** (el buscador ordena por un criterio
a la vez): variar el orden entre lotes con `--orden`, por ejemplo:

```bash
python pipeline.py extraer --lote 1 --orden Released_DESC   # lanzamientos recientes
python pipeline.py extraer --lote 2 --orden Reviews_DESC    # los más reseñados de la historia
python pipeline.py extraer --lote 3 --orden Name_ASC        # mezcla de épocas y géneros
python pipeline.py extraer --lote 4 --orden Released_ASC    # los más antiguos de la tienda
```

Ese plan de 4 lotes x 100 juegos da una muestra más equilibrada para el informe.

Etapas sueltas (si querés controlar cada paso):

```bash
python pipeline.py extraer --lote 1 --cantidad 100       # API de Steam
python pipeline.py steamdb --lote 1                      # páginas de SteamDB
python pipeline.py estructurar --lote 1 --max-fragmentos 3   # prueba corta de la IA
python pipeline.py estructurar --fuente externa          # solo la tercera fuente
python pipeline.py limpiar --lote 1                      # pandas
```

`estructurar` con `--fuente auto` (el valor por defecto) procesa las tres fuentes. Los datos
externos ya estructurados se reutilizan sin volver a llamar a la IA; si agregás documentos
nuevos con `ingerir`, la próxima corrida los incorpora.

Notas de rendimiento:
- La extracción respeta pausas (1,7 s tienda / 0,5 s API) para no pasarse de los límites de Steam.
  Un lote de 100 juegos tarda unos 10 minutos.
- La IA corre en CPU: un fragmento tarda varios minutos. El avance se guarda fragmento a
  fragmento en `data/intermedio/ia_lote_XX_parcial.jsonl`; si cortás, al re-ejecutar retoma.
- Para probar la IA sin esperar mucho: `--max-fragmentos 1` o `--fuente tienda`.

## Carga de datos externos (cualquier dato)

Esta parte acepta datos que no vienen de la API de Steam: PDFs, imágenes, capturas,
CSV, JSON, TXT, HTML, DOCX, XLSX, URLs o texto pegado. La idea es sumar evidencia
al informe sin contaminar el dataset de juegos con filas inventadas o irrelevantes.

```bash
# Opción 1: dejar los archivos en entrada/ y procesar todo
cp informe.pdf captura.png entrada/
python pipeline.py ingerir

# Opción 2: pasar rutas, URLs o texto directamente
python pipeline.py ingerir informe.pdf captura.png
python pipeline.py ingerir https://ejemplo.com/tabla-de-precios.html
python pipeline.py ingerir --texto "Half-Life cuesta 9,99 USD y tiene 100.000 reseñas"

# Autorizar el OCR de antemano (corridas desatendidas)
python pipeline.py ingerir --autorizar-ocr

# Prohibir el OCR aunque la IA lo recomiende
python pipeline.py ingerir --sin-ocr

# Volver a procesar algo que ya estaba en data/intermedio/ingesta/
python pipeline.py ingerir --forzar
```

Si un dato quedó `pendiente_ocr` o con error, al volver a correr `ingerir` se
reintenta solo. `--forzar` rehace también los que ya estaban procesados o
rechazados.

Qué pasa con cada dato:

1. Se guarda una copia en `data/crudo/ingesta/<id>/original.<ext>` y se calcula su hash.
2. Se intenta extraer el texto de forma nativa: TXT/CSV/JSON/HTML, PDF con `pdftotext`
   o `pypdf`, y DOCX/XLSX/PPTX con LibreOffice. Si es imagen o PDF escaneado, no hay
   texto: queda marcado como `requiere_ocr`.
3. **IA portero** (`src/ingesta/verificador.py`) lee el texto (o mira la imagen) y
   devuelve `sirve`, `relevancia`, `tipo_dato`, `datos_aprovechables` y `motivo`.
   Si no aporta al análisis, se rechaza y **no se hace OCR**.
4. Si sirve pero el texto está en una imagen/escaneo, el programa **pregunta antes de
   hacer OCR**. Solo un "sí" explícito habilita la transcripción. La IA de visión
   (`OLLAMA_MODELO_VISION`, por defecto `qwen2.5vl:3b`) hace el OCR; si no está
   instalada se puede usar Tesseract (`INGESTA_MOTOR_OCR=tesseract`).
5. Si no había modelo de visión para verificar la imagen, se puede autorizar el OCR
   igual: cuando termina, la IA de texto verifica la transcripción y decide si sirve.
6. El texto final y la decisión quedan auditados en `data/intermedio/ingesta/<id>/`.
7. Al correr `estructurar` (o `todo`/`lotes` con la etapa IA), esos textos se mandan a la
   **IA estructuradora como tercera fuente**. La IA devuelve dos cosas:
   - `items`: juegos con datos concretos (precio, reseñas, jugadores, etc.);
   - `datos`: cifras de mercado que no encajan en un juego (totales, promedios, evolución).
   Los juegos que coinciden con el dataset se enriquecen en columnas `ext_*`; los que no
   coinciden quedan en `juegos_externos.csv`; las cifras van a `datos_externos.csv`.

Salidas:

```
data/crudo/ingesta/<id>/original.ext      copia inmutable del dato original
data/intermedio/ingesta/<id>/texto.txt    texto nativo u OCR listo para usar
data/intermedio/ingesta/<id>/texto_nativo.txt
data/intermedio/ingesta/<id>/texto_ocr.txt
data/intermedio/ingesta/<id>/decision.json   veredicto de la IA + autorización + motor
data/intermedio/ingesta/<id>/documento.json  metadatos del archivo
data/intermedio/ingesta/resumen_ingesta.md   resumen legible (aceptados, rechazados, pendientes)
data/intermedio/ingesta/resumen_ingesta.csv  el mismo resumen en tabla
data/descartado/ingesta_rechazada.csv        motivos de rechazo (para el informe)
data/intermedio/ia_externa.json              items y datos de la tercera fuente
data/intermedio/ia_externa_parcial.jsonl     avance reanudable de la IA externa
data/procesado/consolidado/datos_externos.csv   cifras de mercado externas
data/procesado/consolidado/juegos_externos.csv  juegos externos sin coincidencia
```

Los textos aceptados se usan como **tercera fuente de la IA estructuradora**. No se
inyectan filas arbitrarias en `juegos_consolidado.csv`: solo se enriquecen juegos que
coinciden por `appid` o nombre, y el resto queda en tablas separadas para revisarlo.
El análisis exploratorio agrega una sección con los datos externos incorporados.

Requisitos y configuración del OCR:

- `poppler-utils` (`pdftoppm`, `pdftotext`) para leer y rasterizar PDFs.
- Un modelo multimodal de Ollama (`ollama pull qwen2.5vl:3b`) o Tesseract con español.
- Variables en `.env`: `OLLAMA_MODELO_VISION`, `INGESTA_MOTOR_OCR`,
  `INGESTA_OCR_MAX_PAGINAS`, `INGESTA_MAX_MB`, `DIRECTORIO_ENTRADA`, etc.
  Están todas documentadas en `.env.example`.
- `python pipeline.py verificar` muestra qué motor de OCR está disponible.

Pruebas rápidas (sin Ollama, la IA se simula):

```bash
python -m unittest discover -s tests -v
```

## Estructura de carpetas

```
entrada/               carpeta cómoda para dejar archivos antes de `ingerir`
data/
  crudo/steam_api/     JSON y HTML crudos de la API y del buscador (evidencia)
  crudo/steamdb/       HTML/texto crudo de SteamDB y registros de bloqueo
  crudo/ingesta/       copia inmutable de cada dato externo (por hash)
  intermedio/          salida de la IA (items estructurados) + bitácora de IA
  intermedio/ingesta/  texto, decisión de la IA y resumen de cada dato externo
  intermedio/ia_externa.json  tercera fuente ya estructurada por la IA
  procesado/           datos limpios por lote + consolidado
  procesado/consolidado/datos_externos.csv   cifras externas para el informe
  procesado/consolidado/juegos_externos.csv  juegos externos sin coincidencia
  descartado/          todo lo que NO es juego o no sirve, con el motivo
reportes/
  tablas/              resultados de cada pregunta en CSV
  figuras/             gráficos PNG
  resumen_analisis.md  resumen con los números calculados
```

## Herramientas auxiliares

| Script | Para qué sirve |
|---|---|
| `herramientas/descargar_paralelo.py` | Descarga archivos grandes con varias conexiones (útil si tu internet va lento por conexión) |
| `herramientas/descargar_modelo.py` | Baja un modelo de Ollama directo del registro, en paralelo y con verificación sha256 |
| `iniciar_ollama.sh` | Arranca el servidor de Ollama y descarga el modelo configurado |

```bash
# Si 'ollama pull' va lento o el modelo no aparece:
python herramientas/descargar_modelo.py qwen2.5:7b --conexiones 10
./iniciar_ollama.sh
```

### ¿Se puede analizar TODOS los juegos de Steam?

Sí. El buscador de la tienda reporta **124.373 juegos** (categoría juegos, sin DLC ni software)
y permite paginar hasta la última página, así que el pipeline puede recorrer el catálogo entero
con el comando `lotes`, que se reanuda solo si se corta:

```bash
# Catálogo completo (dejarlo corriendo; se puede cortar con Ctrl+C y retomar después)
python pipeline.py lotes --hasta max --sin-resenas --seguir

# Solo una tanda (por ejemplo, una noche de corrida)
python pipeline.py lotes --desde 2 --hasta 160 --sin-resenas --seguir

# Después, IA + SteamDB sobre algunos lotes para el informe
python pipeline.py lotes --desde 1 --hasta 5 --etapas steamdb,ia,limpiar --seguir
```

Lo que hay que saber (ritmo medido en la práctica, limitado por Steam, no por el script):

| Modo | Por juego | Catálogo completo (124.373) | Una noche (8 h) |
|---|---|---|---|
| Ficha + reseñas + jugadores | ~3,4 s | ~4,9 días | ~8.400 juegos |
| Ficha + jugadores (`--sin-resenas`) | ~2,2 s | ~3,2 días | ~13.000 juegos |
| Solo ficha (`--sin-resenas --sin-jugadores`) | ~1,7 s | ~2,5 días | ~16.900 juegos |

Steam permite ~200 pedidos cada 5 minutos; bajar más la pausa no acelera, solo genera bloqueos.
El tamaño no es problema: los CSV consolidados de todo el catálogo ocupan unas decenas de MB.

Aclaración sobre la IA local: se aplica a **páginas** (la tabla de SteamDB y las páginas del
buscador), no juego por juego. Para el informe conviene estructurar con IA una muestra de lotes
(por ejemplo 5 lotes = 500 juegos) más la tabla de más jugados; el dataset masivo sale de la API.

## Preguntas del trabajo y dónde se responden

| Pregunta | Salida |
|---|---|
| Evolución de la cantidad de juegos | `reportes/figuras/01_evolucion_por_anio.png`, `reportes/tablas/01_lanzamientos_por_anio.csv` |
| Géneros con mayor presencia/popularidad | `02_generos.png`, `02_generos.csv` |
| Características vs interacción de usuarios | `03_correlaciones.png`, `03_correlaciones.csv`, `05_precio_vs_resenas.png` |
| Precios y descuentos vs actividad | `06_rangos_precio.png`, `07_descuentos.png` (+ tablas 04 y 05) |
| Patrones de comportamiento de usuarios | `08_top_jugadores.png`, `09_valoracion_por_volumen.png`, `10_antiguedad_vs_jugadores.png` |

## Limitaciones a documentar en el informe

- La muestra sale del buscador ordenado por lanzamiento reciente: representa el catálogo actual
  de la tienda, no el histórico completo de Steam.
- SteamDB bloquea el acceso automatizado (Cloudflare): cada página obtenida queda guardada como
  evidencia y los bloqueos quedan registrados en `data/crudo/steamdb/*_meta.json`.
- Los jugadores conectados y el ranking de más jugados son fotos del momento de la extracción;
  conviene anotar fecha y hora (queda en los archivos `*_meta.json`).
- Los precios se informan en la moneda que devuelve Steam para el país configurado en `PAIS`
  (con `PAIS=ar` Steam factura en USD desde 2023; el pipeline detecta la moneda real y rotula con ella).
- Sin reseñas cargadas (`--sin-resenas`) las columnas de reseñas quedan vacías y el análisis
  las omite automáticamente.
- El veredicto sobre los datos externos lo da un modelo local chico: puede equivocarse. Por eso
  cada decisión guarda el motivo, la relevancia y el texto usado, y el rechazo no borra la
  evidencia original.
- El OCR depende de la calidad de la imagen y del modelo de visión. Las tablas complejas o las
  fotos torcidas pueden transcribirse con errores; conviene revisar `texto_ocr.txt` antes de
  citarlo en el informe.
- El OCR nunca se ejecuta sin autorización explícita (`--autorizar-ocr` o respuesta "sí" en la
  terminal). En corridas sin terminal queda pendiente y se puede reintentar después.
- Los datos externos aceptados se guardan como evidencia y texto, pero no se suman
  automáticamente al dataset de Steam: mezclarlos exigiría un esquema común y validación adicional.
