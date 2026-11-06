"""Cálculo da margem real de uma venda.

Fórmula (regra do projeto):
    lucro = preço dos itens − taxas do marketplace − frete pago pela loja
            − custo dos produtos − impostos

O imposto é calculado como uma alíquota (%) sobre o preço dos itens.
A alíquota é configurada nas regras da loja (chave "tax_rate_pct") e deve
ser informada pela contabilidade (regime: Lucro Real).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MarginResult:
    revenue_cents: int
    fee_cents: int
    shipping_cents: int
    cost_cents: int
    tax_cents: int
    profit_cents: int
    margin_pct: float | None  # None quando não há receita

    @property
    def is_loss(self) -> bool:
        return self.profit_cents < 0


def calculate_margin(
    *,
    revenue_cents: int,
    fee_cents: int,
    shipping_cents: int,
    cost_cents: int,
    tax_rate_pct: float,
) -> MarginResult:
    """Calcula lucro e margem de uma venda. Todos os valores em centavos."""
    tax_cents = round(revenue_cents * tax_rate_pct / 100)
    profit = revenue_cents - fee_cents - shipping_cents - cost_cents - tax_cents
    margin = (profit / revenue_cents * 100) if revenue_cents > 0 else None
    return MarginResult(
        revenue_cents=revenue_cents,
        fee_cents=fee_cents,
        shipping_cents=shipping_cents,
        cost_cents=cost_cents,
        tax_cents=tax_cents,
        profit_cents=profit,
        margin_pct=margin,
    )
