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
  atualizar  busca a versão nova no GitHub, instala dependências e atualiza o banco
  iniciar    atualiza, liga o painel + worker e abre o navegador (é o que o "Iniciar painel.bat" usa)
"""

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
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


PANEL_URL = "http://127.0.0.1:8000"


def update() -> int:
    """Busca a versão nova (git pull) e prepara tudo para rodar.

    Se não houver internet ou o git falhar, avisa e segue com a versão atual.
    """
    if (ROOT / ".git").exists() and shutil.which("git"):
        print("Buscando atualizações no GitHub...")
        if run("git", "pull", "--ff-only"):
            print("Não foi possível atualizar agora. Seguindo com a versão atual.")
    else:
        print("Git não encontrado: seguindo com a versão atual.")
    # O git pull pode ter trazido um tarefas.py novo: os próximos passos rodam
    # num processo novo para já usar a versão atualizada.
    return run(sys.executable, str(ROOT / "tarefas.py"), "_preparar")


DEPS_HASH_FILE = VENV / "dependencias.hash"


def _deps_hash() -> str:
    return hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()


def _deps_changed() -> bool:
    """True se o pyproject.toml (lista de dependências) mudou desde a última instalação."""
    try:
        return DEPS_HASH_FILE.read_text().strip() != _deps_hash()
    except OSError:
        return True


def _save_deps_hash() -> None:
    DEPS_HASH_FILE.write_text(_deps_hash())


def prepare() -> int:
    """Instala dependências que faltarem, cria o .env se não existir e atualiza o banco."""
    if not VENV_PY.exists():
        code = install()
        if code:
            return code
    elif _deps_changed():
        print("Instalando dependências novas...")
        code = run(str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", ".[dev]")
        if code:
            return code
    _save_deps_hash()
    if not (ROOT / ".env").exists():
        run(str(VENV_PY), "-m", "app.cli", "init-env")
    print("Atualizando o banco de dados...")
    return run(str(VENV_PY), "-m", "alembic", "upgrade", "head")


def _open_browser_when_ready() -> None:
    """Espera o painel responder e então abre o navegador."""
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{PANEL_URL}/saude", timeout=2)
        except OSError:
            time.sleep(1)
            continue
        webbrowser.open(PANEL_URL)
        return


def start() -> int:
    code = update()
    if code:
        print("Algo deu errado na preparação (veja as mensagens acima).")
        return code
    print()
    print("Painel ligando... O navegador vai abrir sozinho.")
    print("Deixe esta janela aberta enquanto usa o painel. Para desligar: Ctrl+C ou feche a janela.")
    print()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    # O worker (agendador: leitura do Olist e, nas próximas fases, os agentes)
    # roda junto, em segundo plano, e é desligado quando o painel desliga.
    worker = subprocess.Popen([venv_python(), "-m", "app.worker"], cwd=ROOT)
    try:
        return run(venv_python(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()


COMMANDS = {
    "atualizar": update,
    "iniciar": start,
    "_preparar": prepare,
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
