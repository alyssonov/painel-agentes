"""Qual modelo usar em cada tarefa — sempre o mais barato que resolve.

Preços por milhão de tokens (US$), da tabela oficial (referência da Claude API
consultada em 06/11/2026). Servem só para ESTIMAR o gasto no painel; a
cobrança real é a do Console da Anthropic.
"""

from dataclasses import dataclass

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"


@dataclass(frozen=True)
class Price:
    input: float  # US$ por milhão de tokens de entrada
    output: float  # US$ por milhão de tokens de saída
    cache_read: float  # US$ por milhão de tokens lidos do cache de prompt


PRICES = {
    HAIKU: Price(input=0.10, output=0.50, cache_read=0.01),
    SONNET: Price(input=2.00, output=10.00, cache_read=0.20),
}

# Tarefa → (modelo, esforço). Respostas curtas usam Haiku com esforço baixo;
# Sonnet fica para gerar anúncios e análises semanais (fases seguintes).
TASKS = {
    "responder_pergunta": (HAIKU, "low"),
    "responder_mensagem": (HAIKU, "low"),
    "gerar_anuncio": (SONNET, "medium"),
    "analise_semanal": (SONNET, "medium"),
}


def model_for(task: str) -> tuple[str, str]:
    return TASKS[task]


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    price = PRICES[model]
    return (input_tokens * price.input + output_tokens * price.output
            + cache_read_tokens * price.cache_read) / 1_000_000
