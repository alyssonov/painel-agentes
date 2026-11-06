"""Leitura e gravação das regras da loja (tabela store_settings)."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.settings import StoreSetting

# Valores padrão usados enquanto ninguém configurar no painel.
DEFAULTS: dict[str, Any] = {
    # Alíquota efetiva de impostos sobre a venda (%). Deve vir da contabilidade.
    "tax_rate_pct": 0.0,
    "answer_tone": "cordial e objetivo",
    "shipping_days": "1 dia útil para postagem após a confirmação do pagamento",
    "warranty": "90 dias contra defeitos de fabricação",
}


def get_setting(db: Session, key: str) -> Any:
    row = db.scalar(select(StoreSetting).where(StoreSetting.key == key))
    return row.value if row else DEFAULTS.get(key)


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.scalar(select(StoreSetting).where(StoreSetting.key == key))
    if row:
        row.value = value
    else:
        db.add(StoreSetting(key=key, value=value))
