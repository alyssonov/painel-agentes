"""Cópia de segurança do banco SQLite antes de atualizar a estrutura (migrações).

Só para SQLite (o banco local do computador da loja). Em produção (PostgreSQL),
o backup é do servidor. Usa a API de backup do SQLite: a cópia fica consistente
mesmo se o painel estiver aberto. Guarda as últimas KEEP cópias.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
KEEP = 10


def sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///") or url.endswith(":memory:"):
        return None
    path = Path(url.split("///", 1)[1])
    return path if path.is_absolute() else ROOT / path


def pending_migrations(db_file: Path) -> str | None:
    """Versão nova que será aplicada, ou None se o banco já está atualizado (ou não existe)."""
    if not db_file.exists():
        return None
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with sqlite3.connect(db_file) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
    current = row[0] if row else None
    return head if current and current != head else None


def backup_before_migrate(url: str | None = None, *, always: bool = False) -> Path | None:
    """Copia o banco se houver migração pendente (ou sempre, com always=True)."""
    db_file = sqlite_path(url or get_settings().database_url)
    if db_file is None or not db_file.exists():
        return None
    head = pending_migrations(db_file)
    if head is None and not always:
        return None
    folder = db_file.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    label = f"antes-da-versao-{head}" if head else "manual"
    target = folder / f"{db_file.stem}-{datetime.now():%Y%m%d-%H%M%S}-{label}.db"
    with sqlite3.connect(db_file) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    for old in sorted(folder.glob(f"{db_file.stem}-*.db"))[:-KEEP]:
        old.unlink()
    return target
