"""Calculadora de preços por marketplace (Precificação).

Sem IA. Contas explicáveis linha a linha.

Para um preço de venda P (o que o comprador paga, já com o seu desconto):
    taxa do marketplace = P × comissão% + taxa fixa (podem mudar com a faixa de preço)
    você recebe         = P − taxa do marketplace − frete − taxa extra por venda
    lucro               = você recebe − custo − custo fixo − P × (imposto + publicidade + afiliados + rateio%)
    margem              = lucro ÷ P

Caminho inverso (preço para a margem m ou o lucro L desejado):
    P = (custo + taxa fixa + frete + extras + custo fixo R$ + L) ÷ (1 − Σ% − m)
Quando a taxa depende do preço (faixas da Shopee/TikTok, tarifa do Mercado
Livre), o cálculo repete até o preço "assentar" na faixa certa.

De onde vêm as taxas: Mercado Livre → API oficial "Custos por vender"
(/sites/MLB/listing_prices, ver app/services/marketplace_fees.py); Shopee e
TikTok Shop → tabela editável no painel (valores de referência de 2026, a
pessoa confere na Central do Vendedor).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

# Taxa do marketplace para um preço: (comissão %, taxa fixa em centavos).
FeeFn = Callable[[int], tuple[float, int]]


@dataclass(frozen=True)
class Costs:
    """O que vale para todos os marketplaces."""

    cost_cents: int  # custo do produto (unidade)
    tax_pct: float = 0.0  # imposto sobre o preço
    ads_pct: float = 0.0  # anúncios pagos sobre o preço
    affiliate_pct: float = 0.0  # comissão de afiliados sobre o preço
    other_pct: float = 0.0  # custo fixo rateado (% do preço)
    other_cents: int = 0  # custo fixo rateado (R$ por unidade: embalagem, etiqueta…)
    discount_pct: float = 0.0  # desconto que você dá sobre o preço anunciado


@dataclass(frozen=True)
class Channel:
    """Um marketplace/tipo de anúncio."""

    name: str
    fee: FeeFn
    shipping_cents: int = 0  # frete pago pela loja
    extra_cents: int = 0  # outra taxa por venda (ex.: devolução)
    source: str = ""  # de onde veio a taxa (para mostrar na tela)


def flat_fee(pct: float, fixed_cents: int = 0) -> FeeFn:
    """Taxa que não muda com o preço."""
    return lambda _price: (pct, fixed_cents)


def band_fee(bands: list[dict]) -> FeeFn:
    """Taxa por faixa de preço: [{"max": centavos ou None, "pct": %, "fixed": centavos}, …] em ordem."""
    ordered = sorted(bands, key=lambda b: (b.get("max") is None, b.get("max") or 0))

    def fee(price: int) -> tuple[float, int]:
        for band in ordered:
            if band.get("max") is None or price <= band["max"]:
                return float(band["pct"]), int(band.get("fixed") or 0)
        last = ordered[-1]
        return float(last["pct"]), int(last.get("fixed") or 0)

    return fee


@dataclass(frozen=True)
class Result:
    channel: Channel
    price_cents: int  # o que o comprador paga
    listed_cents: int  # preço anunciado (antes do seu desconto)
    commission_pct: float
    commission_cents: int
    fixed_fee_cents: int
    shipping_cents: int
    extra_cents: int
    tax_cents: int
    ads_cents: int
    affiliate_cents: int
    other_cents: int
    cost_cents: int

    @property
    def marketplace_cents(self) -> int:
        return self.commission_cents + self.fixed_fee_cents

    @property
    def received_cents(self) -> int:
        return self.price_cents - self.marketplace_cents - self.shipping_cents - self.extra_cents

    @property
    def profit_cents(self) -> int:
        return (self.received_cents - self.tax_cents - self.ads_cents - self.affiliate_cents
                - self.other_cents - self.cost_cents)

    @property
    def margin_pct(self) -> float | None:
        return self.profit_cents / self.price_cents * 100 if self.price_cents else None

    @property
    def markup(self) -> float | None:
        return self.price_cents / self.cost_cents if self.cost_cents else None


def evaluate(costs: Costs, channel: Channel, price_cents: int, listed_cents: int | None = None) -> Result:
    """Quanto sobra quando o comprador paga `price_cents`."""
    pct, fixed = channel.fee(price_cents)
    return Result(
        channel=channel, price_cents=price_cents, listed_cents=listed_cents or price_cents,
        commission_pct=pct, commission_cents=round(price_cents * pct / 100), fixed_fee_cents=fixed,
        shipping_cents=channel.shipping_cents, extra_cents=channel.extra_cents,
        tax_cents=round(price_cents * costs.tax_pct / 100), ads_cents=round(price_cents * costs.ads_pct / 100),
        affiliate_cents=round(price_cents * costs.affiliate_pct / 100),
        other_cents=round(price_cents * costs.other_pct / 100) + costs.other_cents,
        cost_cents=costs.cost_cents,
    )


def evaluate_listed(costs: Costs, channel: Channel, listed_cents: int) -> Result:
    """A partir do preço ANUNCIADO (aplica o seu desconto)."""
    paid = round(listed_cents * (1 - costs.discount_pct / 100))
    return evaluate(costs, channel, paid, listed_cents)


def round_up_price(price_cents: int, ending_90: bool) -> int:
    """Opcionalmente sobe para terminar em ,90 (61,23 → 61,90; 61,95 → 62,90)."""
    if not ending_90:
        return price_cents
    reais, cents = divmod(price_cents, 100)
    return reais * 100 + 90 if cents <= 90 else (reais + 1) * 100 + 90


def _price_with_fee(costs: Costs, channel: Channel, pct: float, fixed: int, margin_pct: float,
                    profit_cents: int) -> int | None:
    pcts = (pct + costs.tax_pct + costs.ads_pct + costs.affiliate_pct + costs.other_pct + margin_pct) / 100
    room = 1 - pcts
    if room <= 0:
        return None
    base = costs.cost_cents + costs.other_cents + fixed + channel.shipping_cents + channel.extra_cents + profit_cents
    return math.ceil(base / room)


def price_for(costs: Costs, channel: Channel, *, margin_pct: float | None = None,
              profit_cents: int | None = None, ending_90: bool = False) -> Result | None:
    """Preço que entrega a margem (%) OU o lucro (R$) desejado. None = impossível."""
    margin, profit = margin_pct or 0.0, profit_cents or 0

    def target_ok(result: Result) -> bool:
        if margin_pct is not None:
            return (result.margin_pct or -1e9) >= margin - 1e-9
        return result.profit_cents >= profit

    # Repete até a taxa do preço calculado ser a mesma usada no cálculo (faixas de preço).
    price, seen = 1, []
    for _ in range(12):
        pct, fixed = channel.fee(price)
        new = _price_with_fee(costs, channel, pct, fixed, margin, profit)
        if new is None:
            return None
        if new == price or new in seen:
            price = max(new, price)
            break
        seen.append(new)
        price = new
    # Garante a meta (numa troca de faixa a taxa pode subir): sobe até cumprir.
    result = evaluate(costs, channel, price)
    for _ in range(400):
        if target_ok(result):
            break
        price = math.ceil(price * 1.002) + 1
        result = evaluate(costs, channel, price)
    else:
        return None
    # Desconto e final ,90 valem para o preço ANUNCIADO.
    listed = math.ceil(price / (1 - costs.discount_pct / 100)) if costs.discount_pct else price
    listed = round_up_price(listed, ending_90)
    final = evaluate_listed(costs, channel, listed)
    while not target_ok(final):  # arredondar/aplicar desconto nunca pode baixar da meta
        listed = round_up_price(listed + 1, ending_90)
        final = evaluate_listed(costs, channel, listed)
    return final


def break_even(costs: Costs, channel: Channel) -> int | None:
    """Menor preço anunciado sem prejuízo."""
    result = price_for(costs, channel, profit_cents=0)
    return result.listed_cents if result else None


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
