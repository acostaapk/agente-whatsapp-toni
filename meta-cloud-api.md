# WhatsApp Business Cloud API (Meta) — passo a passo com endereços

Rota oficial da Meta para substituir o Baileys/Evolution API quando o volume justificar.
O "cérebro" do agente (system message, roteiro, compliance) **não muda** — só o canal.

---

## 1. Pré-requisitos

- Uma conta no Facebook (pessoal) para criar o portfólio de negócios.
- Um **número de telefone dedicado** (o "chip") que **não** esteja em uso num WhatsApp
  pessoal — ou você migra esse número, ou usa o **número de teste** que a Meta fornece.

---

## 2. Passo a passo (com URLs)

### 2.1. Portfólio de negócios (Business Manager)
1. Acesse https://business.facebook.com/
2. Crie/entre no portfólio (menu à esquerda → *Business settings*):
   https://business.facebook.com/settings/

### 2.2. WhatsApp Business Account (WABA)
1. Em *Business settings* → **Contas** → **Contas do WhatsApp** → **Adicionar**:
   https://business.facebook.com/settings/whatsapp-business-accounts
2. Crie uma WABA e aceite os termos.

### 2.3. App no Meta for Developers
1. Acesse https://developers.facebook.com/apps
2. **Criar app** → caso de uso **Outro / Negócios** → tipo **Negócios** → associe ao portfólio.
3. No painel do app, **Adicionar produto** → **WhatsApp**:
   https://developers.facebook.com/apps/<APP_ID>/whatsapp
   (troque `<APP_ID>` pelo id do seu app)

### 2.4. Número de telefone
1. Em *WhatsApp → API Setup* (Configuração da API), adicione um número de teste ou um número
   real. O **número de teste** já vem com um destinatário de teste para validar sem custo:
   https://developers.facebook.com/apps/<APP_ID>/whatsapp/wa-settings
2. Anote o **Phone Number ID** (fica em *API Setup* → "Enviando mensagens").

### 2.5. Token permanente (System User)
1. *Business settings* → **Usuários** → **Usuários do sistema** → **Adicionar**:
   https://business.facebook.com/settings/system-users
2. Dê a função **Admin** (ou "Desenvolvedor") e associe o **app** e a **WABA**.
3. Clique no usuário → **Gerar token** → selecione o app → permissões:
   `whatsapp_business_messaging` e `whatsapp_business_management`.
4. Copie o token (é o `access_token` permanente).

### 2.6. Webhook → n8n
1. No app → *WhatsApp → Configuração* (Configuration):
   https://developers.facebook.com/apps/<APP_ID>/whatsapp/wa-settings
2. Em **Webhook** → **Editar**:
   - **URL de callback:** `https://SEU-HOST/webhook/evolution` (ou o caminho do seu Webhook
     node no n8n; em produção precisa HTTPS público — use um túnel como Cloudflare Tunnel/ngrok).
   - **Verificar token:** um valor que você escolhe (ex.: `toni-verify-2026`).
3. **Verificar e salvar** (a Meta faz um `GET` com `hub.challenge` que o n8n responde).
4. Em **Campos de webhook** → assinar **`messages`**.

### 2.7. Testar
Use o **Graph API Explorer** para mandar a 1ª mensagem de teste:
- `POST https://graph.facebook.com/v25.0/<PHONE_NUMBER_ID>/messages`
  ```json
  {
    "messaging_product": "whatsapp",
    "to": "<DESTINATARIO>",
    "type": "text",
    "text": { "body": "oi" }
  }
  ```
  com `Authorization: Bearer <ACCESS_TOKEN>`.

---

## 3. Endpoints principais do Graph API

| Operação | Endpoint |
|---|---|
| Enviar texto | `POST /v25.0/{phone-number-id}/messages` (`type: "text"`) |
| Enviar foto | `POST /v25.0/{phone-number-id}/messages` (`type: "image"`, `image: { link }`) |
| Enviar template | `POST /v25.0/{phone-number-id}/messages` (`type: "template"`) |
| Enviar mídia (upload) | `POST /v25.0/{phone-number-id}/media` |
| Perfil do negócio | `GET/POST /v25.0/{phone-number-id}/whatsapp_business_profile` |
| Status da mensagem | webhook `messages` (sent/delivered/read/failed) |

---

## 4. Precificação

- Conversas de **serviço** (resposta ao cliente dentro de 24h da última mensagem dele) →
  **grátis** na maior parte.
- Conversas de **marketing**/**utilidade** (fora da janela de 24h, iniciadas pela empresa) →
  pagas por conversa.
- Docs: https://developers.facebook.com/docs/whatsapp/pricing

---

## 5. O que muda no agente (n8n)

| Item | Baileys/Evolution | Meta Cloud API |
|---|---|---|
| Trigger | Webhook `messages.upsert` da Evolution | node *WhatsApp Business Cloud Trigger* (faz a verificação `hub.challenge`) |
| Envio | `POST /message/sendText` | `POST /messages` (Graph API) |
| Foto | `POST /message/sendMedia` (`media: url`) | `type: "image"`, `image.link` |
| Auth | `apikey` header | `Authorization: Bearer <token>` |

Todo o resto (Config Cliente, system message, roteiro, compliance, pós-venda, Rastro) é
**reaproveitado sem alteração** — e passa a ser editável pela **interface admin** (`admin/`),
que também faz a seleção Meta × Baileys.
