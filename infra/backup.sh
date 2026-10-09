#!/usr/bin/env bash
set -euo pipefail

# Backup do agente Toní (config + histórico + leads + usuários + prompt + túnel).
# Uso:
#   bash infra/backup.sh              -> salva em infra/backups/
#   bash infra/backup.sh /caminho/x   -> salva em outro diretório

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/infra/backups}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/toni-backup-$TS.tar.gz"

mkdir -p "$DEST"

# Itens a incluir (quem não existir é ignorado).
ITEMS=(
  admin/config        # agent-config.json, history.json, qualify.json, users.db, lead_state.json, funnel.jsonl
  admin/prompts       # toni-system.md
  admin/audios        # gravações de voz (se houver)
  infra/tunnel.json   # configuração do túnel Cloudflare
  infra/.env          # segredos do docker-compose
)

ARGS=()
for it in "${ITEMS[@]}"; do
  if [ -e "$ROOT/$it" ]; then
    ARGS+=("$it")
  fi
done

if [ "${#ARGS[@]}" -eq 0 ]; then
  echo "Nada para fazer backup." >&2
  exit 1
fi

tar -czf "$OUT" -C "$ROOT" "${ARGS[@]}"

echo "Backup criado: $OUT"
ls -lh "$OUT"

# Mantém apenas os 10 backups mais recentes.
ls -1t "$DEST"/toni-backup-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
