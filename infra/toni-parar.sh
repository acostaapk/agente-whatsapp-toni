#!/bin/bash
# Para o agente Toni (container agente-admin).
cd "$(dirname "$0")"
docker stop agente-admin >/dev/null
notify-send "Toni — Análise Review" "Agente parado ⏹" -i analise-review
