#!/usr/bin/env python3
"""
Punto de entrada del pipeline de análisis del mercado de Steam.

Uso típico (por lotes chicos):
    python pipeline.py verificar                      # chequea el entorno
    python pipeline.py todo --lote 1                  # extrae, estructura con IA y limpia
    python pipeline.py todo --lote 2
    python pipeline.py consolidar                     # junta todos los lotes
    python pipeline.py analizar                       # tablas, figuras y resumen

Datos externos (PDFs, imágenes, textos, URLs):
    python pipeline.py ingerir                        # procesa la carpeta entrada/
    python pipeline.py ingerir informe.pdf captura.png
    python pipeline.py ingerir --texto "..." --autorizar-ocr
    python pipeline.py ingerir https://ejemplo.com/tabla.html
    python pipeline.py estructurar --fuente externa   # tercera fuente de la IA

Etapas sueltas:
    python pipeline.py extraer --lote 1 --cantidad 100
    python pipeline.py steamdb --lote 1
    python pipeline.py estructurar --lote 1
    python pipeline.py limpiar --lote 1
    python pipeline.py estado
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from src import config
from src.almacenamiento.guardar import estado_lotes
from src.analisis import exploratorio
from src.extraccion import steam_api, steamdb
from src.ia import estructurador, ollama_cliente
from src.ingesta import gestor as ingesta
from src.ingesta import ocr as ocr_ingesta
from src.limpieza import limpiar


def _banner(texto: str) -> None:
    print()
    print("=" * 70)
    print(texto)
    print("=" * 70)


def cmd_verificar(_args) -> int:
    """Chequea que todo lo necesario esté en su lugar."""
    _banner("VERIFICACIÓN DEL ENTORNO")
    problemas = 0

    print(f"Python: {sys.version.split()[0]}")
    print(f"Carpeta de datos: {config.DIRECTORIO_DATOS}")

    print("\n1) IA local (Ollama):")
    if ollama_cliente.disponible():
        modelos = ollama_cliente.modelos_instalados()
        print(f"   - Servidor: OK en {config.OLLAMA_HOST}")
        print(f"   - Modelos instalados: {modelos}")
        if any(modelo.startswith(config.OLLAMA_MODELO.split(':')[0]) for modelo in modelos):
            print(f"   - Modelo configurado ({config.OLLAMA_MODELO}): OK")
        else:
            print(f"   - Modelo configurado ({config.OLLAMA_MODELO}): FALTA. Corré ./iniciar_ollama.sh")
            problemas += 1
    else:
        print(f"   - Servidor: no responde en {config.OLLAMA_HOST}")
        print("     Solución: ejecutá ./iniciar_ollama.sh")
        problemas += 1

    print("\n2) Navegador para SteamDB (Playwright):")
    try:
        import playwright  # noqa: F401

        print("   - Playwright instalado: OK")
    except ImportError:
        print("   - Playwright no instalado. SteamDB quedará solo con el intento por requests.")
        problemas += 1

    print("\n3) Dependencias de análisis:")
    for modulo in ("pandas", "matplotlib", "bs4", "requests", "tqdm"):
        try:
            __import__(modulo)
            print(f"   - {modulo}: OK")
        except ImportError:
            print(f"   - {modulo}: FALTA")
            problemas += 1

    print("\n4) Ingesta de datos externos y OCR:")
    diagnostico_ocr = ocr_ingesta.diagnostico()
    if diagnostico_ocr["ollama"]:
        print(f"   - Servidor de IA: OK en {config.OLLAMA_HOST}")
        if diagnostico_ocr["modelo_vision_instalado"]:
            print(f"   - Modelo de visión ({diagnostico_ocr['modelo_vision']}): OK")
        else:
            print(
                f"   - Modelo de visión ({diagnostico_ocr['modelo_vision']}): FALTA. "
                f"Para OCR: ollama pull {diagnostico_ocr['modelo_vision']}"
            )
    else:
        print(f"   - Servidor de IA: no responde en {config.OLLAMA_HOST} (el OCR con IA no funcionará)")
    if diagnostico_ocr["tesseract"]:
        print(f"   - Tesseract: OK ({diagnostico_ocr['tesseract']})")
    else:
        print("   - Tesseract: no instalado (opcional, solo si no usás IA de visión)")
    if diagnostico_ocr["pdftoppm"]:
        print(f"   - pdftoppm (PDF a imagen): OK ({diagnostico_ocr['pdftoppm']})")
    else:
        print("   - pdftoppm: falta; no se podrán escanear PDFs (sudo apt install poppler-utils)")
    if diagnostico_ocr["pillow"]:
        print("   - Pillow (imágenes): OK")
    else:
        print("   - Pillow: FALTA (uv pip install Pillow)")
    if (
        diagnostico_ocr["ollama"]
        and not diagnostico_ocr["modelo_vision_instalado"]
        and not diagnostico_ocr["tesseract"]
    ):
        print("   - Sin motor de OCR: el OCR de imágenes/escaneos no va a funcionar.")
        problemas += 1
    print(f"   - Carpeta para dejar datos: {config.DIRECTORIO_ENTRADA}")

    print("\n5) Lotes existentes:")
    resumen = estado_lotes()
    if not resumen:
        print("   - Todavía no hay ningún lote. Empezá con: python pipeline.py todo --lote 1")
    for fila in resumen:
        print(f"   - {fila}")

    print(f"\nResultado: {'todo listo' if problemas == 0 else str(problemas) + ' cosa(s) por resolver'}.")
    return 0


def cmd_estado(_args) -> int:
    """Muestra qué etapas están completas en cada lote."""
    _banner("ESTADO DE LOS LOTES")
    resumen = estado_lotes()
    if not resumen:
        print("No hay lotes todavía. Empezá con: python pipeline.py todo --lote 1")
        return 0
    print(f"{'lote':>4} | {'extraido':^9} | {'steamdb':^8} | {'IA':^6} | {'limpio':^7}")
    print("-" * 50)
    for fila in resumen:
        print(
            f"{fila['lote']:>4} | {('si' if fila['extraido'] else 'no'):^9} | "
            f"{('si' if fila['steamdb'] else 'no'):^8} | "
            f"{('si' if fila['estructurado_ia'] else 'no'):^6} | "
            f"{('si' if fila['limpio'] else 'no'):^7}"
        )
    return 0


def cmd_extraer(args) -> int:
    _banner(f"ETAPA 1/4 - EXTRACCIÓN (lote {args.lote})")
    steam_api.extraer_lote(
        lote=args.lote,
        cantidad=args.cantidad,
        con_resenas=not args.sin_resenas,
        con_jugadores=not args.sin_jugadores,
        orden=args.orden,
    )
    return 0


def cmd_steamdb(args) -> int:
    _banner(f"ETAPA 2/4 - STEAMDB (lote {args.lote})")
    uris = getattr(args, "uri", None) or steamdb.URIS_POR_DEFECTO
    steamdb.descargar_lote(args.lote, uris=uris)
    return 0


def cmd_estructurar(args) -> int:
    fuente = getattr(args, "fuente", "auto")
    max_fragmentos = getattr(args, "max_fragmentos", None)
    if fuente == "externa":
        _banner("ESTRUCTURACIÓN CON IA - DATOS EXTERNOS (tercera fuente)")
    else:
        _banner(f"ETAPA 3/4 - ESTRUCTURACIÓN CON IA (lote {args.lote})")
    try:
        estructurador.estructurar_lote(
            lote=args.lote, fuente=fuente, max_fragmentos=max_fragmentos
        )
    except ollama_cliente.ErrorIA as error:
        print(f"\n[error] {error}")
        return 1
    return 0


def cmd_limpiar(args) -> int:
    _banner(f"ETAPA 4/4 - LIMPIEZA (lote {args.lote})")
    try:
        limpiar.limpiar_lote(args.lote)
    except FileNotFoundError as error:
        print(f"\n[error] {error}")
        return 1
    return 0


def cmd_consolidar(_args) -> int:
    _banner("CONSOLIDACIÓN DE TODOS LOS LOTES")
    try:
        limpiar.consolidar()
    except FileNotFoundError as error:
        print(f"\n[error] {error}")
        return 1
    return 0


def cmd_analizar(_args) -> int:
    _banner("ANÁLISIS EXPLORATORIO")
    exploratorio.analizar_todo()
    return 0


def cmd_ingerir(args) -> int:
    """Etapa de ingesta: cualquier dato -> verificación IA -> OCR autorizado."""
    _banner("INGESTA DE DATOS EXTERNOS")
    if args.autorizar_ocr and args.sin_ocr:
        print("[error] --autorizar-ocr y --sin-ocr no se pueden usar juntos.")
        return 2
    autorizacion = "si" if args.autorizar_ocr else "no" if args.sin_ocr else "preguntar"
    resultados = ingesta.ingerir_fuentes(
        fuentes=args.fuentes,
        textos=args.texto,
        autorizacion=autorizacion,
        forzar=args.forzar,
        modelo_vision=args.modelo_vision,
        motor_ocr=args.motor_ocr,
        max_paginas=args.max_paginas,
    )
    errores = [
        resultado
        for resultado in resultados
        if str(resultado.get("estado", "")).startswith("error")
    ]
    if errores:
        print(f"\n[ingesta] {len(errores)} dato(s) terminaron con error. Revisá el detalle arriba.")
        return 1
    if not resultados:
        return 1 if (args.fuentes or args.texto) else 0
    print("\nSiguiente paso sugerido:")
    print("  - Leé data/intermedio/ingesta/resumen_ingesta.md")
    print("  - Para usarlo como tercera fuente de la IA: python pipeline.py estructurar --fuente externa")
    print("  - Después: python pipeline.py consolidar && python pipeline.py analizar")
    return 0


def cmd_lotes(args) -> int:
    """
    Corre varios lotes seguidos (modo catálogo). Saltea lo que ya está hecho,
    se reanuda solo si se corta y estima el tiempo restante.
    """
    _banner("CORRIDA POR LOTES (modo catálogo completo)")
    etapas = [etapa.strip() for etapa in args.etapas.split(",") if etapa.strip()]
    permitidas = {"extraer", "steamdb", "ia", "limpiar"}
    desconocidas = set(etapas) - permitidas
    if desconocidas:
        print(f"[lotes] Etapas desconocidas: {desconocidas}. Usá: {sorted(permitidas)}")
        return 1

    cantidad = args.cantidad or config.LOTE_TAMANO
    if args.hasta == "max":
        total = steam_api.total_juegos_en_tienda()
        hasta = max(1, math.ceil(total / cantidad))
        print(f"[lotes] El buscador de la tienda reporta {total:,} juegos -> lotes 1..{hasta} de {cantidad}")
    else:
        hasta = int(args.hasta)
    desde = args.desde
    if hasta < desde:
        print("[lotes] Rango vacío.")
        return 1

    print(f"[lotes] Rango: {desde}..{hasta} | etapas: {', '.join(etapas)} | cantidad por lote: {cantidad}")
    print("[lotes] Podés cortar con Ctrl+C: al volver a correr, sigue donde quedó.\n")

    completo = {fila["lote"]: fila for fila in estado_lotes()}
    momento = time.time()
    hechos = 0
    salteados = 0
    errores: list[tuple[int, str]] = []

    for lote in range(desde, hasta + 1):
        dos_digitos = f"{lote:02d}"
        estado = completo.get(lote, {})
        tareas = []
        if "extraer" in etapas and (args.forzar or not estado.get("extraido")):
            tareas.append("extraer")
        if "steamdb" in etapas and (args.forzar or not estado.get("steamdb")):
            tareas.append("steamdb")
        if "ia" in etapas and (args.forzar or not estado.get("estructurado_ia")):
            tareas.append("ia")
        if "limpiar" in etapas and (args.forzar or not estado.get("limpio")):
            tareas.append("limpiar")

        total_lotes = hasta - desde + 1
        posicion = lote - desde + 1
        if not tareas:
            salteados += 1
            print(f"[lotes] Lote {lote} ya estaba completo; se saltea ({posicion}/{total_lotes}).")
            continue

        transcurrido = (time.time() - momento) / 60
        pendientes = total_lotes - posicion
        promedio = transcurrido / max(hechos, 1)
        print(
            f"\n[lotes] === Lote {lote} ({posicion}/{total_lotes}) | etapas: {', '.join(tareas)} "
            f"| transcurrido {transcurrido:.1f} min | ETA ~{promedio * pendientes:.0f} min ==="
        )
        try:
            if "extraer" in tareas:
                steam_api.extraer_lote(
                    lote=lote,
                    cantidad=cantidad,
                    con_resenas=not args.sin_resenas,
                    con_jugadores=not args.sin_jugadores,
                    orden=args.orden,
                )
            if "steamdb" in tareas:
                steamdb.descargar_lote(lote)
            if "ia" in tareas:
                estructurador.estructurar_lote(lote)
            if "limpiar" in tareas:
                limpiar.limpiar_lote(lote)
            hechos += 1
        except KeyboardInterrupt:
            print("\n[lotes] Interrumpido por el usuario. Lo hecho quedó guardado.")
            break
        except Exception as error:  # noqa: BLE001
            errores.append((lote, str(error)[:200]))
            print(f"[lotes] ERROR en el lote {lote}: {str(error)[:300]}")
            if not args.seguir:
                print("[lotes] Se corta la corrida. Para seguir a pesar de errores usá --seguir.")
                return 1

    minutos = (time.time() - momento) / 60
    _banner(f"CORRIDA TERMINADA en {minutos:.1f} min")
    print(f"Lotes procesados: {hechos} | salteados (ya estaban): {salteados} | errores: {len(errores)}")
    for lote, motivo in errores[:10]:
        print(f"  - lote {lote}: {motivo}")
    print("\nSiguiente paso:")
    print("  python pipeline.py consolidar && python pipeline.py analizar")
    return 0


def cmd_todo(args) -> int:
    """Corre el pipeline completo para un lote: extraer -> steamdb -> IA -> limpiar."""
    momento = time.time()
    for paso in (cmd_extraer, cmd_steamdb, cmd_estructurar, cmd_limpiar):
        codigo = paso(args)
        if codigo != 0:
            print(f"\n[todo] La etapa {paso.__name__} devolvió error; se corta el lote.")
            return codigo
    minutos = (time.time() - momento) / 60
    _banner(f"LOTE {args.lote} TERMINADO en {minutos:.1f} minutos")
    print("Siguiente paso: python pipeline.py todo --lote", args.lote + 1)
    print("Cuando tengas varios lotes: python pipeline.py consolidar && python pipeline.py analizar")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline de análisis del mercado de videojuegos de Steam.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="comando", required=True)

    def agregar_comun(subparser, con_cantidad: bool = False):
        subparser.add_argument("--lote", type=int, default=1, help="número de lote (1, 2, 3...)")
        if con_cantidad:
            subparser.add_argument(
                "--cantidad", type=int, default=None,
                help=f"juegos por lote (por defecto {config.LOTE_TAMANO})",
            )

    agregar_comun(subparsers.add_parser("verificar", help="chequear el entorno"))

    agregar_comun(subparsers.add_parser("estado", help="ver el estado de cada lote"))

    extraer = subparsers.add_parser("extraer", help="etapa 1: API pública de Steam")
    agregar_comun(extraer, con_cantidad=True)
    extraer.add_argument("--sin-resenas", action="store_true", help="no pedir el resumen de reseñas")
    extraer.add_argument("--sin-jugadores", action="store_true", help="no pedir jugadores conectados")
    extraer.add_argument("--orden", default="Released_DESC", help="orden del buscador de la tienda")

    steamdb_parser = subparsers.add_parser("steamdb", help="etapa 2: páginas de SteamDB")
    agregar_comun(steamdb_parser)
    steamdb_parser.add_argument(
        "--uri", action="append", default=None,
        help="URL de SteamDB a descargar (se puede repetir). Por defecto: la tabla de más jugados",
    )

    estructurar = subparsers.add_parser("estructurar", help="etapa 3: estructurar con la IA local")
    agregar_comun(estructurar)
    estructurar.add_argument(
        "--fuente", choices=["auto", "steamdb", "tienda", "externa"], default="auto",
        help="fuente a procesar: auto (las tres), steamdb, tienda o externa",
    )
    estructurar.add_argument(
        "--max-fragmentos", type=int, default=None,
        help="limitar cuántos fragmentos manda a la IA (útil para probar)",
    )

    agregar_comun(subparsers.add_parser("limpiar", help="etapa 4: limpieza con pandas"))

    agregar_comun(subparsers.add_parser("consolidar", help="juntar todos los lotes limpios"))

    agregar_comun(subparsers.add_parser("analizar", help="análisis exploratorio + figuras"))

    ingerir = subparsers.add_parser(
        "ingerir", help="cargar cualquier dato externo y verificarlo con la IA"
    )
    ingerir.add_argument(
        "fuentes", nargs="*",
        help="archivos, carpetas o URLs (si no se indica nada, procesa la carpeta entrada/)",
    )
    ingerir.add_argument(
        "--texto", action="append", default=[],
        help="texto pegado directamente (se puede repetir)",
    )
    ingerir.add_argument(
        "--autorizar-ocr", action="store_true",
        help="autoriza el OCR sin preguntar (para corridas desatendidas)",
    )
    ingerir.add_argument(
        "--sin-ocr", action="store_true",
        help="prohíbe el OCR aunque la IA lo recomiende",
    )
    ingerir.add_argument(
        "--forzar", action="store_true",
        help="volver a procesar datos que ya estaban en data/intermedio/ingesta/",
    )
    ingerir.add_argument(
        "--modelo-vision", default=None,
        help=f"modelo de Ollama para visión/OCR (por defecto {config.OLLAMA_MODELO_VISION})",
    )
    ingerir.add_argument(
        "--motor-ocr", choices=["auto", "ollama", "tesseract"], default=None,
        help="motor de OCR (por defecto INGESTA_MOTOR_OCR o auto)",
    )
    ingerir.add_argument(
        "--max-paginas", type=int, default=None,
        help=f"máximo de páginas de PDF a escanear (por defecto {config.INGESTA_OCR_MAX_PAGINAS})",
    )

    todo = subparsers.add_parser("todo", help="correr las 4 etapas de un lote")
    agregar_comun(todo, con_cantidad=True)
    todo.add_argument("--sin-resenas", action="store_true", help="no pedir el resumen de reseñas")
    todo.add_argument("--sin-jugadores", action="store_true", help="no pedir jugadores conectados")
    todo.add_argument("--orden", default="Released_DESC", help="orden del buscador de la tienda")

    lotes = subparsers.add_parser("lotes", help="correr muchos lotes seguidos (catálogo completo)")
    lotes.add_argument("--desde", type=int, default=1, help="primer lote (por defecto 1)")
    lotes.add_argument(
        "--hasta", default="max",
        help="último lote, o 'max' para cubrir todo el catálogo de la tienda (por defecto)",
    )
    lotes.add_argument(
        "--cantidad", type=int, default=None,
        help=f"juegos por lote (por defecto {config.LOTE_TAMANO})",
    )
    lotes.add_argument(
        "--etapas", default="extraer,limpiar",
        help="etapas a correr, separadas por coma: extraer,steamdb,ia,limpiar (por defecto extraer,limpiar)",
    )
    lotes.add_argument("--sin-resenas", action="store_true", help="no pedir el resumen de reseñas (más rápido)")
    lotes.add_argument("--sin-jugadores", action="store_true", help="no pedir jugadores conectados")
    lotes.add_argument("--orden", default="Released_DESC", help="orden del buscador de la tienda")
    lotes.add_argument("--forzar", action="store_true", help="rehacer también los lotes ya completos")
    lotes.add_argument("--seguir", action="store_true", help="seguir con el próximo lote si uno falla")

    return parser


def main() -> int:
    parser = construir_parser()
    args = parser.parse_args()
    comandos = {
        "verificar": cmd_verificar,
        "estado": cmd_estado,
        "extraer": cmd_extraer,
        "steamdb": cmd_steamdb,
        "estructurar": cmd_estructurar,
        "limpiar": cmd_limpiar,
        "consolidar": cmd_consolidar,
        "analizar": cmd_analizar,
        "ingerir": cmd_ingerir,
        "todo": cmd_todo,
        "lotes": cmd_lotes,
    }
    return comandos[args.comando](args)


if __name__ == "__main__":
    raise SystemExit(main())
