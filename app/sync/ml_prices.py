"""Leitura das referências de preço do Mercado Livre (agente de Preços). Só LEITURA.

Documentação oficial consultada em 11/11/2026:
  https://developers.mercadolivre.com.br/pt_br/referencias-de-precos
  https://developers.mercadolivre.com.br/pt_br/automatizacoes-de-precos
Formato confirmado com 40 anúncios reais da loja (11/11/2026): os custos
(selling_fees, shipping_fees) correspondem ao PREÇO SUGERIDO; current_price é o
preço de venda atual (com promoção, quando houver).
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.mercadolivre.client import MLClient
from app.integrations.olist.dates import to_cents
from app.models import Listing, PriceReference
from app.models.enums import Channel
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml.precos")

EVERY = timedelta(hours=12)
MAX_ITEMS = 300


def _amount(data: dict, key: str) -> int:
    return to_cents((data.get(key) or {}).get("amount"))


def sync_price_references(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    last = get_cursor(account, "cursor_precos")
    if last and started - last < EVERY:
        return 0
    automated: set[str] = set()
    try:
        for entry in client.iter_automated_price_items(seller_id):
            item = entry.get("item_id") or entry.get("id") if isinstance(entry, dict) else entry
            if item:
                automated.add(str(item))
    except (AuthExpired, TemporaryError):
        raise
    except IntegrationError as exc:
        logger.warning("Não foi possível ler as automatizações de preço: %s", exc)

    seen = 0
    for item_id in client.get_price_suggestion_items(seller_id)[:MAX_ITEMS]:
        try:
            data = client.get_price_suggestion(item_id)
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Mercado Livre pediu pausa nas referências de preço (%s).", exc)
            break
        except IntegrationError as exc:
            logger.warning("Referência de preço de %s não pôde ser lida: %s", item_id, exc)
            continue
        if not isinstance(data, dict) or not data.get("status"):
            continue
        row = db.scalar(select(PriceReference).where(PriceReference.channel == Channel.ML,
                                                     PriceReference.item_id == item_id))
        if row is None:
            row = PriceReference(channel=Channel.ML, item_id=item_id, checked_at=started)
            db.add(row)
        listings = list(db.scalars(select(Listing).where(Listing.channel == Channel.ML, Listing.item_id == item_id)))
        costs = data.get("costs") or {}
        row.status = data.get("status")
        row.applicable = bool(data.get("applicable_suggestion"))
        row.current_cents = _amount(data, "current_price")
        row.suggested_cents = _amount(data, "suggested_price")
        row.lowest_cents = _amount(data, "lowest_price")
        row.selling_fee_cents = to_cents(costs.get("selling_fees"))
        row.shipping_fee_cents = to_cents(costs.get("shipping_fees"))
        row.compared = int((data.get("metadata") or {}).get("compared_values") or 0)
        row.automated = item_id in automated
        row.has_variations = len(listings) > 1 or any(lst.variation_label for lst in listings)
        if listings:
            row.title = listings[0].title
            row.listed_cents = listings[0].price_cents
            row.product_id = listings[0].product_id if not row.has_variations else None
        row.checked_at = started
        seen += 1
        if seen % 20 == 0:
            db.commit()
    set_cursor(account, "cursor_precos", started)
    db.commit()
    return seen
