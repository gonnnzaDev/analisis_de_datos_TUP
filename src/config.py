"""
Configuración central del proyecto.

Todas las rutas y parámetros ajustables viven acá.
Los valores se pueden sobreescribir desde el archivo `.env`
(copiá `.env.example` a `.env` y editá lo que necesites).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


def _texto(clave: str, defecto: str) -> str:
    valor = (os.getenv(clave) or "").strip()
    return valor or defecto


def _numero(clave: str, defecto: float) -> float:
    try:
        return float((os.getenv(clave) or "").strip())
    except ValueError:
        return defecto


def _entero(clave: str, defecto: int) -> int:
    try:
        return int(float((os.getenv(clave) or "").strip()))
    except ValueError:
        return defecto


def _booleano(clave: str, defecto: bool) -> bool:
    valor = (os.getenv(clave) or "").strip().lower()
    if not valor:
        return defecto
    return valor in {"1", "si", "sí", "true", "verdadero", "yes"}


# ---------------------------------------------------------------------------
# IA local (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_HOST = _texto("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODELO = _texto("OLLAMA_MODELO", "qwen2.5:7b")
OLLAMA_TIMEOUT = _entero("OLLAMA_TIMEOUT", 1800)  # segundos de espera por respuesta
OLLAMA_NUM_CTX = _entero("OLLAMA_NUM_CTX", 8192)
OLLAMA_NUM_PREDICT = _entero("OLLAMA_NUM_PREDICT", 2048)
IA_FRAGMENTO_CARACTERES = _entero("IA_FRAGMENTO_CARACTERES", 6000)
IA_FRAGMENTO_SOLAPE = _entero("IA_FRAGMENTO_SOLAPE", 300)

# ---------------------------------------------------------------------------
# Ingesta de datos externos (archivos, imágenes, PDFs, texto pegado...)
# ---------------------------------------------------------------------------
# El modelo de visión se usa para revisar imágenes/escaneos y para hacer OCR.
OLLAMA_MODELO_VISION = _texto("OLLAMA_MODELO_VISION", "qwen2.5vl:3b")
INGESTA_MOTOR_OCR = _texto("INGESTA_MOTOR_OCR", "auto")  # auto | ollama | tesseract
INGESTA_OCR_MAX_PAGINAS = _entero("INGESTA_OCR_MAX_PAGINAS", 15)
INGESTA_OCR_DPI = _entero("INGESTA_OCR_DPI", 150)
INGESTA_OCR_ANCHO_MAX = _entero("INGESTA_OCR_ANCHO_MAX", 2200)
INGESTA_MIN_CARACTERES_PDF = _entero("INGESTA_MIN_CARACTERES_PDF", 40)  # por página
INGESTA_MAX_CARACTERES_IA = _entero("INGESTA_MAX_CARACTERES_IA", 12000)
INGESTA_MAX_MB = _entero("INGESTA_MAX_MB", 50)
DIRECTORIO_ENTRADA = RAIZ / _texto("DIRECTORIO_ENTRADA", "entrada")

# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------
PAUSA_STORE = _numero("PAUSA_STORE", 1.7)  # segundos entre pedidos a store.steampowered.com
PAUSA_API = _numero("PAUSA_API", 0.5)      # segundos entre pedidos a api.steampowered.com
HTTP_TIMEOUT = _entero("HTTP_TIMEOUT", 40)
HTTP_REINTENTOS = _entero("HTTP_REINTENTOS", 4)
PAIS = _texto("PAIS", "ar")                # código de país para precios (ar = Argentina)
IDIOMA = _texto("IDIOMA", "spanish")       # idioma de los textos que devuelve Steam

# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------
LOTE_TAMANO = _entero("LOTE_TAMANO", 100)  # cantidad de juegos por lote
SEMILLA = _entero("SEMILLA", 2026)         # semilla para muestreos reproducibles

# ---------------------------------------------------------------------------
# SteamDB
# ---------------------------------------------------------------------------
STEAMDB_USAR_NAVEGADOR = _booleano("STEAMDB_USAR_NAVEGADOR", True)
STEAMDB_NAVEGADOR_VISIBLE = _booleano("STEAMDB_NAVEGADOR_VISIBLE", True)
STEAMDB_ESPERA_NAVEGADOR = _entero("STEAMDB_ESPERA_NAVEGADOR", 15)  # segundos máx. de espera

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
DIRECTORIO_DATOS = RAIZ / _texto("DIRECTORIO_DATOS", "data")
CRUDO = DIRECTORIO_DATOS / "crudo"
CRUDO_API = CRUDO / "steam_api"
CRUDO_STEAMDB = CRUDO / "steamdb"
CRUDO_INGESTA = CRUDO / "ingesta"
INTERMEDIO = DIRECTORIO_DATOS / "intermedio"
INTERMEDIO_INGESTA = INTERMEDIO / "ingesta"
PROCESADO = DIRECTORIO_DATOS / "procesado"
DESCARTADO = DIRECTORIO_DATOS / "descartado"
REPORTES = RAIZ / "reportes"
TABLAS = REPORTES / "tablas"
FIGURAS = REPORTES / "figuras"


def crear_directorios() -> None:
    """Crea el árbol de carpetas de datos y reportes si no existe."""
    for ruta in (
        CRUDO_API,
        CRUDO_STEAMDB,
        CRUDO_INGESTA,
        INTERMEDIO,
        INTERMEDIO_INGESTA,
        PROCESADO,
        DESCARTADO,
        TABLAS,
        FIGURAS,
        DIRECTORIO_ENTRADA,
    ):
        ruta.mkdir(parents=True, exist_ok=True)


crear_directorios()
