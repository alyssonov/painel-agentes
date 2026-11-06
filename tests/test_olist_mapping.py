"""Conversões de datas, valores e situações do Olist."""

from datetime import datetime

from app.integrations.olist.dates import parse_olist_datetime, to_cents, to_olist_datetime
from app.integrations.olist.mapping import (
    channel_for,
    kanban_for,
    problem_note_for,
    product_cost,
    short_customer_name,
)
from app.models.enums import Channel, KanbanStatus


def test_data_enviada_em_horario_de_brasilia():
    # 13:00 UTC = 10:00 em Brasília
    assert to_olist_datetime(datetime(2026, 11, 6, 13, 0)) == "2026-11-06 10:00:00"
    assert to_olist_datetime(datetime(2026, 11, 6, 2, 0), date_only=True) == "2026-11-05"
    assert to_olist_datetime(None) is None


def test_data_lida_vira_utc():
    assert parse_olist_datetime("2026-11-06 10:00:00") == datetime(2026, 11, 6, 13, 0)
    assert parse_olist_datetime("2026-11-06") == datetime(2026, 11, 6, 3, 0)
    assert parse_olist_datetime("06/11/2026") == datetime(2026, 11, 6, 3, 0)
    assert parse_olist_datetime("2026-11-06T10:00:00-03:00") == datetime(2026, 11, 6, 13, 0)
    assert parse_olist_datetime("0000-00-00") is None
    assert parse_olist_datetime("") is None
    assert parse_olist_datetime("lixo") is None


def test_valores_em_centavos():
    assert to_cents(69.9) == 6990
    assert to_cents("139.80") == 13980
    assert to_cents("1.234,56") == 123456
    assert to_cents(None) == 0
    assert to_cents("abc") == 0


def test_canal_pelo_nome_do_ecommerce():
    assert channel_for({"nome": "Mercado Livre"}) == Channel.ML
    assert channel_for({"canalVenda": "MERCADOLIVRE"}) == Channel.ML
    assert channel_for({"nome": "Shopee"}) == Channel.SHOPEE
    assert channel_for({"nome": "Loja própria"}) == Channel.OTHER
    assert channel_for(None) == Channel.OTHER


def test_situacao_do_pedido_no_quadro():
    assert kanban_for(3) == KanbanStatus.TO_PICK
    assert kanban_for("7") == KanbanStatus.PACKED
    assert kanban_for(5) == KanbanStatus.SHIPPED
    assert kanban_for(2) == KanbanStatus.CANCELLED
    assert kanban_for(8) == KanbanStatus.PROBLEM
    assert kanban_for(99) == KanbanStatus.PROBLEM  # desconhecida: alguém precisa olhar
    assert problem_note_for(99).startswith("Situação desconhecida")
    assert problem_note_for(3) is None


def test_nome_do_cliente_minimizado():
    assert short_customer_name("maria da silva souza") == "Maria S."
    assert short_customer_name("João") == "João"
    assert short_customer_name(None) == "Cliente"


def test_custo_prefere_custo_medio():
    assert product_cost({"precoCusto": 20, "precoCustoMedio": 21.5}) == 21.5
    assert product_cost({"precoCusto": 20, "precoCustoMedio": 0}) == 20
    assert product_cost({}) is None
