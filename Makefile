# Atalhos do projeto. Use sempre o ambiente virtual em .venv.
PY := .venv/bin/python

.PHONY: install setup keys dev worker test migrate seed seed-demo user atualizar iniciar

install:          ## instala as dependências (inclusive de desenvolvimento)
	python3.12 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

setup:            ## cria o .env com chaves novas (não sobrescreve um .env existente)
	$(PY) -m app.cli init-env

keys:             ## mostra chaves novas para SECRET_KEY e ENCRYPTION_KEY
	$(PY) -m app.cli gen-keys

migrate:          ## aplica as migrações do banco
	$(PY) -m alembic upgrade head

user:             ## cria (ou redefine a senha de) um usuário do painel
	$(PY) -m app.cli create-user

seed-demo:        ## recria os dados de simulação (só funciona com DRY_RUN=true)
	$(PY) -m app.cli seed-demo

seed: migrate user seed-demo   ## migra, cria usuário e carrega dados de simulação

dev:              ## sobe o painel em http://localhost:8000
	$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

worker:           ## sobe o agendador dos agentes
	$(PY) -m app.worker

test:             ## roda os testes
	$(PY) -m pytest

atualizar:        ## busca a versão nova, instala dependências e atualiza o banco
	$(PY) tarefas.py atualizar

iniciar:          ## atualiza, liga o painel e abre o navegador
	$(PY) tarefas.py iniciar
