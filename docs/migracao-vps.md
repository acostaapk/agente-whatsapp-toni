# Migração do agente para um VPS caseiro

Guia para mover o agente Toní do PC atual para uma máquina dedicada (Ubuntu Server/Debian headless).

## Pré-requisitos de hardware (já atende)

- CPU 2+ cores (i3 4ª geração serve) · 4+ GB RAM · 20+ GB de disco · internet estável.
- O **LLM roda na nuvem** (DeepSeek/OpenAI) — **não precisa de GPU**.

## 1. Sistema + Docker

```bash
sudo apt update && sudo apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
```

Reinicie a sessão (ou `newgrp docker`) para o grupo valer.

## 2. Copiar o projeto

No PC atual:

```bash
rsync -av --exclude 'node_modules' --exclude 'dist' \
  ~/marketing/agente-whatsapp/ user@vps:/opt/agente-whatsapp/
```

(Ou clona do git, se o repositório estiver versionado.)

## 3. Copiar o estado do agente (o que NÃO está no git)

São os arquivos com segredos/estado:

- `admin/config/agent-config.json` — config (canal, LLM, Meta, modelos)
- `admin/config/history.json` — histórico de conversas
- `admin/config/qualify.json` — estado da qualificação
- `admin/config/users.db` — usuários do login
- `admin/config/lead_state.json` + `admin/config/funnel.jsonl` — leads/funil
- `admin/audios/` — gravações de voz (se houver)
- `infra/tunnel.json` + `infra/.env` — túnel + segredos do compose

**Atalho**: use o `infra/backup.sh` no PC atual e descompacte no VPS.

## 4. Subir os containers

```bash
cd /opt/agente-whatsapp/infra
docker compose up -d --build admin
```

## 5. Túnel Cloudflare (não precisa de IP fixo nem port forwarding)

Instale o `cloudflared`:

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared
```

Copie o `infra/tunnel.json` e crie o serviço systemd:

```ini
# ~/.config/systemd/user/cloudflared-wa.service
[Unit]
Description=Cloudflare Tunnel (wa.analisereview.com.br)
After=network-online.target

[Service]
ExecStart=/usr/local/bin/cloudflared tunnel run --token <TOKEN> <TUNNEL_ID>
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-wa.service
```

> O token e o ID estão no `infra/tunnel.json`. Ou use o `infra/run-tunnel.sh` como `ExecStart`.

## 6. Testar

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://wa.analisereview.com.br/
# 302 = ok (redireciona pro login)
```

Depois mande "oi" no WhatsApp pro número da Toni e confirme a resposta.

## 7. Backup automático (cron)

```cron
# todos os dias às 03h
0 3 * * * bash /opt/agente-whatsapp/infra/backup.sh /opt/agente-whatsapp/infra/backups
```

## Checklist final

- [ ] Docker sobe sozinho (`restart: unless-stopped`)
- [ ] Túnel `enabled` no systemd
- [ ] `curl` no `wa.analisereview.com.br` responde
- [ ] Mensagem de teste no WhatsApp responde com os botões
- [ ] Backup diário rodando
