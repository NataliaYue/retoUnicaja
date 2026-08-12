#!/usr/bin/env bash
# Arranca Ollama con la configuración adecuada para este proyecto.
#
#   OLLAMA_CONTEXT_LENGTH=8192  el system prompt + tools ya ocupan ~2.900 tokens
#                               y MAX_TOKENS son 2.000; con los 4.096 por defecto
#                               el contexto desborda y se recalcula en cada turno.
#                               8192 cabe entero en los 8 GB de la RTX 4060.
#   OLLAMA_KEEP_ALIVE=30m       evita que el modelo (5,9 GB) se descargue a los
#                               5 minutos y haya que releerlo de disco.
#   OLLAMA_FLASH_ATTENTION=1    reduce la memoria del KV cache.

set -euo pipefail

pkill -x ollama 2>/dev/null || true
sleep 1

export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_KEEP_ALIVE=30m
export OLLAMA_FLASH_ATTENTION=1

echo "Arrancando Ollama (ctx=$OLLAMA_CONTEXT_LENGTH, keep_alive=$OLLAMA_KEEP_ALIVE)..."
exec ollama serve
