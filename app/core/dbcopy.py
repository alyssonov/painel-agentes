"""Cópia completa do banco: do SQLite do computador da loja para o PostgreSQL do servidor.

Usado uma vez, na mudança para o servidor (Etapa 7). Copia TODAS as tabelas,
linha por linha, sem passar pelo filtro de empresa (usa o SQLAlchemy Core, não o
ORM), e no final acerta os contadores de id do PostgreSQL.

Regras de segurança:
- origem e destino precisam estar na MESMA versão do banco (migrações);
- o destino precisa estar vazio, ou a pessoa confirma que ele pode ser apagado
  (`apagar_destino=True`) — num servidor novo ele só tem a loja de simulação;
- tudo acontece numa transação só: se algo falhar, o destino fica como estava.

Os tokens das contas conectadas vão criptografados: o servidor precisa usar o
MESMO `ENCRYPTION_KEY` do computador para conseguir lê-los.
"""

from sqlalchemy import Engine, create_engine, func, inspect, select, text

from app.core.db import Base

BATCH = 500


class CopyError(RuntimeError):
    """Problema que impede a cópia (mensagem em português, para mostrar à pessoa)."""


def _version(engine: Engine) -> str | None:
    if not inspect(engine).has_table("alembic_version"):
        return None
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _is_int(column) -> bool:
    try:
        return column.type.python_type is int
    except NotImplementedError:
        return False


def copy_database(src_url: str, dst_url: str, *, apagar_destino: bool = False) -> dict[str, int]:
    """Copia todas as tabelas de `src_url` para `dst_url`. Devolve {tabela: linhas copiadas}."""
    import app.models  # noqa: F401 - registra todas as tabelas no metadata

    if src_url == dst_url:
        raise CopyError("A origem e o destino são o mesmo banco.")
    src, dst = create_engine(src_url), create_engine(dst_url)
    try:
        v_src, v_dst = _version(src), _version(dst)
        if v_src != v_dst:
            raise CopyError(f"As versões dos bancos são diferentes (origem {v_src}, destino {v_dst}). "
                            "Atualize os dois antes (migrações) e tente de novo.")
        tables = Base.metadata.sorted_tables  # pais antes dos filhos (chaves estrangeiras)
        missing = [t.name for t in tables if not inspect(src).has_table(t.name)]
        if missing:
            raise CopyError(f"Tabelas que faltam na origem: {', '.join(missing)}.")
        counts: dict[str, int] = {}
        with dst.begin() as out:
            sizes = {t.name: out.execute(select(func.count()).select_from(t)).scalar() for t in tables}
            filled = [name for name, n in sizes.items() if n]
            # Um banco recém-migrado já nasce com a empresa padrão: isso conta como vazio.
            real_data = [name for name in filled if not (name == "companies" and sizes[name] <= 1)]
            if real_data and not apagar_destino:
                raise CopyError("O banco de destino já tem dados (" + ", ".join(real_data[:5]) +
                                "…). Use a opção para apagar o destino se ele for um servidor novo.")
            if filled:
                if dst.dialect.name == "postgresql":
                    names = ", ".join(f'"{t.name}"' for t in tables)
                    out.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
                else:
                    for table in reversed(tables):
                        out.execute(table.delete())
            with src.connect() as inp:
                for table in tables:
                    total = 0
                    result = inp.execution_options(stream_results=True).execute(select(table))
                    while rows := result.mappings().fetchmany(BATCH):
                        out.execute(table.insert(), [dict(r) for r in rows])
                        total += len(rows)
                    counts[table.name] = total
            if dst.dialect.name == "postgresql":
                # Os ids vieram prontos: o contador de cada tabela continua depois do maior.
                for table in tables:
                    pk = list(table.primary_key.columns)
                    if len(pk) == 1 and _is_int(pk[0]):
                        out.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{pk[0].name}'), "
                            f"COALESCE(MAX(\"{pk[0].name}\"), 1), MAX(\"{pk[0].name}\") IS NOT NULL) "
                            f"FROM \"{table.name}\""))
        return counts
    finally:
        src.dispose()
        dst.dispose()
