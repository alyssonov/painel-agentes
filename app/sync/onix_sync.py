"""Onix → painel: custo, marca, qtd. mínima e código de fábrica dos produtos. Só leitura do Onix.

Para cada produto do Olist (o código no Onix é o mesmo SKU do Olist, confirmado
pelo dono em 26/11/2026), consulta o Onix e grava no CADASTRO COMPLEMENTAR do
painel (product_extras). O Olist e o Onix não são alterados. O custo do Onix é o usado
no lucro real (app/services/costs.py); sem ele, vale o do Olist — regra do dono, 27/11/2026.

- custo do Onix: atualizado sempre (com histórico antes → depois quando muda);
- marca do Onix: guardada à parte (onix_brand) e usada quando o Olist não tem;
- estoque mínimo, código de fábrica e local: só preenchem o que estiver VAZIO
  (nunca sobrescrevem o que a pessoa escreveu no painel).
"""

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core import audit
from app.core.db import utcnow
from app.core.timeutil import to_local
from app.integrations.errors import IntegrationError, TemporaryError
from app.integrations.onix.client import OnixClient
from app.core.alerts import AlertSpec, sync_alerts
from app.integrations.onix.mapping import parse
from app.models.enums import AlertSeverity
from app.models.product_extra import ProductExtra
from app.models import Product, SyncRun
from app.models.enums import SyncStatus
from app.services.products import extra_for, set_cost, snapshot
from app.services.store_settings import get_setting, set_setting

logger = logging.getLogger("painel.onix")
PROVIDER = "onix"
MAX_CONSECUTIVE_FAILURES = 5  # Onix desligado: para logo, em vez de tentar produto por produto
# Último produto lido numa leitura que não terminou (o painel foi desligado ou se
# atualizou no meio): a próxima leitura CONTINUA dali, em vez de recomeçar do zero.
RESUME_KEY = "onix_continuar_depois_do_produto"
INTERRUPTED = "Interrompida no meio (o painel foi desligado ou atualizado); a próxima leitura continua de onde parou."


# Kit vendido com SKU "código/quantidade" (ex.: 13914/2 = 2 caixas do 13914 no Onix).
KIT_SKU = re.compile(r"^\s*(.+?)\s*/\s*(\d{1,3})\s*$")
ALERT_SOURCE = "onix"


def kit_parts(sku: str) -> tuple[str, int] | None:
    """"13914/2" → ("13914", 2). None quando o SKU não está nesse formato."""
    match = KIT_SKU.match(sku or "")
    if not match or int(match.group(2)) < 2:
        return None
    return match.group(1), int(match.group(2))


RETRY_AFTER_ERROR = timedelta(hours=1)
STALLED = timedelta(minutes=15)


def onix_due(session_factory: sessionmaker[Session], *, hours: int, now: datetime | None = None,
             open_hour: int = 0, close_hour: int = 24) -> bool:
    """Está na hora de ler o Onix?

    Só dentro do horário em que o computador do Onix fica ligado (padrão 8h às 18h).
    Deu certo: espera `hours` horas. Falhou (ex.: o computador do Onix estava desligado
    à noite): tenta de novo em 1 hora, em vez de esperar o ciclo inteiro. Em andamento
    (com notícia recente): não começa outra.
    """
    now = now or utcnow()
    # Fora do horário da loja o computador do Onix está desligado: nem tenta.
    if not open_hour <= to_local(now).hour < close_hour:
        return False
    with session_factory() as db:
        last = db.scalar(select(SyncRun).where(SyncRun.provider == PROVIDER).order_by(SyncRun.id.desc()).limit(1))
    if last is None:
        return True
    if last.status == SyncStatus.RUNNING:
        seen = (last.counts or {}).get("em")
        return now - (datetime.fromisoformat(seen) if seen else last.started_at) > STALLED
    if last.status == SyncStatus.OK:
        return now - (last.finished_at or last.started_at) >= timedelta(hours=hours)
    return now - (last.finished_at or last.started_at) >= RETRY_AFTER_ERROR


def run_onix_sync(session_factory: sessionmaker[Session], *, client: OnixClient | None = None) -> SyncRun | None:
    settings = get_settings()
    if not settings.onix_configured and client is None:
        return None
    with session_factory() as db:
        # Leituras antigas que ficaram "em andamento" foram interrompidas.
        db.execute(update(SyncRun).where(SyncRun.provider == PROVIDER, SyncRun.status == SyncStatus.RUNNING)
                   .values(status=SyncStatus.ERROR, error=INTERRUPTED, finished_at=utcnow()))
        run = SyncRun(provider=PROVIDER, status=SyncStatus.RUNNING, started_at=utcnow(), counts={})
        db.add(run)
        db.commit()
        counts = {"consultados": 0, "atualizados": 0, "custos_mudaram": 0, "nao_encontrados": 0, "kits": 0}
        # "Pai" de variações (ex.: capachuvadelta) não existe no Onix: só as variações (9112, 9113…).
        own = client is None
        client = client or OnixClient(settings.onix_api_url)
        failures = 0
        try:
            everything = list(db.scalars(select(Product).where(Product.is_active.is_(True), Product.is_demo.is_(False),
                                                       or_(Product.variation_kind.is_(None),
                                                           Product.variation_kind != "P"))
                                         .order_by(Product.id)))
            resume_after = get_setting(db, RESUME_KEY) or 0
            products = [p for p in everything if p.id > resume_after]
            skipped = len(everything) - len(products)
            # O Onix leva ~2 s por produto: nunca deixar o banco preso esperando a rede.
            # Cada produto é gravado e liberado na hora (antes travava o painel inteiro
            # por minutos, e quem tentasse gravar — ex.: conectar o Olist — dava erro).
            db.commit()
            total = len(everything)
            for done, product in enumerate(products):
                # Andamento para o cartão do Onix ("320 de 1.480 produtos") e ponto de retomada.
                if done:
                    set_setting(db, RESUME_KEY, products[done - 1].id)
                run.counts = {**counts, "feitos": skipped + done, "total": total, "em": utcnow().isoformat()}
                db.commit()
                try:
                    data = client.get_product(product.sku)
                    failures = 0
                except TemporaryError:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        raise
                    continue
                counts["consultados"] += 1
                kit = kit_parts(product.sku) if data is None else None
                if kit:  # "13914/2": procura o 13914 e multiplica o custo pela quantidade
                    try:
                        data = client.get_product(kit[0])
                    except TemporaryError:
                        data = None
                    if data is not None:
                        counts["kits"] += 1
                if data is None:
                    counts["nao_encontrados"] += 1
                    extra_for(db, product).onix_found = False
                    db.commit()
                    continue
                info = parse(data, settings.onix_cost_field)
                extra = extra_for(db, product)
                extra.onix_found = True
                before = snapshot(extra)
                old_cost = extra.onix_cost_cents
                if info.cost_cents is not None:
                    set_cost(extra, info.cost_cents * (kit[1] if kit else 1))
                extra.onix_brand = info.brand
                # Estoque mínimo, código de fábrica e local do item avulso não valem para o kit.
                if not kit and extra.min_stock is None and info.min_qty:
                    extra.min_stock = info.min_qty
                if not kit and not extra.supplier_code and info.factory_code:
                    extra.supplier_code = info.factory_code
                if not kit and not extra.location and info.location:
                    extra.location = info.location
                after = snapshot(extra)
                if after != before:
                    counts["atualizados"] += 1
                    if old_cost != extra.onix_cost_cents:
                        counts["custos_mudaram"] += 1
                    audit.record(db, actor="system", event="produto.cadastro_alterado", target_type="produto",
                                 target_id=product.id, details={**audit.changes(before, after), "origem": "Onix"})
                db.commit()
            set_setting(db, RESUME_KEY, None)  # terminou: a próxima começa do início
            _missing_alert(db)
            run.status = SyncStatus.OK
        except IntegrationError as exc:
            run.status = SyncStatus.ERROR
            run.error = str(exc)[:500]
            logger.warning("Onix: %s", exc)
        finally:
            if own:
                client.__exit__(None, None, None)
        run.counts = counts
        run.finished_at = utcnow()
        audit.record(db, actor="system", event=f"onix.sincronizacao.{run.status}", details=counts)
        db.commit()
        return run


def _missing_alert(db: Session) -> None:
    """Avisa (painel e automações) quantos produtos o Onix não achou e ainda estão sem custo."""
    missing = db.scalars(select(Product).join(ProductExtra, ProductExtra.product_id == Product.id).where(
        ProductExtra.onix_found.is_(False), Product.is_active.is_(True), Product.is_demo.is_(False),
        or_(ProductExtra.onix_cost_cents.is_(None), ProductExtra.onix_cost_cents == 0))).all()
    missing = [p for p in missing if not p.cost_cents]
    specs = []
    if missing:
        sample = ", ".join(p.sku for p in missing[:8]) + ("…" if len(missing) > 8 else "")
        specs.append(AlertSpec("sem_custo", AlertSeverity.WARNING,
                               f"{len(missing)} produto(s) sem custo: o Onix não achou o SKU",
                               f"{sample}. Informe o custo em Produtos → Custos que faltam.", target_type="produto"))
    sync_alerts(db, ALERT_SOURCE, specs)
