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
  diagnostico mostra a situação dos envios do Mercado Livre (sem dados de clientes)
  diagnostico-recebimentos  de onde dá para prever os recebimentos (sem dados de clientes)
  diagnostico-precos  referências de preço do Mercado Livre para os anúncios (só leitura)
  backup     cópia de segurança do banco (feita sozinha antes de cada atualização)
  atualizar  busca a versão nova no GitHub, instala dependências e atualiza o banco
  iniciar    atualiza, liga o painel + worker e abre o navegador (é o que o "Iniciar painel.bat" usa);
             depois, enquanto estiver ligado, se atualiza sozinho quando sai versão nova
"""

import hashlib
import os
import shutil
import socket
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
        # GIT_ASK_YESNO=false: no Windows, se o antivírus/OneDrive estiver segurando
        # algum arquivo interno do Git, ele não para mais perguntando "try again? (y/n)".
        git_env = {**os.environ, "GIT_ASK_YESNO": "false"}
        if subprocess.call(["git", "pull", "--ff-only"], cwd=ROOT, env=git_env):
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


# Tempos de atualização que mudaram de padrão (27/11/2026: o mais rápido que as APIs
# aguentam). Só troca no .env quem ainda está com o valor antigo; quem mudou à mão fica.
ENV_UPGRADES = [("OLIST_SYNC_MINUTES", "10", "2"), ("ML_SYNC_MINUTES", "5", "1"),
                ("SHOPEE_SYNC_MINUTES", "10", "2"), ("AGENT_QUESTIONS_MINUTES", "3", "1")]


def _upgrade_env() -> None:
    """Atualiza no .env só as linhas de configuração que ainda têm o padrão antigo."""
    path = ROOT / ".env"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        for key, old, new in ENV_UPGRADES:
            if line.strip() == f"{key}={old}":
                lines[i] = line.replace(f"{key}={old}", f"{key}={new}")
                changed = True
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
        print("Tempos de atualização do .env ajustados para os novos padrões.")


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
    _upgrade_env()
    run(str(VENV_PY), "-m", "app.cli", "backup-db")  # cópia do banco antes de mudar a estrutura
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


# De quanto em quanto tempo o painel ligado confere se há versão nova no GitHub.
AUTO_UPDATE_SECONDS = int(os.environ.get("AUTO_UPDATE_SECONDS", "60"))


def _git(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_ASK_YESNO": "false", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def _git_ok(*args: str) -> bool:
    try:
        return _git(*args).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _new_version_available() -> bool:
    """True se o GitHub tem uma versão nova que dá para puxar sem conflito (só avança)."""
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return False
    try:
        if _git("fetch", "--quiet").returncode:
            return False  # sem internet agora: tenta de novo mais tarde
        head, upstream = _git("rev-parse", "HEAD").stdout.strip(), _git("rev-parse", "@{u}").stdout.strip()
        return bool(upstream) and head != upstream and \
            _git("merge-base", "--is-ancestor", "HEAD", "@{u}").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _launch() -> tuple[subprocess.Popen, subprocess.Popen]:
    """Liga o painel (web) e o worker (agendador), em segundo plano."""
    worker = subprocess.Popen([venv_python(), "-m", "app.worker"], cwd=ROOT)
    web = subprocess.Popen([venv_python(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", "8000"], cwd=ROOT)
    return web, worker


def _stop(*procs: subprocess.Popen) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def serve(open_browser: bool = True) -> int:
    """Mantém painel + worker ligados e se atualiza sozinho quando sai versão nova.

    A cada AUTO_UPDATE_SECONDS confere o GitHub. Havendo novidade: desliga os dois,
    puxa a versão nova, faz a cópia do banco + migrações (`_preparar`) e liga de
    novo. Basta recarregar a página no navegador (F5) para ver a mudança.
    """
    print()
    print("Painel ligando... " + ("O navegador vai abrir sozinho." if open_browser else ""))
    print("Deixe esta janela aberta enquanto usa o painel. Para desligar: Ctrl+C ou feche a janela.")
    print("Quando houver versão nova, o painel se atualiza sozinho (é só recarregar a página).")
    print()
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    my_hash = _file_hash(Path(__file__))
    web, worker = _launch()
    try:
        next_check = time.monotonic() + AUTO_UPDATE_SECONDS
        warned = False
        while True:
            time.sleep(1)
            if web.poll() is not None:  # o painel parou (erro ou Ctrl+C): encerra tudo
                return web.returncode or 0
            if time.monotonic() < next_check:
                continue
            next_check = time.monotonic() + AUTO_UPDATE_SECONDS
            if not _new_version_available():
                continue
            # Baixa primeiro, com o painel ainda ligado: se falhar, nada é desligado.
            if not _git_ok("pull", "--ff-only"):
                if not warned:
                    print("Há versão nova, mas não deu para baixar agora (tentando de novo a cada minuto).")
                    warned = True
                continue
            warned = False
            print()
            print(time.strftime("%H:%M") + " - Versão nova baixada. Atualizando o painel (alguns segundos)...")
            _stop(web, worker)  # desliga antes da cópia do banco e das migrações
            if run(sys.executable, str(ROOT / "tarefas.py"), "_preparar"):
                print("Algo deu errado ao preparar a versão nova (veja acima). "
                      "Se o painel der erro, feche esta janela e abra de novo.")
            if _file_hash(Path(__file__)) != my_hash:
                # O próprio inicializador mudou: continua pela versão nova dele.
                print("Painel atualizado. Recarregue a página no navegador (F5).")
                return run(sys.executable, str(ROOT / "tarefas.py"), "_servir")
            web, worker = _launch()
            print("Painel atualizado. Recarregue a página no navegador (F5).")
            print()
    finally:
        _stop(web, worker)


def _panel_already_running() -> bool:
    """True se já há algo atendendo na porta do painel (outro painel ligado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def _explain_already_running() -> None:
    print()
    print("O painel já está ligado (provavelmente em outra janela do Terminal/Prompt).")
    print(f"Use o que já está aberto em {PANEL_URL}, ou feche a outra janela e abra de novo.")
    print("Se não achar a outra janela, desligue o painel antigo com:")
    if os.name == "nt":
        print("  (no PowerShell) Get-CimInstance Win32_Process | Where-Object CommandLine -match "
              "'uvicorn app.main|app.worker|tarefas.py' | ForEach-Object { Stop-Process -Id $_.ProcessId }")
    else:
        print('  pkill -f "uvicorn app.main:app"; pkill -f "app.worker"; pkill -f "tarefas.py iniciar"')
    print()


def start() -> int:
    if _panel_already_running():
        _explain_already_running()
        return 1
    code = update()
    if code:
        print("Algo deu errado na preparação (veja as mensagens acima).")
        return code
    return serve()


COMMANDS = {
    "atualizar": update,
    "iniciar": start,
    "_preparar": prepare,
    "_servir": lambda: serve(open_browser=False),
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
    "diagnostico": lambda: run(venv_python(), "-m", "app.cli", "diag-envios"),
    "diagnostico-recebimentos": lambda: run(venv_python(), "-m", "app.cli", "diag-recebimentos"),
    "diagnostico-precos": lambda: run(venv_python(), "-m", "app.cli", "diag-precos"),
    "backup": lambda: run(venv_python(), "-m", "app.cli", "backup-db", "--sempre"),
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
