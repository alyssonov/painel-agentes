"""Dinheiro: guardado em centavos (inteiro), exibido no formato brasileiro."""


def format_brl(cents: int, *, with_symbol: bool = True) -> str:
    """Formata centavos como reais. Ex.: 123456 -> 'R$ 1.234,56'."""
    sign = "-" if cents < 0 else ""
    reais, centavos = divmod(abs(int(cents)), 100)
    inteiro = f"{reais:,}".replace(",", ".")
    texto = f"{sign}{inteiro},{centavos:02d}"
    return f"R$ {texto}" if with_symbol else texto


def format_pct(value: float | None, digits: int = 1) -> str:
    """Formata porcentagem no padrão brasileiro. Ex.: 18.25 -> '18,3%'."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}%".replace(".", ",")
