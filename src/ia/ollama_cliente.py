"""
Cliente mínimo para hablar con Ollama (la IA local).

Ollama expone una API HTTP en http://localhost:11434. Este módulo:
- verifica si el servidor está corriendo y qué modelos hay,
- hace pedidos de chat pidiendo SIEMPRE JSON válido (format="json"),
- registra cada pedido en data/intermedio/registro_ia.jsonl (duración, tokens, error),
- reintenta si el modelo devuelve algo que no se puede parsear.
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import requests

from .. import config
from ..almacenamiento.guardar import ahora_iso, registrar_en_jsonl

BITACORA = "registro_ia.jsonl"


class ErrorIA(RuntimeError):
    """Error controlado del módulo de IA."""


def _url(ruta: str) -> str:
    return f"{config.OLLAMA_HOST.rstrip('/')}{ruta}"


def disponible(timeout: int = 4) -> bool:
    """¿Está Ollama escuchando?"""
    try:
        respuesta = requests.get(_url("/api/tags"), timeout=timeout)
        return respuesta.status_code == 200
    except requests.RequestException:
        return False


def modelos_instalados() -> list[str]:
    """Nombres de los modelos descargados en Ollama."""
    try:
        respuesta = requests.get(_url("/api/tags"), timeout=10)
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except requests.RequestException as error:
        raise ErrorIA(f"No se pudo consultar Ollama en {config.OLLAMA_HOST}: {error}") from error
    except ValueError as error:
        raise ErrorIA(f"Ollama devolvió una respuesta inválida: {error}") from error
    return [modelo.get("name", "") for modelo in cuerpo.get("models", [])]


def modelo_instalado(nombre: str) -> bool:
    """¿Está descargado ese modelo? Acepta 'modelo' cuando hay 'modelo:etiqueta'."""
    try:
        instalados = modelos_instalados()
    except ErrorIA:
        return False
    objetivo = (nombre or "").strip()
    if not objetivo:
        return False
    base_objetivo, _, etiqueta_objetivo = objetivo.partition(":")
    for instalado in instalados:
        if instalado == objetivo:
            return True
        base_instalado, _, etiqueta_instalado = instalado.partition(":")
        if base_instalado != base_objetivo:
            continue
        if not etiqueta_objetivo:
            return True
        if etiqueta_instalado == etiqueta_objetivo or etiqueta_instalado.startswith(
            etiqueta_objetivo + "-"
        ):
            return True
    return False


def asegurar_modelo(modelo: str | None = None) -> None:
    """Frena con un mensaje claro si Ollama no está corriendo o falta el modelo."""
    if not disponible():
        raise ErrorIA(
            "Ollama no está corriendo. Ejecutá ./iniciar_ollama.sh "
            f"(o '{config.OLLAMA_HOST}' si corre en otra máquina)."
        )
    objetivo = modelo or config.OLLAMA_MODELO
    if not modelo_instalado(objetivo):
        raise ErrorIA(
            f"El modelo '{objetivo}' no está descargado. "
            f"Ejecutá: ollama pull {objetivo}. "
            f"Modelos disponibles: {modelos_instalados() or 'ninguno'}"
        )


def _imagen_base64(imagen) -> str:
    """Convierte una ruta o bytes de imagen al base64 que espera la API de Ollama."""
    if isinstance(imagen, (str, Path)):
        datos = Path(imagen).read_bytes()
    else:
        datos = bytes(imagen)
    return base64.b64encode(datos).decode("ascii")


def _mensaje_usuario(usuario: str, imagenes: list | None = None) -> dict:
    mensaje = {"role": "user", "content": usuario}
    if imagenes:
        mensaje["images"] = [_imagen_base64(imagen) for imagen in imagenes]
    return mensaje


def _cuerpo_pedido(
    sistema: str,
    usuario: str,
    imagenes: list | None,
    temperatura: float,
    formato_json: bool,
) -> dict:
    cuerpo = {
        "model": config.OLLAMA_MODELO,
        "messages": [
            {"role": "system", "content": sistema},
            _mensaje_usuario(usuario, imagenes),
        ],
        "stream": False,
        "keep_alive": "15m",
        "options": {
            "temperature": temperatura,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
        },
    }
    if formato_json:
        cuerpo["format"] = "json"
    return cuerpo


def _limpiar_respuesta(texto: str) -> str:
    """Saca los bloques de código con los que algunos modelos envuelven la respuesta."""
    texto = texto.strip()
    texto = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    return texto.strip()


def _extraer_json(texto: str) -> str:
    """Recorta el primer objeto JSON que aparezca en la respuesta del modelo."""
    texto = _limpiar_respuesta(texto)
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio == -1 or fin == -1 or fin <= inicio:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return texto[inicio : fin + 1]


def pedir_json(
    sistema: str,
    usuario: str,
    fuente: str = "general",
    temperatura: float = 0.0,
    intentos: int = 3,
    imagenes: list | None = None,
    modelo: str | None = None,
) -> dict:
    """
    Pide al modelo local una respuesta en JSON y devuelve el objeto ya parseado.

    `fuente` es solo para la bitácora (por ejemplo "steamdb_charts" o "tienda_busqueda").
    `imagenes` permite mandar capturas o páginas escaneadas a un modelo multimodal.
    """
    modelo_usado = modelo or config.OLLAMA_MODELO
    carga = _cuerpo_pedido(sistema, usuario, imagenes, temperatura, formato_json=True)
    carga["model"] = modelo_usado

    ultimo_error = ""
    for intento in range(1, intentos + 1):
        momento = time.time()
        try:
            respuesta = requests.post(
                _url("/api/chat"),
                json=carga,
                timeout=(10, config.OLLAMA_TIMEOUT),
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            contenido = (cuerpo.get("message") or {}).get("content", "")
            datos = json.loads(_extraer_json(contenido))
            duracion = round(time.time() - momento, 1)
            registrar_en_jsonl(
                {
                    "fecha": ahora_iso(),
                    "fuente": fuente,
                    "modelo": modelo_usado,
                    "tipo": "json",
                    "imagenes": len(imagenes or []),
                    "intento": intento,
                    "ok": True,
                    "duracion_seg": duracion,
                    "caracteres_pedido": len(usuario),
                    "tokens_respuesta": cuerpo.get("eval_count"),
                    "tokens_pedido": cuerpo.get("prompt_eval_count"),
                },
                config.INTERMEDIO / BITACORA,
            )
            return datos
        except (
            requests.RequestException,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            AttributeError,
            TypeError,
        ) as error:
            ultimo_error = str(error)
            registrar_en_jsonl(
                {
                    "fecha": ahora_iso(),
                    "fuente": fuente,
                    "modelo": modelo_usado,
                    "tipo": "json",
                    "imagenes": len(imagenes or []),
                    "intento": intento,
                    "ok": False,
                    "duracion_seg": round(time.time() - momento, 1),
                    "caracteres_pedido": len(usuario),
                    "error": ultimo_error[:300],
                },
                config.INTERMEDIO / BITACORA,
            )
            if intento < intentos:
                time.sleep(2 * intento)

    raise ErrorIA(f"La IA no devolvió JSON válido tras {intentos} intentos. Último error: {ultimo_error}")


def pedir_texto(
    sistema: str,
    usuario: str,
    fuente: str = "general",
    temperatura: float = 0.0,
    intentos: int = 2,
    imagenes: list | None = None,
    modelo: str | None = None,
) -> str:
    """
    Pide una respuesta de texto libre (por ejemplo, una transcripción de OCR).
    Igual que `pedir_json`, pero sin `format="json"`.
    """
    modelo_usado = modelo or config.OLLAMA_MODELO
    carga = _cuerpo_pedido(sistema, usuario, imagenes, temperatura, formato_json=False)
    carga["model"] = modelo_usado

    ultimo_error = ""
    for intento in range(1, intentos + 1):
        momento = time.time()
        try:
            respuesta = requests.post(
                _url("/api/chat"),
                json=carga,
                timeout=(10, config.OLLAMA_TIMEOUT),
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            contenido = _limpiar_respuesta((cuerpo.get("message") or {}).get("content", ""))
            if not contenido:
                raise ValueError("la respuesta vino vacía")
            duracion = round(time.time() - momento, 1)
            registrar_en_jsonl(
                {
                    "fecha": ahora_iso(),
                    "fuente": fuente,
                    "modelo": modelo_usado,
                    "tipo": "texto",
                    "imagenes": len(imagenes or []),
                    "intento": intento,
                    "ok": True,
                    "duracion_seg": duracion,
                    "caracteres_pedido": len(usuario),
                    "caracteres_respuesta": len(contenido),
                    "tokens_respuesta": cuerpo.get("eval_count"),
                    "tokens_pedido": cuerpo.get("prompt_eval_count"),
                },
                config.INTERMEDIO / BITACORA,
            )
            return contenido
        except (
            requests.RequestException,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            AttributeError,
            TypeError,
        ) as error:
            ultimo_error = str(error)
            registrar_en_jsonl(
                {
                    "fecha": ahora_iso(),
                    "fuente": fuente,
                    "modelo": modelo_usado,
                    "tipo": "texto",
                    "imagenes": len(imagenes or []),
                    "intento": intento,
                    "ok": False,
                    "duracion_seg": round(time.time() - momento, 1),
                    "caracteres_pedido": len(usuario),
                    "error": ultimo_error[:300],
                },
                config.INTERMEDIO / BITACORA,
            )
            if intento < intentos:
                time.sleep(2 * intento)

    raise ErrorIA(f"La IA no devolvió texto tras {intentos} intentos. Último error: {ultimo_error}")
