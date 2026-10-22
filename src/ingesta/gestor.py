"""
Orquestador de la ingesta de datos externos.

Flujo por cada dato:
1. Se guarda una copia en data/crudo/ingesta/ y se extrae el texto si es posible.
2. La IA "portero" verifica si aporta al análisis.
3. Si hace falta OCR y la persona lo autoriza, otra IA (visión) transcribe.
4. Todo queda auditado en data/intermedio/ingesta/<id>/ y se arma un resumen.

El OCR nunca se ejecuta por sorpresa: se pide autorización explícita o se usa
--autorizar-ocr. Con --sin-ocr se prohíbe aunque la IA lo recomiende.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from .. import config
from ..almacenamiento.guardar import (
    ahora_iso,
    guardar_csv,
    guardar_json,
    guardar_texto,
    leer_json,
    registrar_en_jsonl,
)
from ..ia.ollama_cliente import ErrorIA
from . import archivos, ocr, verificador

AUTORIZACIONES = {"preguntar", "si", "no"}


def _ruta_visible(ruta: Path | None) -> str:
    if not ruta or not Path(ruta).exists():
        return ""
    try:
        return str(Path(ruta).relative_to(config.RAIZ))
    except ValueError:
        return str(ruta)


def _guardar_texto(carpeta: Path, nombre: str, texto: str) -> Path | None:
    if not texto or not texto.strip():
        return None
    ruta = carpeta / nombre
    guardar_texto(texto, ruta)
    return ruta


def _resolver_autorizacion(
    documento: archivos.Documento,
    decision: dict | None,
    autorizacion: str,
    paginas: int,
    modelo: str,
    motor: str = "auto",
) -> tuple[bool, str]:
    """Devuelve (autorizado, motivo). Solo un 'sí' explícito habilita el OCR."""
    if autorizacion == "no":
        return False, "OCR prohibido con --sin-ocr"
    if autorizacion == "si":
        return True, "autorizado con --autorizar-ocr"
    if not sys.stdin.isatty():
        return False, "no hay terminal interactiva; usá --autorizar-ocr"

    if motor == "tesseract":
        detalle_motor = "Tesseract"
    elif motor == "ollama":
        detalle_motor = f"IA de visión ({modelo})"
    else:
        detalle_motor = f"IA de visión ({modelo}) o Tesseract"
    print()
    print(f"[ingesta] El archivo '{documento.nombre_original}' necesita OCR ({paginas} página/s).")
    if decision:
        print(f"[ingesta] La IA lo considera útil (relevancia {decision.get('relevancia', 0)}/100): "
              f"{decision.get('motivo', '')}")
    else:
        print("[ingesta] Todavía no se pudo verificar el contenido; después del OCR lo revisa la IA.")
    print(f"[ingesta] Motor de OCR: {detalle_motor}.")
    try:
        respuesta = input("[ingesta] ¿Autorizás el OCR? [s/N]: ").strip().lower()
    except EOFError:
        return False, "no se pudo leer la respuesta"
    if respuesta in {"s", "si", "sí", "y", "yes"}:
        return True, "autorizado por el usuario"
    return False, "el usuario no autorizó el OCR"


def _cargar_decision_previa(carpeta: Path) -> dict | None:
    ruta = carpeta / "decision.json"
    if not ruta.exists():
        return None
    try:
        datos = leer_json(ruta, None)
    except (json.JSONDecodeError, OSError):
        return None
    return datos if isinstance(datos, dict) else None


def _texto_para_verificar(documento: archivos.Documento, paginas: list[Path],
                           modelo_vision: str | None) -> tuple[dict | None, str, str]:
    """
    Verifica con la primera página de la imagen cuando hay que hacer OCR (así el
    portero ve el contenido completo, no solo el poco texto nativo). Si no hay
    modelo de visión, cae al texto nativo y, como último recurso, deja la
    verificación pendiente para después del OCR.
    """
    if paginas and documento.requiere_ocr:
        try:
            decision = verificador.verificar_imagen(
                paginas[0], documento.contexto, modelo=modelo_vision
            )
            return decision, "imagen", ""
        except (ErrorIA, ValueError) as error:
            print(f"[ingesta] No se pudo verificar la imagen '{documento.nombre_original}': {error}")
            if not documento.texto.strip():
                print("[ingesta] Se puede autorizar el OCR y verificar el texto después.")
                return None, "imagen", str(error)

    if documento.texto.strip():
        try:
            decision = verificador.verificar_texto(documento.texto, documento.contexto)
            return decision, "texto", ""
        except (ErrorIA, ValueError) as error:
            print(f"[ingesta] No se pudo verificar '{documento.nombre_original}': {error}")
            return None, "texto", str(error)

    if paginas:
        try:
            decision = verificador.verificar_imagen(
                paginas[0], documento.contexto, modelo=modelo_vision
            )
            return decision, "imagen", ""
        except (ErrorIA, ValueError) as error:
            print(
                f"[ingesta] No se pudo verificar la imagen '{documento.nombre_original}': {error}\n"
                "[ingesta] Se puede autorizar el OCR y verificar el texto después."
            )
            return None, "imagen", str(error)

    return None, "", "no hay texto ni imágenes para verificar"


def _armar_decision(
    documento: archivos.Documento,
    estado: str,
    verificacion: dict | None,
    verificado_con: str,
    error_verificacion: str,
    datos_ocr: dict | None,
    ruta_texto: Path | None,
    carpeta: Path,
) -> dict:
    return {
        "id": documento.id,
        "archivo": documento.nombre_original,
        "origen": documento.origen,
        "fecha": ahora_iso(),
        "estado": estado,
        "documento": documento.a_dict(),
        "verificacion": verificacion,
        "verificado_con": verificado_con,
        "error_verificacion": error_verificacion[:500],
        "ocr": datos_ocr,
        "texto": _ruta_visible(ruta_texto),
        "carpeta": _ruta_visible(carpeta),
    }


def _guardar_resultado(carpeta: Path, decision: dict) -> dict:
    guardar_json(decision, carpeta / "decision.json")
    guardar_json(decision["documento"], carpeta / "documento.json")
    registrar_en_jsonl(
        {
            "fecha": decision["fecha"],
            "id": decision["id"],
            "archivo": decision["archivo"],
            "estado": decision["estado"],
            "sirve": (decision.get("verificacion") or {}).get("sirve"),
            "ocr": bool((decision.get("ocr") or {}).get("autorizado")),
        },
        config.INTERMEDIO_INGESTA / "registro_ingesta.jsonl",
    )
    return decision


def procesar_documento(
    documento: archivos.Documento,
    autorizacion: str = "preguntar",
    forzar: bool = False,
    modelo_vision: str | None = None,
    motor_ocr: str | None = None,
    max_paginas: int | None = None,
) -> dict:
    """Procesa un documento completo. Devuelve el registro guardado."""
    autorizacion = autorizacion if autorizacion in AUTORIZACIONES else "preguntar"
    carpeta = config.INTERMEDIO_INGESTA / documento.id
    carpeta.mkdir(parents=True, exist_ok=True)
    previa = _cargar_decision_previa(carpeta)

    if previa and not forzar:
        estados_reintentables = {"pendiente_ocr", "error_verificacion", "error_ocr"}
        if previa.get("estado") not in estados_reintentables:
            print(f"[ingesta] '{documento.nombre_original}' ya estaba procesado; se saltea.")
            return {**previa, "nuevo": False}
        print(f"[ingesta] '{documento.nombre_original}' tenía un estado reintentable "
              f"({previa.get('estado')}); se vuelve a procesar.")

    print(f"\n[ingesta] Procesando '{documento.nombre_original}' ({documento.categoria}, "
          f"{documento.bytes / 1024:.0f} KB)...")

    if documento.error_lectura:
        print(f"[ingesta] No se pudo leer: {documento.error_lectura}")
        decision = _armar_decision(
            documento, "error_lectura", None, "", documento.error_lectura, None, None, carpeta
        )
        return _guardar_resultado(carpeta, decision)

    # Las imágenes/escaneos se preparan una vez y se reutilizan para verificar y para OCR.
    paginas: list[Path] = []
    error_paginas = ""
    if documento.requiere_ocr:
        try:
            paginas = ocr.preparar_paginas(
                documento, carpeta / "ocr_paginas", max_paginas=max_paginas
            )
        except (ocr.ErrorOCR, OSError) as error:
            error_paginas = str(error)
    if paginas and documento.paginas and len(paginas) < documento.paginas:
        print(
            f"[ingesta] Se van a escanear {len(paginas)} de {documento.paginas} páginas "
            "(límite configurable con INGESTA_OCR_MAX_PAGINAS o --max-paginas)."
        )

    verificacion, verificado_con, error_verificacion = _texto_para_verificar(
        documento, paginas, modelo_vision
    )
    if verificacion and documento.requiere_ocr:
        verificacion["requiere_ocr"] = True

    if verificacion and not verificacion.get("sirve"):
        print(f"[ingesta] La IA lo rechazó: {verificacion.get('motivo', 'sin motivo')}")
        ruta_texto = _guardar_texto(carpeta, "texto_nativo.txt", documento.texto)
        decision = _armar_decision(
            documento, "rechazado", verificacion, verificado_con, "", None, ruta_texto, carpeta
        )
        return _guardar_resultado(carpeta, decision)

    necesita_ocr = bool(documento.requiere_ocr and paginas)
    if documento.requiere_ocr and not paginas:
        if not documento.texto.strip():
            estado = "error_ocr"
        elif verificacion is None:
            estado = "error_verificacion"
        else:
            estado = "procesado"
        motivo = error_paginas or "no se pudieron preparar páginas para OCR"
        if estado == "error_ocr":
            print(f"[ingesta] No se pudo preparar el OCR: {motivo}")
        datos_ocr = {
            "requerido": True,
            "autorizado": False,
            "motivo_autorizacion": motivo,
            "motor": "",
            "modelo": "",
            "paginas": 0,
            "caracteres": 0,
            "error": motivo,
        }
        ruta_texto = _guardar_texto(carpeta, "texto.txt", documento.texto)
        decision = _armar_decision(
            documento, estado, verificacion, verificado_con,
            error_verificacion, datos_ocr, ruta_texto, carpeta,
        )
        return _guardar_resultado(carpeta, decision)

    if not necesita_ocr:
        if verificacion is None:
            estado = "error_verificacion"
        elif not documento.texto.strip():
            estado = "sin_texto"
        else:
            estado = "procesado"
        ruta_texto = _guardar_texto(carpeta, "texto.txt", documento.texto)
        decision = _armar_decision(
            documento, estado, verificacion, verificado_con,
            error_verificacion, None, ruta_texto, carpeta,
        )
        return _guardar_resultado(carpeta, decision)

    # A partir de acá: hay que hacer OCR.
    if verificacion is None and error_paginas:
        decision = _armar_decision(
            documento, "error_ocr", None, verificado_con, error_verificacion,
            {
                "requerido": True, "autorizado": False,
                "motivo_autorizacion": error_paginas, "motor": "", "modelo": "",
                "paginas": 0, "caracteres": 0, "error": error_paginas,
            },
            None, carpeta,
        )
        return _guardar_resultado(carpeta, decision)

    autorizado, motivo_autorizacion = _resolver_autorizacion(
        documento,
        verificacion,
        autorizacion,
        len(paginas),
        modelo_vision or config.OLLAMA_MODELO_VISION,
        motor_ocr or config.INGESTA_MOTOR_OCR,
    )
    if not autorizado:
        print(f"[ingesta] OCR no autorizado ({motivo_autorizacion}). Queda pendiente.")
        ruta_texto = _guardar_texto(carpeta, "texto_nativo.txt", documento.texto)
        datos_ocr = {
            "requerido": True,
            "autorizado": False,
            "motivo_autorizacion": motivo_autorizacion,
            "motor": "",
            "modelo": "",
            "paginas": len(paginas),
            "caracteres": 0,
            "error": "",
        }
        decision = _armar_decision(
            documento, "pendiente_ocr", verificacion, verificado_con,
            error_verificacion, datos_ocr, ruta_texto, carpeta,
        )
        return _guardar_resultado(carpeta, decision)

    texto_nativo = documento.texto
    ruta_nativo = _guardar_texto(carpeta, "texto_nativo.txt", texto_nativo)
    print(f"[ingesta] OCR autorizado ({motivo_autorizacion}); transcribiendo {len(paginas)} página/s...")
    datos_ocr = {
        "requerido": True,
        "autorizado": True,
        "motivo_autorizacion": motivo_autorizacion,
        "motor": "",
        "modelo": "",
        "paginas": len(paginas),
        "caracteres": 0,
        "error": "",
        "fecha": ahora_iso(),
    }
    ruta_ocr: Path | None = None
    try:
        resultado_ocr = ocr.transcribir(
            paginas, documento.contexto, motor=motor_ocr, modelo=modelo_vision
        )
        texto_ocr = resultado_ocr["texto"]
        datos_ocr.update(
            {
                "motor": resultado_ocr["motor"],
                "modelo": resultado_ocr["modelo"],
                "paginas": resultado_ocr["paginas"],
                "caracteres": resultado_ocr["caracteres"],
            }
        )
        ruta_ocr = _guardar_texto(carpeta, "texto_ocr.txt", texto_ocr)
        print(f"[ingesta] OCR listo: {len(texto_ocr)} caracteres con {resultado_ocr['motor']}.")
    except (ocr.ErrorOCR, OSError) as error:
        datos_ocr["error"] = str(error)[:500]
        print(f"[ingesta] El OCR falló: {error}")
        ruta_texto = _guardar_texto(carpeta, "texto.txt", texto_nativo)
        decision = _armar_decision(
            documento, "error_ocr", verificacion, verificado_con,
            error_verificacion, datos_ocr, ruta_texto, carpeta,
        )
        return _guardar_resultado(carpeta, decision)

    texto_final = texto_ocr or texto_nativo
    if not texto_final:
        print("[ingesta] El OCR no encontró texto visible.")
        estado = "ocr_sin_texto"
    else:
        estado = "procesado"
    if texto_ocr:
        documento.texto = texto_final
        documento.metodo_texto = (
            f"{documento.metodo_texto}+ocr" if texto_nativo else "ocr"
        )

    # Si no se pudo verificar antes (por ejemplo, no había modelo de visión),
    # ahora que hay texto se hace la verificación que faltaba.
    if verificacion is None and texto_ocr:
        print("[ingesta] Verificando el texto obtenido por OCR...")
        try:
            verificacion = verificador.verificar_texto(texto_final, documento.contexto)
            verificado_con = "texto_ocr"
            error_verificacion = ""
        except (ErrorIA, ValueError) as error:
            error_verificacion = str(error)
            print(f"[ingesta] No se pudo verificar el texto del OCR: {error}")
        if verificacion and not verificacion.get("sirve"):
            print(f"[ingesta] La IA rechazó el texto del OCR: {verificacion.get('motivo', '')}")
            estado = "rechazado_tras_ocr"

    ruta_texto = _guardar_texto(carpeta, "texto.txt", texto_final)
    decision = _armar_decision(
        documento, estado, verificacion, verificado_con,
        error_verificacion, datos_ocr, ruta_texto, carpeta,
    )
    decision["ocr"]["ruta_texto_nativo"] = _ruta_visible(ruta_nativo)
    decision["ocr"]["ruta_texto_ocr"] = _ruta_visible(ruta_ocr)
    return _guardar_resultado(carpeta, decision)


# ---------------------------------------------------------------------------
# Entrada múltiple y resumen
# ---------------------------------------------------------------------------

def _recolectar_documentos(
    fuentes: list[str] | None,
    textos: list[str] | None,
) -> tuple[list[archivos.Documento], list[dict]]:
    documentos: list[archivos.Documento] = []
    errores: list[dict] = []
    fuentes = list(fuentes or [])
    if not fuentes and not textos:
        fuentes = [str(config.DIRECTORIO_ENTRADA)]

    for fuente in fuentes:
        texto = str(fuente).strip()
        if not texto:
            continue
        try:
            if texto.lower().startswith(("http://", "https://")):
                documentos.append(archivos.descargar_url(texto))
            else:
                rutas = archivos.listar_fuentes(texto)
                for ruta in rutas:
                    documentos.append(archivos.preparar_fuente(ruta))
        except (archivos.ErrorIngesta, OSError) as error:
            print(f"[ingesta] No se pudo preparar '{texto}': {error}")
            errores.append({"fuente": texto, "error": str(error)})

    for indice, texto in enumerate(textos or [], start=1):
        try:
            documentos.append(archivos.preparar_texto(texto, f"texto_pegado_{indice}.txt"))
        except archivos.ErrorIngesta as error:
            print(f"[ingesta] No se pudo preparar el texto pegado: {error}")
            errores.append({"fuente": f"texto pegado {indice}", "error": str(error)})

    # Un mismo contenido puede venir repetido (misma URL, mismo archivo en dos rutas).
    unicos: dict[str, archivos.Documento] = {}
    for documento in documentos:
        unicos.setdefault(documento.sha256, documento)
    return list(unicos.values()), errores


def exportar_resumen() -> dict:
    """Reescribe el resumen legible de todo lo que pasó por la ingesta."""
    registros = []
    for ruta in sorted(config.INTERMEDIO_INGESTA.glob("*/decision.json")):
        try:
            datos = leer_json(ruta, None)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(datos, dict):
            registros.append(datos)
    if not registros:
        return {"cantidad": 0}

    filas = []
    for datos in registros:
        verificacion = datos.get("verificacion") or {}
        datos_ocr = datos.get("ocr") or {}
        documento = datos.get("documento") or {}
        filas.append(
            {
                "id": datos.get("id", ""),
                "archivo": datos.get("archivo", ""),
                "categoria": documento.get("categoria", ""),
                "estado": datos.get("estado", ""),
                "sirve": verificacion.get("sirve"),
                "relevancia": verificacion.get("relevancia"),
                "tipo_dato": verificacion.get("tipo_dato", ""),
                "motivo": verificacion.get("motivo", ""),
                "verificado_con": datos.get("verificado_con", ""),
                "ocr_requerido": datos_ocr.get("requerido"),
                "ocr_autorizado": datos_ocr.get("autorizado"),
                "ocr_motor": datos_ocr.get("motor", ""),
                "paginas": documento.get("paginas"),
                "caracteres_texto": documento.get("caracteres_texto"),
                "texto": datos.get("texto", ""),
                "fecha": datos.get("fecha", ""),
            }
        )

    tabla = pd.DataFrame(filas).sort_values(["fecha", "archivo"], ascending=[False, True])
    guardar_csv(tabla, config.INTERMEDIO_INGESTA / "resumen_ingesta.csv")
    guardar_json(filas, config.INTERMEDIO_INGESTA / "resumen_ingesta.json")

    aceptados = tabla[tabla["sirve"] == True]  # noqa: E712 - comparación explícita con bool
    rechazados = tabla[tabla["estado"].isin(["rechazado", "rechazado_tras_ocr"])]
    pendientes = tabla[tabla["estado"] == "pendiente_ocr"]
    errores = tabla[tabla["estado"].astype(str).str.startswith("error")]
    if not rechazados.empty:
        guardar_csv(rechazados, config.DESCARTADO / "ingesta_rechazada.csv")
    else:
        ruta_rechazados = config.DESCARTADO / "ingesta_rechazada.csv"
        if ruta_rechazados.exists():
            ruta_rechazados.unlink()

    lineas = [
        "# Ingesta de datos externos",
        "",
        f"Actualizado: {ahora_iso()}",
        "",
        f"- Datos procesados: **{len(tabla)}**",
        f"- Aceptados por la IA: **{len(aceptados)}**",
        f"- Rechazados: **{len(rechazados)}**",
        f"- Pendientes de autorización de OCR: **{len(pendientes)}**",
        f"- Con error: **{len(errores)}**",
        "",
        "## Detalle",
        "",
        "| archivo | estado | sirve | relevancia | tipo | OCR | texto |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, fila in tabla.iterrows():
        archivo = str(fila["archivo"]).replace("|", "/").replace("\n", " ")
        if pd.notna(fila["ocr_autorizado"]) and bool(fila["ocr_autorizado"]):
            ocr_estado = f"sí ({fila['ocr_motor']})"
        elif pd.notna(fila["ocr_requerido"]) and bool(fila["ocr_requerido"]):
            ocr_estado = "pendiente/no autorizado"
        else:
            ocr_estado = "no"
        lineas.append(
            f"| {archivo} | {fila['estado']} | {fila['sirve']} | "
            f"{fila['relevancia']} | {fila['tipo_dato']} | {ocr_estado} | {fila['texto']} |"
        )
    guardar_texto("\n".join(lineas) + "\n", config.INTERMEDIO_INGESTA / "resumen_ingesta.md")
    return {
        "cantidad": len(tabla),
        "aceptados": len(aceptados),
        "rechazados": len(rechazados),
        "pendientes": len(pendientes),
        "errores": len(errores),
    }


def ingerir_fuentes(
    fuentes: list[str] | None = None,
    textos: list[str] | None = None,
    autorizacion: str = "preguntar",
    forzar: bool = False,
    modelo_vision: str | None = None,
    motor_ocr: str | None = None,
    max_paginas: int | None = None,
) -> list[dict]:
    """Procesa todas las fuentes y devuelve los registros resultantes."""
    documentos, errores_entrada = _recolectar_documentos(fuentes, textos)
    if not documentos:
        if not errores_entrada:
            print(
                f"[ingesta] No hay archivos para procesar. Copiá algo en "
                f"{config.DIRECTORIO_ENTRADA.relative_to(config.RAIZ)}/ "
                "o pasá una ruta, una URL o --texto."
            )
        return []

    resultados = []
    for documento in documentos:
        resultados.append(
            procesar_documento(
                documento,
                autorizacion=autorizacion,
                forzar=forzar,
                modelo_vision=modelo_vision,
                motor_ocr=motor_ocr,
                max_paginas=max_paginas,
            )
        )

    resumen = exportar_resumen()
    print()
    print(
        f"[ingesta] Resumen: {resumen.get('cantidad', len(resultados))} dato(s), "
        f"{resumen.get('aceptados', 0)} aceptado(s), "
        f"{resumen.get('rechazados', 0)} rechazado(s), "
        f"{resumen.get('pendientes', 0)} pendiente(s) de OCR, "
        f"{resumen.get('errores', 0)} con error."
    )
    print(f"[ingesta] Detalle: {_ruta_visible(config.INTERMEDIO_INGESTA / 'resumen_ingesta.md')}")
    return resultados
