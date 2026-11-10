"""Leitura das avaliações dos anúncios e do histórico de reclamações/devoluções (Agente 6). Só LEITURA.

Documentação oficial consultada em 11/11/2026:
  https://developers.mercadolivre.com.br/pt_br/opinioes-sobre-um-produto   GET /reviews/item/{id}
  https://developers.mercadolivre.com.br/pt_br/gerenciar-reclamacoes       busca com range + motivos
Roda a cada 24 h.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.mercadolivre.client import MLClient
from app.integrations.olist.dates import parse_olist_datetime as parse_datetime
from app.models import Claim, ClaimReason, ItemRating, Listing, ProductReview
from app.models.enums import Channel
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml.avaliacoes")

EVERY = timedelta(hours=24)
CLAIMS_DAYS = 90
REVIEWS_DAYS = 180
LOW_RATE = 3
MAX_ITEMS = 400


def sync_reviews(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    """Avaliações dos anúncios ativos + histórico de reclamações dos últimos 90 dias."""
    last = get_cursor(account, "cursor_avaliacoes")
    if last and started - last < EVERY:
        return 0
    listings: dict[str, Listing] = {}
    for listing in db.scalars(select(Listing).where(Listing.channel == Channel.ML, Listing.status == "active")):
        listings.setdefault(listing.item_id, listing)
    since = started - timedelta(days=REVIEWS_DAYS)
    read = 0
    for item_id, listing in list(listings.items())[:MAX_ITEMS]:
        try:
            data = client.get_item_reviews(item_id)
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Mercado Livre pediu pausa nas avaliações (%s).", exc)
            break
        except IntegrationError as exc:
            logger.warning("Avaliações de %s não puderam ser lidas: %s", item_id, exc)
            continue
        product_id = listing.product_id if not listing.variation_label else None
        levels = data.get("rating_levels") or {}
        rating = db.scalar(select(ItemRating).where(ItemRating.channel == Channel.ML, ItemRating.item_id == item_id))
        if rating is None:
            rating = ItemRating(channel=Channel.ML, item_id=item_id, checked_at=started)
            db.add(rating)
        rating.product_id, rating.title = product_id, listing.title
        rating.average = data.get("rating_average")
        rating.total = int((data.get("paging") or {}).get("total") or sum(int(v or 0) for v in levels.values()))
        rating.low = sum(int(levels.get(k) or 0) for k in ("one_star", "two_star", "three_star"))
        rating.checked_at = started
        for review in data.get("reviews") or []:
            created = parse_datetime(review.get("date_created"))
            rate = int(review.get("rate") or 0)
            if not review.get("id") or not created or created < since or rate > LOW_RATE or rate <= 0:
                continue
            if review.get("status") not in (None, "published"):
                continue
            rid = str(review["id"])
            row = db.scalar(select(ProductReview).where(ProductReview.channel == Channel.ML,
                                                        ProductReview.review_id == rid))
            if row is None:
                variation = ", ".join(str(a.get("value_name")) for a in review.get("attributes_variation") or []
                                      if a.get("value_name")) or None
                db.add(ProductReview(channel=Channel.ML, review_id=rid, item_id=item_id, product_id=product_id,
                                     rate=rate, title=(review.get("title") or "")[:255] or None,
                                     content=(review.get("content") or "")[:2000] or None,
                                     variation=variation, created_at=created))
        read += 1
        if read % 20 == 0:
            db.commit()
    db.commit()
    try:
        _sync_claim_history(db, client, seller_id, started)
    except (AuthExpired, TemporaryError):
        raise
    except IntegrationError as exc:
        logger.warning("Histórico de reclamações não pôde ser lido: %s", exc)
    set_cursor(account, "cursor_avaliacoes", started)
    db.commit()
    return read


def _sync_claim_history(db: Session, client: MLClient, seller_id: str, started) -> None:
    for data in client.iter_claims_since(seller_id, started - timedelta(days=CLAIMS_DAYS)):
        external_id = str(data.get("id") or "")
        if not external_id:
            continue
        row = db.scalar(select(Claim).where(Claim.channel == Channel.ML, Claim.external_id == external_id))
        if row is None:
            row = Claim(channel=Channel.ML, external_id=external_id, status=data.get("status") or "closed",
                        seller_actions=[], checked_at=started)
            db.add(row)
        row.status = data.get("status") or row.status
        row.type, row.stage, row.reason_id = data.get("type"), data.get("stage"), data.get("reason_id")
        if data.get("resource_id"):
            row.order_ref = str(data["resource_id"])
        row.opened_at = parse_datetime(data.get("date_created")) or row.opened_at
    db.commit()
    known = set(db.scalars(select(ClaimReason.reason_id)))
    for reason_id in {r for r in db.scalars(select(Claim.reason_id).where(Claim.reason_id.is_not(None)))} - known:
        try:
            info = client.get_claim_reason(reason_id)
        except (AuthExpired, TemporaryError):
            raise
        except IntegrationError:
            info = {}
        db.add(ClaimReason(reason_id=reason_id, name=(info.get("name") or None),
                           detail=(info.get("detail") or "")[:300] or None))
    db.commit()
