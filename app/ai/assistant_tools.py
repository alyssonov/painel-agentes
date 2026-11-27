"""Ferramentas do Assistente: consultas SOMENTE LEITURA aos dados da loja.

Regras (iguais às do resto do painel):
- nenhuma ferramenta escreve nada (nem no banco do painel, nem no Olist, nem nos
  marketplaces). Para agir (responder cliente, mudar preço...), o assistente
  indica a tela onde a pessoa aprova;
- cada ferramenta respeita as permissões de quem pergunta: sem "ver o
  financeiro", não vêm lucro, margem nem custo; o DRE fica bloqueado;
- dados pessoais mínimos: só o nome abreviado do cliente (como no resto do painel);
- respostas curtas (no máximo LIMIT linhas) para controlar o custo da IA.
"""

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.config import has_real_data
from app.core.money import format_brl
from app.core.permissions import AGENTS, FINANCE
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Action, Alert, Claim, PurchaseOrder, Question
from app.models.enums import CHANNEL_LABELS, ActionStatus, AlertStatus, Channel, QuestionStatus
from app.models.purchase import PO_STATUS
from app.models.user import User
from app.services.dashboard import _orders_between, order_margin
from app.services.dre import month_dre, month_start, parse_month
from app.services.orders import PERIODS, SITUATIONS, OrderFilter, list_orders
from app.services.products import FILTERS, format_compatibility, list_products
from app.services.purchase import purchase_suggestions
from app.services.stock import stock_rows
from app.services.store_settings import get_setting
from app.services.store_settings import other_costs_cents

LIMIT = 40
CHANNELS = {"ml": Channel.ML, "shopee": Channel.SHOPEE, "outro": Channel.OTHER}


class ToolDenied(Exception):
    """A pessoa não tem permissão para esta consulta."""


def _brl(cents: int | None) -> str | None:
    return format_brl(cents) if cents is not None else None


def _pct(value: float | None) -> str | None:
    return f"{value:.1f}%".replace(".", ",") if value is not None else None


def _period(inp: dict) -> tuple[date, date]:
    """(primeiro dia, último dia) no fuso da loja."""
    today = local_today()
    try:
        start = date.fromisoformat(inp["inicio"]) if inp.get("inicio") else None
        end = date.fromisoformat(inp["fim"]) if inp.get("fim") else None
    except ValueError as exc:
        raise ValueError("Datas no formato AAAA-MM-DD.") from exc
    if start is None:
        days = max(1, min(int(inp.get("dias") or 30), 400))
        start = today - timedelta(days=days - 1)
    end = min(end or today, today)
    if start > end:
        raise ValueError("A data inicial vem depois da final.")
    return start, end


# --- Consultas ---------------------------------------------------------------------

def sales_summary(db: Session, user: User, inp: dict) -> dict:
    start, end = _period(inp)
    group = inp.get("agrupar_por") or "total"
    channel = CHANNELS.get(inp.get("marketplace") or "")
    finance = user.can(FINANCE)
    tax = float(get_setting(db, "tax_rate_pct") or 0)
    other = other_costs_cents(db)
    orders = _orders_between(db, local_day_bounds_utc(start)[0], local_day_bounds_utc(end)[1], channel)
    buckets: dict[str, dict] = defaultdict(lambda: {"faturamento": 0, "lucro": 0, "pedidos": 0, "unidades": 0})
    for order in orders:
        m = order_margin(order, tax, other)
        if group in ("produto", "marca"):
            for item in order.items:
                product = item.product
                if group == "produto":
                    key = f"{item.title} ({item.sku})"
                else:
                    key = (product.brand if product and product.brand else "Sem marca")
                b = buckets[key]
                b["faturamento"] += item.quantity * item.unit_price_cents
                b["unidades"] += item.quantity
                b["pedidos"] += 1
            continue
        if group == "dia":
            key = to_local(order.ordered_at).strftime("%d/%m/%Y")
        elif group == "marketplace":
            key = CHANNEL_LABELS.get(Channel(order.channel), order.channel)
        else:
            key = "Total"
        b = buckets[key]
        b["faturamento"] += m.revenue_cents
        b["lucro"] += m.profit_cents
        b["pedidos"] += 1
        b["unidades"] += sum(i.quantity for i in order.items)
    rows = []
    items = sorted(buckets.items(), key=lambda kv: kv[0]) if group == "dia" else \
        sorted(buckets.items(), key=lambda kv: -kv[1]["faturamento"])
    for key, b in items[:LIMIT]:
        row = {"grupo": key, "faturamento": _brl(b["faturamento"]), "pedidos": b["pedidos"], "unidades": b["unidades"]}
        if finance and group not in ("produto", "marca"):
            row["lucro_real"] = _brl(b["lucro"])
            row["margem"] = _pct(b["lucro"] / b["faturamento"] * 100 if b["faturamento"] else None)
        rows.append(row)
    total_rev = sum(b["faturamento"] for b in buckets.values())
    return {"periodo": f"{start:%d/%m/%Y} a {end:%d/%m/%Y}", "agrupado_por": group,
            "marketplace": inp.get("marketplace") or "todos", "linhas": rows,
            "total_faturamento": _brl(total_rev), "total_pedidos": len(orders),
            "observacao": "Vendas pela data da venda, sem canceladas. Lucro real = mesma conta da tela Lucro real."
            + ("" if finance else " (Lucro e margem ocultos: a pessoa não tem acesso ao financeiro.)"),
            "mais_linhas": max(0, len(items) - LIMIT)}


def find_products(db: Session, user: User, inp: dict) -> dict:
    flt = inp.get("filtro") if inp.get("filtro") in FILTERS else ""
    rows = list_products(db, str(inp.get("busca") or "")[:80], flt, str(inp.get("marca") or "")[:80])
    finance = user.can(FINANCE)
    limit = max(1, min(int(inp.get("limite") or 20), LIMIT))
    out = []
    for r in rows[:limit]:
        e = r.extra
        item = {"sku": r.product.sku, "nome": r.product.name, "marca": r.brand, "estoque": r.stock,
                "preco_olist": _brl(r.product.price_cents), "link": f"/produtos/{r.product.id}"}
        if e:
            item.update({"compativel_com": format_compatibility(e.compatibility).replace("\n", "; ") or None,
                         "fornecedor": e.supplier, "local": e.location, "estoque_minimo": e.min_stock})
        if finance:
            item["custo_olist"] = _brl(r.product.cost_cents)
            if e and e.onix_cost_cents:
                item["custo_onix"] = _brl(e.onix_cost_cents)
        out.append({k: v for k, v in item.items() if v not in (None, "")})
    return {"encontrados": len(rows), "mostrando": len(out), "produtos": out}


def find_orders(db: Session, user: User, inp: dict) -> dict:
    f = OrderFilter.from_query(str(inp.get("periodo") or "7"), str(inp.get("marketplace") or ""),
                               str(inp.get("situacao") or ""), str(inp.get("busca") or ""), 1)
    result = list_orders(db, f, paginate=False)
    limit = max(1, min(int(inp.get("limite") or 20), LIMIT))
    rows = [{"pedido": r.number, "marketplace": r.channel_label, "data": r.ordered_at.strftime("%d/%m %H:%M"),
             "cliente": r.customer, "itens": "; ".join(f"{q}x {t}" for q, t, _s in r.items),
             "valor": _brl(r.total_cents), "situacao": r.situation_label,
             "prazo_despacho": r.deadline.strftime("%d/%m %H:%M") if r.deadline else None,
             "nota_fiscal": "emitida" if r.has_invoice else "não"} for r in result.rows[:limit]]
    return {"periodo": PERIODS[f.period], "total": result.total, "vendas": _brl(result.revenue_cents),
            "por_situacao": {SITUATIONS[k]: v for k, v in result.counts.items() if v}, "pedidos": rows}


def stock_view(db: Session, user: User, inp: dict) -> dict:
    kind = inp.get("tipo") or "comprar"
    from app.web.routes.reports import _purchase_rules, _rules
    rules = _rules(db, "estoque")
    rows = stock_rows(db, window_days=int(rules.get("janela_vendas_dias", 30)), demo=not has_real_data(db))
    if kind == "comprar":
        lead, coverage = _purchase_rules(rules)
        items = purchase_suggestions(db, rows, lead_days=lead, coverage_days=coverage)
        out = [{"sku": i.sku, "nome": i.name, "disponivel": i.available, "a_caminho": i.on_order,
                "vendas_por_dia": round(i.avg_daily, 1), "comprar": i.quantity,
                "urgencia": "comprar já" if i.urgency == "agora" else "esta semana",
                **({"custo_estimado": _brl(i.total_cost_cents)} if user.can(FINANCE) and i.unit_cost_cents else {})}
               for i in items[:LIMIT]]
        return {"sugestao_de_compra": out, "total_itens": len(items), "tela": "/estoque-precos"}
    status = {"esgotados": "esgotado", "baixo": "baixo", "parados": "parado"}.get(kind)
    chosen = [r for r in rows if (r.status == status if status else r.diverges)]
    out = [{"sku": r.sku, "nome": r.name, "disponivel": r.available, "vendidos_na_janela": r.sold_units,
            "dura_dias": round(r.days_left) if r.days_left is not None else None} for r in chosen[:LIMIT]]
    return {"tipo": kind, "total": len(chosen), "produtos": out, "tela": "/estoque-precos"}


def dre_view(db: Session, user: User, inp: dict) -> dict:
    if not user.can(FINANCE):
        raise ToolDenied("Esta pessoa não tem permissão para ver o financeiro (DRE).")
    month = parse_month(str(inp.get("mes") or "")) or month_start(local_today())
    d = month_dre(db, month)
    return {"mes": d.label, "parcial": d.partial, "vendas": _brl(d.revenue), "comissoes": _brl(d.fees),
            "frete_e_tarifas": _brl(d.shipping), "impostos": _brl(d.taxes), "receita_liquida": _brl(d.net_revenue),
            "custo_produtos": _brl(d.cost), "lucro_das_vendas": _brl(d.gross_profit),
            "despesas": {k: _brl(v) for k, v in d.expenses.items()}, "total_despesas": _brl(d.total_expenses),
            "lucro_liquido": _brl(d.net_profit), "margem_liquida": _pct(d.pct(d.net_profit)),
            "faturamento_que_paga_as_despesas": _brl(d.break_even), "pedidos": d.orders,
            "vendas_com_taxa_a_conferir": d.unconfirmed, "tela": "/financeiro/dre"}


def pending_view(db: Session, user: User, inp: dict) -> dict:
    waiting_q = db.scalar(select(func.count()).select_from(Question).where(
        Question.status.in_([QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN]))) or 0
    actions = dict(db.execute(select(Action.type, func.count()).where(
        Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])).group_by(Action.type)).all())
    claims = db.scalar(select(func.count()).select_from(Claim).where(Claim.status == "opened")) or 0
    alerts = [{"gravidade": a.severity, "alerta": a.title} for a in db.scalars(
        select(Alert).where(Alert.status == AlertStatus.OPEN).order_by(Alert.created_at.desc()).limit(15))]
    late = list_orders(db, OrderFilter(period="30", situation="atrasado"), paginate=False).total
    return {"perguntas_aguardando": waiting_q, "respostas_e_acoes_para_aprovar": actions,
            "reclamacoes_abertas": claims, "pedidos_atrasados": late, "alertas_abertos": alerts,
            "telas": {"perguntas": "/perguntas", "pos_venda": "/pos-venda", "pedidos": "/pedidos?situacao=atrasado",
                      "precos": "/precificacao/massa", "anuncios": "/anuncios"}}


def find_listings(db: Session, user: User, inp: dict) -> dict:
    from app.web.routes.listings import search_listings
    found = search_listings(db, str(inp.get("busca") or "")[:80])
    limit = max(1, min(int(inp.get("limite") or 20), LIMIT))
    rows = [{"anuncio": f["l"].title, "codigo": f["l"].item_id, "sku": f["l"].sku,
             "marketplace": CHANNEL_LABELS.get(Channel(f["l"].channel), f["l"].channel),
             "preco": _brl(f["l"].price_cents), "estoque": f["l"].available_quantity, "situacao": f["l"].status,
             "qualidade": f["q"].level_wording if f["q"] else None, "link": f["l"].permalink} for f in found[:limit]]
    return {"encontrados": len(found), "anuncios": rows}


def purchases_view(db: Session, user: User, inp: dict) -> dict:
    status = inp.get("situacao") if inp.get("situacao") in PO_STATUS else "aberto"
    orders = list(db.scalars(select(PurchaseOrder).where(PurchaseOrder.status == status)
                             .order_by(PurchaseOrder.number.desc()).limit(LIMIT)))
    today = local_today()
    return {"situacao": PO_STATUS[status], "pedidos_de_compra": [
        {"numero": o.number, "fornecedor": o.supplier.name, "feito_em": f"{o.ordered_on:%d/%m/%Y}",
         "previsto": f"{o.expected_on:%d/%m/%Y}" if o.expected_on else None,
         "atrasado": bool(o.status == "aberto" and o.expected_on and o.expected_on < today),
         "itens": "; ".join(f"{i.quantity}x {i.name}" for i in o.items[:10]), "link": f"/compras/{o.id}"}
        for o in orders]}


def ads_view(db: Session, user: User, inp: dict) -> dict:
    """Mercado Ads: investimento, retorno, campanhas e anúncios gastando sem vender."""
    if not user.can(FINANCE):
        raise ToolDenied("Publicidade (investimento e retorno) é do financeiro: esta pessoa não tem acesso.")
    from app.services.ads import STATUS_TEXT, ads_summary, group_roas
    from app.sync.ml_ads import NO_SALES_MIN_COST_CENTS
    days = int(inp.get("dias") or 30) if str(inp.get("dias") or 30) in ("7", "30", "90") else 30
    s = ads_summary(db, days)

    def ratio(value: float | None) -> str | None:
        return f"{value:.1f}x".replace(".", ",") if value is not None else None

    return {
        "situacao": STATUS_TEXT.get(s.status or "", "ok" if s.status == "ok" else "ainda não lido"),
        "periodo": f"últimos {days} dias", "investimento": _brl(s.cost), "vendas_pelos_anuncios": _brl(s.revenue),
        "retorno_roas": ratio(s.roas), "acos": _pct(s.acos), "tacos": _pct(s.tacos),
        "faturamento_total_ml": _brl(s.ml_revenue), "cliques": s.clicks, "vendas": s.sales,
        "campanhas_30_dias": [{"campanha": c.name, "situacao": c.status, "orcamento_dia": _brl(c.budget_cents),
                               "investimento": _brl(c.cost_cents), "vendas_pelos_anuncios": _brl(c.revenue_cents),
                               "retorno": ratio(c.revenue_cents / c.cost_cents if c.cost_cents else None),
                               "vendas": c.sales} for c in s.campaigns[:LIMIT]],
        "anuncios_gastando_sem_vender": [{"anuncio": g.title, "codigo": g.external_id, "investimento": _brl(g.cost_cents),
                                          "cliques": g.clicks} for g in s.groups
                                         if g.cost_cents >= NO_SALES_MIN_COST_CENTS and g.sales == 0][:15],
        "anuncios_que_mais_gastaram": [{"anuncio": g.title, "investimento": _brl(g.cost_cents),
                                        "vendas_pelos_anuncios": _brl(g.revenue_cents), "retorno": ratio(group_roas(g)),
                                        "vendas": g.sales} for g in s.groups[:10]],
        "observacao": "ROAS = vendas pelos anúncios ÷ investimento; TACOS = investimento ÷ faturamento total no ML. "
                      "O investimento já entra no DRE. Tela: /publicidade.",
    }


def accounts_view(db: Session, user: User, inp: dict) -> dict:
    """Situação das contas conectadas (sem chaves nem tokens)."""
    from app.models import IntegrationAccount, SyncRun
    from app.models.enums import PROVIDER_LABELS
    rows = []
    for acc in db.scalars(select(IntegrationAccount).where(IntegrationAccount.is_demo.is_(False))):
        last = db.scalar(select(SyncRun).where(SyncRun.provider == acc.provider).order_by(SyncRun.id.desc()).limit(1))
        rows.append({"conta": PROVIDER_LABELS.get(acc.provider, acc.provider),
                     "conectada": bool(acc.access_token_enc), "situacao": acc.status, "erro": acc.last_error,
                     "ultima_atualizacao": to_local(last.started_at).strftime("%d/%m %H:%M") if last else None,
                     "resultado": last.status if last else None})
    onix = db.scalar(select(SyncRun).where(SyncRun.provider == "onix").order_by(SyncRun.id.desc()).limit(1))
    rows.append({"conta": "Onix (loja física, só das 8h às 18h)", "ultima_leitura":
                 to_local(onix.started_at).strftime("%d/%m %H:%M") if onix else None,
                 "resultado": onix.status if onix else None, "numeros": onix.counts if onix else None,
                 "erro": onix.error if onix else None})
    return {"contas": rows, "tela": "/integracoes",
            "observacao": "Se uma conta estiver com erro de acesso, a solução é 'Conectar de novo' em Contas conectadas."}


def automations_view(db: Session, user: User, inp: dict) -> dict:
    """Quais avisos por e-mail estão ligados (Automações)."""
    if not user.can(AGENTS):
        raise ToolDenied("Automações são de quem configura os agentes: esta pessoa não tem acesso.")
    from app.automations.catalog import AUTOMATIONS
    from app.automations.runner import ensure_rows, quiet_settings, recipients
    rows = ensure_rows(db)
    quiet = quiet_settings(db)
    return {"automacoes": [{"nome": d.name, "ligada": rows[d.slug].enabled, "o_que_avisa": d.description,
                            "ultimo_aviso": to_local(rows[d.slug].last_sent_at).strftime("%d/%m %H:%M")
                            if rows[d.slug].last_sent_at else None, "erro": rows[d.slug].last_error}
                           for d in AUTOMATIONS],
            "quantos_recebem": len(recipients(db)),
            "horario_silencioso": f"das {quiet['de']}h às {quiet['ate']}h" if quiet.get("ativo") else "desligado",
            "tela": "/automacoes"}


# --- Definições enviadas à IA -------------------------------------------------------

_DAYS = {"type": "integer", "description": "Últimos N dias (padrão 30). Ignorado se 'inicio' for informado."}
TOOLS: list[dict[str, Any]] = [
    {"name": "resumo_vendas",
     "description": "Faturamento, pedidos, unidades e (se permitido) lucro real e margem de um período, "
                    "agrupados por total, dia, marketplace, produto ou marca. Use para qualquer pergunta sobre "
                    "vendas, faturamento, lucro ou para montar relatórios de vendas.",
     "input_schema": {"type": "object", "properties": {
         "dias": _DAYS, "inicio": {"type": "string", "description": "AAAA-MM-DD"},
         "fim": {"type": "string", "description": "AAAA-MM-DD"},
         "agrupar_por": {"type": "string", "enum": ["total", "dia", "marketplace", "produto", "marca"]},
         "marketplace": {"type": "string", "enum": ["ml", "shopee", "outro"]}}}},
    {"name": "buscar_produtos",
     "description": "Produtos do Olist com marca, estoque, preço, compatibilidade com motos, fornecedor, local e "
                    "(se permitido) custo. A busca acha por palavras em qualquer ordem (nome, SKU, marca, moto).",
     "input_schema": {"type": "object", "properties": {
         "busca": {"type": "string"}, "marca": {"type": "string"},
         "filtro": {"type": "string", "enum": [k for k in FILTERS if k]},
         "limite": {"type": "integer", "description": "Máximo de produtos (até 40)."}}}},
    {"name": "buscar_pedidos",
     "description": "Pedidos com situação do envio (a enviar, atrasado, enviado, entregue, Full, problema, "
                    "cancelado), prazo de despacho e nota fiscal. Busca por nº do pedido, produto, SKU ou cliente.",
     "input_schema": {"type": "object", "properties": {
         "busca": {"type": "string"}, "situacao": {"type": "string", "enum": list(SITUATIONS)},
         "periodo": {"type": "string", "enum": list(PERIODS)},
         "marketplace": {"type": "string", "enum": ["ml", "shopee", "outro"]},
         "limite": {"type": "integer"}}}},
    {"name": "estoque",
     "description": "Estoque: sugestão de compra (o que comprar e quanto), produtos esgotados, com estoque baixo, "
                    "parados (sem vendas) ou com divergência entre o Olist e o anúncio.",
     "input_schema": {"type": "object", "properties": {
         "tipo": {"type": "string", "enum": ["comprar", "esgotados", "baixo", "parados", "divergencias"]}},
         "required": ["tipo"]}},
    {"name": "dre_do_mes",
     "description": "DRE de um mês: vendas, comissões, frete, impostos, custo, lucro das vendas, despesas e lucro "
                    "líquido. Só para quem vê o financeiro.",
     "input_schema": {"type": "object", "properties": {"mes": {"type": "string", "description": "AAAA-MM"}}}},
    {"name": "pendencias",
     "description": "O que está esperando a loja agora: perguntas, respostas e mudanças para aprovar, "
                    "reclamações abertas, pedidos atrasados e alertas.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "buscar_anuncios",
     "description": "Anúncios do Mercado Livre e da Shopee (preço, estoque, situação, nota de qualidade). "
                    "Busca por palavras do título, código MLB ou SKU, em qualquer ordem.",
     "input_schema": {"type": "object", "properties": {"busca": {"type": "string"}, "limite": {"type": "integer"}},
                      "required": ["busca"]}},
    {"name": "compras",
     "description": "Pedidos de compra feitos aos fornecedores (a caminho, recebidos ou cancelados).",
     "input_schema": {"type": "object", "properties": {
         "situacao": {"type": "string", "enum": list(PO_STATUS)}}}},
    {"name": "publicidade",
     "description": "Mercado Ads (anúncios patrocinados do Mercado Livre): investimento, vendas geradas, retorno "
                    "(ROAS), ACOS, TACOS, campanhas e anúncios que gastam sem vender. Só para quem vê o financeiro.",
     "input_schema": {"type": "object", "properties": {
         "dias": {"type": "integer", "enum": [7, 30, 90], "description": "Período (padrão 30)."}}}},
    {"name": "contas_conectadas",
     "description": "Situação das contas (Olist, Mercado Livre, Shopee, Onix): conectada ou não, última "
                    "atualização e erros. Use quando perguntarem se algo está conectado ou por que um dado não chegou.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "automacoes",
     "description": "Avisos por e-mail (Automações): quais estão ligados, último aviso, horário silencioso.",
     "input_schema": {"type": "object", "properties": {}}},
]
RUNNERS = {"resumo_vendas": sales_summary, "buscar_produtos": find_products, "buscar_pedidos": find_orders,
           "estoque": stock_view, "dre_do_mes": dre_view, "pendencias": pending_view,
           "buscar_anuncios": find_listings, "compras": purchases_view, "publicidade": ads_view,
           "contas_conectadas": accounts_view, "automacoes": automations_view}


def run_tool(db: Session, user: User, name: str, inp: dict) -> tuple[str, bool]:
    """Executa uma consulta. Devolve (JSON para a IA, deu_erro)."""
    runner = RUNNERS.get(name)
    if runner is None:
        return json.dumps({"erro": f"Consulta desconhecida: {name}"}, ensure_ascii=False), True
    try:
        result = runner(db, user, inp if isinstance(inp, dict) else {})
    except ToolDenied as exc:
        return json.dumps({"erro": str(exc)}, ensure_ascii=False), True
    except (ValueError, TypeError) as exc:
        return json.dumps({"erro": f"Parâmetro inválido: {exc}"}, ensure_ascii=False), True
    return json.dumps(result, ensure_ascii=False, default=str), False
