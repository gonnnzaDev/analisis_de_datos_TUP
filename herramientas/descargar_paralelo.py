#!/usr/bin/env python3
"""
Descargador paralelo por rangos HTTP, pensado para archivos grandes
(tarball de Ollama, modelo GGUF, etc.) cuando la conexión por conexión es lenta.

Uso:
    python herramientas/descargar_paralelo.py URL DESTINO [--conexiones 8] [--sha256 HEX]

Verifica la integridad si se pasa --sha256 (comparando el hash del archivo final).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

BLOQUE = 4 * 1024 * 1024  # 4 MB por pedido


def obtener_tamano(url: str) -> int | None:
    """Tamaño total del archivo, si el servidor lo informa."""
    pedido = Request(url, method="HEAD")
    with urlopen(pedido, timeout=30) as respuesta:
        tamano = respuesta.headers.get("Content-Length")
    return int(tamano) if tamano else None


def descargar_rango(url: str, inicio: int, fin: int, destino: Path, callback) -> None:
    pedido = Request(url, headers={"Range": f"bytes={inicio}-{fin}"})
    with urlopen(pedido, timeout=120) as respuesta:
        datos = respuesta.read()
    with destino.open("r+b") as archivo:
        archivo.seek(inicio)
        archivo.write(datos)
    callback(len(datos))


def descargar_paralelo(url: str, destino: Path, conexiones: int = 8, sha256: str | None = None) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    tamano = obtener_tamano(url)
    if tamano is None:
        print("El servidor no informa el tamaño; se descarga en una sola conexión.")
        with urlopen(Request(url), timeout=300) as respuesta:
            destino.write_bytes(respuesta.read())
    else:
        print(f"Tamaño total: {tamano / 1e6:.1f} MB | conexiones: {conexiones}")
        with destino.open("wb") as archivo:
            archivo.truncate(tamano)

        tareas = []
        for indice in range(conexiones):
            inicio = indice * BLOQUE
            while inicio < tamano:
                tareas.append((inicio, min(inicio + BLOQUE - 1, tamano - 1)))
                inicio += BLOQUE * conexiones
        tareas.sort()

        descargado = [0]
        candado = threading.Lock()
        momento = [time.time()]

        def progreso(bytes_nuevos: int) -> None:
            with candado:
                descargado[0] += bytes_nuevos
                transcurrido = time.time() - momento[0]
                velocidad = descargado[0] / max(transcurrido, 0.001) / 1e6
                porcentaje = 100 * descargado[0] / tamano
                print(
                    f"\r  {porcentaje:5.1f}%  {descargado[0] / 1e6:7.1f} MB  ({velocidad:4.1f} MB/s)",
                    end="",
                    flush=True,
                )

        pendientes = list(tareas)
        fallidos: list[tuple[int, int]] = []

        def trabajador() -> None:
            while True:
                with candado:
                    if not pendientes:
                        return
                    inicio, fin = pendientes.pop(0)
                try:
                    descargar_rango(url, inicio, fin, destino, progreso)
                except Exception:  # noqa: BLE001 - se reintenta en otra ronda
                    with candado:
                        fallidos.append((inicio, fin))

        # Hasta 4 rondas: los errores transitorios de red (DNS, timeouts) se reintentan.
        pendientes = list(tareas)
        fallidos: list[tuple[int, int]] = []
        for ronda in range(4):
            if ronda > 0:
                with candado:
                    pendientes = list(fallidos)
                    fallidos.clear()
                if not pendientes:
                    break
                print(f"\n  Reintento {ronda}: quedan {len(pendientes)} pedidos pendientes...")
                time.sleep(3 * ronda)
            hilos = [threading.Thread(target=trabajador, daemon=True) for _ in range(conexiones)]
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join()
        print()

        if fallidos:
            raise RuntimeError(
                f"Quedaron {len(fallidos)} pedidos sin completar tras 4 rondas. "
                "El archivo quedó incompleto: volvé a ejecutar el mismo comando y se reintenta todo."
            )

    if sha256:
        print("Verificando integridad (sha256)...")
        huella = hashlib.sha256()
        with destino.open("rb") as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                huella.update(bloque)
        if huella.hexdigest().lower() != sha256.lower():
            destino.unlink(missing_ok=True)
            raise RuntimeError("El sha256 no coincide: el archivo se borró.")
        print("Integridad OK.")

    print(f"Listo: {destino} ({os.path.getsize(destino) / 1e6:.1f} MB)")
    return destino


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("destino")
    parser.add_argument("--conexiones", type=int, default=8)
    parser.add_argument("--sha256", default=None)
    args = parser.parse_args()
    descargar_paralelo(args.url, Path(args.destino), args.conexiones, args.sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
