"""Dados de simulação (modo DRY_RUN).

Gera uma loja fictícia com produtos, pedidos de hoje e dos últimos dias,
perguntas, alertas e execuções dos agentes, para testar e apresentar o
painel sem tocar em nenhuma conta real.

Proteções:
- só roda com DRY_RUN=true;
- recusa rodar se existir alguma conta real conectada (com token);
- só apaga registros marcados como `is_demo`.
"""

import random
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agents.registry import ensure_agents
from app.config import get_settings
from app.core import audit
from app.core.timeutil import local_day_bounds_utc, local_today, utcnow
from app.models import (
    Action,
    Agent,
    AgentRun,
    AiUsage,
    Alert,
    IntegrationAccount,
    Order,
    OrderItem,
    Product,
    Question,
    StockSnapshot,
)
from app.models.enums import (
    ActionType,
    AlertSeverity,
    Channel,
    IntegrationStatus,
    KanbanStatus,
    Provider,
    QuestionStatus,
)
from app.services.store_settings import get_setting, set_setting

# Alíquota usada só na simulação (a real vem da contabilidade).
DEMO_TAX_RATE_PCT = 12.0


class SeedRefused(RuntimeError):
    """A simulação não pode ser carregada nas condições atuais."""


# (sku, nome, custo, preço) — valores em reais, todos fictícios.
_PRODUCTS = [
    ("CAN-TER-500", "Caneca Térmica Inox 500ml", 21.90, 69.90),
    ("GAR-TER-1L", "Garrafa Térmica 1L com Alça", 34.50, 99.90),
    ("POT-VID-KIT5", "Kit 5 Potes de Vidro Herméticos", 42.00, 119.90),
    ("POT-PLA-KIT10", "Kit 10 Potes Plásticos Empilháveis", 38.00, 44.90),  # dá prejuízo
    ("TAB-BAM-3", "Kit 3 Tábuas de Bambu", 29.00, 89.90),
    ("ORG-GAV-6", "Organizador de Gaveta 6 Divisórias", 11.50, 39.90),
    ("ESC-SIL-3", "Kit 3 Espátulas de Silicone", 9.80, 34.90),
    ("COA-INO-24", "Coador de Café Inox 24cm", 14.00, 49.90),
    ("POR-TEM-6", "Porta-Temperos Giratório 6 Potes", 33.00, 94.90),
    ("ESC-PRA-4", "Escorredor de Pratos Inox Dobrável", 58.00, 159.90),
    ("LUM-LED-USB", "Luminária LED de Mesa Recarregável", 27.00, 79.90),
    ("CAB-ORG-10", "Kit 10 Cabides de Veludo", 16.00, 49.90),
    ("SAC-VAC-6", "Kit 6 Sacos a Vácuo para Roupas", 24.00, 69.90),
    ("TAP-BAN-ANT", "Tapete de Banheiro Antiderrapante", 18.00, 54.90),
    ("ESP-BAN-INO", "Espelho de Banheiro com LED", 89.00, 229.90),
    ("FOR-MIC-3", "Kit 3 Formas de Silicone para Air Fryer", 12.00, 39.90),
    ("JAR-VID-1L", "Jarra de Vidro 1L com Tampa", 19.00, 59.90),
    ("PAN-ANT-20", "Frigideira Antiaderente 20cm", 31.00, 89.90),
]

_FIRST_NAMES = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gabriela", "Heitor", "Isabela",
                "João", "Karina", "Lucas", "Mariana", "Nicolas", "Olívia", "Paulo", "Renata", "Sérgio",
                "Tatiane", "Vitor"]
_LAST_INITIALS = "ABCDFGLMNOPRSTV"

_QUESTIONS = [
    ("Serve para bebida gelada também?", "Sim! Mantém gelado por até 12 horas e quente por até 6 horas."),
    ("Qual o prazo de envio para o RJ?", "Postamos em até 1 dia útil após o pagamento. O prazo de entrega aparece no anúncio ao informar seu CEP."),
    ("Pode ir na lava-louças?", "Pode sim, recomendamos a parte de cima da lava-louças."),
    ("Tem na cor preta?", "No momento temos as cores disponíveis no anúncio. A preta está esgotada."),
    ("Vem com nota fiscal?", "Sim, todos os pedidos acompanham nota fiscal."),
    ("Qual o tamanho exato?", "As medidas estão na ficha técnica do anúncio."),
    ("Tem garantia?", "Sim, 90 dias contra defeitos de fabricação."),
    ("Faz desconto para 3 unidades?", None),  # negociação: vai para humano
    ("É BPA free?", "Sim, o material é livre de BPA."),
    ("Chega antes do Natal?", "Postamos em até 1 dia útil. Informe seu CEP no anúncio para ver o prazo exato."),
]


def _assert_safe_to_seed(db: Session) -> None:
    if not get_settings().dry_run:
        raise SeedRefused("Os dados de simulação só podem ser carregados com DRY_RUN=true.")
    real_connected = db.scalar(
        select(func.count()).select_from(IntegrationAccount).where(
            IntegrationAccount.is_demo.is_(False),
            IntegrationAccount.access_token_enc.is_not(None),
        )
    )
    if real_connected:
        raise SeedRefused("Existe conta real conectada. A simulação não será carregada por segurança.")


def clear_demo(db: Session, *, pause_agents: bool = False) -> None:
    """Apaga somente o que foi criado pela simulação.

    pause_agents: usado ao conectar uma conta real — os agentes voltam a
    "Pausado" (na simulação eles aparecem ativos só para demonstração).
    """
    demo_question_ids = [str(i) for i in db.scalars(select(Question.id).where(Question.is_demo.is_(True)))]
    if demo_question_ids:
        db.execute(delete(Action).where(Action.target_type == "pergunta", Action.target_id.in_(demo_question_ids)))
    demo_order_ids = select(Order.id).where(Order.is_demo.is_(True))
    db.execute(delete(OrderItem).where(OrderItem.order_id.in_(demo_order_ids)))
    db.execute(delete(Order).where(Order.is_demo.is_(True)))
    demo_product_ids = select(Product.id).where(Product.is_demo.is_(True))
    db.execute(delete(StockSnapshot).where(StockSnapshot.product_id.in_(demo_product_ids)))
    db.execute(delete(Product).where(Product.is_demo.is_(True)))
    for model in (Question, Alert, AgentRun, AiUsage, IntegrationAccount):
        db.execute(delete(model).where(model.is_demo.is_(True)))
    if pause_agents:
        for agent in db.scalars(select(Agent)):
            agent.enabled = False
        # A alíquota de exemplo da simulação não vale para a loja real.
        if get_setting(db, "tax_rate_is_demo"):
            set_setting(db, "tax_rate_pct", 0.0)
            set_setting(db, "tax_rate_is_demo", False)


def _cents(reais: float) -> int:
    return round(reais * 100)


def _short_name(rng: random.Random) -> str:
    return f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_INITIALS)}."


def _fees(channel: str, revenue_cents: int, rng: random.Random) -> tuple[int, int]:
    """Taxa do marketplace e frete pago pela loja (valores aproximados e fictícios)."""
    if channel == Channel.ML:
        fee = round(revenue_cents * 0.14) + (675 if revenue_cents < 7900 else 0)
        shipping = rng.choice([1990, 2290, 2490]) if revenue_cents >= 7900 else 0
    else:
        fee = round(revenue_cents * 0.20) + 400
        shipping = 0
    return fee, shipping


def seed_demo(db: Session, *, now: datetime | None = None, rng_seed: int | None = None) -> dict[str, int]:
    """Recria todos os dados de simulação. Retorna um resumo do que foi criado."""
    _assert_safe_to_seed(db)
    ensure_agents(db)
    now = now or utcnow()
    today = local_today()
    rng = random.Random(rng_seed if rng_seed is not None else today.toordinal())

    clear_demo(db)
    db.flush()

    # --- Contas conectadas (fictícias) -----------------------------------
    db.add_all([
        IntegrationAccount(provider=Provider.ML, account_name="Loja Exemplo (ML)", external_id="demo-ml",
                           status=IntegrationStatus.OK, last_ok_at=now, is_demo=True,
                           extra={"reputation_label": "MercadoLíder Gold", "reputation_color": "verde"}),
        IntegrationAccount(provider=Provider.SHOPEE, account_name="Loja Exemplo (Shopee)", external_id="demo-shopee",
                           status=IntegrationStatus.OK, last_ok_at=now, is_demo=True,
                           extra={"rating": 4.8}),
        IntegrationAccount(provider=Provider.OLIST, account_name="Olist ERP (exemplo)", external_id="demo-olist",
                           status=IntegrationStatus.OK, last_ok_at=now, is_demo=True),
    ])

    if not get_setting(db, "tax_rate_pct"):
        set_setting(db, "tax_rate_pct", DEMO_TAX_RATE_PCT)
        set_setting(db, "tax_rate_is_demo", True)

    # --- Produtos e estoque -----------------------------------------------
    products: list[Product] = []
    for sku, name, cost, price in _PRODUCTS:
        product = Product(sku=sku, name=name, cost_cents=_cents(cost), price_cents=_cents(price),
                          olist_id=f"demo-{sku}", is_demo=True,
                          specs={"Material": "Ver anúncio", "Garantia": "90 dias"})
        products.append(product)
    db.add_all(products)
    db.flush()
    low_stock = {"CAN-TER-500": 4, "SAC-VAC-6": 2}
    for product in products:
        qty = low_stock.get(product.sku, rng.randint(15, 140))
        db.add(StockSnapshot(product_id=product.id, quantity=qty, reserved=rng.randint(0, 3), captured_at=now))

    # --- Pedidos: hoje e últimos 7 dias -------------------------------------
    order_counter = 0

    def make_order(ordered_at: datetime, kanban: str, problem: str | None = None,
                   channel: str | None = None, only: Product | None = None) -> Order:
        nonlocal order_counter
        order_counter += 1
        channel = channel or rng.choices([Channel.ML, Channel.SHOPEE], weights=[60, 40])[0]
        order = Order(channel=channel,
                      external_id=f"{'2000' if channel == Channel.ML else '24'}{9000000 + order_counter}",
                      olist_id=f"demo-{order_counter}", customer_short_name=_short_name(rng),
                      kanban_status=kanban, problem_note=problem, ordered_at=ordered_at, fees_known=True, is_demo=True)
        chosen = [only] if only else rng.sample(products, k=rng.choices([1, 2], weights=[80, 20])[0])
        for product in chosen:
            qty = rng.choices([1, 2, 3], weights=[75, 20, 5])[0]
            order.items.append(OrderItem(product_id=product.id, sku=product.sku, title=product.name,
                                         quantity=qty, unit_price_cents=product.price_cents))
        order.items_total_cents = sum(i.quantity * i.unit_price_cents for i in order.items)
        order.marketplace_fee_cents, order.shipping_cost_cents = _fees(channel, order.items_total_cents, rng)
        db.add(order)
        return order

    day_start, _ = local_day_bounds_utc(today)
    elapsed = max((now - day_start).total_seconds(), 60)
    # Quantidade de pedidos proporcional ao horário (como numa loja real).
    today_count = max(6, round(30 * elapsed / 86400))
    for i in range(today_count):
        ordered_at = day_start + timedelta(seconds=elapsed * (i + rng.random()) / today_count)
        age_hours = (now - ordered_at).total_seconds() / 3600
        if age_hours > 6:
            kanban = rng.choices([KanbanStatus.SHIPPED, KanbanStatus.PACKED], weights=[80, 20])[0]
        elif age_hours > 2:
            kanban = rng.choices([KanbanStatus.PACKED, KanbanStatus.TO_PICK], weights=[50, 50])[0]
        else:
            kanban = KanbanStatus.TO_PICK
        make_order(ordered_at, kanban)
    # Dois pedidos com problema, para a demonstração.
    make_order(day_start + timedelta(seconds=elapsed * 0.3), KanbanStatus.PROBLEM, "Pronto para envio sem NF-e")
    make_order(day_start + timedelta(seconds=elapsed * 0.5), KanbanStatus.PROBLEM, "Endereço incompleto")
    # Uma venda com prejuízo (produto barato demais na Shopee), coerente com o alerta.
    loss_product = next(p for p in products if p.sku == "POT-PLA-KIT10")
    make_order(day_start + timedelta(seconds=elapsed * 0.7), KanbanStatus.PACKED,
               channel=Channel.SHOPEE, only=loss_product)
    today_count += 3

    for days_ago in range(1, 8):
        start, _ = local_day_bounds_utc(today - timedelta(days=days_ago))
        for _ in range(rng.randint(20, 34)):
            make_order(start + timedelta(minutes=rng.randint(0, 24 * 60 - 1)), KanbanStatus.SHIPPED)

    # --- Perguntas ------------------------------------------------------------
    statuses_today = ([QuestionStatus.ANSWERED] * 11 + [QuestionStatus.PROPOSED] * 2
                      + [QuestionStatus.HUMAN, QuestionStatus.NEW])
    for i, status in enumerate(statuses_today):
        # Perguntas comuns (com resposta); a de desconto só para "precisa de você".
        text, answer = rng.choice([qa for qa in _QUESTIONS if qa[1]])
        if status == QuestionStatus.HUMAN:
            text, answer = _QUESTIONS[7]
        product = rng.choice(products)
        asked = day_start + timedelta(seconds=elapsed * (i + rng.random()) / len(statuses_today))
        db.add(Question(channel=rng.choice([Channel.ML, Channel.SHOPEE]), external_id=f"demo-q-{i}",
                        listing_external_id=f"demo-{product.sku}", listing_title=product.name,
                        text=text, asked_at=asked, status=status,
                        answer_text=answer if status == QuestionStatus.ANSWERED else None,
                        answered_at=asked + timedelta(minutes=rng.randint(2, 25))
                        if status == QuestionStatus.ANSWERED else None,
                        is_demo=True))

    db.flush()
    # Sugestões do agente para as perguntas na fila (para demonstrar a aprovação).
    for question in db.scalars(select(Question).where(
            Question.is_demo.is_(True), Question.status.in_([QuestionStatus.PROPOSED, QuestionStatus.HUMAN]))):
        human = question.status == QuestionStatus.HUMAN
        suggestion = "" if human else next((a for t, a in _QUESTIONS if t == question.text and a), "")
        db.add(Action(agent_slug="perguntas", type=ActionType.ANSWER_QUESTION, channel=question.channel,
                      target_type="pergunta", target_id=str(question.id),
                      payload={"text": suggestion}, original_payload={"text": suggestion},
                      confidence="baixa" if human else "alta", topic="negociacao" if human else "produto",
                      reason="Pedido de desconto ou negociação: precisa de uma pessoa." if human else
                      "A resposta está na ficha do produto.", needs_human=human, dry_run=True))

    # --- Alertas do dia -----------------------------------------------------
    db.add_all([
        Alert(source="nfe", severity=AlertSeverity.CRITICAL, is_demo=True,
              title="1 pedido pronto para envio está sem nota fiscal",
              detail="Emita a nota no Olist para o pedido não atrasar."),
        Alert(source="posvenda", severity=AlertSeverity.CRITICAL, is_demo=True,
              title="Reclamação aberta no Mercado Livre — responder até amanhã",
              detail="Cliente diz que o produto chegou amassado."),
        Alert(source="estoque", severity=AlertSeverity.WARNING, is_demo=True,
              title="Caneca Térmica Inox 500ml acaba em cerca de 2 dias",
              detail="Restam 4 unidades. Média de 2 vendas por dia."),
        Alert(source="estoque", severity=AlertSeverity.WARNING, is_demo=True,
              title="Kit 6 Sacos a Vácuo: estoque do Olist (2) diferente da Shopee (5)"),
        Alert(source="margem", severity=AlertSeverity.WARNING, is_demo=True,
              title="Kit 10 Potes Plásticos está sendo vendido com prejuízo na Shopee"),
    ])

    db.flush()

    # --- Execuções dos agentes (métrica do dia) -------------------------------
    answered = statuses_today.count(QuestionStatus.ANSWERED)
    waiting = len(statuses_today) - answered
    summaries = {
        "perguntas": f"{answered} respondidas · {waiting} aguardando você",
        "anuncios": "3 sugestões de melhoria nesta semana",
        "estoque": "2 produtos com estoque baixo",
        "precos": "4 sugestões de preço aguardando",
        "posvenda": "9 mensagens respondidas · 1 reclamação",
        "avaliacoes": "2 problemas recorrentes encontrados",
        "margem": f"{today_count} vendas calculadas · 1 produto no prejuízo",
        "relatorio": "Próximo envio hoje às 19h",
        "nfe": "1 pedido sem nota",
    }
    for agent in db.scalars(select(Agent)):
        agent.enabled = True
        db.add(AgentRun(agent_id=agent.id, started_at=now - timedelta(minutes=rng.randint(2, 50)),
                        finished_at=now, status="ok", items_processed=rng.randint(3, 40),
                        summary=summaries.get(agent.slug), is_demo=True))

    # --- Uso de IA (fictício) ------------------------------------------------
    for _ in range(18):
        db.add(AiUsage(at=day_start + timedelta(seconds=elapsed * rng.random()), agent_slug="perguntas",
                       task="responder_pergunta", model="claude-haiku-5-5",
                       input_tokens=rng.randint(600, 1400), output_tokens=rng.randint(60, 180),
                       cost_brl_millicents=rng.randint(3000, 9000), is_demo=True))

    audit.record(db, actor="system", event="simulacao.carregada", details={"pedidos": order_counter})
    db.commit()
    return {"produtos": len(products), "pedidos": order_counter, "perguntas": len(statuses_today)}


def refresh_demo_if_stale(db: Session) -> bool:
    """No modo simulação, recria os dados se ainda não houver pedidos de hoje.

    Assim a Visão geral sempre mostra um "hoje" realista na demonstração.
    Retorna True se recriou.
    """
    if not get_settings().dry_run:
        return False
    start, end = local_day_bounds_utc(local_today())
    has_today = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.is_demo.is_(True), Order.ordered_at >= start, Order.ordered_at < end
        )
    )
    if has_today:
        return False
    try:
        seed_demo(db)
    except SeedRefused:
        db.rollback()
        return False
    return True
