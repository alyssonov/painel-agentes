"""Datas: o banco guarda UTC; a loja enxerga o horário de Brasília."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.core.db import utcnow


def store_tz() -> ZoneInfo:
    return get_settings().tz


def local_today() -> date:
    """Data de hoje no fuso da loja."""
    return datetime.now(store_tz()).date()


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Início e fim de um dia local, convertidos para UTC (sem fuso) para consultar o banco."""
    tz = store_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return (
        start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
        end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
    )


def to_local(dt_utc: datetime) -> datetime:
    """Converte uma data do banco (UTC) para o horário da loja."""
    return dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(store_tz())


__all__ = ["local_today", "local_day_bounds_utc", "to_local", "utcnow", "store_tz"]
