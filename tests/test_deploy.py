"""Arquivos do servidor (Etapa 7): conferências que protegem segredos e dados."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_segredos_e_dados_nao_vao_para_a_imagem():
    ignored = {line.strip() for line in _read(".dockerignore").splitlines()}
    assert {".env", "data", ".git", ".venv"} <= ignored


def test_servidor_usa_postgres_https_e_cookie_seguro():
    compose = _read("docker-compose.yml")
    assert "postgresql+psycopg://" in compose
    assert 'SESSION_COOKIE_SECURE: "true"' in compose and "APP_ENV: production" in compose
    assert "${POSTGRES_PASSWORD:?" in compose and "${DOMAIN:?" in compose  # sem senha/domínio, nem liga
    assert "5432:" not in compose  # o banco nunca fica exposto na internet
    assert "reverse_proxy web:8000" in _read("deploy/Caddyfile")


def test_imagem_nao_roda_como_root_e_aplica_migracoes():
    dockerfile = _read("Dockerfile")
    assert "USER painel" in dockerfile and "alembic upgrade head" in dockerfile


def test_atualizacao_faz_copia_do_banco_antes_de_religar():
    script = _read("deploy/atualizar.sh")
    assert script.index("pg_dump") < script.index("docker compose up")
    assert "--ff-only" in script


def test_instalador_nao_perde_o_agendamento_em_servidor_novo():
    # Num servidor novo não há agendamento: o grep não acha nada e não pode derrubar o script.
    assert "grep -v 'deploy/atualizar.sh' || true" in _read("deploy/instalar.sh")
