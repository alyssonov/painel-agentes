"""Campos do produto no Onix → dados do painel (explicação dos campos dada pelo fornecedor)."""

from dataclasses import dataclass
from datetime import date
from typing import Any

COST_FIELDS = {"custo_prod": "custo", "vlmedio_prod": "custo médio", "custoatac_prod": "custo atacado"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in ("", "null", "none") else text


def to_cents(value: Any) -> int | None:
    """"24.700000" → 2470. Vazio, zero ou inválido → None."""
    text = _clean(value)
    if text is None:
        return None
    try:
        cents = round(float(text.replace(",", ".")) * 100)
    except ValueError:
        return None
    return cents if cents > 0 else None


def to_int(value: Any) -> int | None:
    text = _clean(value)
    try:
        number = int(float(text)) if text is not None else None
    except ValueError:
        return None
    return number if number is not None and number >= 0 else None


@dataclass
class OnixProduct:
    code: str
    name: str | None
    brand: str | None
    cost_cents: int | None  # o custo escolhido em ONIX_COST_FIELD
    min_qty: int | None
    factory_code: str | None
    location: str | None
    last_purchase: date | None


def parse(data: dict[str, Any], cost_field: str = "custo_prod") -> OnixProduct:
    field = cost_field if cost_field in COST_FIELDS else "custo_prod"
    last = _clean(data.get("ultcomp_prod"))
    try:
        last_purchase = date.fromisoformat(last[:10]) if last else None
    except ValueError:
        last_purchase = None
    min_qty = to_int(data.get("qtdminima_prod"))
    return OnixProduct(
        code=str(data.get("cod_prod")).strip(),
        name=_clean(data.get("descr_prod")),
        brand=(_clean(data.get("marca_prod")) or "")[:80] or None,
        cost_cents=to_cents(data.get(field)),
        min_qty=min_qty if min_qty else None,
        factory_code=(_clean(data.get("codfab_prod")) or "")[:64] or None,
        location=(_clean(data.get("local_prod")) or "")[:60] or None,
        last_purchase=last_purchase,
    )
