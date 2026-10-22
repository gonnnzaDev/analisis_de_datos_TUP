"""
IA "portero": decide si un dato externo aporta algo al análisis.

Es una llamada independiente del estructurador de Steam. Recibe el texto
extraído o una imagen y devuelve un veredicto en JSON:

    sirve, relevancia, confianza, tipo_dato, datos_aprovechables, requiere_ocr, motivo

La decisión queda guardada junto al dato para poder auditar por qué se aceptó
o se rechazó. Esta IA nunca transcribe: de eso se encarga el módulo de OCR.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..ia.ollama_cliente import ErrorIA, asegurar_modelo, pedir_json

PROMPT_SISTEMA = """\
Sos el portero de datos de un análisis del mercado de videojuegos de Steam.
El análisis estudia:
1. evolución de la cantidad de juegos lanzados por año;
2. géneros con mayor presencia y popularidad;
3. relación entre características de los juegos (precio, logros, plataformas,
   categorías) y la interacción de los usuarios (reseñas, jugadores);
4. precios, descuentos y actividad;
5. patrones de comportamiento de los usuarios (jugadores conectados, reseñas).

Tu tarea es decidir si el contenido que te paso aporta datos verificables para
ESE análisis. No transcribas ni resumas el contenido completo: solo evaluá si
sirve, qué datos se podrían aprovechar y si hace falta OCR.

Devolvés SIEMPRE un único objeto JSON válido, sin explicaciones ni bloques de código:
{
  "sirve": true o false,
  "relevancia": número entero de 0 a 100,
  "confianza": número entero de 0 a 100,
  "tipo_dato": "juegos" | "precios" | "resenas" | "jugadores" | "generos" | "fechas" | "mercado" | "mixto" | "otro",
  "datos_aprovechables": ["lista corta de datos concretos que se ven en el contenido"],
  "requiere_ocr": true o false,
  "motivo": "explicación breve de por qué sirve o no sirve"
}

Reglas:
- "sirve" es true solo si hay al menos un dato concreto y verificable que aporte
  al análisis (juegos, precios, descuentos, reseñas, jugadores, géneros, fechas
  o tendencias del mercado de videojuegos).
- Un texto genérico, una publicidad, una factura, un documento personal, una
  captura sin relación o un texto que solo habla de la IA NO sirven.
- Si el contenido habla de videojuegos pero no trae ningún dato (solo opiniones
  sin números ni nombres), "sirve" es false.
- No inventes datos que no estén en el contenido.
- "requiere_ocr" es true solo si el contenido visible está en una imagen o en un
  escaneo y todavía no se puede leer como texto.
"""

PROMPT_IMAGEN = """\
Sos el portero de datos de un análisis del mercado de videojuegos de Steam.
Te van a mostrar una imagen (captura de pantalla, tabla, página escaneada o foto).
Decidí si esa imagen aporta datos verificables para el análisis del mercado de
videojuegos: juegos, precios, descuentos, reseñas, jugadores, géneros, fechas o
tendencias. NO transcribas el contenido: solo clasificalo.

Devolvés SIEMPRE un único objeto JSON válido, sin explicaciones ni bloques de código:
{
  "sirve": true o false,
  "relevancia": número entero de 0 a 100,
  "confianza": número entero de 0 a 100,
  "tipo_dato": "juegos" | "precios" | "resenas" | "jugadores" | "generos" | "fechas" | "mercado" | "mixto" | "otro",
  "datos_aprovechables": ["lista corta de datos concretos que se ven en la imagen"],
  "requiere_ocr": true o false,
  "motivo": "explicación breve de por qué sirve o no sirve"
}

Reglas:
- "sirve" es true solo si la imagen contiene datos concretos del mercado de
  videojuegos (por ejemplo, una tabla de precios, una lista de juegos con
  jugadores, una ficha de Steam o reseñas con números).
- Una imagen decorativa, un meme, una captura de otro tema o una foto sin datos
  NO sirve.
- Si la imagen contiene texto que hay que leer para aprovechar los datos,
  "requiere_ocr" es true.
- No inventes datos que no se vean en la imagen.
"""


def _a_booleano(valor) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    if isinstance(valor, str):
        limpio = valor.strip().lower()
        if limpio in {"true", "si", "sí", "yes", "1", "verdadero"}:
            return True
        if limpio in {"false", "no", "0", "falso"}:
            return False
    return None


def _a_entero(valor, defecto: int = 0) -> int:
    try:
        numero = int(float(valor))
    except (TypeError, ValueError):
        return defecto
    return max(0, min(100, numero))


def _a_lista(valor) -> list[str]:
    if isinstance(valor, str):
        partes = [parte.strip() for parte in valor.replace(";", ",").split(",")]
    elif isinstance(valor, list):
        partes = [str(parte).strip() for parte in valor]
    else:
        return []
    return [parte[:200] for parte in partes if parte][:10]


def normalizar_decision(datos: dict, modelo: str, verificado_con: str) -> dict:
    """Valida y acota la respuesta del modelo para que sea segura de usar."""
    if not isinstance(datos, dict):
        raise ValueError("la verificación no devolvió un objeto JSON")

    sirve = _a_booleano(datos.get("sirve"))
    if sirve is None:
        # Algunos modelos usan otras claves equivalentes.
        for clave in ("util", "útil", "relevante", "aporta"):
            sirve = _a_booleano(datos.get(clave))
            if sirve is not None:
                break
    if sirve is None:
        raise ValueError("la verificación no incluyó un valor booleano en 'sirve'")

    motivo = str(datos.get("motivo") or "").strip()
    if not motivo:
        motivo = "La IA no explicó el motivo."

    return {
        "sirve": sirve,
        "relevancia": _a_entero(datos.get("relevancia")),
        "confianza": _a_entero(datos.get("confianza")),
        "tipo_dato": str(datos.get("tipo_dato") or "otro").strip()[:40],
        "datos_aprovechables": _a_lista(datos.get("datos_aprovechables")),
        "requiere_ocr": bool(_a_booleano(datos.get("requiere_ocr"))),
        "motivo": motivo[:500],
        "modelo": modelo,
        "verificado_con": verificado_con,
    }


def _muestra(texto: str, maximo: int | None = None) -> str:
    """Recorta un texto largo conservando el principio y el final."""
    maximo = maximo or config.INGESTA_MAX_CARACTERES_IA
    texto = texto.strip()
    if len(texto) <= maximo:
        return texto
    cabeza = int(maximo * 0.65)
    cola = maximo - cabeza
    return f"{texto[:cabeza]}\n\n[...recorte del medio...]\n\n{texto[-cola:]}"


def verificar_texto(
    texto: str,
    contexto: str,
    modelo: str | None = None,
    maximo: int | None = None,
) -> dict:
    """Pide al modelo de texto que evalúe si el contenido aporta al análisis."""
    if not texto.strip():
        raise ValueError("no hay texto para verificar")
    modelo_usado = modelo or config.OLLAMA_MODELO
    asegurar_modelo(modelo_usado)
    usuario = (
        f"Archivo o fuente: {contexto}\n\n"
        f"Contenido (puede ser una muestra recortada):\n"
        f"-----\n{_muestra(texto, maximo)}\n-----\n\n"
        "Evaluá si este contenido sirve para el análisis y devolvé el JSON pedido."
    )
    try:
        datos = pedir_json(
            PROMPT_SISTEMA,
            usuario,
            fuente=f"ingesta_texto:{contexto}",
            modelo=modelo_usado,
        )
    except ErrorIA:
        raise
    except ValueError as error:
        raise ErrorIA(f"La verificación no devolvió JSON válido: {error}") from error
    try:
        return normalizar_decision(datos, modelo_usado, "texto")
    except ValueError as error:
        raise ErrorIA(f"La verificación no tiene el formato esperado: {error}") from error


def verificar_imagen(
    imagen: Path,
    contexto: str,
    modelo: str | None = None,
) -> dict:
    """Pide al modelo de visión que evalúe una imagen sin transcribirla."""
    modelo_usado = modelo or config.OLLAMA_MODELO_VISION
    asegurar_modelo(modelo_usado)
    usuario = (
        f"Archivo: {contexto}\n\n"
        "La imagen está adjunta. Mirala y decidí si sirve para el análisis. "
        "No transcribas el contenido; solo devolvé el JSON pedido."
    )
    try:
        datos = pedir_json(
            PROMPT_IMAGEN,
            usuario,
            fuente=f"ingesta_imagen:{contexto}",
            imagenes=[imagen],
            modelo=modelo_usado,
        )
    except ErrorIA:
        raise
    except ValueError as error:
        raise ErrorIA(f"La verificación de la imagen no devolvió JSON válido: {error}") from error
    try:
        return normalizar_decision(datos, modelo_usado, "imagen")
    except ValueError as error:
        raise ErrorIA(f"La verificación de la imagen no tiene el formato esperado: {error}") from error
