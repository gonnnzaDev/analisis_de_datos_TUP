"""Pruebas de la ingesta externa (sin Ollama: la IA se simula)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from src import config
from src.ia import ollama_cliente
from src.ingesta import archivos, gestor, verificador


class CategoriaDeTest(unittest.TestCase):
    def test_reconoce_formatos(self):
        self.assertEqual(archivos.categoria_de("a.pdf"), "pdf")
        self.assertEqual(archivos.categoria_de("a.PNG"), "imagen")
        self.assertEqual(archivos.categoria_de("a.html"), "html")
        self.assertEqual(archivos.categoria_de("a.csv"), "texto")
        self.assertEqual(archivos.categoria_de("a.docx"), "documento")
        self.assertEqual(archivos.categoria_de("a.rar"), "desconocido")

    def test_mime_sin_extension(self):
        self.assertEqual(archivos.categoria_de("descarga", "application/pdf"), "pdf")
        self.assertEqual(archivos.categoria_de("descarga", "image/jpeg"), "imagen")


class ModeloInstaladoTest(unittest.TestCase):
    def test_acepta_variantes_de_etiqueta(self):
        with patch.object(
            ollama_cliente, "modelos_instalados", return_value=["qwen2.5:7b-instruct-q4_K_M"]
        ):
            self.assertTrue(ollama_cliente.modelo_instalado("qwen2.5:7b"))
        with patch.object(
            ollama_cliente, "modelos_instalados", return_value=["qwen2.5vl:7b"]
        ):
            self.assertFalse(ollama_cliente.modelo_instalado("qwen2.5vl:3b"))


class VerificadorTest(unittest.TestCase):
    def test_normaliza_y_acota(self):
        decision = verificador.normalizar_decision(
            {
                "sirve": "sí",
                "relevancia": 140,
                "confianza": -5,
                "tipo_dato": "precios",
                "datos_aprovechables": "precio A; precio B",
                "requiere_ocr": "no",
                "motivo": "tiene precios",
            },
            modelo="falso",
            verificado_con="texto",
        )
        self.assertTrue(decision["sirve"])
        self.assertEqual(decision["relevancia"], 100)
        self.assertEqual(decision["confianza"], 0)
        self.assertEqual(decision["datos_aprovechables"], ["precio A", "precio B"])
        self.assertFalse(decision["requiere_ocr"])

    def test_rechaza_respuesta_sin_sirve(self):
        with self.assertRaises(ValueError):
            verificador.normalizar_decision({"motivo": "x"}, "falso", "texto")


class IngestaTest(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix="test_ingesta_")
        self.raiz = Path(self.temporal.name)
        for nombre in ("crudo", "intermedio", "descartado", "entrada"):
            (self.raiz / nombre).mkdir()
        self.parches = [
            patch.object(config, "CRUDO_INGESTA", self.raiz / "crudo"),
            patch.object(config, "INTERMEDIO_INGESTA", self.raiz / "intermedio"),
            patch.object(config, "DESCARTADO", self.raiz / "descartado"),
            patch.object(config, "DIRECTORIO_ENTRADA", self.raiz / "entrada"),
        ]
        for parche in self.parches:
            parche.start()

    def tearDown(self):
        for parche in self.parches:
            parche.stop()
        self.temporal.cleanup()

    @staticmethod
    def _decision(**cambios):
        base = {
            "sirve": True,
            "relevancia": 85,
            "confianza": 90,
            "tipo_dato": "precios",
            "datos_aprovechables": ["precio"],
            "requiere_ocr": False,
            "motivo": "aporta precios de juegos",
            "modelo": "falso",
            "verificado_con": "texto",
        }
        base.update(cambios)
        return base

    def test_texto_aceptado(self):
        archivo = self.raiz / "entrada" / "datos.txt"
        archivo.write_text("Half-Life cuesta 9.99 USD.", encoding="utf-8")
        with patch.object(
            gestor.verificador, "verificar_texto", return_value=self._decision()
        ):
            resultados = gestor.ingerir_fuentes([str(archivo)], autorizacion="no")
        self.assertEqual(resultados[0]["estado"], "procesado")
        decision = resultados[0]
        self.assertTrue(decision["verificacion"]["sirve"])
        texto = self.raiz / "intermedio" / decision["id"] / "texto.txt"
        self.assertIn("Half-Life", texto.read_text(encoding="utf-8"))
        self.assertTrue((self.raiz / "intermedio" / "resumen_ingesta.csv").exists())

    def test_texto_rechazado_no_hace_ocr(self):
        archivo = self.raiz / "entrada" / "poema.txt"
        archivo.write_text("Era un jardín soleado.", encoding="utf-8")
        with patch.object(
            gestor.verificador,
            "verificar_texto",
            return_value=self._decision(sirve=False, motivo="no tiene datos de mercado"),
        ), patch.object(gestor.ocr, "transcribir") as transcribir:
            resultados = gestor.ingerir_fuentes([str(archivo)], autorizacion="si")
        self.assertEqual(resultados[0]["estado"], "rechazado")
        transcribir.assert_not_called()
        self.assertTrue((self.raiz / "descartado" / "ingesta_rechazada.csv").exists())

    def test_imagen_pide_autorizacion_y_hace_ocr(self):
        imagen = Image.new("RGB", (600, 200), "white")
        ImageDraw.Draw(imagen).text((20, 20), "Half-Life 9.99 USD", fill="black")
        ruta = self.raiz / "entrada" / "captura.png"
        imagen.save(ruta)

        with patch.object(
            gestor.verificador,
            "verificar_imagen",
            return_value=self._decision(requiere_ocr=True),
        ), patch.object(
            gestor.ocr,
            "transcribir",
            return_value={
                "texto": "Half-Life 9.99 USD",
                "motor": "falso",
                "modelo": "falso",
                "paginas": 1,
                "caracteres": 19,
                "textos_por_pagina": ["Half-Life 9.99 USD"],
            },
        ):
            resultados = gestor.ingerir_fuentes([str(ruta)], autorizacion="si")

        self.assertEqual(resultados[0]["estado"], "procesado")
        self.assertTrue(resultados[0]["ocr"]["autorizado"])
        self.assertEqual(resultados[0]["ocr"]["motor"], "falso")
        texto = self.raiz / "intermedio" / resultados[0]["id"] / "texto.txt"
        self.assertIn("Half-Life", texto.read_text(encoding="utf-8"))

    def test_imagen_sin_autorizacion_queda_pendiente(self):
        ruta = self.raiz / "entrada" / "captura.png"
        Image.new("RGB", (100, 100), "gray").save(ruta)
        with patch.object(
            gestor.verificador,
            "verificar_imagen",
            return_value=self._decision(requiere_ocr=True),
        ), patch.object(gestor.ocr, "transcribir") as transcribir:
            resultados = gestor.ingerir_fuentes([str(ruta)], autorizacion="no")
        self.assertEqual(resultados[0]["estado"], "pendiente_ocr")
        self.assertFalse(resultados[0]["ocr"]["autorizado"])
        transcribir.assert_not_called()

    def test_archivo_binario_da_error_legible(self):
        archivo = self.raiz / "entrada" / "raro.bin"
        archivo.write_bytes(b"\x00\x01\x02\x03" * 100)
        resultados = gestor.ingerir_fuentes([str(archivo)], autorizacion="no")
        self.assertEqual(resultados[0]["estado"], "error_lectura")
        self.assertIn("binario", resultados[0]["documento"]["error_lectura"].lower())


if __name__ == "__main__":
    unittest.main()
