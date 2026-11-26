"""Calculadora de preços por marketplace (Precificação).

Sem IA e sem chamadas externas: só contas, explicáveis linha a linha.

Para um preço P, num marketplace com comissão r (%), taxa fixa F e frete F$:
    você recebe = P − P×r − F − frete
    lucro       = você recebe − custo − outros custos (R$) − P×(imposto% + publicidade% + outros%)
    margem      = lucro ÷ P

Caminho inverso (o preço que entrega a margem m ou o lucro L desejado):
    P = (custo + F + frete + outros R$ + L) ÷ (1 − r − imposto% − publicidade% − outros% − m)

As taxas variam por categoria, tipo de anúncio e faixa de preço: quando o
produto tem anúncio no Mercado Livre, o painel preenche com os valores que o
próprio Mercado Livre informa (referência de preço do anúncio); senão, a
pessoa digita. Nada aqui é "taxa oficial" inventada.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Costs:
    """O que vale para todos os marketplaces (custos do produto e da loja)."""

    cost_cents: int  # custo do produto
    tax_pct: float = 0.0  # imposto sobre o preço
    ads_pct: float = 0.0  # publicidade sobre o preço
    other_pct: float = 0.0  # outros custos percentuais (operação, cartão…)
    other_cents: int = 0  # outros custos por unidade (embalagem, etiqueta…)


@dataclass(frozen=True)
class Channel:
    """Um marketplace/tipo de anúncio a comparar."""

    name: str
    commission_pct: float
    fixed_fee_cents: int = 0
    shipping_cents: int = 0  # frete pago pela loja nessa venda


@dataclass(frozen=True)
class Result:
    channel: Channel
    price_cents: int
    commission_cents: int
    fixed_fee_cents: int
    shipping_cents: int
    tax_cents: int
    ads_cents: int
    other_cents: int
    cost_cents: int

    @property
    def received_cents(self) -> int:
        """O que o marketplace repassa (antes do custo do produto e dos impostos)."""
        return self.price_cents - self.commission_cents - self.fixed_fee_cents - self.shipping_cents

    @property
    def profit_cents(self) -> int:
        return self.received_cents - self.tax_cents - self.ads_cents - self.other_cents - self.cost_cents

    @property
    def margin_pct(self) -> float | None:
        return self.profit_cents / self.price_cents * 100 if self.price_cents else None

    @property
    def markup(self) -> float | None:
        """Preço ÷ custo (o "markup" que muita gente usa)."""
        return self.price_cents / self.cost_cents if self.cost_cents else None


def _percent_total(costs: Costs, channel: Channel) -> float:
    return (channel.commission_pct + costs.tax_pct + costs.ads_pct + costs.other_pct) / 100


def evaluate(costs: Costs, channel: Channel, price_cents: int) -> Result:
    """Quanto sobra vendendo a `price_cents`."""
    return Result(
        channel=channel, price_cents=price_cents,
        commission_cents=round(price_cents * channel.commission_pct / 100),
        fixed_fee_cents=channel.fixed_fee_cents, shipping_cents=channel.shipping_cents,
        tax_cents=round(price_cents * costs.tax_pct / 100),
        ads_cents=round(price_cents * costs.ads_pct / 100),
        other_cents=round(price_cents * costs.other_pct / 100) + costs.other_cents,
        cost_cents=costs.cost_cents,
    )


def round_up_price(price_cents: int, ending_90: bool) -> int:
    """Arredonda para cima; opcionalmente para terminar em ,90 (ex.: 61,23 → 61,90)."""
    if not ending_90:
        return price_cents
    reais, cents = divmod(price_cents, 100)
    return reais * 100 + 90 if cents <= 90 else (reais + 1) * 100 + 90


def price_for(costs: Costs, channel: Channel, *, margin_pct: float | None = None,
              profit_cents: int | None = None, ending_90: bool = False) -> Result | None:
    """Preço que entrega a margem (%) OU o lucro (R$) desejado. None = impossível (percentuais ≥ 100%)."""
    fixed = costs.cost_cents + costs.other_cents + channel.fixed_fee_cents + channel.shipping_cents
    room = 1 - _percent_total(costs, channel) - (margin_pct or 0) / 100
    if room <= 0:
        return None
    price = math.ceil((fixed + (profit_cents or 0)) / room)
    return evaluate(costs, channel, round_up_price(price, ending_90))


def break_even(costs: Costs, channel: Channel) -> int | None:
    """Menor preço sem prejuízo (lucro zero)."""
    result = price_for(costs, channel, profit_cents=0)
    return result.price_cents if result else None


def parse_money(text: str | None) -> int | None:
    """ "61,90", "1.234,50", "R$ 10" → centavos. Vazio ou inválido → None."""
    clean = (text or "").strip().replace("R$", "").replace(" ", "")
    if not clean:
        return None
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    try:
        value = round(float(clean) * 100)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_percent(text: str | None) -> float | None:
    """ "16,5" ou "16.5" → 16.5. Vazio ou inválido → None."""
    clean = (text or "").strip().replace("%", "").replace(" ", "").replace(",", ".")
    if not clean:
        return None
    try:
        value = float(clean)
    except ValueError:
        return None
    return value if -100 < value < 100 else None
