# CLAUDE.md — Painel de Agentes (Mercado Livre + Shopee + Olist ERP)

Este arquivo orienta qualquer sessão do Claude Code neste repositório.
Leia-o inteiro antes de mudar qualquer coisa. A arquitetura detalhada está em
`docs/ARQUITETURA.md`. A visão do produto (para onde ele vai) está em
`docs/VISAO_PRODUTO.md`; o diagnóstico e o plano atuais em
`docs/DIAGNOSTICO_E_PLANO.md`. Tudo que depende do usuário fica em
`PENDENCIAS.md` (mantenha atualizado a cada entrega).

## O que é o projeto

Nome do produto: **Gerente do Ano** (logo em `app/web/static/img/`, nas cores do tema escuro — azul-noite com destaque `#3B82F6`; "GERENTE" em Inter e "do ano" em Orbitron, ambas OFL, convertidas em contornos: selo
circular completo com o robô no login; só o robô, em círculo, no menu lateral,
na aba do navegador e no atalho do celular).

Sistema único com painel web onde agentes de IA automatizam o dia a dia de uma
loja que vende no **Mercado Livre** e na **Shopee**, usando o **Olist ERP
(antigo Tiny)** como sistema de gestão. O painel será apresentado ao dono da
loja: precisa ser **confiável, seguro e fácil para quem não é técnico**.

## Regras inegociáveis

1. **Só APIs oficiais.**
   - Olist ERP: API REST v3 com OAuth 2.0.
   - Mercado Livre: API oficial (developers.mercadolivre.com.br).
   - Shopee: Shopee Open Platform (API v2 oficial).
   - WhatsApp: WhatsApp Business Platform (Cloud API oficial da Meta).
   - **Proibido:** automação de navegador (Selenium, Playwright etc.), scraping,
     bibliotecas não oficiais, endpoints não documentados.
   - Antes de implementar qualquer integração, **consulte a documentação oficial
     atual** e registre no código (comentário) o link e a data da consulta.
2. **O Olist é a fonte da verdade.**
   - O cliente do Olist é **somente leitura**. Ele não deve ter nenhum método que
     escreva no Olist.
   - O sistema **nunca altera estoque** (nem no Olist nem nos anúncios: o Olist
     sincroniza o estoque com os marketplaces; o painel só avisa divergências) e
     **nunca emite, cancela ou altera notas fiscais**. Isso continua no Olist.
   - O Gerente do Ano pode ter **cadastro complementar** próprio (compatibilidade
     com motos, fornecedor, custo do Onix, estoque mínimo…), guardado só no seu
     banco — decisão do dono em 26/11/2026.
3. **Humano no controle.**
   - Toda ação que fala com cliente (resposta, mensagem) ou muda preço/anúncio
     vira uma **Ação proposta** que espera aprovação manual.
   - Cada agente tem uma chave "manual / automático" no painel. O padrão é
     **manual**. No automático, a ação só é executada se passar pelas regras e
     limites do agente (ex.: margem mínima, confiança alta).
   - Toda escrita externa passa obrigatoriamente pelo **`ActionGateway`**
     (`app/core/actions.py`). Nenhum agente chama método de escrita de
     integração diretamente.
4. **Modo simulação (`DRY_RUN=true`) é o padrão.**
   - **Nunca** faz chamadas de escrita reais (responder cliente, mudar preço,
     anúncio etc.). Só é desligado quando o dono decidir, editando o `.env`.
   - Sem conta conectada, mostra a loja fictícia (`is_demo`). Com conta real
     conectada, **lê dados reais** (leitura é segura: o cliente do Olist não
     tem escrita), e a loja fictícia é apagada.
   - O painel mostra a etiqueta "Modo simulação" no topo de toda página enquanto
     estiver ligado (a faixa amarela foi trocada por ela — decisão do dono em 26/11/2026).
   - Mensagens **internas** (relatórios para a própria loja, por e-mail ou
     WhatsApp) não são ações para clientes: são enviadas também no modo
     simulação, pelo `app/notify/`, e ficam registradas em `reports` e no
     `audit_log`. Mensagens para clientes continuam só pelo `ActionGateway`.
5. **Segurança.**
   - Segredos só no `.env` (nunca no código, nunca no git). Manter
     `.env.example` atualizado e comentado.
   - Login com senha no painel (hash Argon2, sessão em cookie seguro, CSRF).
   - Tokens OAuth guardados **criptografados** no banco (Fernet, chave no
     `.env`) e renovados automaticamente antes de expirar.
   - Nunca registrar tokens, senhas ou dados pessoais completos nos logs.
6. **Auditoria.** Tudo que um agente faz gera registro em `audit_log`: o quê,
   quando, qual pedido/anúncio/pergunta, qual agente, se foi aprovado, por quem
   e o resultado. O `audit_log` é só de inserção (nunca update/delete).
7. **Custos de IA sob controle.**
   - Usar a Claude API com o **modelo mais barato que resolve a tarefa**
     (configurável por tarefa em `app/ai/models.py`; padrão: Haiku para
     classificação e respostas curtas, Sonnet só para geração de anúncios e
     análises semanais).
   - Limitar o contexto enviado (histórico curto, só os campos necessários da
     ficha do produto).
   - Cache de respostas repetidas (`ai_cache`) e uso de prompt caching da API.
   - Registrar tokens e custo estimado de cada chamada (`ai_usage`) e mostrar o
     gasto do dia no painel. Respeitar o teto diário `AI_DAILY_BUDGET_BRL`.
8. **Limites das APIs.** Cada integração tem limitador de taxa próprio,
   tentativas automáticas com espera exponencial (só para erros temporários) e
   alerta no painel quando a integração cai.
9. **Código.** Simples, organizado por módulos, **comentários e docstrings em
   português**, nomes de variáveis/funções em inglês. Cada módulo novo vem com
   testes. README explica instalação, configuração e uso.

## Fluxo de trabalho

- O projeto é construído **uma fase por vez** (ver `docs/ARQUITETURA.md`, seção
  "Fases"). Ao fim de cada fase: parar, explicar como testar e esperar a
  confirmação do usuário antes de seguir.
- Não adiantar funcionalidades de fases futuras.
- Toda mudança no banco vem com migração Alembic.
- Testes nunca fazem chamadas de rede reais (usar clientes falsos ou `respx`).
- O servidor usa PostgreSQL (`docker-compose.yml`, guia em `docs/IMPLANTACAO.md`):
  mudança no banco deve passar também com `TEST_POSTGRES_URL=postgresql+psycopg://...`
  (os testes recriam as tabelas desse banco — use um banco só de teste).

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic Settings.
- Painel: templates Jinja2 + HTMX + CSS próprio (sem build de frontend).
- Agendador: APScheduler em um processo `worker` separado do `web`.
- Banco: PostgreSQL em produção, SQLite em desenvolvimento.
- HTTP: httpx; tentativas: tenacity.
- Testes: pytest, pytest-asyncio, respx.
- Implantação: Docker Compose (web, worker, postgres, caddy com HTTPS).

## Comandos

O usuário trabalha no **Windows** (sem `make`). Os comandos oficiais são os do
`tarefas.py`, que funciona em qualquer sistema; o `Makefile` é só um atalho
equivalente para Mac/Linux. Ao criar um comando novo, adicione nos dois.

```bash
python tarefas.py install    # cria o .venv e instala as dependências
python tarefas.py setup      # cria o .env com chaves novas (não sobrescreve)
python tarefas.py migrate    # aplica as migrações do banco
python tarefas.py user       # cria um usuário do painel (ou redefine a senha)
python tarefas.py seed-demo  # recria os dados de simulação (só com DRY_RUN=true)
python tarefas.py dev        # sobe o painel em http://localhost:8000
python tarefas.py worker     # sobe o agendador dos agentes
python tarefas.py test       # roda os testes
python tarefas.py diagnostico  # situação dos envios do Mercado Livre (sem dados de clientes)
python tarefas.py diagnostico-recebimentos  # de onde prever os recebimentos (sem dados de clientes)
python tarefas.py diagnostico-precos  # referências de preço do ML para os anúncios (só leitura)
python tarefas.py backup     # cópia de segurança do banco (data/backups/)
python tarefas.py atualizar  # git pull + dependências novas + cópia do banco + migrações
python tarefas.py iniciar    # atualizar + liga o painel + abre o navegador; depois se atualiza sozinho (git a cada minuto)
```

O usuário liga o painel com dois cliques em `Iniciar painel.bat`, que só chama
`tarefas.py iniciar`. **Não altere o .bat** (o Windows lê .bat enquanto
executa, e o `git pull` poderia trocá-lo no meio): coloque a lógica no
`tarefas.py`. No Mac, o equivalente é `Iniciar painel.command` (mesma regra:
só chama `tarefas.py iniciar`; o conteúdo fica entre `{ }` para o bash ler tudo
antes de rodar). Toda fase nova deve funcionar só com esse fluxo (migrações
automáticas, nada manual).

## Convenções de código

- Dinheiro sempre em centavos (inteiro, colunas `*_cents`); formatar com
  `app/core/money.py`.
- Datas no banco em UTC sem fuso (`utcnow()`); "hoje" é calculado no fuso da
  loja com `app/core/timeutil.py`.
- Registros da simulação têm `is_demo=true`.
- **Multiempresa:** tabela nova de dados herda `CompanyScoped`
  (`app/models/company.py`) e unicidades incluem `company_id`. O filtro por
  empresa é automático (`app/core/tenant.py`); nunca contorne com
  `all_companies=True` sem necessidade. Ver `docs/ARQUITETURA.md` §28.
- Integrações ficam em `app/integrations/<nome>/` (auth, client, mapping,
  dates); a leitura API → banco fica em `app/sync/`. Testes de integração usam
  APIs simuladas com `respx` (ex.: `tests/olist_fake.py`).
- Olist: token de acesso vale 4 h e refresh token **1 dia** — a sincronização
  periódica renova; passou disso, a pessoa precisa clicar em "Conectar de novo".
- Clientes de API herdam de `ReadOnlyApiClient` (`app/integrations/http.py`),
  que só faz GET. Métodos públicos só `get_*`/`iter_*`. Escrita em marketplace
  fica em módulo separado, importado só pelo `ActionGateway`.
- Mercado Livre e Shopee exigem redirect HTTPS público: usamos a página de
  repasse do repositório `alyssonov/painel-retorno` (GitHub Pages).
- Toda rota POST usa a dependência `verify_csrf`; toda página usa
  `current_user`, que também confere a permissão pela tabela `ROUTE_RULES` de
  `app/core/permissions.py` — rota nova: acrescente a regra lá (sem regra, exige
  "ver o painel"). Nos templates, `can("ver_financeiro")` esconde o que a pessoa
  não pode ver (esconder não substitui a conferência no servidor).
- **Assistente de IA** (`app/ai/assistant.py`, `app/ai/assistant_tools.py`): as
  consultas dele são SÓ LEITURA e respeitam as permissões de quem pergunta;
  dado novo para o assistente = nova consulta lá (com teste). Nunca dar a ele
  ferramenta que escreva ou fale com cliente.
- **Busca:** toda caixa de busca usa `app/services/search.py` (palavras em
  qualquer ordem, sem acento) — nunca `LIKE '%frase inteira%'`.
- Sem scripts/estilos inline nem de terceiros (CSP `'self'`): JS e CSS ficam
  em `app/web/static/`.

## Visual do painel

- Identidade visual (decisão de 26/11/2026, etapa 2): **tema escuro** como
  padrão (estrutura da referência "Qo_Shop" + cores da referência "Pi ERP") e
  **tema claro opcional** (botão sol/lua no topo; rota `POST /tema`, cookie
  `tema`). Fundo `--bg #0A101C`, cards `--card #111A2B` com borda `--line`,
  destaque azul `--accent #3B82F6`, cantos 12px (cards) / 8px (botões), fonte do
  sistema (Segoe UI no Windows). Todas as cores são variáveis em
  `:root, :root[data-theme="dark"]` e `:root[data-theme="light"]` no
  `app/web/static/css/app.css` — **nunca usar cor fixa** fora delas.
- Precificação: calculadora "Saia do Prejuízo" (do dono), em
  `app/web/static/js/calculadora.js` — ver `docs/ARQUITETURA.md` §34.
- Menu lateral em **grupos** (`NAV_GROUPS` em `app/web/templating.py`): Visão
  geral; Operação (Pedidos, Produtos, Anúncios, Estoque, Compras, Precificação); Atendimento
  (Perguntas, Pós-venda); Financeiro (Lucro real, Recebimentos, DRE do mês,
  Despesas, Gráficos, Curva ABC); Inteligência (Assistente, Agentes, Automações, Relatórios); Administração (Equipe, Contas
  conectadas). O menu só mostra o que a pessoa pode abrir. O item ativo é o endereço mais
  específico (`active_nav`). O status das contas fica só na página Contas
  conectadas (sem quadro no pé do menu — decisão do dono em 26/11/2026), em
  cartões compactos que cabem na tela sem rolar.
- **Cores de status, com significado fixo** (variáveis `--ok`, `--warn`,
  `--danger`, `--info` e versões `-soft`): verde = tudo certo; âmbar = atenção
  / precisa de você; vermelho = problema / urgente; azul = informação / em
  andamento. Usar pouco, só para sinalizar; sempre junto com texto ou ícone
  (nunca só a cor); texto colorido com contraste ≥ 4,5:1 nos dois temas. Cores ficam como
  variáveis em `:root` no `app/web/static/css/app.css`.
- Visão geral: saudação; 4 cards de hoje (faturamento, margem média real,
  perguntas respondidas, reputação, com faixa superior na cor da situação e
  variação em relação a ontem); faixa "O que pede atenção hoje"; 4 cards
  "Este mês" (faturamento, lucro real, pedidos, ticket médio, comparados com o
  mesmo período do mês passado — `app/services/month.py`); gráfico de
  faturamento dos últimos 30 dias + "Por marketplace"; "Agentes agora"; kanban **"Envios de hoje"** (A separar, Embalado, Enviado,
  Com problema) com filtro Todos / Mercado Livre / Shopee. Regra do dono:
  **"Faturamento hoje" = o que foi VENDIDO hoje**; o quadro = o que **precisa
  ser DESPACHADO hoje** (prazo de despacho/SLA do marketplace, tabela
  `shipments`) + atrasados; Full fica de fora. Sem conta real (simulação), o
  quadro mostra os pedidos fictícios do dia.
- Linguagem do painel: português simples, sem jargão técnico.
