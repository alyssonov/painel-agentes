# Arquitetura — Painel de Agentes (Fase 0)

Documento de proposta. Nada aqui é código ainda; serve para alinharmos antes da
Fase 1.

---

## 1. Decisões de stack (e por quê)

| Camada | Escolha | Por quê |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | Como sugerido. Bom suporte a async (muitas chamadas de API em paralelo), tipagem com Pydantic, SDK oficial da Anthropic em Python. |
| Painel | **Jinja2 + HTMX** (em vez de Next.js) | Um único projeto, uma única linguagem, sem etapa de build e sem segundo servidor. Para um painel de filas, cards e botões "Aprovar", HTMX entrega a interatividade necessária com muito menos peças para manter. Se no futuro quiserem app mobile, a API JSON já existirá. |
| Agendador | **APScheduler em processo `worker` separado** | Se o painel reiniciar, os agentes continuam; e um agente pesado não deixa o painel lento. Job store no próprio banco, para não perder agendamentos. |
| Banco | **PostgreSQL** (prod) / **SQLite** (dev) via SQLAlchemy 2 + Alembic | Como sugerido. Migrações versionadas. |
| HTTP | **httpx** + **tenacity** | Cliente async com timeouts; tentativas com espera exponencial. |
| Criptografia | **cryptography (Fernet)** | Tokens OAuth criptografados no banco com chave no `.env`. |
| Login | Sessão por cookie (`HttpOnly`, `Secure`, `SameSite=Lax`) + hash **Argon2** + CSRF | Simples e seguro para poucos usuários. Bloqueio após tentativas erradas. |
| IA | **Claude API** (SDK `anthropic`) | Modelo configurável por tarefa; ver seção 7. |
| E-mail | SMTP (qualquer provedor) | Sem dependência de fornecedor. |
| WhatsApp | **WhatsApp Business Platform – Cloud API** (Meta) | Oficial. Exige template aprovado para mensagens iniciadas pela empresa (o relatório diário). |
| PDF | **WeasyPrint** | Gera o PDF a partir do mesmo HTML/CSS do painel. |
| Implantação | **Docker Compose**: `web`, `worker`, `db`, `caddy` | Caddy dá HTTPS automático no VPS. |

---

## 2. Visão geral

```
                    ┌──────────────── Painel (navegador) ────────────────┐
                    │  Login · Visão geral · Filas de aprovação · Agentes │
                    └──────────────────────────┬─────────────────────────┘
                                               │ HTTPS (Caddy)
┌──────────────────────────────────────────────▼──────────────────────────────┐
│ web (FastAPI)                                                               │
│   rotas do painel ── serviços ── ActionGateway (aprovar/rejeitar/executar)  │
└──────────────┬──────────────────────────────────────────────┬───────────────┘
               │                     PostgreSQL               │
┌──────────────▼──────────────────────────────────────────────▼───────────────┐
│ worker (APScheduler)                                                        │
│   Sincronizadores (leem APIs → banco)     Agentes (leem banco → propõem)    │
│        │                                         │                          │
│        ▼                                         ▼                          │
│   Integrações: OlistClient (só leitura) · MercadoLivreClient · ShopeeClient │
│                WhatsAppClient · EmailClient · ClaudeClient                  │
│   (em DRY_RUN, cada uma é trocada pela versão Fake com dados fictícios)     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Princípios

1. **Sincronizar ≠ agir.** Os *sincronizadores* só leem as APIs e gravam um
   espelho no banco (pedidos, estoque, perguntas...). Os *agentes* trabalham
   em cima desse espelho. Isso reduz chamadas às APIs (rate limit) e deixa os
   agentes fáceis de testar.
2. **Toda escrita externa é uma `Ação`.** Um agente nunca "responde" uma
   pergunta: ele cria uma `Ação proposta` (`answer_question`, `send_message`,
   `update_price`, `update_listing`, `send_report`). O **ActionGateway** é o
   único código que executa ações, e ele:
   - verifica se o agente está em modo automático **e** se a ação passa nas
     regras (confiança, margem mínima, limite de variação de preço etc.);
   - senão, deixa a ação na fila esperando aprovação no painel;
   - em `DRY_RUN`, marca a ação como "executada (simulação)" sem chamar a API;
   - grava tudo no `audit_log`.
3. **Olist sem escrita, por construção.** O `OlistClient` não tem métodos de
   escrita, e o `ActionGateway` não tem nenhum tipo de ação que mire o Olist.
   Um teste automático garante isso.
4. **Fake por padrão.** Cada integração tem uma interface (`Protocol`) e duas
   implementações: real e fake. Uma fábrica escolhe com base em `DRY_RUN`.

### Ciclo de vida de uma Ação

```
proposta ──► aguardando_aprovacao ──► aprovada ──► executando ──► executada
    │                │                                  │
    │                └──► rejeitada / editada           └──► falhou (com tentativas)
    └──► (modo automático + regras OK) ──► aprovada (por "agente:<nome>")
```

---

## 3. Estrutura de pastas

```
painel-agentes/
├── CLAUDE.md
├── README.md
├── .env.example
├── Makefile
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── Caddyfile
├── alembic/                     # migrações do banco
├── docs/
│   ├── ARQUITETURA.md
│   └── IMPLANTACAO.md           # (Fase 8)
├── app/
│   ├── main.py                  # cria o app FastAPI (processo web)
│   ├── worker.py                # inicia o APScheduler (processo worker)
│   ├── config.py                # leitura do .env (Pydantic Settings)
│   ├── core/
│   │   ├── db.py                # engine, sessão
│   │   ├── security.py          # senhas, sessão, CSRF, criptografia de tokens
│   │   ├── actions.py           # ActionGateway (único ponto de escrita externa)
│   │   ├── audit.py             # gravação no audit_log
│   │   ├── alerts.py            # criação e resolução de alertas
│   │   ├── ratelimit.py         # limitador de taxa por integração
│   │   └── money.py             # utilidades de dinheiro (Decimal, centavos)
│   ├── models/                  # tabelas SQLAlchemy (uma por domínio)
│   ├── integrations/
│   │   ├── base.py              # Protocols + fábrica real/fake
│   │   ├── oauth.py             # guarda/renova tokens criptografados
│   │   ├── olist/               # client.py (só leitura), fake.py, schemas.py
│   │   ├── mercadolivre/        # client.py, fake.py, schemas.py
│   │   ├── shopee/              # client.py (assinatura HMAC), fake.py, schemas.py
│   │   ├── whatsapp/
│   │   └── email/
│   ├── sync/                    # sincronizadores: API → banco
│   │   ├── olist_sync.py
│   │   ├── ml_sync.py
│   │   └── shopee_sync.py
│   ├── ai/
│   │   ├── client.py            # wrapper da Claude API: cache, custo, orçamento
│   │   ├── models.py            # qual modelo usar em cada tarefa
│   │   └── prompts/             # prompts versionados (.md)
│   ├── agents/
│   │   ├── base.py              # classe Agent: run(), métricas, registro
│   │   ├── questions.py         # 1 Respondedor de perguntas
│   │   ├── listings.py          # 2 Criador/otimizador de anúncios
│   │   ├── stock_watch.py       # 3 Vigia de estoque
│   │   ├── pricing.py           # 4 Monitor de preços
│   │   ├── after_sales.py       # 5 Pós-venda
│   │   ├── reviews.py           # 6 Avaliações e devoluções
│   │   ├── margin.py            # 7 Margem real
│   │   ├── daily_report.py      # 8 Relatório diário
│   │   └── nfe_check.py         # 9 Conferência de NF-e
│   ├── services/                # regras de negócio usadas por web e agentes
│   ├── fake_data/               # gerador de dados de simulação (seed)
│   └── web/
│       ├── routes/              # uma rota por seção do menu + auth + oauth callbacks
│       ├── templates/           # Jinja2 (base, componentes, páginas)
│       └── static/              # css, htmx, ícones
└── tests/
    ├── unit/
    ├── integration/             # com respx (HTTP simulado)
    └── conftest.py
```

---

## 4. Modelo de dados

Convenções: dinheiro em **centavos** (inteiro, colunas `*_cents`; nunca float); datas em UTC no banco,
exibidas em `America/Sao_Paulo`; `channel` ∈ {`ml`, `shopee`}; toda tabela tem
`id`, `created_at`, `updated_at`.

### 4.1 Acesso e configuração

| Tabela | Campos principais |
|---|---|
| `users` | email, nome, password_hash, papel (`admin`/`operador`), ativo, falhas_login, bloqueado_ate |
| `integration_accounts` | provider (`olist`/`ml`/`shopee`/`whatsapp`), nome_conta, external_id (seller_id/shop_id), access_token_enc, refresh_token_enc, expires_at, status (`ok`/`erro`/`desconectado`), last_ok_at, last_error |
| `store_settings` | chave/valor (JSON): tom das respostas, prazos de envio, garantia, horário do relatório, destinatários |
| `agents` | slug, nome, ativo, modo (`manual`/`automatico`), config (JSON validado por Pydantic por agente), last_run_at, last_status |

### 4.2 Espelho dos dados (vindos das APIs)

| Tabela | Origem | Campos principais |
|---|---|---|
| `products` | Olist | olist_id, sku, nome, descricao, ficha (JSON), custo, preco_base, ncm, imposto_estimado_pct, ativo |
| `stock_snapshots` | Olist | product_id, saldo, reservado, capturado_em |
| `listings` | ML/Shopee | channel, external_id, product_id (via SKU), titulo, preco, estoque_anuncio, status, url, comissao_pct |
| `listing_metrics` | ML/Shopee | listing_id, data, visitas, vendas, conversao |
| `orders` | Olist + canais | olist_id, channel, external_id, cliente_nome (abreviado), status_olist, status_kanban, total, frete_cobrado, frete_custo, taxas_marketplace, data_pedido |
| `order_items` | Olist | order_id, product_id, quantidade, preco_unit |
| `invoices` | Olist | order_id, olist_id, numero, serie, chave, situacao (`autorizada`/`rejeitada`/`pendente`...), emitida_em, link_pdf — **só leitura** |
| `questions` | ML/Shopee | channel, external_id, listing_id, texto, perguntado_em, status (`nova`/`proposta`/`respondida`/`humano`) |
| `conversations` / `messages` | ML/Shopee | order_id, channel, direcao (cliente/loja), texto, enviado_em |
| `claims` | ML/Shopee | order_id, tipo (reclamação/devolução/mediação), motivo, status, prazo_resposta |
| `reviews` | ML/Shopee | listing_id, nota, comentario, data |
| `returns` | ML/Shopee | order_id, motivo, status |
| `competitor_prices` | APIs permitidas | listing_id, fonte, preco_observado, observado_em |

### 4.3 Trabalho dos agentes

| Tabela | Campos principais |
|---|---|
| `agent_runs` | agent_id, inicio, fim, status, itens_processados, erro |
| `actions` | agent_id, tipo, alvo_tipo/alvo_id (pergunta, anúncio, pedido), payload (JSON), payload_original (antes de edição), confianca, motivo, status (ver ciclo), decidido_por, decidido_em, executado_em, resultado, dry_run |
| `price_rules` | product_id, margem_minima_pct, preco_min, preco_max, variacao_max_pct, auto_permitido |
| `margin_entries` | order_item_id, preco, taxas, frete, custo, impostos, lucro, margem_pct (cálculo guardado com a versão das fórmulas) |
| `alerts` | origem (agente/integração), severidade, titulo, detalhe, alvo, status (`aberto`/`resolvido`), resolvido_em |
| `insights` | agent_id, tipo (FAQ, problema recorrente, anúncio fraco), dados (JSON), semana |
| `reports` | tipo (diario/semanal/mensal), periodo, conteudo (JSON), pdf_path, enviado_para, enviado_em |
| `notifications` | canal (email/whatsapp), destino, status, erro |

### 4.4 Auditoria e custo de IA

| Tabela | Campos principais |
|---|---|
| `audit_log` | quando, ator (`user:<id>` ou `agent:<slug>`), evento, alvo_tipo, alvo_id, action_id, detalhes (JSON sem segredos), dry_run — **somente inserção** |
| `ai_usage` | quando, agent_id, tarefa, modelo, tokens_entrada, tokens_saida, tokens_cache, custo_estimado_brl, cache_hit |
| `ai_cache` | hash (modelo + prompt normalizado), resposta, criado_em, expira_em |

---

## 5. Integrações — pontos de atenção

(Será confirmado na documentação oficial no início da fase de cada uma.)

- **Olist ERP API v3:** OAuth 2.0 (código de autorização + refresh token).
  Leitura de produtos, estoque, pedidos, notas fiscais e contas a receber.
  Limite de requisições por minuto depende do plano contratado — o limitador
  será configurável.
- **Mercado Livre:** OAuth 2.0; access token de curta duração renovado pelo
  refresh token. Notificações por webhook (perguntas, pedidos, mensagens,
  reclamações) **e** varredura periódica como reforço. Webhook exige o painel
  acessível por HTTPS público (o VPS resolve; em dev usamos só varredura).
- **Shopee Open Platform v2:** cada chamada é assinada (HMAC-SHA256 com
  partner_key); tokens de curta duração com refresh. Exige app aprovado no
  portal de parceiros.
- **WhatsApp Cloud API:** mensagens iniciadas pela loja (relatório das 19h)
  precisam de **template aprovado** pela Meta; o resumo vai como variáveis do
  template, com link para o painel/PDF.
- **Monitor de preços (Agente 4) — risco de escopo:** não existe API oficial
  da Shopee para preço de concorrentes, e o acesso público à busca do ML foi
  restringido. O caminho oficial provável é usar os recursos do próprio ML
  para o vendedor (sugestões de preço / "preço para ganhar" dos seus anúncios)
  e, na Shopee, comparação com dados que a API devolve para a própria loja.
  Vou validar isso na Fase 7; se não houver fonte oficial suficiente, o agente
  funcionará com preço de referência informado manualmente. **Scraping está
  descartado.**

---

## 6. Os agentes no modelo

| # | Agente | Lê | Propõe ações | Agendamento padrão |
|---|---|---|---|---|
| 1 | Respondedor de perguntas | questions, products, stock, store_settings | `answer_question` (ou marca "humano") | a cada 5 min + webhook |
| 2 | Anúncios | products, listing_metrics, insights | `create_listing`, `update_listing` (nunca automático para publicar) | semanal |
| 3 | Vigia de estoque | stock_snapshots, order_items, listings | só alertas | a cada 30 min |
| 4 | Preços | competitor_prices, price_rules, margin | `update_price` (respeitando margem mínima) | 2× ao dia |
| 5 | Pós-venda | messages, claims, invoices | `send_message` | a cada 10 min |
| 6 | Avaliações e devoluções | reviews, returns | insights | semanal |
| 7 | Margem real | orders, order_items, products | margin_entries + alertas de prejuízo | a cada hora |
| 8 | Relatório diário | tudo | `send_report` | 19h diário; semanal/mensal |
| 9 | Conferência de NF-e | orders, invoices | só alertas | a cada 30 min |

Agentes 3, 7 e 9 **não usam IA** (são cálculos) — custo zero de tokens.

---

## 7. IA: custo sob controle

- `app/ai/models.py` define o modelo por tarefa. Padrão: **Haiku** para
  classificar perguntas, avaliar confiança e respostas curtas; **Sonnet** só
  para criar anúncios e análises semanais. Trocável pelo `.env`.
- Contexto mínimo: só os campos relevantes da ficha, as regras da loja e as
  últimas N mensagens da conversa.
- Prompt caching da API para a parte fixa (regras da loja + instruções).
- `ai_cache`: perguntas iguais/normalizadas reaproveitam a resposta.
- Teto diário (`AI_DAILY_BUDGET_BRL`): ao atingir, agentes de IA pausam e o
  painel mostra alerta. Gasto do dia sempre visível na Visão geral.
- Saída estruturada (JSON) com `confianca` e `motivo` — regra fixa em código
  força "humano" para desconto, negociação, assuntos fora da ficha ou
  confiança baixa, independentemente do que o modelo disser.

---

## 8. Fases

| Fase | Entrega |
|---|---|
| 0 | Esta proposta + `CLAUDE.md` ✅ |
| 1 | Base, banco, login, Visão geral, modo simulação com dados falsos ✅ |
| 2 | Olist (OAuth, somente leitura) + painel com dados reais ✅ |
| 3 | ML + Shopee + Agente 1 com aprovação manual |
| 4 | Agentes 3, 9 e 7 ✅ |
| 5 | Agente 8 (WhatsApp/e-mail) — e-mail ✅; WhatsApp e PDFs a seguir |
| 6 | Agente 5 |
| 7 | Agentes 2, 4 e 6 |
| 8 | Revisão de segurança, testes, Docker, guia do VPS |

---

## 9. Decisões tomadas com o usuário

1. **Usuários:** por enquanto só dois (o usuário e o dono), ambos `admin`. O
   campo `role` já existe para operadores no futuro.
2. **Olist:** fonte do estoque e do custo dos produtos usado no lucro. O limite
   de requisições da API será configurável (plano não informado).
3. **Apps de desenvolvedor:** já existem no Mercado Livre e na Shopee.
4. **Impostos:** regime **Lucro Real**; a margem usa **uma alíquota efetiva
   única** sobre o valor da venda (`store_settings.tax_rate_pct`), informada
   pela contabilidade. Na simulação usamos 12% só como exemplo.
5. **Domínio:** ainda não há. Até a Fase 8 tudo roda local; para produção será
   preciso um domínio/subdomínio com HTTPS (OAuth e webhooks exigem).

## 10. Tabelas criadas na Fase 1

`users`, `integration_accounts`, `store_settings`, `agents`, `agent_runs`,
`products`, `stock_snapshots`, `orders`, `order_items`, `questions`, `alerts`,
`audit_log`, `ai_usage`. As demais (seção 4) entram nas fases em que forem
usadas, cada uma com sua migração.

Registros criados pela simulação têm `is_demo = true`; a simulação só apaga
esses registros e se recusa a rodar com `DRY_RUN=false` ou com conta real
conectada.

## 11. Fase 2 — Olist (somente leitura)

Documentação oficial consultada em 06/11/2026: especificação OpenAPI
"Olist ERP API v3" 3.1 (`api.tiny.com.br/public-api/v3/swagger/swagger.json`)
e os guias de autenticação e limites em `api-docs.erp.olist.com`.

- **Conexão:** OAuth 2 (código de autorização) em `accounts.tiny.com.br`,
  com `state` anti-falsificação. Tokens criptografados em
  `integration_accounts`. Token de acesso: 4 h; refresh token: 1 dia.
- **Cliente** (`app/integrations/olist/client.py`): só GET, com limitador
  guiado pelos cabeçalhos `X-RateLimit-*`, novas tentativas com espera
  crescente (erros 5xx/rede) ou espera do reset (429) e renovação do token
  em caso de 401. Um teste lê o código e falha se aparecer qualquer escrita.
- **Sincronização** (`app/sync/olist_sync.py`), a cada `OLIST_SYNC_MINUTES`
  no worker: produtos (custo médio ou preço de custo) → estoque (fotografias
  em `stock_snapshots`) → pedidos (lista + detalhe de cada pedido alterado) →
  notas fiscais (janela de 7 dias). Depois da primeira vez, só o que mudou.
  Falha → conta "Com erro" + alerta único; recuperou → alerta resolvido.
- **Mapeamento das situações do pedido no quadro:** Aberto/Aprovado/Preparando
  envio → A separar; Faturado/Pronto para envio → Embalado; Enviado/Entregue →
  Enviado; Dados incompletos/Não entregue/Em devolução/desconhecida → Com
  problema; Cancelado → fora do quadro e do faturamento.
- **Canal:** pelo nome da integração de e-commerce do pedido no Olist
  (Mercado Livre, Shopee ou "Outros").
- **Margem:** o Olist não informa a taxa do marketplace; até a Fase 3 a margem
  não a inclui e o painel avisa. A alíquota de impostos é editável em
  "Contas conectadas".
- **Dados pessoais:** do cliente guardamos só primeiro nome + inicial.
- Tabelas novas: `invoices`, `sync_runs`. Pedidos passam a ser únicos por
  `olist_id`.

## 12. Fase 3 — etapa 1: Mercado Livre (leitura)

Documentação oficial consultada em 06/11/2026 via busca (o portal
developers.mercadolivre.com.br recusa leitura automática): autenticação e
autorização, gerenciamento de perguntas e respostas, gerenciamento de vendas
e packs.

- **Conexão:** OAuth 2 com **PKCE (S256)** e `state`. O Mercado Livre exige
  redirect HTTPS público: usamos uma página estática no GitHub Pages
  (`alyssonov/painel-retorno`) que só repassa `?code&state` para
  `http://localhost:8000/integracoes/mercadolivre/retorno`. O código sozinho
  não serve para nada sem o client secret e o code_verifier (que ficam no
  painel). Na Fase 8, com domínio próprio, essa página deixa de ser usada.
- **Tokens:** acesso de 6 h; refresh token de uso único (sempre guardamos o
  último). Renovação com trava por integração (`app/integrations/oauth.py`),
  compartilhada com o Olist.
- **Base de leitura comum** (`app/integrations/http.py`): só GET, novas
  tentativas, 429 e 401 tratados. Escritas ficarão em módulos separados,
  chamados apenas pelo ActionGateway.
- **Sincronização** (`app/sync/ml_sync.py`, a cada `ML_SYNC_MINUTES`):
  reputação (`/users/me`) → perguntas (`/questions/search`, não respondidas +
  recentes; títulos via `/items`) → taxas (`/orders/search`, `sale_fee` ×
  quantidade, somadas por pacote) gravadas em `marketplace_fees` e aplicadas
  aos pedidos do Olist pelo número do pedido/pacote — também quando o pedido
  chega no Olist depois.
- **Partes comuns de sincronização** em `app/sync/common.py` (execução única,
  alerta único por integração, cursores).
- Tabela nova: `marketplace_fees`.
- **A conferir no primeiro uso real:** se a soma `sale_fee × quantidade`
  bate com a tarifa mostrada na tela de vendas do Mercado Livre.

## 13. Fase 3 — etapa 2: Agente 1 (Respondedor de perguntas)

- **IA** (`app/ai/`): `models.py` define modelo e esforço por tarefa
  (respostas: `claude-haiku-5-5`, esforço `low`; anúncios/análises:
  `claude-sonnet-5-5`) e os preços para estimar custo. `client.py` faz toda
  chamada: teto diário → cache (`ai_cache`, 7 dias) → SDK oficial com saída
  JSON por esquema (`output_config.format`) e `cache_control` no prompt fixo →
  registro em `ai_usage`. Recusa do modelo → pergunta vai para uma pessoa.
- **ActionGateway** (`app/core/actions.py`): `propose` → `approve`
  (opcionalmente editado) / `reject`; executa só aprovadas; `DRY_RUN` →
  status `simulada` sem chamar nada; falha no envio → `falhou` (pode tentar de
  novo); tudo no `audit_log`. Os módulos de escrita (ex.:
  `mercadolivre/writes.py`, `POST /answers`) só são importados aqui — um
  teste lê o código e garante.
- **Agente** (`app/agents/questions.py`): regras fixas antes da IA
  (desconto/negociação → pessoa, sem gastar IA); contexto mínimo (ficha do
  anúncio, trecho da descrição, estoque do Olist pelo SKU do anúncio, regras
  da loja); resposta com `confianca` e `assunto`; confiança baixa, assunto
  delicado ou dado de contato → "Precisa de você". Modo automático aprova só
  confiança alta. Máx. 15 perguntas por rodada, a cada
  `AGENT_QUESTIONS_MINUTES`.
- **Painel**: `/perguntas` (fila com Aprovar / Editar / Responder eu mesmo /
  Tirar da fila, e "Respondidas hoje") e `/agentes` (ativar/pausar, manual/
  automático, regras das respostas, métrica do dia, gasto de IA).
- Tabelas novas: `actions`, `ai_cache`.

## 14. Fase 4 — Vigia de estoque, Conferência de NF-e e Margem real

Sem IA. Os três só leem e criam/resolvem alertas.

- **Alertas** (`app/core/alerts.py`): cada agente informa a lista de alertas
  que valem agora (por chave); `sync_alerts` cria, atualiza e resolve — sem
  duplicar. Os agentes não rodam sem conta real conectada (`has_real_data`).
- **Regras por agente** (`app/agents/config.py`): valores padrão e limites;
  salvas em `agents.config` pela página Agentes.
- **Anúncios do Mercado Livre** (`listings`, só leitura): `GET
  /users/{id}/items/search` (modo `scan` acima de 1000) + `/items?ids=`, uma
  linha por variação, ligadas ao produto do Olist pelo SKU. Relidos de hora
  em hora. Anúncios Full (`logistic_type=fulfillment`) não entram na
  comparação de estoque.
- **Vigia de estoque** (`agents/stock_watch.py`, `services/stock.py`):
  disponível = saldo − reservado do Olist; média diária na janela; dias
  restantes; esgotado / acabando / divergência; até 8 alertas + 1 resumo.
- **Conferência de NF-e** (`agents/nfe_check.py`): usa a situação do pedido
  no Olist, o `idNotaFiscal` do pedido (nova coluna `orders.invoice_olist_id`)
  e as notas lidas (`invoices`). Nota "ok" = situações 2, 6, 7, 8.
- **Margem real** (`services/margin_report.py`, `agents/margin.py`): taxa,
  frete e imposto do pedido rateados entre os itens pelo valor; calculado na
  hora (sem tabela `margin_entries`, desnecessária neste volume).
- Páginas: `/estoque-precos` e `/margem`.

## 15. Fase 5 — Relatório diário (e-mail)

- **Envio interno** (`app/notify/email.py`): SMTP com STARTTLS (Gmail com
  senha de app), texto + HTML. Só para a loja; clientes nunca passam por aqui.
- **Relatório** (`app/reports/daily.py`): números do dia (margem pelo
  `margin_report`, comparação com ontem no mesmo horário, canais, mais
  vendidos, perguntas, alertas abertos, resumo de cada agente, gasto de IA),
  renderizados em texto e HTML (estilos inline, padrão de e-mail). Sem IA.
- **Agendamento**: o worker confere a cada 10 min; envia se já passou do
  `hora_envio` (regra do agente, padrão 19) e o de hoje ainda não saiu.
  Tabela `reports` (único por tipo + dia) impede duplicidade; falha → alerta
  e nova tentativa a cada 30 min; envio de teste é registrado à parte
  (`kind=teste`).
- **Página** `/relatorios`: destinatários (`store_settings.report_recipients`),
  envio de teste, histórico e prévia em `/relatorios/previa` (única rota com
  CSP que permite estilos inline, exibida só dentro do painel).

## 16. Alerta de despacho e Termômetro da reputação (extras da Fase 4)

- **Envios** (`shipments`, só leitura): na leitura das vendas do Mercado Livre
  anotamos o `shipping.id` de cada pedido; de 15 em 15 min o painel consulta
  `GET /shipments/{id}` (situação e tipo logístico) e, para os que esperam
  despacho, `GET /shipments/{id}/sla` (`expected_date` = prazo limite).
  Envios já despachados deixam de ser consultados. Full fica de fora.
- **Agente `despacho`**: atrasado → urgente; vence dentro de `horas_aviso` →
  atenção; até 10 alertas + resumo.
- **Reputação**: `GET /users/me` → `seller_reputation.metrics` (claims,
  delayed_handling_time, cancellations; taxa e período) guardados em
  `integration_accounts.extra.reputation_metrics`.
- **Agente `reputacao`**: limites do verde configuráveis (a documentação
  oficial traz tabelas diferentes entre páginas/países; padrões da tabela MLB:
  reclamações 2%, atrasos 10%, cancelamentos 1,5%); aviso a partir de 80% do
  limite, urgente acima dele.
