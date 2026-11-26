"""Histórico: tudo que pessoas, agentes e o sistema fizeram (lê o audit_log).

Só leitura. Mostra o valor anterior e o novo quando a alteração registrou
"antes/depois" (ver `audit.changes`). Filtra por período, por quem fez, por tipo
de acontecimento e por registro (ex.: uma pergunta, uma pessoa, um agente).
"""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.tenant import company_of
from app.core.timeutil import to_local
from app.models import Agent, AuditLog, Product
from app.models.user import User
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter(prefix="/historico")
PAGE = 50
PERIODS = {"1": "Hoje e ontem", "7": "7 dias", "30": "30 dias", "90": "90 dias", "365": "1 ano"}

# Grupos de acontecimentos para o filtro (prefixos do nome do evento).
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "acessos": ("Entradas no painel", ("login.", "logout")),
    "equipe": ("Equipe", ("equipe.", "usuario.")),
    "acoes": ("Respostas, preços e anúncios", ("acao.",)),
    "agentes": ("Agentes", ("agente.",)),
    "config": ("Configurações da loja", ("loja.", "precificacao.", "relatorio.destinatarios")),
    "produtos": ("Cadastro de produtos", ("produto.",)),
    "financeiro": ("Despesas", ("financeiro.",)),
    "contas": ("Contas conectadas", ("olist.", "ml.", "shopee.", "onix.")),
    "relatorios": ("Relatórios enviados", ("relatorio.",)),
}

EVENT_LABELS = {
    "login.ok": "Entrou no painel", "login.falhou": "Tentativa de entrada recusada",
    "login.bloqueado": "Entrada bloqueada (muitas tentativas)", "logout": "Saiu do painel",
    "equipe.pessoa_adicionada": "Adicionou uma pessoa", "equipe.pessoa_alterada": "Alterou uma pessoa",
    "equipe.senha_redefinida": "Redefiniu a senha", "usuario.criado": "Pessoa criada (pelo instalador)",
    "usuario.senha_redefinida": "Senha redefinida (pelo instalador)",
    "acao.proposta": "Sugeriu uma ação", "acao.aprovada": "Aprovou", "acao.aprovada_editada": "Aprovou com edição",
    "acao.descartada": "Descartou a sugestão", "acao.executada": "Ação executada",
    "acao.simulada": "Ação simulada (modo simulação)", "acao.falhou": "Ação falhou",
    "agente.ativado": "Ativou o agente", "agente.pausado": "Pausou o agente",
    "agente.modo_alterado": "Mudou o modo do agente", "agente.regras_alteradas": "Mudou as regras do agente",
    "loja.regras_alteradas": "Mudou as regras da loja", "loja.aliquota_alterada": "Mudou a alíquota de impostos",
    "precificacao.taxas_alteradas": "Mudou a tabela de taxas", "relatorio.destinatarios": "Mudou quem recebe o relatório",
    "simulacao.carregada": "Carregou a loja de simulação",
    "financeiro.despesa_criada": "Lançou uma despesa", "financeiro.despesa_alterada": "Alterou uma despesa",
    "financeiro.despesa_excluida": "Excluiu uma despesa",
    "produto.cadastro_alterado": "Mudou o cadastro do produto", "produto.planilha_importada": "Enviou planilha de produtos",
}
PROVIDERS = {"olist": "Olist", "ml": "Mercado Livre", "shopee": "Shopee", "onix": "Onix"}
PROVIDER_EVENTS = {"conectado": "conectado", "desconectado": "desconectado", "conexao.falhou": "conexão falhou",
                   "conexao.recusada": "conexão recusada", "sincronizacao.manual": "atualização pedida",
                   "sincronizacao.ok": "atualizado", "sincronizacao.erro": "atualização falhou"}
TARGETS = {"pergunta": "Pergunta", "conversa": "Conversa", "anuncio": "Anúncio", "anuncio_texto": "Texto do anúncio",
           "agente": "Agente", "integracao": "Conta", "user": "Pessoa", "relatorio": "Relatório",
           "sync_run": "Atualização", "pedido": "Pedido", "produto": "Produto",
           "despesa": "Despesa"}
FIELD_LABELS = {"ativo": "Ativo", "modo": "Modo", "nome": "Nome", "papel": "Papel", "permissoes": "Permissões",
                "aliquota_pct": "Alíquota (%)", "destinatarios": "Destinatários", "text": "Texto",
                "texto": "Texto", "price_cents": "Preço (centavos)", "custo_onix": "Custo do Onix",
                "estoque_minimo": "Estoque mínimo", "prazo_dias": "Prazo do fornecedor (dias)",
                "localizacao": "Local", "compatibilidade": "Compatível com", "observacoes": "Observações",
                "fornecedor": "Fornecedor", "codigo_fornecedor": "Código no fornecedor", "valor": "Valor",
                "categoria": "Categoria", "repete": "Repete", "desde": "Desde", "ate": "Até"}


def event_label(event: str) -> str:
    if event in EVENT_LABELS:
        return EVENT_LABELS[event]
    provider, _, rest = event.partition(".")
    if provider in PROVIDERS:
        return f"{PROVIDERS[provider]}: {PROVIDER_EVENTS.get(rest, rest.replace('.', ' '))}"
    if event.startswith("relatorio."):
        return f"Relatório {event.split('.', 1)[1]}"
    return event.replace(".", " · ").replace("_", " ")


def _value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "sim" if value else "não"
    if isinstance(value, list):
        return ", ".join(_value(v) for v in value) or "—"
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_value(v)}" for k, v in value.items())
    text = str(value)
    return text if len(text) <= 300 else text[:300] + "…"


def describe(details: dict) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """(mudanças "campo, antes, depois", outros detalhes "campo, valor")."""
    details = details or {}
    before, after = details.get("antes"), details.get("depois")
    changes = []
    if isinstance(after, dict):
        for key in dict.fromkeys([*(before or {}), *after]):
            changes.append((FIELD_LABELS.get(key, key.replace("_", " ")),
                            _value((before or {}).get(key)) if before is not None else "—", _value(after.get(key))))
    extra = [(FIELD_LABELS.get(k, k.replace("_", " ")), _value(v)) for k, v in details.items()
             if k not in ("antes", "depois")]
    return changes, extra


@router.get("")
def history_page(request: Request, dias: str = "30", quem: str = "", tipo: str = "", registro: str = "",
                 pagina: int = 1, db: Session = Depends(get_db), user: User = Depends(current_user)):
    dias = dias if dias in PERIODS else "30"
    stmt = select(AuditLog).where(AuditLog.at >= utcnow() - timedelta(days=int(dias)))
    if quem == "agentes":
        stmt = stmt.where(AuditLog.actor.like("agent:%"))
    elif quem == "sistema":
        stmt = stmt.where(AuditLog.actor == "system")
    elif quem.startswith("user:"):
        stmt = stmt.where(AuditLog.actor == quem)
    if tipo in GROUPS:
        stmt = stmt.where(or_(*[AuditLog.event.startswith(p) for p in GROUPS[tipo][1]]))
    target_type, _, target_id = registro.partition(":")
    if target_type in TARGETS and target_id:
        stmt = stmt.where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pagina = max(1, pagina)
    rows = list(db.scalars(stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc())
                           .offset((pagina - 1) * PAGE).limit(PAGE)))

    people = {u.id: u.name for u in db.scalars(select(User).where(User.company_id == company_of(db)))}
    agents = {a.slug: a.name for a in db.scalars(select(Agent))}
    product_ids = {int(r.target_id) for r in rows if r.target_type == "produto" and str(r.target_id).isdigit()}
    if target_type == "produto" and target_id.isdigit():
        product_ids.add(int(target_id))
    products = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))} \
        if product_ids else {}

    def target_name(kind: str, ref: str) -> str:
        if kind == "user" and ref.isdigit():
            return people.get(int(ref), ref)
        if kind == "produto" and ref.isdigit():
            return products.get(int(ref), ref)
        return agents.get(ref, ref)

    def who(actor: str) -> tuple[str, str]:
        kind, _, ref = actor.partition(":")
        if kind == "user" and ref.isdigit():
            return people.get(int(ref), f"Pessoa nº {ref}"), "pessoa"
        if kind == "agent":
            return agents.get(ref, ref), "agente"
        return "Sistema", "sistema"

    entries = []
    for row in rows:
        name, kind = who(row.actor)
        changes, extra = describe(row.details)
        target = None
        if row.target_type:
            label = TARGETS.get(row.target_type, row.target_type)
            target = {"label": f"{label}: {target_name(row.target_type, str(row.target_id))}", "filter": f"{row.target_type}:{row.target_id}"}
        entries.append({"at": to_local(row.at).strftime("%d/%m/%Y %H:%M"), "who": name, "kind": kind,
                        "event": event_label(row.event), "target": target, "changes": changes, "extra": extra,
                        "simulated": row.dry_run and row.event.startswith("acao.")})
    registro_label = None
    if target_type in TARGETS and target_id:
        registro_label = f"{TARGETS[target_type]}: {target_name(target_type, target_id)}"
    return render(request, "history.html", {
        "entries": entries, "total": total, "page": pagina, "pages": max(1, -(-total // PAGE)),
        "periods": PERIODS, "groups": {k: v[0] for k, v in GROUPS.items()}, "people": people,
        "f": {"dias": dias, "quem": quem, "tipo": tipo, "registro": registro}, "registro_label": registro_label,
    }, db=db)
