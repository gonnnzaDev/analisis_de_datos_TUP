#!/usr/bin/env bash
# Arranca el servidor local de Ollama (IA) y asegura el modelo configurado.
# No usa sudo: Ollama vive en ~/.local/ollama (instalación de usuario).

set -e

OLLAMA_BIN="${HOME}/.local/ollama/bin/ollama"
MODELO="${OLLAMA_MODELO:-qwen2.5:7b}"

if [ ! -x "$OLLAMA_BIN" ]; then
  echo "No se encontró Ollama en $OLLAMA_BIN"
  echo "Instalalo con:"
  echo "  curl -fL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o ollama.tar.zst"
  echo "  mkdir -p ~/.local/ollama && tar -I zstd -xf ollama.tar.zst -C ~/.local/ollama && rm ollama.tar.zst"
  exit 1
fi

if pgrep -f "ollama serve" > /dev/null 2>&1; then
  echo "El servidor de Ollama ya está corriendo."
else
  echo "Arrancando el servidor de Ollama en segundo plano..."
  setsid nohup "$OLLAMA_BIN" serve >> /tmp/ollama_serve.log 2>&1 < /dev/null &
  sleep 3
fi

echo "Verificando/descargando el modelo $MODELO (la primera vez tarda)..."
"$OLLAMA_BIN" pull "$MODELO"

echo
echo "Listo. La IA local está disponible."
echo "  - Servidor: http://localhost:11434"
echo "  - Modelo:   $MODELO"
echo "  - Registro: /tmp/ollama_serve.log"
echo
echo "Para OCR de imágenes/PDFs escaneados falta el modelo de visión:"
echo "  $OLLAMA_BIN pull ${OLLAMA_MODELO_VISION:-qwen2.5vl:3b}"
echo "  (o configurá INGESTA_MOTOR_OCR=tesseract si preferís Tesseract)"
