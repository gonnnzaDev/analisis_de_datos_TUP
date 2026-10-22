"""Módulo de almacenamiento: guarda y lee JSON/CSV, y reporta el estado de los lotes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .. import config


def ahora_iso() -> str:
    """Fecha y hora actual en formato ISO (zona horaria local)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def guardar_json(datos, ruta: Path) -> Path:
    """Guarda un objeto Python como JSON legible (UTF-8)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
    return ruta


def leer_json(ruta: Path, defecto=None):
    """Lee un JSON. Si no existe, devuelve `defecto`."""
    ruta = Path(ruta)
    if not ruta.exists():
        return defecto
    with ruta.open("r", encoding="utf-8") as archivo:
        return json.load(archivo)


def guardar_texto(texto: str, ruta: Path) -> Path:
    """Guarda texto plano (por ejemplo, HTML crudo)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def guardar_csv(tabla: pd.DataFrame, ruta: Path) -> Path:
    """Guarda un DataFrame como CSV (UTF-8 con BOM, para abrir bien en Excel)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(ruta, index=False, encoding="utf-8-sig")
    return ruta


def leer_csv(ruta: Path) -> pd.DataFrame | None:
    """Lee un CSV. Si no existe, devuelve None."""
    ruta = Path(ruta)
    if not ruta.exists():
        return None
    return pd.read_csv(ruta)


def registrar_en_jsonl(registro: dict, ruta: Path) -> None:
    """Agrega una línea JSON a un archivo .jsonl (bitácora de la IA, por ejemplo)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


def lotes_disponibles() -> list[int]:
    """Detecta qué números de lote existen en data/ (según los archivos crudos)."""
    numeros: set[int] = set()
    for carpeta in (config.CRUDO_API, config.CRUDO_STEAMDB, config.INTERMEDIO, config.PROCESADO):
        if not carpeta.exists():
            continue
        for archivo in carpeta.rglob("lote_*"):
            nombre = archivo.name
            partes = nombre.split("_")
            for parte in partes:
                if parte.isdigit():
                    numeros.add(int(parte))
                    break
    return sorted(numeros)


def estado_lotes() -> list[dict]:
    """Resume, para cada lote detectado, qué etapas están completas."""
    resumen = []
    for lote in lotes_disponibles():
        dos_digitos = f"{lote:02d}"
        carpeta_lote = config.PROCESADO / f"lote_{dos_digitos}"
        etapas = {
            "extraido": (config.CRUDO_API / f"lote_{dos_digitos}_appdetails.json").exists(),
            "steamdb": (config.CRUDO_STEAMDB / f"lote_{dos_digitos}_steamdb.txt").exists(),
            "estructurado_ia": (config.INTERMEDIO / f"ia_lote_{dos_digitos}.json").exists(),
            "limpio": (carpeta_lote / f"juegos_lote_{dos_digitos}.csv").exists(),
        }
        resumen.append({"lote": lote, **etapas})
    return resumen
