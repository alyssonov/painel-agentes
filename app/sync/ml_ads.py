"""Mercado Ads (Product Ads) → banco local. Só leitura: nada é criado, pausado ou alterado.

Roda junto com a sincronização do Mercado Livre, mas no máximo a cada 3 horas (o
Mercado Livre atualiza as métricas uma vez por dia, às 10h de Brasília).

- campanhas: números somados dos últimos 30 dias;
- dia a dia de cada campanha: 90 dias na primeira vez, depois os últimos 7 (o
  número de um dia ainda muda por até 14 dias por causa da janela de atribuição);
- anúncios (Ad Groups): números dos últimos 30 dias, para achar o que gasta sem vender.

Sem Product Ads na conta (anunciante não encontrado), só anota a situação e para.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.alerts import AlertSpec, sync_alerts
from app.core.money import format_brl
from app.core.timeutil import local_today
from app.integrations.errors import AuthExpired, IntegrationError
from app.integrations.mercadolivre.client import MLClient
from app.models import AdCampaign, AdDaily, AdGroup
from app.models.enums import AlertSeverity
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml_ads")

ADS_EVERY = timedelta(hours=3)
PERIOD_DAYS = 30
FIRST_DAILY_DAYS = 90  # limite da API
DAILY_DAYS = 7
ALERT_SOURCE = "publicidade"
# Anúncio que gastou pelo menos isto em 30 dias sem nenhuma venda por publicidade vira alerta.
NO_SALES_MIN_COST_CENTS = 3000
MAX_ALERTS = 10


def _cents(value) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def apply_metrics(row, metrics: dict | None) -> None:
    """Copia as métricas da API (em reais) para a linha (em centavos)."""
    m = {str(k).lower(): v for k, v in (metrics or {}).items()}
    row.cost_cents = _cents(m.get("cost"))
    row.revenue_cents = _cents(m.get("total_amount"))
    row.direct_cents = _cents(m.get("direct_amount"))
    row.clicks = _int(m.get("clicks"))
    row.prints = _int(m.get("prints"))
    row.units = _int(m.get("units_quantity"))
    row.sales = _int(m.get("advertising_items_quantity"))


def ads_due(account, started) -> bool:
    last = get_cursor(account, "cursor_ads")
    return not last or started - last >= ADS_EVERY


def sync_ads(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    """Lê campanhas, dia a dia e anúncios. Devolve quantas campanhas foram lidas.

    Um problema inesperado aqui fica só na publicidade: nunca derruba a atualização do
    Mercado Livre (perguntas, vendas, reputação...).
    """
    if not ads_due(account, started):
        return 0
    try:
        return _sync_ads(db, client, account, started)
    except (AuthExpired, IntegrationError):
        raise
    except Exception as exc:  # noqa: BLE001 - erro só da publicidade: registra e segue
        logger.exception("Erro inesperado ao ler a publicidade do Mercado Livre")
        db.rollback()
        account.extra = {**(account.extra or {}), "ads_status": "erro",
                         "ads_error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        set_cursor(account, "cursor_ads", started)  # tenta de novo no próximo ciclo (3 h)
        db.commit()
        return 0


def _sync_ads(db: Session, client: MLClient, account, started) -> int:
    try:
        advertisers = client.get_ads_advertisers()
    except AuthExpired:
        raise
    except IntegrationError as exc:  # 403: o app não tem a permissão Publicidade
        account.extra = {**(account.extra or {}), "ads_status": "sem_permissao", "ads_error": str(exc)[:300]}
        set_cursor(account, "cursor_ads", started)
        db.commit()
        return 0
    chosen = next((a for a in advertisers if a.get("site_id") == "MLB"), advertisers[0] if advertisers else None)
    if not chosen:
        account.extra = {**(account.extra or {}), "ads_status": "sem_ads", "ads_error": None}
        set_cursor(account, "cursor_ads", started)
        db.commit()
        return 0
    site, adv = str(chosen.get("site_id") or "MLB"), chosen["advertiser_id"]
    first = get_cursor(account, "cursor_ads_diario") is None
    today = local_today()
    period_from = (today - timedelta(days=PERIOD_DAYS - 1)).isoformat()
    to = today.isoformat()

    # Campanhas (últimos 30 dias).
    seen: list[str] = []
    for c in client.iter_ads_campaigns(site, adv, period_from, to):
        cid = str(c["id"])
        seen.append(cid)
        row = db.scalar(select(AdCampaign).where(AdCampaign.campaign_id == cid))
        if row is None:
            row = AdCampaign(campaign_id=cid, name="", status="")
            db.add(row)
        row.name = str(c.get("name") or f"Campanha {cid}")[:200]
        row.status = str(c.get("status") or "")[:20]
        row.strategy = c.get("strategy")
        row.budget_cents = _cents(c.get("budget") or c.get("daily_budget"))
        row.roas_target = c.get("roas_target")
        row.period_days = PERIOD_DAYS
        apply_metrics(row, c.get("metrics"))
    db.flush()

    # Dia a dia de cada campanha.
    daily_from = (today - timedelta(days=(FIRST_DAILY_DAYS if first else DAILY_DAYS) - 1)).isoformat()
    for cid in seen:
        for d in client.get_ads_campaign_daily(site, cid, daily_from, to):
            try:
                day = date.fromisoformat(str(d["date"])[:10])
            except ValueError:
                continue
            row = db.scalar(select(AdDaily).where(AdDaily.campaign_id == cid, AdDaily.day == day))
            if row is None:
                row = AdDaily(campaign_id=cid, day=day)
                db.add(row)
            apply_metrics(row, d)
        db.commit()  # grava aos poucos

    # Anúncios (Ad Groups) dos últimos 30 dias: a lista é refeita do zero.
    db.execute(delete(AdGroup))
    groups: dict[str, dict] = {}
    for g in client.iter_ads_groups(site, adv, period_from, to):
        # A lista vem em páginas e pode repetir um anúncio (a ordem muda durante a leitura).
        groups.setdefault(str(g.get("id")), g)
    for g in groups.values():
        row = AdGroup(ad_group_id=str(g.get("id")), external_id=str(g.get("ad_group_external_id") or "") or None,
                      group_type=g.get("ad_group_type"), title=str(g.get("title") or "Anúncio")[:255],
                      campaign_id=str(g.get("campaign_id") or "") or None, status=str(g.get("status") or "")[:20],
                      thumbnail=(g.get("thumbnail") or None), period_days=PERIOD_DAYS)
        apply_metrics(row, g.get("metrics"))
        db.add(row)
    db.flush()
    _alerts(db)
    account.extra = {**(account.extra or {}), "ads_status": "ok", "ads_error": None,
                     "ads_advertiser": f"{site}:{adv}"}
    set_cursor(account, "cursor_ads", started)
    set_cursor(account, "cursor_ads_diario", started)
    db.commit()
    return len(seen)


def _alerts(db: Session) -> None:
    """Anúncio patrocinado que gastou sem vender nada nos últimos 30 dias."""
    wasting = list(db.scalars(select(AdGroup).where(AdGroup.cost_cents >= NO_SALES_MIN_COST_CENTS, AdGroup.sales == 0)
                              .order_by(AdGroup.cost_cents.desc())))
    specs = [AlertSpec(f"sem_venda:{g.ad_group_id}", AlertSeverity.WARNING,
                       f"Anúncio patrocinado gastou {format_brl(g.cost_cents)} em 30 dias sem vender",
                       f"{g.title[:120]} — {g.clicks} cliques. Veja em Financeiro → Publicidade.",
                       target_type="anuncio")
             for g in wasting[:MAX_ALERTS]]
    if len(wasting) > MAX_ALERTS:
        rest = sum(g.cost_cents for g in wasting[MAX_ALERTS:])
        specs.append(AlertSpec("sem_venda:resumo", AlertSeverity.INFO,
                               f"Mais {len(wasting) - MAX_ALERTS} anúncio(s) patrocinado(s) sem vender",
                               f"Somam {format_brl(rest)} em 30 dias."))
    sync_alerts(db, ALERT_SOURCE, specs)
