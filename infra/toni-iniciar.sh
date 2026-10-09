#!/bin/bash
# Inicia o agente Toni (container agente-admin).
set -e
cd "$(dirname "$0")"
docker start agente-admin >/dev/null
sleep 1
if curl -sf -o /dev/null http://127.0.0.1:8090/; then
  notify-send "Toni — Análise Review" "Agente iniciado (porta 8090) ✅" -i analise-review
else
  notify-send "Toni — Análise Review" "Container iniciado, mas sem resposta na 8090" -i dialog-warning
fi
