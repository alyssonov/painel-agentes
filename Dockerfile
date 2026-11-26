# Imagem do Gerente do Ano para o servidor (Etapa 7). A mesma imagem roda o
# painel (web) e o agendador (worker) — ver docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home --uid 1000 painel

# Dependências primeiro (a camada só refaz quando o pyproject.toml muda).
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py && pip install . && pip uninstall -y painel-agentes \
    && rm -rf app build *.egg-info

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN mkdir -p data && chown -R painel:painel /app
USER painel

EXPOSE 8000
# Aplica as migrações e liga o painel atrás do Caddy (que cuida do HTTPS).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
