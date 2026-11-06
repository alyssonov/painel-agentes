"""Atalhos do projeto que funcionam no Windows, Mac e Linux (substitui o `make`).

Uso:  python tarefas.py <comando>

Comandos:
  install    cria o ambiente virtual (.venv) e instala as dependências
  setup      cria o .env com chaves novas (não sobrescreve um .env existente)
  keys       mostra chaves novas para SECRET_KEY e ENCRYPTION_KEY
  migrate    aplica as migrações do banco
  user       cria um usuário do painel (ou redefine a senha)
  seed-demo  recria os dados de simulação (só com DRY_RUN=true)
  dev        sobe o painel em http://localhost:8000
  worker     sobe o agendador dos agentes
  test       roda os testes
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
# No Windows o Python do ambiente virtual fica em Scripts\python.exe.
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args: str) -> int:
    """Roda um comando dentro da pasta do projeto e devolve o código de saída."""
    return subprocess.call(list(args), cwd=ROOT)


def venv_python() -> str:
    if not VENV_PY.exists():
        print("Ambiente ainda não instalado. Rode primeiro:  python tarefas.py install")
        sys.exit(1)
    return str(VENV_PY)


def install() -> int:
    if sys.version_info < (3, 12):
        print(f"É preciso Python 3.12 ou mais novo (este é {sys.version.split()[0]}).")
        return 1
    if not VENV_PY.exists():
        code = run(sys.executable, "-m", "venv", str(VENV))
        if code:
            return code
    run(str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip")
    return run(str(VENV_PY), "-m", "pip", "install", "-e", ".[dev]")


COMMANDS = {
    "install": install,
    "setup": lambda: run(venv_python(), "-m", "app.cli", "init-env"),
    "keys": lambda: run(venv_python(), "-m", "app.cli", "gen-keys"),
    "migrate": lambda: run(venv_python(), "-m", "alembic", "upgrade", "head"),
    "user": lambda: run(venv_python(), "-m", "app.cli", "create-user"),
    "seed-demo": lambda: run(venv_python(), "-m", "app.cli", "seed-demo"),
    "dev": lambda: run(venv_python(), "-m", "uvicorn", "app.main:app", "--reload",
                       "--host", "127.0.0.1", "--port", "8000"),
    "worker": lambda: run(venv_python(), "-m", "app.worker"),
    "test": lambda: run(venv_python(), "-m", "pytest"),
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    try:
        return COMMANDS[sys.argv[1]]()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
