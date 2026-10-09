# Design: respostas rápidas e imagens no atendimento humano

**Status:** desenho aprovado em conversa; aguardando revisão desta especificação antes da implementação.  
**Tela:** `https://toni.analisereview.com.br/mensagens`  
**Aplicação:** `/home/andre/marketing/agente-whatsapp` (FastAPI + HTML/CSS/JS sem framework).

## 1. Objetivo

Melhorar a tela de conversas para um atendente humano preparar respostas e imagens de maquininhas com poucos cliques, revisar cada envio e manter a conversa completa no painel.

## 2. Decisões aprovadas

- Os painéis ficam integrados à tela `/mensagens`.
- A seleção de uma resposta prepara o texto no composer. A seleção de uma imagem abre sua prévia e um campo de legenda opcional. **Selecionar nunca envia.** O atendente precisa confirmar pelo botão de envio.
- A biblioteca de respostas é compartilhada entre atendentes, persistida no servidor e editável no painel por qualquer pessoa já autenticada no admin. Não existe hoje um sistema de papéis/permissões por usuário; não será introduzido um neste escopo.
- A biblioteca inicia com os cinco textos sugeridos nesta especificação; editar a biblioteca altera o conteúdo compartilhado, enquanto o rascunho do composer pode ser ajustado antes de cada envio.
- As imagens disponíveis são T1, T2, T3 e T3 Smart. O catálogo de imagens não é editável neste escopo.
- Desktop: lista de conversas, chat e painel de ferramentas em três colunas. Mobile: lista e ferramentas acessíveis por painéis/drawers recolhíveis.
- As abas existentes Templates, Perfil e Números continuam disponíveis e sem alterações de comportamento.
- A implantação no container de produção será solicitada separadamente após testes e aprovação do usuário.

## 3. Conteúdo inicial da biblioteca

| ID | Categoria | Rótulo | Texto inicial |
|---|---|---|---|
| `acolhimento` | Atendimento | Acolhimento | Entendi. Vou verificar isso e te retorno por aqui. |
| `diagnostico` | Atendimento | Diagnóstico | Qual modelo você está usando e o que aparece na tela? |
| `foto-print` | Atendimento | Foto ou print | Se puder, envie uma foto ou um print do que aparece. |
| `pedido` | Pedido | Etapa do pedido | Me diga em qual etapa do pedido você está e qual mensagem aparece. |
| `confirmacao` | Atendimento | Confirmar resolução | Conseguiu resolver? Se não, me conte em qual etapa precisa de ajuda. |

Os textos são apenas conteúdo inicial: atendentes autenticados podem adicionar, editar, categorizar e remover respostas. O painel exibe uma lista e uma área de edição acessível; salvar altera a biblioteca compartilhada, não envia mensagem.

## 4. Biblioteca de imagens e compatibilidade

O projeto já documenta fotos WebP em `README.md` e mantém os originais no site principal. A referência de mídia do WhatsApp lista JPEG/PNG para mensagens de imagem e WebP para stickers. Portanto:

1. Gerar derivados JPEG locais a partir das quatro imagens existentes, sem alterar os originais em `/home/andre/marketing/analisereview/public/`.
2. Incluir os derivados em `admin/static/machines/`, servidos pelo mount público `/static` existente.
3. Usar URLs estáveis no host do admin, por exemplo `https://toni.analisereview.com.br/static/machines/t1.jpg`.
4. O servidor mantém a lista permitida `{t1, t2, t3, t3_smart}` e resolve `asset_id` para URL confiável. O navegador nunca envia URL arbitrária para o provedor.

Os JPEGs são prévias e payloads de envio; não se adiciona upload, proxy de URL ou transformação dinâmica de imagens. A conversão deve preservar o produto visual, usar perfil RGB/sRGB e manter cada arquivo abaixo do limite de 5 MB para imagem.

## 5. Fluxo de interface

### Desktop

```text
┌ Conversas ───────────┬ Atendimento / histórico ──────────────┬ Ferramentas ───────────┐
│ busca e lista         │ cliente, mensagens, prévia e composer │ Respostas | Maquininhas │
│                       │                                        │ cards/lista pesquisável │
└───────────────────────┴────────────────────────────────────────┴────────────────────────┘
```

- Lista de conversas à esquerda, chat central flexível e painel lateral à direita.
- Abas internas do painel: **Respostas** e **Maquininhas**.
- Busca/categorias reduzem as respostas disponíveis; comandos de editar/adicionar/remover ficam no próprio painel.
- Selecionar uma resposta preenche o campo de texto, com opção de edição do rascunho.
- Selecionar uma máquina mostra a prévia e prepara a legenda. O botão **Enviar imagem** fica separado da seleção.
- Envio de texto e imagem mostra estados de carregamento/erro e mantém o rascunho se o servidor falhar.
- Um envio em andamento não apaga um novo rascunho digitado ou preparado depois que a requisição começou; o mesmo telefone não pode disparar envios duplicados em paralelo.
- Ao trocar de conversa, o histórico mostra um estado de carregamento e descarta respostas de requisições antigas, para não exibir o cliente anterior sob o novo cabeçalho. Se a chamada atual falhar, mostra erro com ação de tentar novamente em vez de deixar o estado de carregamento preso.

### Mobile

- O chat permanece como conteúdo principal.
- Lista de conversas e painel de ferramentas são abertos por controles explícitos, com rótulos acessíveis e estado aberto/fechado exposto.
- Fechar um drawer devolve o foco ao controle que o abriu; `Escape` também pode fechá-lo.
- Não ocultar permanentemente a lista de conversas em telas pequenas.

### Direção visual

Refinar apenas `/mensagens`, mantendo a identidade escura/verde Toni: melhor hierarquia, contraste, espaçamento, estados vazios/selecionados, botões e foco por teclado. Reutilizar HTML/CSS/JS atuais; não adicionar framework nem mexer nas demais abas.

## 6. Arquitetura e dados

### Respostas compartilhadas

- Armazenar `human-support-replies.json` em `admin/config/`, dentro do volume persistente já montado no container.
- Se o arquivo ainda não existir, carregar os cinco itens iniciais e persistir a biblioteca no primeiro salvamento.
- Estrutura versionada: `{ "version": N, "replies": [{"id", "category", "label", "text"}] }`.
- Usar lock de processo e gravação temporária + `os.replace` para evitar arquivos parcialmente escritos.
- `PUT` exige `base_version`; responder `409` se outra edição tiver sido salva desde que o atendente carregou a versão, preservando alterações concorrentes.
- Validar quantidade máxima, IDs únicos/estáveis com no máximo 40 caracteres, campos obrigatórios e limites de tamanho de categoria, rótulo e texto. Exibir erros de validação no editor.
- Nenhum texto da biblioteca é passado ao LLM; conteúdo do usuário deve ser renderizado como texto, nunca HTML.

### APIs

- `GET /api/human-support/library` — exige sessão existente; retorna versão, respostas e os quatro metadados públicos de imagem.
- `PUT /api/human-support/replies` — exige sessão; valida e salva a biblioteca compartilhada com controle de versão.
- `POST /api/whatsapp/send-media` — passa a receber `{ "to": "…", "asset_id": "…", "caption": "…" }`; caption é opcional e limitado a 1.024 caracteres; rejeita IDs ausentes/desconhecidos, URLs fornecidas pelo cliente e destinatários bloqueados; resolve a URL no servidor e envia via `_send_media` para respeitar Meta ou Evolution.
- `POST /api/conversations/{phone}/send` — permanece o caminho de envio de texto; a biblioteca não altera o fluxo do texto livre. Rejeita destinatários bloqueados antes de chamar `_send_reply`, evitando registrar como enviado um texto que o helper suprimiu.
- `GET /api/conversations/{phone}` — continua aceitando o histórico antigo `{role, content, ts}` e pode retornar imagem enviada com campos opcionais `kind: "image"` e `asset_id`.

### Histórico e falhas

- Após a aceitação do provedor, append de histórico de imagem: `role`, `content` (legenda ou marcador curto), `ts`, `kind` e `asset_id`.
- Não persistir bytes da imagem nem URLs arbitrárias no histórico. A UI resolve o `asset_id` usando o catálogo confiável.
- O ledger existente registra texto/legenda ou um marcador conciso; não adicionar campos de cliente nem mídia binária.
- Se o provedor falhar, retornar erro genérico adequado, não registrar envio bem-sucedido e manter a prévia/rascunho no painel. Não fazer retry automático em timeout ambíguo.
- Qualquer endpoint novo ou alterado mantém `_current_user`; ambos os envios rejeitam destinatários bloqueados e não registram sucesso quando não houve chamada/aceitação pelo transporte. Seleção de catálogo, edição e envio não lê runtime config nem expõe `/api/config`.

## 7. Arquivos previstos

- `admin/static/chat.html` — layout, catálogo/editor da biblioteca, prévia, interação de envio, drawers e renderização de imagens no histórico.
- `admin/static/human-support-ui.js` — helpers puros de preparar rascunhos/payloads, proteger rascunhos durante envios em andamento e invalidar requisições de conversas antigas.
- `admin/main.py` — catálogo inicial e imagens permitidas, leitura/salvamento versionado do JSON, envio de mídia com ID, guarda de bloqueio e append de histórico.
- `admin/static/machines/t1.jpg`, `t2.jpg`, `t3.jpg`, `t3-smart.jpg` — derivados JPEG das imagens públicas existentes.
- `admin/tests/test_human_support.py` — testes de API, validação/conflito de edição, autorização, fluxo de mídia e histórico sem chamadas reais.
- `admin/tests/test_human_support_ui.js` — testes Node nativo dos helpers de preparar resposta/imagem e criar payload sem enviar.
- `admin/config/human-support-replies.json` — arquivo operacional criado no primeiro salvamento; não será lido nem alterado manualmente durante desenvolvimento.
- `admin/.dockerignore` — impede que runtime config, `.env`, bancos, backups e testes entrem no contexto do build; o Dockerfile consome apenas código, prompts e static.
- `admin/Dockerfile` — nenhuma mudança esperada: já copia `static/` e a pasta `config` é volume persistente.

## 8. Critérios de aceitação

1. Desktop mostra três colunas utilizáveis; mobile permite abrir lista e ferramentas sem perder acesso ao chat.
2. Respostas carregam, podem ser adicionadas/editadas/removidas e alterações aparecem para outras sessões autenticadas; salvamento concorrente antigo recebe conflito legível.
3. Selecionar uma resposta ou imagem não chama endpoint de envio.
4. Resposta pode ser editada no composer antes de enviar.
5. Imagem oferece prévia e legenda; somente confirmação explícita envia.
6. Os endpoints de texto e mídia rejeitam contatos bloqueados sem chamar o provedor nem gravar histórico; a API de mídia só resolve os quatro IDs permitidos, usa o canal configurado e não registra sucesso após falha.
7. Imagens aceitas pelo histórico aparecem no thread depois do envio; registros antigos continuam aparecendo como texto.
8. Testes verificam autenticação, biblioteca/versionamento, entradas inválidas, conflito, seleção sem envio, payload de mídia por ID, dispatch Meta/Evolution com helpers mockados, erro sem histórico e sucesso com histórico.
9. Não há mensagens reais enviadas durante desenvolvimento.
10. Build/QA local passam; deploy só após aprovação separada.
11. Trocas rápidas entre conversas não permitem que uma resposta antiga de rede substitua o histórico da conversa atualmente selecionada.
12. Falha ao carregar histórico substitui o estado de carregamento por erro legível e ação de nova tentativa.
13. Texto ou imagem preparado enquanto um envio anterior está pendente continua disponível depois da resposta; o botão bloqueia duplicações para o mesmo telefone.

## 9. Limites

- Sem edição da biblioteca de imagens; sem upload de mídia; sem alteração ao site Astro; sem mudança no prompt, comportamento do bot ou templates WhatsApp.
- Sem novo framework, tabela SQLite, sistema de papéis ou armazenamento externo.
- Sem leitura/alteração de configuração runtime, segredos, backups ou dados de clientes.
- O build local exclui runtime config, `.env`, bancos, backups, testes e docs do contexto Docker.
- O `ui9000` disponível é um renderizador de gráficos/tabelas, não de telas web. A implementação visual será guiada pela skill `frontend-design`, não por um chart inventado.

## 10. Revisão da especificação

- O comportamento de seleção manual, biblioteca compartilhada e permissões para usuários autenticados foi confirmado pelo usuário.
- O layout aprovado mantém as quatro abas existentes e limita o redesign a Conversas.
- Compatibilidade de mídia foi tratada convertendo WebP em JPEG antes do envio.
- A especificação separa salvar uma resposta de enviar uma mensagem e separa seleção de imagem da confirmação de envio.
- A autorização de deploy permanece separada; não haverá publicação só por aprovação desta spec.
