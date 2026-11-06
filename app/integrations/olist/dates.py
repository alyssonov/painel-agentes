"""Datas e valores no formato do Olist.

O Olist trabalha no horário de Brasília, com datas como "2023-01-01 10:00:00"
(exemplos da especificação OpenAPI v3). No nosso banco tudo fica em UTC.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

_BRASILIA = ZoneInfo("America/Sao_Paulo")
_UTC = ZoneInfo("UTC")


def to_olist_datetime(dt_utc: datetime | None, *, date_only: bool = False) -> str | None:
    """Converte UTC (sem fuso) para o texto que a API espera, em horário de Brasília."""
    if dt_utc is None:
        return None
    local = dt_utc.replace(tzinfo=_UTC).astimezone(_BRASILIA)
    return local.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")


def parse_olist_datetime(value: str | None) -> datetime | None:
    """Lê uma data do Olist e devolve em UTC (sem fuso). Aceita vários formatos."""
    if not value or not isinstance(value, str) or value.startswith("0000"):
        return None
    text = value.strip().replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BRASILIA)  # sem fuso = horário de Brasília
    return parsed.astimezone(_UTC).replace(tzinfo=None)


def parse_olist_date(value: str | None) -> date | None:
    dt = parse_olist_datetime(value)
    return dt.date() if dt else None


def to_cents(value) -> int:
    """Converte um valor em reais (número ou texto, ex.: "123.45" ou "1.234,56") para centavos."""
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        text = value.strip()
        if "," in text:  # formato brasileiro
            text = text.replace(".", "").replace(",", ".")
    else:
        text = str(value)
    try:
        return int((Decimal(text) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return 0
