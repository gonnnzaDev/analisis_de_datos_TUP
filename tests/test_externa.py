"""Pruebas de la tercera fuente: datos externos estructurados con la IA local."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import config
from src.ia import estructurador
from src.limpieza import limpiar


class ExternaTest(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix="test_externa_")
        self.raiz = Path(self.temporal.name)
        self.intermedio = self.raiz / "intermedio"
        self.ingesta = self.intermedio / "ingesta"
        self.procesado = self.raiz / "procesado"
        self.tablas = self.raiz / "tablas"
        self.ingesta.mkdir(parents=True)
        (self.procesado / "consolidado").mkdir(parents=True)
        self.tablas.mkdir()
        self.parches = [
            patch.object(config, "INTERMEDIO", self.intermedio),
            patch.object(config, "INTERMEDIO_INGESTA", self.ingesta),
            patch.object(config, "PROCESADO", self.procesado),
            patch.object(config, "TABLAS", self.tablas),
        ]
        for parche in self.parches:
            parche.start()

    def tearDown(self):
        for parche in self.parches:
            parche.stop()
        self.temporal.cleanup()

    def _crear_aceptado(self, id_documento="doc1", archivo="informe.pdf"):
        carpeta = self.ingesta / id_documento
        carpeta.mkdir(parents=True, exist_ok=True)
        texto = carpeta / "texto.txt"
        texto.write_text(
            "Half-Life cuesta 9,99 USD y tiene 10.000 reseñas. "
            "El precio promedio del mercado fue 12 USD en 2024.",
            encoding="utf-8",
        )
        decision = {
            "id": id_documento,
            "archivo": archivo,
            "estado": "procesado",
            "texto": str(texto),
            "verificacion": {"sirve": True, "motivo": "datos de mercado"},
        }
        (carpeta / "decision.json").write_text(
            json.dumps(decision, ensure_ascii=False), encoding="utf-8"
        )
        return texto

    def test_estructura_solo_datos_aceptados(self):
        self._crear_aceptado()
        rechazado = self.ingesta / "doc2"
        rechazado.mkdir()
        (rechazado / "texto.txt").write_text("Un poema sin datos.", encoding="utf-8")
        (rechazado / "decision.json").write_text(
            json.dumps(
                {
                    "id": "doc2",
                    "archivo": "poema.txt",
                    "estado": "rechazado",
                    "texto": str(rechazado / "texto.txt"),
                    "verificacion": {"sirve": False},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        respuesta = {
            "items": [
                {
                    "nombre": "Half-Life",
                    "appid": 70,
                    "tipo": "juego",
                    "precio": 9.99,
                    "resenas_totales": 10000,
                },
                {
                    "nombre": "Half-Life Soundtrack",
                    "appid": None,
                    "tipo": "banda_sonora",
                },
            ],
            "datos": [
                {
                    "tipo": "mercado",
                    "juego": "",
                    "metrica": "precio_promedio",
                    "valor": 12,
                    "unidad": "USD",
                    "fecha": "2024",
                    "detalle": "Precio promedio del mercado en 2024.",
                }
            ],
        }

        with patch.object(estructurador, "pedir_json", return_value=respuesta) as pedir:
            resultado = estructurador.estructurar_externa()

        self.assertEqual(pedir.call_count, 1)
        self.assertEqual(len(resultado["items"]), 1)
        self.assertEqual(resultado["items"][0]["nombre"], "Half-Life")
        self.assertEqual(resultado["items"][0]["documento"], "informe.pdf")
        self.assertEqual(len(resultado["datos"]), 1)
        self.assertEqual(resultado["datos"][0]["metrica"], "precio_promedio")
        self.assertEqual(len(resultado["descartados"]), 1)
        self.assertEqual(resultado["documentos"], 1)

        guardado = json.loads((self.intermedio / "ia_externa.json").read_text(encoding="utf-8"))
        self.assertEqual(len(guardado["items"]), 1)
        self.assertEqual(len(guardado["datos"]), 1)

        # La segunda corrida no vuelve a llamar a la IA: usa el parcial.
        with patch.object(estructurador, "pedir_json", return_value=respuesta) as pedir_otra:
            segunda = estructurador.estructurar_externa()
        self.assertEqual(pedir_otra.call_count, 0)
        self.assertEqual(len(segunda["items"]), 1)

    def test_consolidacion_enriquece_y_separa_datos(self):
        (self.intermedio / "ia_externa.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "nombre": "Half-Life",
                            "appid": 70,
                            "tipo": "juego",
                            "precio": 9.99,
                            "descuento_pct": 50,
                            "fecha_lanzamiento": "1998-11-19",
                            "jugadores_actuales": 1234,
                            "resenas_totales": 10000,
                            "positivas_pct": 96.5,
                            "generos": ["Acción"],
                            "notas": "dato externo",
                            "fuente": "externa:doc1",
                            "documento": "informe.pdf",
                        },
                        {
                            "nombre": "Juego Nuevo Externo",
                            "appid": None,
                            "tipo": "juego",
                            "precio": 4.99,
                            "fuente": "externa:doc1",
                            "documento": "informe.pdf",
                        },
                    ],
                    "datos": [
                        {
                            "tipo": "mercado",
                            "juego": "",
                            "appid": None,
                            "metrica": "precio_promedio",
                            "valor": 12,
                            "unidad": "USD",
                            "fecha": "2024",
                            "detalle": "Precio promedio.",
                            "fuente": "externa:doc1",
                            "documento": "informe.pdf",
                            "documento_id": "doc1",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        consolidado = pd.DataFrame(
            [
                {
                    "appid": 70,
                    "nombre": "Half-Life",
                    "precio_final": None,
                    "descuento_pct": None,
                    "resenas_totales": 5000,
                }
            ]
        )

        resultado = limpiar._enriquecer_con_externa(consolidado)

        self.assertEqual(resultado.loc[0, "ext_precio"], 9.99)
        self.assertEqual(resultado.loc[0, "ext_descuento_pct"], 50)
        self.assertEqual(resultado.loc[0, "ext_documento"], "informe.pdf")
        # Los datos que ya existían en Steam no se pisan.
        self.assertEqual(resultado.loc[0, "resenas_totales"], 5000)
        self.assertTrue((self.procesado / "consolidado" / "juegos_externos.csv").exists())
        self.assertTrue((self.procesado / "consolidado" / "datos_externos.csv").exists())


if __name__ == "__main__":
    unittest.main()
