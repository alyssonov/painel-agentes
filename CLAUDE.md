# CLAUDE.md — Painel de Agentes (Mercado Livre + Shopee + Olist ERP)

Este arquivo orienta qualquer sessão do Claude Code neste repositório.
Leia-o inteiro antes de mudar qualquer coisa. A arquitetura detalhada está em
`docs/ARQUITETURA.md`.

## O que é o projeto

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
   - O sistema **nunca altera estoque** e **nunca emite, cancela ou altera notas
     fiscais**. Isso continua sendo feito no Olist.
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
   - Usa clientes falsos com dados fictícios e **nunca** faz chamadas de escrita
     reais. Só é desligado quando o dono decidir, editando o `.env`.
   - O painel mostra uma faixa visível "MODO SIMULAÇÃO" enquanto estiver ligado.
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
python tarefas.py atualizar  # git pull + dependências novas + migrações
python tarefas.py iniciar    # atualizar + liga o painel + abre o navegador
```

O usuário liga o painel com dois cliques em `Iniciar painel.bat`, que só chama
`tarefas.py iniciar`. **Não altere o .bat** (o Windows lê .bat enquanto
executa, e o `git pull` poderia trocá-lo no meio): coloque a lógica no
`tarefas.py`. Toda fase nova deve funcionar só com esse fluxo (migrações
automáticas, nada manual).

## Convenções de código

- Dinheiro sempre em centavos (inteiro, colunas `*_cents`); formatar com
  `app/core/money.py`.
- Datas no banco em UTC sem fuso (`utcnow()`); "hoje" é calculado no fuso da
  loja com `app/core/timeutil.py`.
- Registros da simulação têm `is_demo=true`.
- Toda rota POST usa a dependência `verify_csrf`; toda página usa
  `current_user`.
- Sem scripts/estilos inline nem de terceiros (CSP `'self'`): JS e CSS ficam
  em `app/web/static/`.

## Visual do painel

- Menu lateral: Visão geral, Perguntas, Anúncios, Estoque e preços, Pós-venda,
  Margem real, Relatórios, Agentes. Abaixo do menu: contas conectadas
  (Mercado Livre, Shopee, Olist ERP) com status.
- Fundo verde-claro suave, cards brancos com cantos bem arredondados, destaque
  verde-escuro `#1E3A2A`, fonte Inter.
- Visão geral: 4 cards (faturamento hoje, margem média real, perguntas
  respondidas, reputação); faixa verde-escura "O que pede atenção"; lista
  "Agentes agora"; kanban "Pedidos de hoje" (A separar, Embalado, Enviado,
  Com problema) com filtro Todos / Mercado Livre / Shopee.
- Linguagem do painel: português simples, sem jargão técnico.
