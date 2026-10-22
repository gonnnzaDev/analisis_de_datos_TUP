#!/usr/bin/env python3
"""
Descarga un modelo de Ollama directo del registro, en paralelo y con verificación sha256.

Sirve cuando 'ollama pull' va muy lento: baja el manifiesto y cada capa (blob)
con varias conexiones y los coloca en la carpeta de modelos de Ollama,
con los mismos nombres que usa Ollama internamente.

Uso:
    python herramientas/descargar_modelo.py qwen2.5:7b
    python herramientas/descargar_modelo.py qwen2.5:7b --conexiones 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from descargar_paralelo import descargar_paralelo

REGISTRO = os.environ.get("OLLAMA_REGISTRY", "https://registry.ollama.ai")


def carpeta_modelos() -> Path:
    base = os.environ.get("OLLAMA_MODELS")
    if base:
        return Path(base)
    return Path.home() / ".ollama" / "models"


def bajar_manifiesto(modelo: str, etiqueta: str) -> dict:
    url = f"{REGISTRO}/v2/library/{modelo}/manifests/{etiqueta}"
    print(f"Manifiesto: {url}")
    with urlopen(Request(url), timeout=60) as respuesta:
        contenido = respuesta.read()
    datos = json.loads(contenido)
    destino = carpeta_modelos() / "manifests" / "registry.ollama.ai" / "library" / modelo / etiqueta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    print(f"Manifiesto guardado en {destino}")
    return datos


def _sha256_coincide(ruta: Path, huella: str) -> bool:
    calculador = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            calculador.update(bloque)
    return calculador.hexdigest().lower() == huella.lower()


def bajar_blob(digest: str, tamano: int, conexiones: int) -> None:
    algoritmo, huella = digest.split(":", 1)
    destino = carpeta_modelos() / "blobs" / f"{algoritmo}-{huella}"
    if destino.exists() and destino.stat().st_size == tamano and _sha256_coincide(destino, huella):
        print(f"Ya está y verifica: {destino.name} ({tamano / 1e6:.1f} MB)")
        return
    if destino.exists():
        print(f"Archivo incompleto o corrupto, se vuelve a bajar: {destino.name}")
        destino.unlink()
    if algoritmo != "sha256":
        raise RuntimeError(f"Algoritmo de hash no soportado: {algoritmo}")
    url = f"{REGISTRO}/v2/library/{_modelo[0]}/blobs/{digest}"
    print(f"\nCapa {huella[:16]}... ({tamano / 1e6:.1f} MB)")
    descargar_paralelo(url, destino, conexiones=conexiones, sha256=huella)


_modelo: tuple[str, str] = ("qwen2.5", "7b")


def main() -> int:
    global _modelo
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modelo", help="nombre con etiqueta, por ejemplo qwen2.5:7b")
    parser.add_argument("--conexiones", type=int, default=10)
    args = parser.parse_args()

    nombre, _, etiqueta = args.modelo.partition(":")
    etiqueta = etiqueta or "latest"
    _modelo = (nombre, etiqueta)

    manifiesto = bajar_manifiesto(nombre, etiqueta)
    capas = list(manifiesto.get("layers", []))
    config = manifiesto.get("config")
    if config:
        capas.insert(0, config)

    print(f"\nCapas a descargar: {len(capas)}")
    total = sum(capa.get("size", 0) for capa in capas)
    print(f"Total: {total / 1e9:.2f} GB\n")

    for capa in capas:
        bajar_blob(capa["digest"], capa.get("size", 0), args.conexiones)

    print("\nTodo descargado. Verificá con: ~/.local/ollama/bin/ollama list")
    print(f"Si no aparece, probá: ~/.local/ollama/bin/ollama pull {args.modelo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
