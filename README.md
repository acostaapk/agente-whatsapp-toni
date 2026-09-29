# Agente WhatsApp — Toní (Maquininha Ton)

Agente de IA de atendimento/consultoria no WhatsApp para o negócio **maquininha de cartão Ton**,
operado pelo Parceiro Renda Ton **AnaliseReview**. Ele qualifica o interessado, recomenda o
modelo certo como um consultor e conduz ao pedido pelo link de indicação — tudo dentro das
regras do **regulamento Renda Ton** (vigente desde 01/09/2026).

> **Regra de ouro de compliance:** o agente **não** vende recompensa, **não** promete ganho e
> **não** inventa valor. Dúvida fora da base de conhecimento → escala para humano.

---

## Arquivos

| Arquivo | Conteúdo |
|---|---|
| `agente_maquininha_ton.md` | **Spec completa** — nome/identidade, regras de negócio, base de conhecimento, roteiro de consultoria, gatilhos de escalonamento, `Config Cliente`, system message, schema de saída, pós-venda e canal (Baileys). |
| `meta-cloud-api.md` | Passo a passo da **WhatsApp Cloud API (Meta)**, com endereços de cada configuração. |
| `admin/` | **Interface admin** (seleciona canal Meta × Baileys e edita o prompt/textos do agente). |
| `infra/` | `docker-compose` (Evolution API + Postgres + n8n + admin) + `.env` + `get-qr.sh`. |
| `graphify.sh` + `.env.graphify` | Grafo de conhecimento (economia de tokens) + config do LLM local. |

---

## Identidade

| Campo | Valor |
|---|---|
| **Nome** | **Toní** |
| Empresa | AnaliseReview (Parceiro Renda Ton / Renda Extra) |
| Serviço | maquininha de cartão Ton |
| Papel | consultor (recomenda o modelo certo) |

---

## Fluxo do agente

```
Anúncio (Google Ads, UTM) → WhatsApp (Baileys/Evolution API) → n8n
   → AI Agent "Toní" (LLM + system message) → responde/qualifica
   → foto + link direto do modelo → pedido (checkout da Ton)
   → pós-venda: agradecimento + link do app da Ton
   → webhook → Rastro (dashboard de receita por campanha)
```

---

## Stack técnica

- **Canal WhatsApp:** Baileys via **Evolution API** (self-hosted) — alternativa: Chatwoot
  (workflow já existe em `../Rastro/n8n/workflow_agente_chatwoot.json`).
- **Orquestração:** **n8n** (Webhook → Config Cliente → AI Agent → Structured Output Parser → envio).
- **LLM:** nó `OpenAI Chat Model` apontando para o LLM local
  `http://127.0.0.1:8888/v1` — modelo `unsloth/gemma-4-12B-it-qat-GGUF` (chave em `.env.graphify`).
- **Dashboard:** **Rastro** (FastAPI + Postgres + Next.js) — recebe cada lead via webhook.

---

## Setup rápido

### 1. Canal (Evolution API / Baileys)
```bash
docker run -d --name evolution -p 8080:8080 atendai/evolution-api:v2.1.1
# depois: POST /instance/create → lê o QR → pareia o número no WhatsApp
```

### 2. n8n
```bash
# se ainda não tem n8n:
docker run -d --name n8n -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
```

### 3. Workflow no n8n
1. **Webhook** (recebe `messages.upsert` da Evolution API).
2. **Config Cliente** (node *Set*) — cole o JSON da **seção 7** do spec.
3. **AI Agent** — cole o system message da **seção 8** do spec, em modo *Expression*.
4. **Structured Output Parser** — schema da **seção 9**.
5. **HTTP Request** — envia a resposta (texto) e a foto do modelo (mídia) via Evolution API.

Detalhes de envio (seção 10 do spec):
- Texto: `POST /message/sendText/{instance}` → `{number, text}`
- Foto: `POST /message/sendMedia/{instance}` → `{number, mediatype:"image", media:"<url>", caption:"<texto>"}`

### 4. Rastro (registro do lead)
Conectar o `rastro_webhook_url` do tenant (payload conforme `../Rastro/n8n/README.md`).

---

## Assets (fotos + links diretos)

Fotos dos modelos em alta resolução (720×1080), servidas de `analisereview.com.br`:

| Modelo | Foto | Link direto de compra (checkout) |
|---|---|---|
| T1 | `https://analisereview.com.br/t1-showcase-new.webp` | `ton.com.br/checkout/cart?...TONMEGA_TIER_D150...` |
| T2 | `https://analisereview.com.br/t2-showcase-new.webp` | `...TONMEGA_TIER_D195...` |
| T3 | `https://analisereview.com.br/t3-showcase-new.webp` | `...TONMEGA_TIER_S920...` |
| T3 Smart | `https://analisereview.com.br/t3-smart-showcase-new.webp` | `...TONMEGA_TIER_SMART_POS...` |

- Link do app (pós-venda): `https://appton.onelink.me/EhqG/y54ixyhr`
- Links completos (com `referrer` do parceiro) na **seção 4** do spec.

---

## Regras de negócio (resumo do compliance)

1. Nunca inventar valores, taxas, equipamentos, condições ou prazos.
2. Nunca prometer recompensa/ganho garantido (Renda Ton depende de nível/elegibilidade/KYC/volume).
3. Nunca usar superlativo absoluto ("a mais barata", "menor taxa", "sem taxa").
4. Valores/taxas sempre com fonte e data + ressalva "podem mudar".
5. Entrega, cadastro, pedido e suporte são da Ton — apontar canais oficiais.
6. Desconto na adesão é **possibilidade**, nunca garantia de valor.

Fonte: `../relatórios g-ads/regulamento-renda-ton.pdf` e `../relatórios g-ads/plano_implementacao_renda_ton_google_ads.pdf`.

---

## Status / pendências

- [x] Spec completa do agente (nome, prompt, base de conhecimento, roteiro, compliance).
- [x] Fotos HD dos modelos + links diretos de checkout.
- [x] Pós-venda (agradecimento + link do app).
- [ ] Subir Evolution API (Baileys) e parear o número.
- [ ] Subir n8n e importar/criar o workflow.
- [ ] Testar 10–20 conversas simuladas antes de ativar com leads reais.
- [ ] Conectar o webhook do Rastro.

---

## LLM + Graphify (economia de tokens)

**LLM do agente:** OpenAI-compatível local em `http://127.0.0.1:8888/v1`, modelo
`unsloth/gemma-4-12B-it-qat-GGUF`, chave `sk-unsloth-…` — config em `.env.graphify` e
editável na **interface admin** (`admin/config/agent-config.json` → campo `llm`). Use o mesmo
endpoint no nó `OpenAI Chat Model` do n8n.

**Graphify** (grafo de conhecimento do repositório, reduz leitura/contexto):

| Comando | O quê |
|---|---|
| `./graphify.sh query "pergunta"` | pergunta ao grafo (BFS) |
| `./graphify.sh explain "nó"` | explica um nó e vizinhos |
| `./graphify.sh extract .` | reconstrói o grafo (AST + semântica via LLM) |
| `./graphify.sh cluster-only .` | re-agrupa e regenera `GRAPH_REPORT.md` |

Artefatos em `graphify-out/` (graph.json, GRAPH_REPORT.md, graph.html). O LLM do graphify é o
mesmo local (`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL` em `.env.graphify`).

> O modelo `gemma-4-12B-it-qat` é um *instruct* (responde direto, sem `reasoning_content`) —
> funciona bem tanto para o agente quanto para nomear comunidades do graphify.

---

## Interface admin

Interface web para **selecionar o canal** (Baileys × Meta) e **editar o prompt/textos** do agente
sem mexer no código. Roda em `http://localhost:8090` (container `agente-admin`).

- **Edita:** canal, nome/empresa/serviço, tempo de reengajamento, critério de alto valor,
  link do app, **system message**, **base de conhecimento**, **modelos (foto+link)** e as
  credenciais do canal (Evolution ou Meta).
- **Onde os dados ficam:** `admin/config/agent-config.json` (fonte de verdade, persistida no host).
- **Como o agente usa:** o workflow n8n lê `GET http://localhost:8090/api/config` no início
  (HTTP Request node) e usa os campos no AI Agent — nada fica hardcoded no workflow.
- **API:** `GET /api/config` (ler) · `PUT /api/config` (salvar).

Para subir (junto com o resto da infra):
```bash
cd infra && docker compose up -d
```

---

## Templates originais (no repo Rastro)

O `agente_maquininha_ton.md` substitui o exemplo genérico ("Clínica Sorriso Pleno") dos
templates originais:
- `../Rastro/system_message_n8n.md`
- `../Rastro/system_message_chatwoot.md`
- `../Rastro/n8n/workflow_agente_chatwoot.json` (workflow importável, Chatwoot-based)
