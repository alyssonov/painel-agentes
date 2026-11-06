# Painel de Agentes — Mercado Livre + Shopee + Olist ERP

Painel web onde agentes de IA ajudam no dia a dia da loja: responder
perguntas, vigiar estoque, conferir notas, calcular a margem real, enviar
relatórios e mais. O **Olist ERP é a fonte da verdade**: o sistema só lê do
Olist e nunca mexe em estoque ou nota fiscal.

> **Situação atual: Fase 1.** Login, Visão geral e modo simulação com dados
> fictícios. As integrações reais chegam a partir da Fase 2.
> Veja `docs/ARQUITETURA.md`.

## Requisitos

- Python 3.12
- `make`
- (Produção, Fase 8) Docker e um VPS

## Instalação (desenvolvimento)

```bash
make install
```

```bash
make setup
```

`make setup` cria o arquivo `.env` com chaves de segurança novas. **Guarde uma
cópia segura do `.env`**, principalmente da `ENCRYPTION_KEY`: sem ela, os
tokens das contas conectadas não podem ser lidos e será preciso reconectar.

```bash
make migrate
```

```bash
make user
```

`make user` pede e-mail, nome e senha (mínimo 10 caracteres, com letras e
números). Rode de novo com o mesmo e-mail para trocar a senha; isso também
desconecta as sessões abertas desse usuário.

```bash
make seed-demo
```

Carrega a loja fictícia (produtos, pedidos de hoje, perguntas, alertas).

## Uso

```bash
make dev
```

Abra http://localhost:8000 e entre com o usuário criado.

Em outro terminal (opcional na Fase 1):

```bash
make worker
```

Na Fase 1 o worker só renova os dados de simulação quando o dia vira. O próprio
painel também faz isso ao iniciar.

## Modo simulação (`DRY_RUN`)

- Ligado por padrão (`DRY_RUN=true` no `.env`).
- O painel mostra uma faixa amarela **MODO SIMULAÇÃO** no topo.
- Usa dados fictícios e **nenhuma** ação real é enviada a clientes ou lojas.
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
make test
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
```
