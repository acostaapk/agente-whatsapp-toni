# Funil consultivo de vendas Ton pelo WhatsApp — Plano de Implementação

> **Para agentes de implementação:** execute o plano tarefa por tarefa, com revisão entre tarefas. Faça TDD em cada regra de negócio e não envie mensagens reais durante os testes.

**Objetivo:** Implementar no bot WhatsApp um funil consultivo determinístico para qualificar, recomendar, fechar e encaminhar o cliente ao pós-venda com alegações comerciais seguras.

**Arquitetura:** Preservar o FastAPI e o estado atuais. Extrair decisões comerciais puras para `sales_policy.py`, deixar roteamento/envio em `main.py`, manter prompt e base comercial sincronizados e usar um harness de webhook dry-run que captura saídas sem chamar a Meta.

**Stack:** Python 3.13, FastAPI, unittest, Docker Compose, WhatsApp Cloud API.

**Especificação:** `docs/superpowers/specs/2026-10-07-whatsapp-sales-funnel-design.md`

## Restrições globais

- Manter a qualificação atual: tipo de negócio, comprovante, volume de vendas e CPF/CNPJ.
- Não oferecer TapTon espontaneamente.
- Não enviar fotos de produto no fluxo de venda por enquanto.
- Preservar o link afiliado atual. Usar o snapshot promocional por modelo mostrado na página oficial aberta pelo proprietário em 07/10/2026: T1 70% OFF sem cupom adicional; T2/T3 72% OFF + cupom 20%; T3 Smart 71% OFF + cupom 20%. Informar que pode mudar e o total válido é o do carrinho; não chamar desconto de cashback.
- Se o cliente escolher CNPJ, perguntar por botão se já tem uma Ton ativa nesse CNPJ. Se sim, não enviar checkout para repetir cadastro no mesmo CNPJ; explicar a alternativa de compra pelo CPF, sem solicitar número de documento.
- Ao demonstrar intenção de compra, enviar o link do modelo escolhido imediatamente. Não perguntar Android/iPhone antes da compra.
- T1 pode ser recomendada como menor investimento somente se o cliente indicar essa prioridade e não precisar de Pix na maquininha; se Pix for necessário, priorizar T2.
- Um único lembrete de checkout após 30 minutos. Suprimir se o cliente responder, confirmar compra, pedir atendimento ou optar por não receber mensagens.
- Desativar mensagens automáticas de 24h e 72h até existirem modelos aprovados/configurados na Meta. Não enviar texto livre fora da janela de atendimento.
- Manter as respostas sem voz e o opt-out persistente já existentes.
- Não alegar que clique significa compra concluída; o checkout Ton não oferece confirmação de venda ao bot nesta integração.
- Bateria de casos usa transporte falso/dry-run e não envia mensagens para números reais.
- Preservar todos os arquivos modificados pelo usuário; não sobrescrever o `.env`, tokens, links de afiliado ou outros campos de credenciais ao atualizar a base.

---

## Mapa dos arquivos

- **Criar `admin/sales_policy.py`:** regras puras de recomendação, roteamento CNPJ/Ton ativa e estágio comercial; sem rede, disco ou mensagens.
- **Modificar `admin/main.py`:** chamar as regras puras, obter o estado dos botões, aplicar respostas determinísticas para objeções de alta frequência, remover fotos do fluxo de venda e manter a política de envio/follow-up.
- **Modificar `admin/prompts/toni-system.md`:** remover instruções antigas que contradizem a implementação e deixar o LLM apenas como fallback para perguntas abertas.
- **Modificar `admin/config/agent-config.example.json`:** sincronizar apenas os campos comerciais que não são segredos; nunca gravar tokens.
- **Modificar a base runtime `admin/config/agent-config.json`:** atualizar somente o valor `base_conhecimento` via leitura/alteração JSON que preserve todos os demais campos/credenciais. Não exibir o arquivo completo em logs ou na saída do agente.
- **Criar `admin/tests/test_sales_policy.py`:** testes unitários das escolhas comerciais.
- **Criar `admin/tests/test_whatsapp_funnel.py`:** bateria dry-run de payloads e respostas, patchando os transportes de envio e o LLM.
- **Criar `admin/tests/fixtures/funnel_cases.json`:** casos de conversação com entrada, estado inicial e asserções de resposta/estágio.
- **Criar `admin/tests/fixtures/test_agent_config.json`:** catálogo e base sanitizados para os testes, sem tokens, credenciais ou telefones de clientes.
- **Modificar `admin/Dockerfile`:** copiar os novos módulos Python para `/app`.

---

### Task 1 — Criar as regras puras de recomendação e elegibilidade

**Arquivos:** criar `admin/sales_policy.py`; criar `admin/tests/test_sales_policy.py`.

**Interface produzida:**

```python
def recommend_model(
    *, explicit_model: str | None, needs_print: bool | None,
    wants_touchscreen_or_4g: bool, needs_pix: bool,
    lowest_investment: bool, catalog_ids: set[str],
) -> str | None:
    if explicit_model in catalog_ids:
        if explicit_model == "t1" and needs_pix and "t2" in catalog_ids:
            return "t2"
        return explicit_model
    if wants_touchscreen_or_4g and needs_print and "t3_smart" in catalog_ids:
        return "t3_smart"
    if needs_print and "t3" in catalog_ids:
        return "t3"
    if lowest_investment and not needs_pix and "t1" in catalog_ids:
        return "t1"
    if (needs_pix or needs_print is False) and "t2" in catalog_ids:
        return "t2"
    return None

def same_cnpj_ton_restricted(*, selected_document: str, has_active_ton: bool) -> bool:
    return selected_document.lower() == "cnpj" and has_active_ton
```

- [ ] **Passo 1: escrever testes que falham** para: modelo explicitamente pedido e compatível; modelo explicitamente pedido mas incompatível com impressão/Pix/Android respeita a necessidade e escolhe alternativa compatível ou `None`; T1 pedido sem Pix → T1; T1 pedido com Pix → T2 se disponível, caso contrário `None`; T2 pedido com comprovante impresso → modelo imprimível disponível, caso contrário `None`; impresso sem tela → T3; impresso + tela/4G → T3 Smart; digital + Pix → T2; menor investimento sem Pix → T1; catálogo sem modelo compatível → `None`; Ton ativa no CNPJ só bloqueia quando documento selecionado é CNPJ. A explicação textual da incompatibilidade é testada na Tarefa 4.
- [ ] **Passo 2: executar a suíte**: `python3 -m unittest discover -s admin/tests -p 'test_sales_policy.py' -v`. Confirmar falhas por funções ausentes.
- [ ] **Passo 3: implementar só as duas funções puras**, sem heurística textual; `main.py` passa sinais extraídos para elas.
- [ ] **Passo 4: repetir a suíte** e confirmar os casos passam. Não incluir valores de preço/taxa nessas funções.

### Task 2 — Harness de webhook dry-run

**Arquivos:** criar `admin/tests/whatsapp_test_harness.py`; criar/estender `admin/tests/test_whatsapp_funnel.py`.

**Interface produzida:**

```python
class WhatsAppDryRun:
    def __init__(self):
        self.replies: list[tuple[str, str]] = []
        self.buttons: list[tuple[str, str, list[tuple[str, str]]]] = []
        self.events: list[tuple[str, str]] = []

    def send_text(self, to: str, text: str) -> None:
        self.replies.append((to, text))

    def send_buttons(self, to: str, text: str, buttons: list) -> None:
        self.buttons.append((to, text, list(buttons)))
```

O teste chama `asyncio.run(main.whatsapp_message(FakeRequest(payload)))`; `FakeRequest.json()` retorna o payload Cloud API sintético. `_load` usa a fixture `admin/tests/fixtures/test_agent_config.json`; `_send_reply` e `_send_buttons` gravam no `WhatsAppDryRun`; `_http_json` devolve a resposta fixa da fixture. `_thread_ctx` executa o alvo em linha, com `_pausa` e `time.sleep` substituídos por no-op. Redirecionar `HISTORY_PATH`, `LEDGER_PATH`, `FOLLOWUP_PATH`, `BLOCKED_PATH`, `LEAD_PATH`, `FUNNEL_PATH`, `QUALIFY_PATH` e `USERS_DB_PATH` para `TemporaryDirectory`; limpar os estados em memória antes de cada caso. Durante o import e cada caso, bloquear `urllib.request.urlopen`; no teste de áudio, substituir explicitamente `stt.transcribe_audio_url` por uma transcrição fixa.

- [ ] **Passo 1: testes RED** para (a) webhook sintético captura texto e botões; (b) a inicialização não toca `admin/config/users.db` nem lê outros estados reais; (c) mensagem de áudio com URL é transcrita por fake, com `urllib.request.urlopen` global bloqueado.
- [ ] **Passo 2: rodar** `python3 -m unittest discover -s admin/tests -p 'test_whatsapp_funnel.py' -v`; confirmar que o teste falha antes do harness.
- [ ] **Passo 3: adicionar o harness** com `unittest.mock.patch` aos limites `_send_reply`, `_send_buttons`, `_http_json`, `_thread_ctx` e às funções STT; antes de importar `admin.main`, redirecionar `sqlite3.connect` para um SQLite temporário, definir credenciais de teste por ambiente, suprimir stdout do bootstrap e ocultar os JSON de configuração reais. Bloquear `urllib.request.urlopen` durante import e execução. Usar payloads sintéticos e caminhos temporários para todos os arquivos de estado. `_http_json` e STT retornam respostas fixas.
- [ ] **Passo 4: repetir** e confirmar que as listas capturaram as mensagens e que não houve chamada de rede.

### Task 3 — Integrar qualificação e regra Ton ativa no CNPJ

**Arquivos:** modificar `admin/main.py`; usar `admin/sales_policy.py`; testes em `admin/tests/test_whatsapp_funnel.py`.

- [ ] **Passo 1: escrever testes RED** para (a) uma seleção nova de CNPJ pergunta “Já tem Ton ativa neste CNPJ?” mesmo se o estado anterior tinha `has_active_ton=True`; (b) “Não” continua à recomendação apropriada; (c) “Sim” oferece botões “Continuar pelo CPF”/“Não seguir agora”, sem link antes da confirmação e sem gravar documentos; (d) CPF não recebe pergunta adicional sobre Ton ativa; (e) pedidos em texto “Quero comprar a T3” e “Me manda o link” em estado CNPJ pendente/ativo não emitem checkout; (f) consultas de preço “Quanto custa a T3?”, “Quanto é a T3?” e consulta genérica sem preços respondem antes do gate, sem checkout e sem pergunta extra de qualificação; (g) resposta de dúvida com estado CNPJ ativo não anexa cartão/mídia de modelo com link.
- [ ] **Passo 2: executar** o teste específico e confirmar que falha porque o botão de Ton ativa não existe.
- [ ] **Passo 3: adicionar os botões e estados** `cnpj_tem_ton_sim` / `cnpj_tem_ton_nao` e `continuar_cpf` / `nao_seguir` em `_handle_button`; persistir apenas `has_active_ton` e `continue_as_cpf` como booleanos. Aplicar a trava a todo caminho que envia checkout — botões e texto quente de compra/modelo/link. Uma consulta de preço responde sem checkout e depois mostra a confirmação de elegibilidade; a resposta fallback de catálogo sem preços não deve introduzir outra pergunta de qualificação antes do gate. Reutilizar o checkout Mega+/CPF somente após o cliente apertar “Continuar pelo CPF”; nunca pedir número de documento.
- [ ] **Passo 4: usar `recommend_model`** no ponto de recomendação. Preservar a prioridade de lead quente: modelo/link/preço explicitamente pedidos são respondidos antes da qualificação restante.
- [ ] **Passo 5: rodar os testes específicos** e verificar saída dry-run, estado e link escolhido.

### Task 4 — Tornar fechamento e objeções comuns determinísticos

**Arquivos:** modificar `admin/main.py` e `admin/sales_policy.py`; atualizar testes.

- [ ] **Passo 1: testes RED** para: “já tenho maquininha” pergunta o motivo e não oferece T2 imediatamente; resposta ao motivo existente retoma a recomendação; “me manda o link” reenvia o mesmo modelo; “sim/quero comprar” envia link sem perguntar sistema operacional; “preço da T1 + preciso de Pix” explica a limitação e leva ao T2 sem checkout T1; se a conversa primeiro disser que precisa de Pix e depois que não precisa mais, a declaração posterior prevalece, incluindo “não vou precisar de Pix”; pergunta sobre taxa não inventa parcelamento; objeção de preço/desconto não afirma 5%/cashback e inclui o link já selecionado quando existe; perguntar desconto da T1 com necessidade de Pix também escolhe T2 ou, sem T2, não envia checkout; pergunta de garantia é respondida e termina sem repetir pergunta de link; TapTon não aparece sem solicitação; um checkout em fila é bloqueado se o estado muda para CNPJ com Ton ativa antes do envio.
- [ ] **Passo 2: rodar cada teste** e guardar a falha esperada.
- [ ] **Passo 3: adicionar roteamento determinístico** antes do LLM para estes intents e respeitar a precedência suporte/pós-venda > venda. Não adicionar novo pitch a respostas de suporte. Se o cliente pedir T1 e também precisar de Pix, explicar a limitação e oferecer T2.
- [ ] **Passo 4: revalidar elegibilidade no envio em segundo plano** imediatamente antes de qualquer mensagem/caption que contenha checkout. Se a qualificação mudou para CNPJ com Ton ativa sem confirmação CPF, suprimir checkout e emitir somente os botões CPF/Não seguir. O teste captura o worker, muda o estado antes de executá-lo e confirma que nenhuma URL de checkout sai.
- [ ] **Passo 5: validar desconto** pelo fluxo/URL atual; não criar URL ou código promocional alternativo.
- [ ] **Passo 6: executar os testes e inspecionar textos completos** para garantir uma só pergunta/CTA por mensagem e nenhuma necessidade atribuída sem informação do cliente.

### Task 5 — Alinhar prompt e base comercial

**Arquivos:** modificar `admin/prompts/toni-system.md`, `admin/main.py`, `admin/config/agent-config.example.json`, testes e somente os campos comerciais permitidos (`base_conhecimento`, `system_message`, `tempo_reengajamento`) de `admin/config/agent-config.json`.

- [ ] **Passo 1: testes RED** que carreguem prompt/base de exemplo e falhem se houver instruções ativas de: reengajar em 2h em qualquer seção; oferecer TapTon espontaneamente; enviar fotos de produto; oferecer T2 imediatamente a “já tenho maquininha”; perguntar OS antes de compra; 24h/72h sem template; cashback; promoção diferente do snapshot de modelo. Testar que o snapshot acompanha todo checkout (recomendação do LLM, recomendações determinísticas, `_deliver_offer`, botão Comprar, “sim/quero comprar”, desconto/objeção e reenvio); URL não configurada escrita pelo LLM é removida; Black usa o link Black quando elegível; snapshots sem cupom ou data válida usam fallback cauteloso.
- [ ] **Passo 2: rodar os testes** e registrar cada contradição detectada.
- [ ] **Passo 3: remover as instruções antigas** e manter uma regra única por tema. O LLM será fallback apenas para perguntas abertas e não pode adicionar um segundo CTA após resposta determinística. Remover chamadas de mídia de produto do fluxo de oferta/recomendação em todos os transports; atualizar os testes que antes exigiam o cartão. Criar/reutilizar helper de cópia promocional por modelo em todas as rotas de checkout; `_apply_model_link` deve remover URLs arbitrárias do texto LLM e inserir somente o link selecionado por `_choose_link` (Mega+ ou Black conforme elegibilidade). `_promo_snapshot` só é válido com desconto, preço, data válida e chave cupom presente, cujo valor seja `null` ou percentual autorizado.
- [ ] **Passo 4: enriquecer a base** com o catálogo/condições oficiais consultados em 07/10/2026 e com o snapshot promocional observado pelo proprietário no link afiliado: T1 70% OFF sem cupom extra; T2/T3 72% OFF + cupom 20%; T3 Smart 71% OFF + cupom 20%. Incluir preço final observado no carrinho (T1 R$16,80; T2 R$39,90; T3 R$86,40; T3 Smart R$153,50), parcelas, link e data por modelo no exemplo sanitizado/runtime. Qualificar que preço/cupom pode mudar e que o carrinho decide o valor final; não alegar cashback.
- [ ] **Passo 5: atualizar os exemplos sanitizados** sem segredos. Atualizar no runtime, por JSON parse/serialize, somente `base_conhecimento`, `system_message` legado e `tempo_reengajamento`; colocar o último em “30 minutos”. Comparar antes/depois para comprovar que todos os demais campos foram preservados sem imprimir seus valores.
- [ ] **Passo 6: executar os testes de consistência** e confirmar que buscas por instruções antigas não encontram regras ativas.

### Task 6 — Integridade de checkout, pós-compra e reengajamento

**Arquivos:** modificar `admin/main.py`, `admin/followup_policy.py`, `admin/config/agent-config.example.json`, `admin/tests/test_followup_policy.py`, `admin/tests/test_prompt_consistency.py` e `admin/tests/test_whatsapp_funnel.py`.

- [ ] **Passo 1: testes RED de integridade**: (a) LLM claims em dinheiro por extenso/parcelas removidos; (b) suporte/handoff filtra pitch positivo; (c) botão Android/iPhone só envia app se estado `app_os` após compra declarada; (d) URL externa removida em modelo/no-modelo/dúvida; (e) um link configurado; (f) Black CNPJ usa link Black em todos os caminhos e o exemplo o contém; (g) snapshot incompleto usa fallback; (h) história reconhece Black; (i) catálogo ativo usa datas 07/10/2026.
- [ ] **Passo 2: executar testes focados** e registrar as falhas antes de alterar os caminhos de saída.
- [ ] **Passo 3: sanitizar claims comerciais em todas as respostas LLM** antes do envio, incluindo valores por extenso; não remover fatos determinísticos verificados. Filtrar pitch em suporte/handoff; validar `step=app_os` antes de aceitar botão Android/iPhone; remover URLs não allowlisted em todos os modos; ajustar `_deliver_offer`/todas as rotas para `_choose_link`; adicionar `link_black` ao exemplo e `_already_offered`; alinhar datas ativas do catálogo a 07/10/2026 e manter fallback cauteloso para Black sem promo por plano.
- [ ] **Passo 4: escrever testes RED de follow-up**: lembrete não antes dos 30 min; disparo após 30 min usando timestamp da última mensagem real de checkout mesmo se o bot enviar botões/bolhas depois; dentro da janela Meta e sem nova resposta do cliente após a oferta, inclusive botão “Tirar dúvidas”; compra declarada, handoff e opt-out suprimem; um segundo toque nunca dispara.
- [ ] **Passo 5: executar** `python3 -m unittest discover -s admin/tests -p 'test_followup_policy.py' -v`; se necessário, ajustar `_needs_purchase_followup` para localizar a última oferta real, comparar com a mensagem mais recente do cliente e persistir o envio único com o timestamp dessa oferta; bolhas/botões do bot depois do link não contam como resposta do cliente.
- [ ] **Passo 6: testar pós-compra e opt-out**: compra declarada leva à pergunta Android/iPhone com motivo, link de app configurado/universal e disponibilidade; clique no checkout não confirma compra; opt-out suprime mensagens.
- [ ] **Passo 7: verificar que 24h/72h não enviam texto livre** nem depoimento, urgência/escassez ou nova oferta; esses toques permanecem desativados até templates aprovados.

### Task 7 — Bateria dry-run de 80 jornadas

**Arquivos:** criar `admin/tests/fixtures/funnel_cases.json`; estender `admin/tests/test_whatsapp_funnel.py`.

- [ ] **Passo 1: escrever fixtures RED** com 80 casos categorizados: saudação/anúncio, qualificação, lead quente, modelos/preços, objeções, Ton ativa no CNPJ, CPF alternativo, desconto, suporte, handoff, compra declarada, app, janela Meta, opt-out e duplicatas; adicionar testes suplementares para catálogo parcial com modelo explicitamente pedido incompatível, áudio/texto de suporte com pitch positivo incluindo oração contrastiva, e preservação de recusas como “Não compre”/“Não recomendo comprar”.
- [ ] **Passo 2: executar** `python3 -m unittest discover -s admin/tests -p 'test_whatsapp_funnel.py' -v`; verificar falhas por casos ainda sem handler.
- [ ] **Passo 3: rodar todos os casos no harness** sem chamadas reais à Meta, sem mensagem a números, sem chamadas ao LLM pago/local e sem escrita nos dados de produção. O `setUp` usa dicionários temporários para histórico/qualificação/leads e `tearDown` restaura os globais.
- [ ] **Passo 4: validar critérios automatizados**: por etapa, o conjunto de checkout URLs deve ser exatamente o esperado (um link correto ou nenhum); catálogo parcial sem modelo que satisfaça comprovante/Pix não pode cair em T3 ou primeiro item; links de app nunca podem ser checkout. TapTon é bloqueado quando não solicitado, mas pergunta explícita não pode ser confundida com oferta espontânea. Sem promo/taxa antiga ou não validada, sem duas perguntas, sem pitch em suporte/handoff, opt-out bloqueia, 24h/72h não enviam.
- [ ] **Passo 5: ler o relatório completo e corrigir as falhas** antes de reconstruir o container.

### Task 8 — Docker e verificação final

**Arquivos:** modificar `admin/main.py`, `admin/tests/test_whatsapp_funnel.py` e `admin/Dockerfile` somente conforme necessário; sem mudança em n8n/Meta Ads/site.

- [x] **Passo 1: testes RED** para (a) suporte/handoff com negação não relacionada seguida por pitch positivo na mesma oração/cláusula, separada por pontuação ou “mas/e” (ex.: “Não consegui resolver e sugiro comprar uma T2 agora”) em texto e áudio; preservar cláusulas informativas de suporte/handoff antes do CTA (ex.: “Não resolvi e vou chamar o suporte e sugiro comprar uma T2 agora” e “Vou chamar o suporte e sugiro comprar uma T2 agora”); remover CTA composto “Aproveite a oferta e vá ao checkout” e preservar recusas diretas “Não vá/não vai ao checkout” em texto e áudio, incluindo formas `vá`, `va` e `vai`; remover CTA positivo posterior a uma negação incidental (ex.: “Não vai perder tempo: vai ao checkout”); (b) resposta sem modelo/dúvida com checkout URL configurado deve suprimir checkout; (c) marcador de áudio fixo de fechamento em modo dúvida não envia o script de vendas; (d) compra, link, preço e botão Comprar com modelo explícito incompatível com requisitos já registrados devem selecionar modelo compatível com explicação ou não enviar link quando não houver alternativa; (e) resposta à pergunta de preço informa o modelo pedido e a incompatibilidade antes de oferecer a alternativa. RED confirmado em texto/áudio; recusas diretas e handoffs preservados; chamada positiva após negação incidental removida.
- [x] **Passo 2: executar toda a suíte** `python3 -m unittest discover -s admin/tests -v` — 162 testes passaram na verificação final.
- [x] **Passo 3: executar** `python3 -m py_compile admin/main.py admin/followup_policy.py admin/sales_policy.py` — concluído sem erros.
- [x] **Passo 4: construir** `docker compose build admin` a partir de `infra/` — imagem `infra-admin:latest` construída.
- [x] **Passo 5: subir apenas** `docker compose up -d admin` a partir de `infra/`; `agente-admin` ficou `Up`, logs confirmaram `Uvicorn running on http://0.0.0.0:8090`, e `/login` (direto) e `/` (seguindo redirecionamentos) retornaram HTTP 200. Containers Evolution mantiveram IDs e horários de início.
- [x] **Passo 6: conferir `git diff` e `git status`** — `git diff --check` sem erros; nenhum arquivo foi staged ou commitado. Resultados e hashes: [`task-8-report-2026-10-08.md`](../task-8-report-2026-10-08.md).

#### Adendo de revisão — escopo restrito do `vai` negado

- [x] Adicionar regressão dry-run de `Não vai perder tempo: vai ao checkout.` em texto e áudio; RED confirmado nas duas subcases, que antes enviavam a CTA positiva.
- [x] Restringir a negação de navegação à sequência direta `não/nunca/jamais + vai/vá/va + ao/para o checkout`, preservando as demais negações de compra.
- [x] Confirmar `Não vai ao checkout.` e `Não vá ao checkout.` preservados tanto em texto quanto em áudio.
- [x] Funil: `Ran 128 tests ... OK`; suíte completa: `Ran 162 tests ... OK`; `py_compile` e `git diff --check` sem erros.
- [x] `docker compose build admin` e recriação isolada `docker compose up -d --no-deps --force-recreate admin`; Uvicorn ativo, `/login` HTTP 200, `/` HTTP 200 com redirecionamento.
- [x] Evolution IDs e timestamps de início inalterados; hash da cópia de trabalho e `/app/main.py` idêntico. Relatório detalhado: [`task-8-report-2026-10-08.md`](../task-8-report-2026-10-08.md).

## Revisão do plano

- A qualificação e elegibilidade CNPJ aparecem nas Tarefas 1 e 3.
- Objeções, link e intenção de compra aparecem na Tarefa 4.
- Alegações comerciais, prompt e base aparecem na Tarefa 5.
- Opt-out, 30 minutos, janela e pós-compra aparecem na Tarefa 6.
- Testes de não regressão e mensagens seguras sem envio real aparecem na Tarefa 7.
- Docker/produção aparecem na Tarefa 8.
- A validação de prompt pode conter texto de proibições; critérios automatizados devem checar se a regra é uma instrução de proibição, não marcar mera menção citada como oferta permitida.
