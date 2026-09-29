#!/bin/bash
# Gera/atualiza o QR code do WhatsApp (Baileys) da instância "toni".
# O QR expira em ~60s — rode este script e escaneie imediatamente.
# Uso: ./get-qr.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="$(grep EVOLUTION_API_KEY "$DIR/.env" | cut -d= -f2)"
OUT="$DIR/qr-toni.png"

curl -s "http://localhost:8080/instance/connect/toni" -H "apikey: $KEY" \
  | python3 -c "
import json,sys,base64
d=json.load(sys.stdin)
state=d.get('instance',{}).get('state')
b=d.get('base64')
if not b:
    print('sem QR no momento (state:', state, ')'); sys.exit(1)
if ',' in b: b=b.split(',',1)[1]
open('$OUT','wb').write(base64.b64decode(b))
print('QR atualizado ->', '$OUT')
print('Abra a imagem e escaneie com: WhatsApp > Aparelhos conectados > Conectar aparelho')
"
