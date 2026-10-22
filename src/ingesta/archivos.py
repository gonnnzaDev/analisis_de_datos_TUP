"""
Lectura y preparación de datos externos.

Acepta archivos sueltos, carpetas, URLs y texto pegado. Detecta el tipo de
contenido, guarda una copia inmutable en data/crudo/ingesta/ y extrae el texto
cuando el formato lo permite. Si el contenido es una imagen o un PDF escaneado,
queda marcado como `requiere_ocr` para que el orquestador decida qué hacer.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import unescape

import requests
from bs4 import BeautifulSoup

from .. import config
from ..http_util import crear_sesion

EXTENSIONES_TEXTO = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".yaml", ".yml", ".log", ".ini", ".srt", ".vtt", ".tex",
}
EXTENSIONES_HTML = {".html", ".htm", ".xhtml"}
EXTENSIONES_PDF = {".pdf"}
EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
EXTENSIONES_DOCUMENTO = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods"}

NOMBRES_IGNORADOS = {"leeme.txt", "readme.md", ".gitkeep"}


class ErrorIngesta(RuntimeError):
    """Error controlado de la ingesta (archivo ilegible, formato no soportado, etc.)."""


@dataclass
class Documento:
    """Un dato externo ya guardado en crudo, con su texto extraído si se pudo."""

    id: str
    ruta: Path
    nombre_original: str
    origen: str
    categoria: str
    mime: str
    bytes: int
    sha256: str
    texto: str = ""
    metodo_texto: str = ""
    paginas: int | None = None
    requiere_ocr: bool = False
    motivo_ocr: str = ""
    error_lectura: str = ""

    @property
    def contexto(self) -> str:
        """Descripción corta para pasarle a la IA."""
        partes = [self.nombre_original, self.categoria]
        if self.paginas:
            partes.append(f"{self.paginas} página(s)")
        return " · ".join(partes)

    def a_dict(self) -> dict:
        try:
            ruta_crudo = str(self.ruta.relative_to(config.RAIZ))
        except ValueError:
            ruta_crudo = str(self.ruta)
        return {
            "id": self.id,
            "archivo": self.nombre_original,
            "origen": self.origen,
            "categoria": self.categoria,
            "mime": self.mime,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "paginas": self.paginas,
            "metodo_texto": self.metodo_texto,
            "requiere_ocr": self.requiere_ocr,
            "motivo_ocr": self.motivo_ocr,
            "error_lectura": self.error_lectura,
            "ruta_crudo": ruta_crudo,
            "caracteres_texto": len(self.texto),
        }


# ---------------------------------------------------------------------------
# Detección de formato y lectura
# ---------------------------------------------------------------------------

def categoria_de(nombre: str, mime: str = "") -> str:
    """Clasifica un archivo en texto, html, pdf, imagen, documento o desconocido."""
    extension = Path(nombre).suffix.lower()
    mime = (mime or "").split(";")[0].strip().lower()
    if extension in EXTENSIONES_PDF or mime == "application/pdf":
        return "pdf"
    if extension in EXTENSIONES_IMAGEN or mime.startswith("image/"):
        return "imagen"
    if extension in EXTENSIONES_HTML or mime in {"text/html", "application/xhtml+xml"}:
        return "html"
    if extension in EXTENSIONES_DOCUMENTO:
        return "documento"
    if extension in EXTENSIONES_TEXTO or mime.startswith("text/") or mime in {
        "application/json", "application/xml", "application/x-yaml",
    }:
        return "texto"
    return "desconocido"


def _normalizar_texto(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    texto = texto.replace("\f", "\n")
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def leer_texto_plano(ruta: Path) -> tuple[str, str]:
    """
    Decodifica un archivo de texto probando UTF-8 y variantes de Windows.
    Devuelve (texto, codificación). Lanza ErrorIngesta si parece binario.
    """
    datos = Path(ruta).read_bytes()
    if not datos:
        return "", "vacio"
    if b"\x00" in datos[:8192]:
        raise ErrorIngesta("el archivo contiene bytes nulos (parece binario)")

    for codificacion in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
        if not texto:
            continue
        control = sum(1 for caracter in texto if ord(caracter) < 32 and caracter not in "\n\r\t")
        if control / max(len(texto), 1) > 0.05:
            raise ErrorIngesta("el archivo parece binario (demasiados caracteres de control)")
        return _normalizar_texto(texto), codificacion
    raise ErrorIngesta("no se pudo decodificar el texto (probé UTF-8, cp1252 y latin-1)")


def texto_de_html(contenido: str) -> str:
    sopa = BeautifulSoup(contenido, "html.parser")
    for etiqueta in sopa(["script", "style", "noscript", "svg"]):
        etiqueta.decompose()
    return _normalizar_texto(sopa.get_text("\n", strip=True))


def _paginas_de_pdf(ruta: Path) -> int | None:
    """Cuenta páginas con pdfinfo y, si no está, con una heurística sobre el PDF."""
    if shutil.which("pdfinfo"):
        try:
            resultado = subprocess.run(
                ["pdfinfo", str(ruta)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            coincidencia = re.search(r"^Pages:\s+(\d+)", resultado.stdout, re.MULTILINE)
            if coincidencia:
                return int(coincidencia.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        datos = ruta.read_bytes()
        return max(len(re.findall(rb"/Type\s*/Page\b", datos)), 0) or None
    except OSError:
        return None


def texto_de_pdf(ruta: Path) -> tuple[str, int | None, str]:
    """
    Extrae el texto de un PDF. Devuelve (texto, páginas, método).
    Usa pdftotext (poppler) y, si no está, pypdf.
    """
    paginas = _paginas_de_pdf(ruta)
    if shutil.which("pdftotext"):
        try:
            resultado = subprocess.run(
                ["pdftotext", "-layout", str(ruta), "-"],
                capture_output=True, timeout=180, check=False,
            )
            if resultado.returncode == 0:
                texto = resultado.stdout.decode("utf-8", errors="replace")
                return _normalizar_texto(texto), paginas, "pdftotext"
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return "", paginas, "sin_herramienta_pdf"
    try:
        lector = PdfReader(str(ruta))
        paginas = paginas or len(lector.pages)
        texto = "\n\n".join((pagina.extract_text() or "") for pagina in lector.pages)
        return _normalizar_texto(texto), paginas, "pypdf"
    except Exception as error:  # noqa: BLE001 - un PDF roto no debe romper el lote
        raise ErrorIngesta(f"no se pudo leer el PDF: {error}") from error


def _texto_de_docx(ruta: Path) -> str:
    """Fallback sin LibreOffice: lee el XML interno de un .docx o .pptx."""
    try:
        with zipfile.ZipFile(ruta) as paquete:
            nombres = paquete.namelist()
            if any(nombre.startswith("word/") for nombre in nombres):
                partes = [nombre for nombre in nombres if nombre == "word/document.xml"]
            elif any(nombre.startswith("ppt/slides/slide") for nombre in nombres):
                partes = sorted(nombre for nombre in nombres if re.match(r"ppt/slides/slide\d+\.xml$", nombre))
            else:
                return ""
            textos = []
            for nombre in partes:
                xml = paquete.read(nombre).decode("utf-8", errors="replace")
                xml = xml.replace("</w:p>", "\n").replace("</a:p>", "\n")
                xml = xml.replace("</w:tc>", "\t").replace("</a:tc>", "\t")
                textos.append(unescape(re.sub(r"<[^>]+>", " ", xml)))
            return _normalizar_texto("\n".join(textos))
    except (OSError, zipfile.BadZipFile):
        return ""


def texto_de_documento(ruta: Path) -> tuple[str, str]:
    """
    Convierte documentos de oficina a texto con LibreOffice.
    Si no está instalado, intenta leer .docx/.pptx por dentro.
    Devuelve (texto, método).
    """
    ejecutable = shutil.which("soffice") or shutil.which("libreoffice")
    if ejecutable:
        config.INTERMEDIO_INGESTA.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ingesta_", dir=config.INTERMEDIO_INGESTA) as temporal:
            destino = Path(temporal)
            formato = "csv" if ruta.suffix.lower() in {".xlsx", ".xls", ".ods"} else "txt:Text"
            try:
                subprocess.run(
                    [
                        ejecutable, "--headless", "--norestore",
                        f"-env:UserInstallation=file://{destino / 'perfil'}",
                        "--convert-to", formato, "--outdir", str(destino), str(ruta),
                    ],
                    capture_output=True, timeout=180, check=False,
                    env={**os.environ, "HOME": temporal},
                )
            except (OSError, subprocess.SubprocessError):
                pass
            for convertido in destino.iterdir():
                if convertido.is_file() and convertido.suffix.lower() in {".txt", ".csv"}:
                    try:
                        texto, _ = leer_texto_plano(convertido)
                        if texto:
                            return texto, "libreoffice"
                    except ErrorIngesta:
                        continue

    if ruta.suffix.lower() in {".docx", ".pptx"}:
        texto = _texto_de_docx(ruta)
        if texto:
            return texto, "xml_interno"
    return "", ""


def analizar_documento(documento: Documento) -> Documento:
    """Completa los campos de texto/OCR de un documento recién guardado."""
    documento.texto = ""
    documento.metodo_texto = ""
    documento.requiere_ocr = False
    documento.motivo_ocr = ""
    documento.error_lectura = ""

    try:
        if documento.categoria == "texto":
            documento.texto, codificacion = leer_texto_plano(documento.ruta)
            documento.metodo_texto = f"texto_plano ({codificacion})"
        elif documento.categoria == "html":
            crudo, _ = leer_texto_plano(documento.ruta)
            documento.texto = texto_de_html(crudo)
            documento.metodo_texto = "html"
        elif documento.categoria == "pdf":
            documento.texto, documento.paginas, metodo = texto_de_pdf(documento.ruta)
            documento.metodo_texto = metodo
            paginas = max(documento.paginas or 1, 1)
            if len(documento.texto) < config.INGESTA_MIN_CARACTERES_PDF * paginas:
                documento.requiere_ocr = True
                documento.motivo_ocr = (
                    f"PDF con poco texto extraíble ({len(documento.texto)} caracteres "
                    f"en {paginas} página/s)"
                )
        elif documento.categoria == "documento":
            documento.texto, documento.metodo_texto = texto_de_documento(documento.ruta)
            if not documento.texto:
                documento.error_lectura = (
                    "no se pudo convertir el documento a texto "
                    "(¿está instalado LibreOffice?)"
                )
        elif documento.categoria == "imagen":
            documento.requiere_ocr = True
            documento.metodo_texto = "imagen"
            documento.motivo_ocr = "es una imagen: el texto solo se puede obtener con OCR"
        else:
            # Archivos sin extensión conocida: último intento, leerlos como texto.
            documento.texto, codificacion = leer_texto_plano(documento.ruta)
            documento.categoria = "texto"
            documento.metodo_texto = f"texto_plano ({codificacion})"
    except (ErrorIngesta, OSError) as error:
        documento.error_lectura = str(error)

    if not documento.texto and not documento.requiere_ocr and not documento.error_lectura:
        documento.error_lectura = "el archivo no contiene texto legible"
    return documento


# ---------------------------------------------------------------------------
# Guardado y preparación de la entrada
# ---------------------------------------------------------------------------

def _slug(texto: str, maximo: int = 48) -> str:
    limpio = unicodedata.normalize("NFKD", texto)
    limpio = "".join(caracter for caracter in limpio if not unicodedata.combining(caracter))
    limpio = re.sub(r"[^A-Za-z0-9]+", "_", limpio).strip("_").lower()
    return (limpio or "dato")[:maximo]


def _nombre_seguro(nombre: str) -> str:
    nombre = Path(nombre).name
    nombre = re.sub(r"[^\w.\-() ]+", "_", nombre, flags=re.UNICODE).strip(" .")
    return nombre[:120] or "archivo"


def preparar_bytes(
    contenido: bytes,
    nombre: str,
    origen: str = "entrada directa",
    mime: str = "",
) -> Documento:
    """Guarda una copia en crudo y devuelve el documento listo para verificar."""
    tamano = len(contenido)
    limite = config.INGESTA_MAX_MB * 1024 * 1024
    if tamano == 0:
        raise ErrorIngesta(f"'{nombre}' está vacío")
    if tamano > limite:
        raise ErrorIngesta(
            f"'{nombre}' supera el límite de {config.INGESTA_MAX_MB} MB "
            "(se puede cambiar con INGESTA_MAX_MB en .env)"
        )

    sha256 = hashlib.sha256(contenido).hexdigest()
    identificador = f"{_slug(Path(nombre).stem)}_{sha256[:10]}"
    carpeta = config.CRUDO_INGESTA / identificador
    carpeta.mkdir(parents=True, exist_ok=True)

    extension = Path(nombre).suffix.lower()
    destino = carpeta / f"original{extension}"
    if not destino.exists():
        destino.write_bytes(contenido)

    documento = Documento(
        id=identificador,
        ruta=destino,
        nombre_original=nombre,
        origen=origen,
        categoria=categoria_de(nombre, mime),
        mime=mime or (mimetypes.guess_type(nombre)[0] or ""),
        bytes=tamano,
        sha256=sha256,
    )
    return analizar_documento(documento)


def preparar_fuente(ruta: Path | str) -> Documento:
    """Prepara un archivo local."""
    ruta = Path(ruta).expanduser()
    if not ruta.exists():
        raise ErrorIngesta(f"no existe la ruta: {ruta}")
    if not ruta.is_file():
        raise ErrorIngesta(f"la ruta no es un archivo: {ruta}")
    limite = config.INGESTA_MAX_MB * 1024 * 1024
    if ruta.stat().st_size > limite:
        raise ErrorIngesta(
            f"'{ruta.name}' supera el límite de {config.INGESTA_MAX_MB} MB "
            "(se puede cambiar con INGESTA_MAX_MB en .env)"
        )
    return preparar_bytes(ruta.read_bytes(), ruta.name, origen=str(ruta.resolve()))


def preparar_texto(texto: str, nombre: str = "texto_pegado.txt") -> Documento:
    """Prepara un texto pegado directamente en la terminal."""
    if not texto.strip():
        raise ErrorIngesta("el texto pegado está vacío")
    return preparar_bytes(
        texto.encode("utf-8"),
        nombre=nombre,
        origen="texto pegado en la terminal",
        mime="text/plain",
    )


def _nombre_desde_url(url: str, respuesta: requests.Response, mime: str) -> str:
    disposicion = respuesta.headers.get("Content-Disposition", "")
    coincidencia = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", disposicion, re.IGNORECASE)
    if coincidencia:
        nombre = unquote(coincidencia.group(1).strip())
        if nombre:
            return _nombre_seguro(nombre)

    nombre = unquote(Path(urlparse(url).path).name)
    if not nombre:
        nombre = "descarga"
    if not Path(nombre).suffix:
        extension = mimetypes.guess_extension(mime.split(";")[0].strip()) if mime else None
        if extension:
            nombre += extension
        elif mime.startswith("text/html"):
            nombre += ".html"
    return _nombre_seguro(nombre)


def descargar_url(url: str) -> Documento:
    """Descarga una URL (página, PDF o imagen) y la prepara como documento."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ErrorIngesta(f"la URL no empieza con http:// o https://: {url}")
    sesion = crear_sesion()
    try:
        respuesta = sesion.get(url, timeout=(10, config.HTTP_TIMEOUT))
        respuesta.raise_for_status()
    except requests.RequestException as error:
        raise ErrorIngesta(f"no se pudo descargar {url}: {error}") from error

    limite = config.INGESTA_MAX_MB * 1024 * 1024
    declarado = respuesta.headers.get("Content-Length")
    if declarado and declarado.isdigit() and int(declarado) > limite:
        raise ErrorIngesta(
            f"la descarga supera el límite de {config.INGESTA_MAX_MB} MB "
            "(se puede cambiar con INGESTA_MAX_MB en .env)"
        )

    mime = (respuesta.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    nombre = _nombre_desde_url(url, respuesta, mime)
    return preparar_bytes(respuesta.content, nombre=nombre, origen=url, mime=mime)


def listar_fuentes(ruta: Path | str) -> list[Path]:
    """Lista archivos de una carpeta (recursivo) o devuelve un archivo suelto."""
    ruta = Path(ruta).expanduser()
    if not ruta.exists():
        raise ErrorIngesta(f"no existe la ruta: {ruta}")
    if ruta.is_file():
        return [ruta]
    archivos = []
    for archivo in sorted(ruta.rglob("*")):
        if not archivo.is_file():
            continue
        relativo = archivo.relative_to(ruta)
        if any(parte.startswith(".") for parte in relativo.parts):
            continue
        if archivo.name.lower() in NOMBRES_IGNORADOS:
            continue
        archivos.append(archivo)
    return archivos
