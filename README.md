# Painel de Agentes — Mercado Livre + Shopee + Olist ERP

Painel web onde agentes de IA ajudam no dia a dia da loja: responder
perguntas, vigiar estoque, conferir notas, calcular a margem real, enviar
relatórios e mais. O **Olist ERP é a fonte da verdade**: o sistema só lê do
Olist e nunca mexe em estoque ou nota fiscal.

> **Situação atual: Fase 3 (em andamento).** Login, Visão geral, modo
> simulação, Olist ERP (somente leitura), Mercado Livre e o **Respondedor de
> perguntas** com aprovação manual. A Shopee vem a seguir.
> Veja `docs/ARQUITETURA.md`.

## Requisitos

- Python 3.12 ou mais novo. No Windows, ao instalar pelo site python.org, marque
  **"Add python.exe to PATH"**.
- (Produção, Fase 8) Docker e um VPS

## Comandos

Todos os comandos são rodados no terminal, **dentro da pasta do projeto**, com
`python tarefas.py <comando>`. Funciona no Windows, Mac e Linux.

> No Windows, se `python` não for reconhecido, use `py` no lugar
> (ex.: `py tarefas.py install`). Quem tem `make` (Mac/Linux) pode usar
> `make <comando>`, que faz a mesma coisa.

## Instalação (primeira vez)

```bash
python tarefas.py install
```

Cria a pasta `.venv` e instala as dependências (demora alguns minutos).

```bash
python tarefas.py setup
```

Cria o arquivo `.env` com chaves de segurança novas. **Guarde uma cópia segura
do `.env`**, principalmente da `ENCRYPTION_KEY`: sem ela, os tokens das contas
conectadas não podem ser lidos e será preciso reconectar.

```bash
python tarefas.py migrate
```

Cria o banco de dados (arquivo `data/painel.db`).

```bash
python tarefas.py user
```

Pede e-mail, nome e senha (mínimo 10 caracteres, com letras e números). A
senha não aparece enquanto você digita, é normal. Rode de novo com o mesmo
e-mail para trocar a senha; isso também desconecta as sessões abertas.

```bash
python tarefas.py seed-demo
```

Carrega a loja fictícia (produtos, pedidos de hoje, perguntas, alertas).

## Uso (dia a dia) — jeito fácil

No Windows, dê **dois cliques em `Iniciar painel.bat`** (na pasta do projeto).
Ele faz tudo sozinho:

1. busca a versão mais nova no GitHub (se não tiver internet, segue com a atual);
2. instala dependências novas, se houver;
3. atualiza o banco de dados;
4. liga o painel e abre o navegador em http://127.0.0.1:8000.

Deixe a janela preta aberta enquanto usa o painel. Para desligar, feche a
janela (ou `Ctrl+C`).

Dica: clique com o botão direito em `Iniciar painel.bat` → **Mostrar mais
opções** → **Enviar para** → **Área de trabalho (criar atalho)**. Assim o
painel liga direto da área de trabalho.

Em qualquer sistema, o mesmo pode ser feito com:

```bash
python tarefas.py iniciar
```

## Uso (modo desenvolvimento)

```bash
python tarefas.py dev
```

Abra http://localhost:8000 no navegador e entre com o usuário criado. Para
parar o painel, aperte `Ctrl+C` no terminal.

Em outro terminal (opcional na Fase 1):

```bash
python tarefas.py worker
```

Na Fase 1 o worker só renova os dados de simulação quando o dia vira. O próprio
painel também faz isso ao iniciar.

## Conectar o Olist ERP (somente leitura)

1. No Olist: **Menu > Configurações > aba Geral > Aplicativos > + novo
   aplicativo**. Na URL de redirecionamento, coloque
   `http://localhost:8000/integracoes/olist/retorno`. Nas permissões, marque
   só o necessário (pedidos, produtos, estoque, notas fiscais). Salve e copie
   o Client ID e o Client Secret.
2. No arquivo `.env` (abra com `notepad .env` no terminal, na pasta do
   projeto), preencha `OLIST_CLIENT_ID` e `OLIST_CLIENT_SECRET`. **Nunca**
   envie essas chaves por chat ou e-mail.
3. Ligue o painel e clique em **Contas conectadas** (canto inferior esquerdo)
   → **Conectar o Olist**. Entre com o login do Olist e autorize.
4. A importação começa sozinha (pedidos dos últimos 15 dias, produtos,
   estoque e notas). Depois, o painel busca novidades a cada 10 minutos
   enquanto estiver ligado.

Bom saber:
- A autorização do Olist vale **1 dia sem uso**. Se o painel ficar desligado
  por mais tempo, aparece um alerta: é só clicar em **Conectar de novo**.
- Ao conectar, a loja fictícia da simulação é apagada.
- Em **Contas conectadas** também fica a **alíquota de impostos** usada na
  margem real (informada pela contabilidade).

## Conectar o Mercado Livre

1. Em developers.mercadolivre.com.br/devcenter (com a conta **da loja**), o
   aplicativo deve ter:
   - URI de redirect: `https://alyssonov.github.io/painel-retorno/mercadolivre/`
     (o Mercado Livre exige HTTPS público; essa página, no repositório
     `alyssonov/painel-retorno`, só repassa o login para o painel local);
   - fluxos **Authorization Code** e **Refresh Token**, com **PKCE**;
   - permissões: Usuários (leitura), Comunicações pré e pós-venda (leitura e
     escrita), Publicação (leitura), Métricas (leitura), Venda e envios
     (leitura); o resto sem acesso. Sem tópicos de notificação.
2. No `.env`, preencha `ML_CLIENT_ID` e `ML_CLIENT_SECRET`.
3. Em **Contas conectadas**, clique em **Conectar o Mercado Livre** e autorize.

O painel passa a ler, a cada 5 minutos: perguntas, reputação e a taxa de cada
venda (que entra na margem real). Nesta etapa **nada é respondido**.

## Respondedor de perguntas (Agente 1)

1. Crie uma chave em console.anthropic.com (organização da empresa, cartão em
   Billing e **limite mensal de gasto**) e coloque no `.env`:
   `ANTHROPIC_API_KEY=...`.
2. Em **Agentes**, clique em **Ativar** no Respondedor de perguntas e ajuste
   as **Regras das respostas** (nome da loja, tom, prazo de envio, garantia).
3. A cada 3 minutos ele lê as perguntas novas e sugere uma resposta usando a
   ficha do anúncio, o estoque do Olist e as regras da loja.
4. Em **Perguntas**, você **Aprova**, **Edita** ou **Responde você mesmo**.
   Pedidos de desconto/negociação, confiança baixa ou assuntos fora da ficha
   ficam marcados como **Precisa de você**.
5. Modo **Automático** (opcional, em Agentes): só respostas com confiança
   **alta** saem sozinhas; o resto continua esperando você.

Custo: usa o modelo mais barato (Claude Haiku), com cache de perguntas
repetidas e teto diário (`AI_DAILY_BUDGET_BRL`). O gasto estimado do dia
aparece na Visão geral e em Agentes. Com `DRY_RUN=true`, as respostas
aprovadas **não são enviadas** (ficam marcadas como simulação).

## Modo simulação (`DRY_RUN`)

- Ligado por padrão (`DRY_RUN=true` no `.env`).
- O painel mostra uma faixa amarela **MODO SIMULAÇÃO** no topo.
- **Nenhuma** ação real é enviada a clientes ou lojas.
- Sem conta conectada, mostra uma loja fictícia. Com o Olist conectado,
  mostra os dados reais (só leitura) e a faixa avisa isso.
- Os dados fictícios ficam marcados (`is_demo`) e a simulação se recusa a
  rodar se o `DRY_RUN` estiver desligado ou se houver uma conta real
  conectada.
- Só desligue quando o dono da loja decidir, editando o `.env`.

## Configuração (`.env`)

Todas as opções estão comentadas em `.env.example`. As principais:

| Variável | Para que serve |
|---|---|
| `DRY_RUN` | Modo simulação (padrão `true`) |
| `SECRET_KEY` | Assina o cookie de login |
| `ENCRYPTION_KEY` | Criptografa os tokens das contas conectadas |
| `SESSION_COOKIE_SECURE` | `true` em produção (HTTPS) |
| `DATABASE_URL` | SQLite no desenvolvimento, PostgreSQL em produção |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | Bloqueio após senhas erradas |
| `AI_DAILY_BUDGET_BRL` | Teto de gasto diário com IA |

O `.env` **nunca** vai para o git (está no `.gitignore`).

## Testes

```bash
python tarefas.py test
```

Os testes usam bancos temporários e nunca acessam a internet.

## Segurança (o que já existe)

- Senhas com hash Argon2, bloqueio temporário após 5 tentativas erradas,
  mensagem de erro que não revela se o e-mail existe.
- Sessão em cookie assinado, `HttpOnly`, `SameSite=Lax` (e `Secure` em
  produção), expira em 12 horas; trocar a senha derruba as sessões antigas.
- Proteção CSRF em todos os formulários.
- Cabeçalhos de segurança (CSP restrita, sem iframes, sem cache de páginas com
  dados).
- Tokens OAuth guardados criptografados (usado a partir da Fase 2).
- Registro de auditoria só de inserção: logins, criação de usuário, carga da
  simulação. Tentativas de alterar ou apagar são bloqueadas.

## Estrutura

```
app/
  config.py         configurações (.env)
  main.py           painel web (FastAPI)
  worker.py         agendador dos agentes
  cli.py            comandos de administração
  core/             banco, segurança, auditoria, datas, dinheiro
  models/           tabelas do banco
  services/         regras de negócio (visão geral, margem, regras da loja)
  agents/           agentes (registro; lógica a partir da Fase 3)
  fake_data/        dados de simulação
  web/              rotas, templates, CSS, htmx
alembic/            migrações do banco
tests/              testes automáticos
docs/               arquitetura e (na Fase 8) guia de implantação
tarefas.py          atalhos dos comandos (substitui o make)
```
