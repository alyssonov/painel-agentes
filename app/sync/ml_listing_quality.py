"""Leitura da qualidade dos anúncios do Mercado Livre (agente de Anúncios). Só LEITURA.

Documentação oficial consultada em 11/11/2026:
  https://developers.mercadolivre.com.br/pt_br/qualidade-das-publicacoes
  GET /item/{id}/performance → score, level, level_wording, buckets[].variables[].rules[]
  (rules com status PENDING trazem wordings.label/title/link: o que fazer e onde, no ML).
Roda a cada 7 dias (a nota muda devagar).
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.mercadolivre.client import MLClient
from app.models import Listing, ListingQuality
from app.models.enums import Channel
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml.anuncios")

EVERY = timedelta(days=7)
MAX_ITEMS = 400
OFFICIAL_LINK_PREFIXES = ("https://www.mercadolivre.com.br/", "https://www.mercadolibre.com")


def pending_actions(data: dict) -> list[dict]:
    """O que o Mercado Livre diz que falta no anúncio (regras pendentes), sem repetir."""
    found, seen = [], set()
    for bucket in data.get("buckets") or []:
        for variable in bucket.get("variables") or []:
            for rule in variable.get("rules") or []:
                if rule.get("status") != "PENDING":
                    continue
                wordings = rule.get("wordings") or {}
                label = wordings.get("label") or variable.get("title") or rule.get("key")
                if not label or label in seen:
                    continue
                seen.add(label)
                link = wordings.get("link") or ""
                found.append({"bucket": bucket.get("title") or bucket.get("key"), "label": label,
                              "title": wordings.get("title") or variable.get("title"),
                              "link": link if link.startswith(OFFICIAL_LINK_PREFIXES) else None})
    return found[:8]


def sync_listing_quality(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    last = get_cursor(account, "cursor_qualidade")
    if last and started - last < EVERY:
        return 0
    listings: dict[str, Listing] = {}
    for listing in db.scalars(select(Listing).where(Listing.channel == Channel.ML, Listing.status == "active")):
        listings.setdefault(listing.item_id, listing)
    read = 0
    for item_id, listing in list(listings.items())[:MAX_ITEMS]:
        try:
            data = client.get_item_performance(item_id)
            item = client.get_item(item_id) or {}
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Mercado Livre pediu pausa na qualidade dos anúncios (%s).", exc)
            break
        except IntegrationError as exc:
            logger.warning("Qualidade de %s não pôde ser lida: %s", item_id, exc)
            continue
        if not isinstance(data, dict):
            continue
        row = db.scalar(select(ListingQuality).where(ListingQuality.channel == Channel.ML,
                                                     ListingQuality.item_id == item_id))
        if row is None:
            row = ListingQuality(channel=Channel.ML, item_id=item_id, checked_at=started)
            db.add(row)
        row.title = item.get("title") or listing.title
        row.product_id = listing.product_id if not listing.variation_label else None
        row.score = data.get("score")
        row.level, row.level_wording = data.get("level"), data.get("level_wording")
        row.pending = pending_actions(data)
        row.sold_quantity = int(item.get("sold_quantity") or 0)
        row.checked_at = started
        read += 1
        if read % 20 == 0:
            db.commit()
    set_cursor(account, "cursor_qualidade", started)
    db.commit()
    return read
