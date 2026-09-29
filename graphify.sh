#!/bin/bash
# Wrapper do graphify com o LLM local configurado (economia de tokens).
# Uso: ./graphify.sh query "sua pergunta" | ./graphify.sh extract .
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a
source "$DIR/.env.graphify"
set +a
exec "$DIR/.venv-graphify/bin/graphify" "$@"
