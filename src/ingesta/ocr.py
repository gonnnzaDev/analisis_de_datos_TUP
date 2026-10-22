"""
OCR de imágenes y PDFs escaneados.

El motor preferido es una IA de visión local (Ollama). Si no hay modelo de
visión instalado, se puede usar Tesseract cuando está disponible. El OCR solo
se ejecuta cuando el orquestador ya recibió la autorización correspondiente.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .. import config
from ..almacenamiento.guardar import ahora_iso, registrar_en_jsonl
from ..ia import ollama_cliente
from ..ia.ollama_cliente import ErrorIA, pedir_texto

PROMPT_SISTEMA = """\
Sos un motor de OCR. Transcribí fielmente TODO el texto visible en la imagen,
en el orden de lectura, sin resumir, sin interpretar y sin agregar comentarios.
Conservá números, precios, fechas, nombres y tablas. Para las tablas, mantené
las filas y columnas separadas con tabulaciones o barras verticales.
Si la imagen no contiene texto, respondé exactamente: SIN_TEXTO
"""


class ErrorOCR(RuntimeError):
    """Error controlado del OCR (motor no disponible, PDF que no se puede abrir, etc.)."""


def diagnostico() -> dict:
    """Estado de las herramientas de OCR, para el comando `verificar`."""
    servidor = ollama_cliente.disponible()
    modelo_vision = config.OLLAMA_MODELO_VISION
    instalado = False
    if servidor:
        try:
            instalado = ollama_cliente.modelo_instalado(modelo_vision)
        except ErrorIA:
            instalado = False
    try:
        import PIL  # noqa: F401

        pillow = True
    except ImportError:
        pillow = False

    return {
        "ollama": servidor,
        "modelo_vision": modelo_vision,
        "modelo_vision_instalado": instalado,
        "tesseract": shutil.which("tesseract") or "",
        "pdftoppm": shutil.which("pdftoppm") or "",
        "pillow": pillow,
    }


def elegir_motor(preferido: str | None = None, modelo: str | None = None) -> str:
    """Devuelve 'ollama' o 'tesseract' según la configuración y lo instalado."""
    preferido = (preferido or config.INGESTA_MOTOR_OCR or "auto").strip().lower()
    modelo = modelo or config.OLLAMA_MODELO_VISION

    if preferido == "ollama":
        _asegurar_motor_ollama(modelo)
        return "ollama"
    if preferido == "tesseract":
        if not shutil.which("tesseract"):
            raise ErrorOCR(
                "El motor de OCR configurado es Tesseract, pero no está instalado. "
                "Instalalo con el gestor de paquetes del sistema o cambiá INGESTA_MOTOR_OCR=ollama."
            )
        return "tesseract"

    # auto: primero IA de visión, después Tesseract.
    try:
        _asegurar_motor_ollama(modelo)
        return "ollama"
    except ErrorOCR:
        if shutil.which("tesseract"):
            return "tesseract"
        raise ErrorOCR(
            f"No hay motor de OCR disponible. Opciones:\n"
            f"  - IA de visión: ejecutá 'ollama pull {modelo}' (recomendado)\n"
            f"  - Tesseract: instalá 'tesseract-ocr' y el idioma español"
        ) from None


def _asegurar_motor_ollama(modelo: str) -> None:
    if not ollama_cliente.disponible():
        raise ErrorOCR(
            "Ollama no está corriendo; no se puede usar la IA de visión para OCR. "
            "Ejecutá ./iniciar_ollama.sh o usá INGESTA_MOTOR_OCR=tesseract."
        )
    if not ollama_cliente.modelo_instalado(modelo):
        raise ErrorOCR(
            f"El modelo de visión '{modelo}' no está descargado. "
            f"Ejecutá: ollama pull {modelo}"
        )


# ---------------------------------------------------------------------------
# Preparación de imágenes y páginas
# ---------------------------------------------------------------------------

def preparar_imagen(ruta: Path, salida_dir: Path | None = None) -> Path:
    """Orienta, aplana y achica una imagen para que el OCR sea más confiable."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return ruta

    salida_dir = salida_dir or ruta.parent
    salida_dir.mkdir(parents=True, exist_ok=True)
    destino = salida_dir / f"{ruta.stem}_prep.png"
    if destino.exists() and destino.stat().st_mtime >= ruta.stat().st_mtime:
        return destino

    try:
        with Image.open(ruta) as imagen:
            imagen.seek(0)
            imagen = ImageOps.exif_transpose(imagen)
            if imagen.mode in ("RGBA", "LA", "P"):
                convertida = imagen.convert("RGBA")
                fondo = Image.new("RGB", convertida.size, "white")
                fondo.paste(convertida, mask=convertida.getchannel("A"))
                imagen = fondo
            else:
                imagen = imagen.convert("RGB")
            if imagen.width > config.INGESTA_OCR_ANCHO_MAX:
                alto = round(imagen.height * config.INGESTA_OCR_ANCHO_MAX / imagen.width)
                imagen = imagen.resize((config.INGESTA_OCR_ANCHO_MAX, alto), Image.LANCZOS)
            imagen.save(destino, format="PNG", optimize=True)
    except (OSError, ValueError) as error:
        raise ErrorOCR(f"no se pudo preparar la imagen {ruta.name}: {error}") from error
    return destino


def _clave_pagina(ruta: Path) -> int:
    """Ordena pagina-1.png, pagina-2.png, ..., pagina-10.png de forma numérica."""
    coincidencia = re.search(r"(\d+)$", ruta.stem)
    return int(coincidencia.group(1)) if coincidencia else 0


def renderizar_pdf(
    ruta: Path,
    salida_dir: Path,
    max_paginas: int | None = None,
    dpi: int | None = None,
) -> list[Path]:
    """Convierte las primeras páginas de un PDF a PNG usando pdftoppm o PyMuPDF."""
    max_paginas = max(1, max_paginas or config.INGESTA_OCR_MAX_PAGINAS)
    dpi = max(72, dpi or config.INGESTA_OCR_DPI)
    salida_dir.mkdir(parents=True, exist_ok=True)

    existentes = sorted(salida_dir.glob("pagina-*.png"), key=_clave_pagina)
    if existentes:
        return existentes[:max_paginas]

    if shutil.which("pdftoppm"):
        prefijo = salida_dir / "pagina"
        try:
            resultado = subprocess.run(
                [
                    "pdftoppm", "-png", "-r", str(dpi),
                    "-f", "1", "-l", str(max_paginas),
                    str(ruta), str(prefijo),
                ],
                capture_output=True, timeout=300, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ErrorOCR(f"no se pudo renderizar el PDF: {error}") from error
        paginas = sorted(salida_dir.glob("pagina-*.png"), key=_clave_pagina)
        if resultado.returncode == 0 and paginas:
            return paginas[:max_paginas]
        if resultado.returncode != 0:
            detalle = resultado.stderr.decode("utf-8", errors="replace")[:200]
            raise ErrorOCR(f"pdftoppm falló al renderizar el PDF: {detalle}")

    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        raise ErrorOCR(
            "No se puede renderizar el PDF para OCR: falta 'pdftoppm' (poppler). "
            "En Debian/Ubuntu: sudo apt install poppler-utils"
        ) from None

    try:
        documento = fitz.open(str(ruta))
        paginas = []
        for indice in range(min(len(documento), max_paginas)):
            pagina = documento.load_page(indice)
            pixmap = pagina.get_pixmap(dpi=dpi)
            destino = salida_dir / f"pagina-{indice + 1:02d}.png"
            pixmap.save(str(destino))
            paginas.append(destino)
        documento.close()
    except Exception as error:  # noqa: BLE001 - PyMuPDF devuelve errores variados
        raise ErrorOCR(f"no se pudo renderizar el PDF con PyMuPDF: {error}") from error
    return paginas


def preparar_paginas(
    documento,
    carpeta: Path,
    max_paginas: int | None = None,
) -> list[Path]:
    """Devuelve las imágenes que hay que pasarle al OCR para un documento."""
    if documento.categoria == "imagen":
        return [preparar_imagen(documento.ruta, carpeta)]
    if documento.categoria == "pdf":
        return renderizar_pdf(documento.ruta, carpeta, max_paginas=max_paginas)
    return []


# ---------------------------------------------------------------------------
# Transcripción
# ---------------------------------------------------------------------------

def _idiomas_tesseract() -> str:
    try:
        resultado = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "eng"
    idiomas = set(resultado.stdout.split())
    if "spa" in idiomas and "eng" in idiomas:
        return "spa+eng"
    if "spa" in idiomas:
        return "spa"
    return "eng"


def _limpiar_ocr(texto: str) -> str:
    texto = texto.replace("\x00", "").replace("\r\n", "\n").strip()
    if re.fullmatch(r"sin[\s_]*texto\.?", texto, flags=re.IGNORECASE):
        return ""
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _ocr_tesseract(pagina: Path) -> str:
    try:
        resultado = subprocess.run(
            ["tesseract", str(pagina), "stdout", "-l", _idiomas_tesseract()],
            capture_output=True, timeout=300, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ErrorOCR(f"Tesseract falló: {error}") from error
    if resultado.returncode != 0:
        detalle = resultado.stderr.decode("utf-8", errors="replace")[:200]
        raise ErrorOCR(f"Tesseract devolvió un error: {detalle}")
    return _limpiar_ocr(resultado.stdout.decode("utf-8", errors="replace"))


def transcribir(
    paginas: list[Path],
    contexto: str,
    motor: str | None = None,
    modelo: str | None = None,
) -> dict:
    """
    Transcribe una lista de imágenes. Devuelve el texto unido y de dónde salió.
    No decide si el OCR está autorizado: eso lo hace el orquestador.
    """
    if not paginas:
        raise ErrorOCR("no hay páginas para transcribir")

    motor_elegido = elegir_motor(motor, modelo)
    modelo_vision = modelo or config.OLLAMA_MODELO_VISION
    textos: list[str] = []
    momento = ahora_iso()

    for indice, pagina in enumerate(paginas, start=1):
        if motor_elegido == "ollama":
            usuario = (
                f"Contexto: {contexto}. Página {indice} de {len(paginas)}.\n"
                "Transcribí todo el texto visible."
            )
            try:
                texto = pedir_texto(
                    PROMPT_SISTEMA,
                    usuario,
                    fuente=f"ocr:{contexto}",
                    imagenes=[pagina],
                    modelo=modelo_vision,
                )
            except ErrorIA as error:
                raise ErrorOCR(f"la IA de visión falló en la página {indice}: {error}") from error
            texto = _limpiar_ocr(texto)
        else:
            texto = _ocr_tesseract(pagina)

        if len(paginas) > 1:
            textos.append(f"--- página {indice} ---\n{texto}")
        elif texto:
            textos.append(texto)

    transcripcion = "\n\n".join(parte for parte in textos if parte).strip()
    registrar_en_jsonl(
        {
            "fecha": momento,
            "fuente": f"ocr:{contexto}",
            "motor": motor_elegido,
            "modelo": modelo_vision if motor_elegido == "ollama" else "",
            "paginas": len(paginas),
            "caracteres": len(transcripcion),
            "ok": bool(transcripcion),
        },
        config.INTERMEDIO / "registro_ia.jsonl",
    )
    return {
        "texto": transcripcion,
        "motor": motor_elegido,
        "modelo": modelo_vision if motor_elegido == "ollama" else "",
        "paginas": len(paginas),
        "caracteres": len(transcripcion),
        "textos_por_pagina": textos,
    }
