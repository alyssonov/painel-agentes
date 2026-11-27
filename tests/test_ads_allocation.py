"""Publicidade dividida venda a venda (lucro real de cada venda) e o DRE sem contar duas vezes."""

from datetime import date, datetime

from app.core.timeutil import local_day_bounds_utc
from app.models import AdDaily, AdGroup, Listing, Order, OrderItem, Product
from app.models.enums import Channel
from app.services.ads_allocation import allocate_ads
from app.services.dashboard import order_margin
from app.services.dre import ADS_CATEGORY, month_dre

DAY = date(2026, 11, 20)


def _product(db, sku):
    p = Product(sku=sku, name=sku, cost_cents=1000)
    db.add(p)
    db.flush()
    return p


def _order(db, ref, product, total, channel=Channel.ML, when=None):
    o = Order(channel=channel, external_id=ref, customer_short_name="Ana S.", items_total_cents=total,
              ordered_at=when or local_day_bounds_utc(DAY)[0].replace(hour=15), net_received_cents=total - 1000)
    o.items.append(OrderItem(sku=product.sku, title=product.name, quantity=1, unit_price_cents=total,
                             product_id=product.id))
    db.add(o)
    db.flush()
    return o


def _setup(db):
    pastilha, retentor = _product(db, "PAST-01"), _product(db, "RET-01")
    db.add(Listing(channel=Channel.ML, external_id="MLB1", item_id="MLB1", sku="PAST-01", product_id=pastilha.id,
                   title="Pastilha", price_cents=10000, status="active", synced_at=datetime(2026, 11, 20)))
    db.add(AdGroup(ad_group_id="1", external_id="MLB1", title="Pastilha", campaign_id="C1"))
    return pastilha, retentor


def test_gasto_da_campanha_vai_para_as_vendas_do_produto_anunciado(db):
    pastilha, retentor = _setup(db)
    a = _order(db, "A", pastilha, 10000)
    b = _order(db, "B", pastilha, 30000)
    c = _order(db, "C", retentor, 50000)  # produto sem anúncio
    s = _order(db, "S", pastilha, 10000, channel=Channel.SHOPEE)  # Shopee não leva Mercado Ads
    db.add(AdDaily(campaign_id="C1", day=DAY, cost_cents=1000))
    db.commit()
    allocate_ads(db, DAY, DAY)
    assert (a.ads_cost_cents, b.ads_cost_cents, c.ads_cost_cents, s.ads_cost_cents) == (250, 750, 0, 0)
    # lucro = recebido (10000 − 1000) − custo 1000 − imposto 10% − publicidade 250
    m = order_margin(a, 10.0)
    assert m.ads_cents == 250 and m.profit_cents == 9000 - 1000 - 1000 - 250


def test_campanha_sem_venda_do_produto_divide_entre_as_vendas_do_dia(db):
    _pastilha, retentor = _setup(db)
    a = _order(db, "A", retentor, 10000)
    b = _order(db, "B", retentor, 30000)
    db.add(AdDaily(campaign_id="C1", day=DAY, cost_cents=1001))
    db.commit()
    allocate_ads(db, DAY, DAY)
    assert a.ads_cost_cents + b.ads_cost_cents == 1001 and a.ads_cost_cents == 250  # sem perder centavo


def test_dre_nao_conta_a_publicidade_duas_vezes(db):
    pastilha, _ = _setup(db)
    _order(db, "A", pastilha, 10000)
    db.add(AdDaily(campaign_id="C1", day=DAY, cost_cents=1000))
    db.add(AdDaily(campaign_id="C1", day=date(2026, 11, 21), cost_cents=400))  # dia sem venda
    db.commit()
    allocate_ads(db, date(2026, 11, 20), date(2026, 11, 21))
    db.commit()
    dre = month_dre(db, DAY)
    assert dre.ads == 1000 and dre.expenses[ADS_CATEGORY] == 400


def test_refazer_a_divisao_nao_acumula(db):
    pastilha, _ = _setup(db)
    a = _order(db, "A", pastilha, 10000)
    daily = AdDaily(campaign_id="C1", day=DAY, cost_cents=1000)
    db.add(daily)
    db.commit()
    allocate_ads(db, DAY, DAY)
    daily.cost_cents = 600  # o ML corrigiu o número do dia
    allocate_ads(db, DAY, DAY)
    assert a.ads_cost_cents == 600
