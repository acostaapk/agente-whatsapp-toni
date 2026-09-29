#!/bin/bash
# Sobe o túnel nomeado do agente (wa.analisereview.com.br -> localhost:5678).
# Uso: ./run-tunnel.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
TID="$(python3 -c "import json;print(json.load(open('$DIR/tunnel.json'))['tunnel_id'])")"
TOK="$(python3 -c "import json;print(json.load(open('$DIR/tunnel.json'))['token'])")"
exec cloudflared tunnel run --token "$TOK" "$TID"
