# Workflow n8n — Meta Cloud API (agente Toní)

Workflow importável (`workflow_meta_cloud_api.json`) para o canal **oficial da Meta**. Ele é
**config-driven**: lê `GET http://localhost:8090/api/config` (interface admin) — prompt, base de
conhecimento, modelos, credenciais e LLM não ficam hardcoded.

## Fluxo

```
Meta Webhook (POST /webhook/whatsapp)
  → Extrair Mensagem (nº do cliente + texto)
  → Carregar Config (GET admin /api/config)
  → Montar Prompt (system message + base de conhecimento + mensagem)
  → Chamar LLM (OpenAI-compatível local, gemma-4-12B)
  → Extrair Resposta (content do LLM)
  → Enviar WhatsApp (Graph API /messages)
```

## Importar

1. n8n (`http://localhost:5678`) → **Workflows** → **…** → **Import from File** → este `.json`.
2. O workflow já referencia os endpoints locais (`localhost:8090` admin, `127.0.0.1:8888` LLM).

## Verificação do webhook (GET hub.challenge) — obrigatória

A Meta, ao clicar em "Verificar e salvar", faz um **GET** com `hub.challenge` esperando a resposta
ecoar esse valor. O nó genérico `Meta Webhook` (POST) não responde a GET. **Duas opções:**

1. **Recomendado:** troque o nó `Meta Webhook` pelo nó nativo **"WhatsApp Business Cloud Trigger"**
   (built-in no n8n 2.x). Ele tem o campo *Verify Token* e responde ao GET automaticamente.
   Crie a credencial **WhatsApp App** (App ID + App Secret do painel do desenvolvedor).
2. **Manual (rápido):** configure o nó `Meta Webhook` com *HTTP Method = GET* temporariamente,
   clique "Verificar e salvar" na Meta, e depois volte para POST. (Só serve para a verificação
   única; as mensagens chegam por POST.)

> Numa implementação final, use a opção 1.

## Túnel (HTTPS público)

O webhook da Meta exige URL pública com HTTPS. Para teste local:
```bash
# Cloudflare Tunnel (recomendado) ou ngrok apontando pro n8n (porta 5678)
ngrok http 5678
```
Callback URL na Meta: `https://<túnel>/webhook/whatsapp` · Verify token: `toni-verify-2026`.

## Número de teste (recipiente)

O `+1 555-192-4600` é número de **teste** da Meta: só envia para os **destinatários cadastrados**.
Em `wa-settings` → **Para (To)** → adicione seu WhatsApp pessoal → valide com o OTP.

## Teste ponta a ponta

1. Envie "oi" do seu WhatsApp para o número de teste (ou use o Graph API Explorer para mandar
   pro seu número como destinatário).
2. O agente responde usando o system message + base de conhecimento da admin.
