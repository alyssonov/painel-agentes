"""Separação dos dados por empresa (multiempresa).

Como funciona:
- cada sessão do banco sabe de qual empresa ela é (`session.info["company_id"]`);
  no painel, quem define é o usuário logado (`app/web/auth.py`). Sem definição,
  vale a empresa padrão (a loja de hoje) — o worker e os comandos usam essa;
- toda consulta às tabelas com `CompanyScoped` recebe sozinha o filtro
  "company_id = empresa da sessão" (inclusive relacionamentos e update/delete);
- todo registro novo recebe a empresa da sessão, e gravar um registro de OUTRA
  empresa é bloqueado (erro), para um vazamento nunca passar despercebido.

Para uma rotina que precise ver todas as empresas (ex.: o worker percorrendo as
lojas no futuro), use `.execution_options(all_companies=True)` na consulta.
"""

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.company import DEFAULT_COMPANY_ID, CompanyScoped

INFO_KEY = "company_id"


class CrossCompanyWrite(RuntimeError):
    """Tentativa de gravar um registro de outra empresa nesta sessão."""


def company_of(session: Session) -> int:
    """Empresa da sessão (padrão: a loja instalada)."""
    return int(session.info.get(INFO_KEY) or DEFAULT_COMPANY_ID)


def set_company(session: Session, company_id: int) -> None:
    """Define a empresa da sessão (feito no login de cada requisição)."""
    session.info[INFO_KEY] = int(company_id)


@event.listens_for(Session, "do_orm_execute")
def _filter_by_company(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("all_companies"):
        return
    company_id = company_of(state.session)
    state.statement = state.statement.options(with_loader_criteria(
        CompanyScoped, lambda cls: cls.company_id == company_id, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _stamp_company(session: Session, _flush_context, _instances) -> None:
    company_id = company_of(session)
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, CompanyScoped):
            continue
        if obj.company_id is None:
            obj.company_id = company_id
        elif obj.company_id != company_id:
            raise CrossCompanyWrite(
                f"{type(obj).__name__} da empresa {obj.company_id} gravado numa sessão da empresa {company_id}.")
