"""Curva ABC: quais poucos produtos trazem a maior parte do faturamento (ou do lucro).

Sem IA (custo zero). Usa o mesmo cálculo por produto da Margem real.
Classes (padrão de mercado, regra de Pareto):
  A → produtos que, somados do maior para o menor, fazem os primeiros 80%
  B → os próximos 15% (até 95%)
  C → o resto (e, no critério "lucro", quem deu prejuízo)
"""

from dataclasses import dataclass

from app.services.margin_report import ProductMargin

LIMIT_A, LIMIT_B = 80.0, 95.0
CLASS_INFO = {
    "A": ("Os que sustentam a loja: nunca deixe faltar.", "ok"),
    "B": ("Importantes: acompanhe o estoque e o preço.", "info"),
    "C": ("Vendem pouco: avalie se vale manter, juntar em kit ou liquidar.", "pausado"),
}


@dataclass
class AbcRow:
    product: ProductMargin
    value: int  # faturamento ou lucro (centavos), conforme o critério
    share: float  # % do total
    cumulative: float  # % acumulado até este produto
    cls: str


@dataclass
class AbcSummary:
    cls: str
    products: int
    share_products: float
    share_value: float


def abc_curve(products: list[ProductMargin], criterion: str = "faturamento") -> tuple[list[AbcRow], list[AbcSummary]]:
    def value_of(p: ProductMargin) -> int:
        return p.revenue if criterion == "faturamento" else p.profit

    ranked = sorted(products, key=lambda p: (-value_of(p), p.name))
    total = sum(max(value_of(p), 0) for p in ranked)
    rows, cumulative = [], 0.0
    for product in ranked:
        value = value_of(product)
        share = (max(value, 0) / total * 100) if total else 0.0
        if value <= 0 or not total:
            cls = "C"
        elif cumulative < LIMIT_A:
            cls = "A"
        elif cumulative < LIMIT_B:
            cls = "B"
        else:
            cls = "C"
        cumulative += share
        rows.append(AbcRow(product, value, share, min(cumulative, 100.0), cls))
    summary = []
    for cls in "ABC":
        members = [r for r in rows if r.cls == cls]
        summary.append(AbcSummary(cls, len(members), len(members) / len(rows) * 100 if rows else 0.0,
                                  sum(r.share for r in members)))
    return rows, summary
