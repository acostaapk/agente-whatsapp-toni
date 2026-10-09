# Funil consultivo de vendas Ton pelo WhatsApp

**Data:** 07/10/2026  
**Status:** desenho aprovado em conversa; aguardando revisão desta especificação antes do plano de implementação.  
**Escopo:** bot WhatsApp oficial da Meta em `admin/main.py`; não inclui anúncios, site/pixel, CRM, n8n ou automação externa.

## Objetivo

Conduzir leads de WhatsApp até o checkout de parceiro do modelo adequado, responder objeções com informação coerente, reduzir respostas inconsistentes do LLM e oferecer pós-compra básico. Preservar o atendimento humano, a regra de opt-out e os limites da API oficial do WhatsApp.

## Decisões confirmadas

- Manter a qualificação atual: tipo de negócio, comprovante, volume de vendas e CPF/CNPJ. Não substituir por um formulário novo.
- Não oferecer TapTon espontaneamente.
- Não enviar fotos no fluxo de venda por enquanto.
- Preservar o link afiliado atual. Usar somente o snapshot promocional por modelo observado pelo proprietário na página oficial aberta pelo link de parceiro em 07/10/2026: T1 70% OFF sem cupom adicional; T2/T3 72% OFF + cupom 20%; T3 Smart 71% OFF + cupom 20%. Informar que é condição observada, pode mudar e o total válido é o do carrinho; não chamar desconto de cashback.
- Sempre que enviar link de checkout de um modelo, acompanhar com o snapshot desse modelo e a ressalva de confirmar no carrinho; não reservar essa informação apenas para a pergunta “tem desconto?”.
- Se o cliente escolher CNPJ, perguntar por botão se já tem uma Ton ativa nesse CNPJ. Se sim, não enviar checkout para repetir cadastro no mesmo CNPJ; explicar a alternativa de compra pelo CPF e pedir confirmação explícita antes de seguir, sem solicitar número de documento.
- Ao demonstrar intenção de compra, enviar o link do modelo escolhido imediatamente. Não perguntar Android/iPhone antes da compra.
- T1 pode ser mostrada quando o cliente pedir esse modelo explicitamente; como recomendação proativa de menor investimento, somente se a pessoa indicar essa prioridade e não precisar de Pix na maquininha. Se pedir T1 e disser que precisa de Pix, explicar a limitação e oferecer T2.
- Depois de confirmação explícita da compra, perguntar Android/iPhone e enviar o link de app configurado; em seguida, oferecer ajuda de ativação.
- Um único lembrete de checkout após 30 minutos. Suprimir se o cliente responder, confirmar compra, pedir atendimento ou optar por não receber mensagens.
- Desativar mensagens automáticas de 24h e 72h até existirem modelos aprovados/configurados na Meta. Não enviar texto livre fora da janela de atendimento.
- Manter as respostas sem voz e o opt-out persistente já existentes.

## Jornada proposta

### 1. Entrada e intenção

Usar a mensagem pré-preenchida do anúncio como contexto. A saudação atual permanece curta. Distinguir lead exploratório, lead quente (modelo/preço/link/compra), dúvida pré-venda, problema pós-venda, pedido de atendente e opt-out. Lead quente recebe primeiro a resposta/link pedido; não deve ser forçado a repetir a qualificação.

### 2. Qualificação consultiva

No fluxo exploratório, coletar apenas o que falta:

1. tipo de negócio/uso;
2. comprovante impresso ou digital;
3. faixa de vendas mensal;
4. CPF ou CNPJ por botão (nunca pedir o número do documento);
5. se escolheu CNPJ: botão “Já tem uma Ton ativa neste CNPJ?” (Sim/Não); se Sim, oferecer “Continuar pelo CPF” ou “Não seguir agora”, sem trocar o documento silenciosamente.

Recomendar exatamente um modelo compatível com as respostas, sem atribuir necessidades que a pessoa não mencionou:

- T2 para quem aceita comprovante digital/não precisa de impressora; também é o modelo de entrada com Pix na máquina.
- T3 para comprovante impresso sem requisitos de tela Android/4G.
- T3 Smart para comprovante impresso com necessidade declarada de tela Android ou 4G.
- T1 quando explicitamente solicitada, informando que não aceita Pix na máquina; ou como recomendação de menor investimento se o cliente não precisar de Pix. Se o cliente disser que precisa de Pix, recomendar T2.

TapTon não entra na recomendação proativa.

### 3. Oferta e objeções

Uma fonte comercial versionada fornece modelos, preços, taxas, link afiliado e regras. Respostas determinísticas cobrem pelo menos:

- já possui maquininha (perguntar o motivo antes de oferecer outra; remover a regra antiga que oferece T2 imediatamente);
- já possui Ton no mesmo CNPJ (bloquear checkout duplicado para aquele CNPJ e oferecer CPF como alternativa);
- preço/desconto (reutilizar a frase e o link de desconto vigentes, sem percentual garantido);
- taxas e planos (Mega+/Black, fonte/data, conferir valor individual no app);
- garantia, Pix, frete/prazo e recebimento;
- confiança/compra oficial;
- pós-venda, reclamação ou solicitação de humano (encaminhar, sem continuar o pitch).

O LLM fica para perguntas abertas não cobertas, subordinado à base atual e às regras. Uma resposta de dúvida não injeta link, Pix ou segunda oferta automaticamente.

Antes de enviar uma recomendação com checkout produzida pelo LLM, remover URLs que não pertençam ao modelo/link configurado e substituir afirmações promocionais/preços/cupom do LLM pelo snapshot comercial verificado daquele modelo. Se o snapshot estiver ausente ou incompleto, usar texto cauteloso e nunca completar por inferência. Em todas as rotas de checkout para CNPJ elegível ao Black, selecionar o link pelo estado/plano correto; não usar o Mega+ por padrão quando o Black deve ser aplicado.

### 4. Fechamento

- Recomendação inicial: link direto do modelo escolhido com os parâmetros de parceiro existentes e o snapshot promocional correspondente.
- Pedido posterior de link: reenviar o mesmo modelo, link e snapshot promocional correspondente.
- “Sim/Quero comprar” após a oferta: enviar link imediatamente, sem pergunta intermediária.
- Não alegar que clique significa compra concluída; o checkout Ton não oferece confirmação de venda ao bot nesta integração.

### 5. Follow-up

- Um único toque aos 30 minutos: pergunta se conseguiu concluir ou se precisa de ajuda; mensagem livre somente enquanto a janela de atendimento da Meta estiver aberta.
- Cancelar o toque se houver nova mensagem do cliente, compra informada, handoff ou opt-out.
- 24h/72h permanecem desligados até configuração de templates aprovados; não incluir depoimento, urgência ou escassez sem fonte verificável.

### 6. Pós-compra e atendimento

Após “comprei/finalizei/pedido feito”, parabenizar, perguntar o sistema operacional com o motivo (“para mandar o link certo do app”), enviar link iOS/Android se configurado; caso contrário, enviar o link universal atual da Ton. Em seguida, se colocar à disposição para ativação. Se o pedido não puder ser confirmado pela integração, tratar a mensagem do cliente como declaração, não como verificação transacional.

Mensagens de opt-out (“pare”, “não quero receber”, etc.) devem confirmar a remoção e gravar o número na lista persistente de bloqueio antes de qualquer outra rotina. A lista de bloqueio suprime respostas automáticas, botões e follow-ups.

O filtro de suporte/handoff aplica-se igualmente a texto, fala livre marcada pelo LLM e gravações de voz fixas; nenhum formato pode conter pitch de vendas em um atendimento de problema.

## Regras de fonte e alegações

- Fonte comercial: base autorizada do bot e páginas oficiais da Ton; dados variáveis carregam data de consulta e recomendação para confirmar a taxa no app.
- O site oficial consultado em 07/10/2026 promove taxas “a partir de 0,74%” e recebimento “em até 1 minuto” como opções/condições do produto. Usar linguagem condicionada ao plano, modalidade, conta e escolha do cliente no app; não prometer universalmente.
- Frete/prazo: comunicar o prazo oficial configurado; a observação de entrega em 1–2 dias não deve ser tratada como garantia.
- O link oficial de parceiro fornecido pelo proprietário mostra o cupom de 20% ativo na consulta de 07/10/2026 nos modelos T2/T3/T3 Smart; T1 aparece sem o cupom adicional. Essa condição é snapshot, não garantia futura; confirmar no carrinho.
- Depoimentos, “últimos dias”, “só hoje”, taxas específicas por parcela e comparações concorrenciais exigem fonte aprovada; caso contrário, não usar.

## Estado e eventos

Reutilizar o estado atual de qualificação, modelo escolhido, estágio da oferta, última atividade e bloqueio. Registrar eventos sem PII desnecessária: qualificação concluída, modelo recomendado, link enviado, compra declarada pelo cliente, link de app enviado, handoff e opt-out. Não registrar número de CPF/CNPJ.

## Arquivos previstos

- `admin/main.py`: roteamento determinístico de objeções/intenção, controle da nova pergunta condicional de CNPJ, fechamento, pós-compra e supressão de follow-up.
- `admin/prompts/toni-system.md`: remover regras conflitantes antigas (inclusive reengajamento em 2h e pitch automático de T2 para quem já tem máquina), consolidar recomendação T1/T2/T3/T3 Smart e proibição de oferta proativa de TapTon.
- `admin/config/agent-config.json` e exemplo versionado: dados comerciais verificados, links e respostas rápidas, sem segredos ou alteração do link de desconto existente.
- `admin/followup_policy.py` e `admin/tests/`: política pura de janela, estado e elegibilidade dos toques, com testes sem envio real de mensagens.
- `admin/Dockerfile`: garantir inclusão de módulos novos, caso necessário.

## Critérios de aceite

1. Lead quente recebe o link solicitado sem voltar à qualificação.
2. Fluxo exploratório faz perguntas pendentes uma vez e recomenda um único modelo adequado.
3. CNPJ com Ton ativa dispara a confirmação condicional; o bot nunca troca para CPF sem confirmação explícita e nunca envia link para repetir cadastro no mesmo CNPJ.
4. Não há TapTon proativo, foto de produto, percentual de desconto não confirmado, preço/taxa antiga, escassez falsa ou promessa de entrega garantida.
5. A resposta à dúvida é separada da oferta; link só é enviado quando o cliente avança.
6. O único follow-up automático é o de 30 minutos, elegível dentro da janela Meta; 24h/72h não enviam.
7. Opt-out é persistido e impede mensagens subsequentes.
8. Compra declarada leva ao fluxo Android/iPhone e link do app; clique no checkout não é tratado como compra.
9. Bateria de casos usa transporte falso/dry-run e não envia mensagens para números reais.

## Fora de escopo

- Criar/analisar campanhas de anúncios ou alterar o orçamento.
- Publicar conteúdo ou alterar a página/site/pixel.
- Integrar confirmação de compra da Ton/afiliados, comissão ou conversão de checkout.
- Enviar follow-ups de 24h/72h sem templates Meta aprovados.
- Oferecer TapTon.
