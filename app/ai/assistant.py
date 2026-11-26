"""Assistente do painel: um chat com IA que conhece o Gerente do Ano e consulta os dados da loja.

O que faz: tira dúvidas sobre o painel, mostra o caminho até cada tela, monta
relatórios com os dados da loja (consultas só de leitura, em
`app/ai/assistant_tools.py`) e responde perguntas gerais.

Como funciona (Claude API, SDK oficial `anthropic`; uso de ferramentas no
Messages API com o loop feito aqui — referência da Claude API consultada em
26/11/2026):
1. confere o teto diário de IA (AI_DAILY_BUDGET_BRL) antes de CADA chamada;
2. envia: instruções fixas + mapa das telas (com cache de prompt) + só as últimas
   mensagens da conversa (texto, sem os detalhes das consultas antigas);
3. quando a IA pede uma consulta (tool_use), executa aqui, só leitura, com as
   permissões de quem pergunta, e devolve o resultado (tool_result);
4. registra tokens e custo de cada chamada (ai_usage), como os outros agentes.

O assistente nunca age (não responde cliente, não muda preço): ele indica a tela
onde a pessoa aprova. Isso mantém a regra "humano no controle".
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.assistant_tools import TOOLS, run_tool
from app.ai.client import AIError, AINotConfigured, BudgetExceeded, spent_today_brl
from app.ai.models import estimate_cost_usd, model_for
from app.config import get_settings
from app.core.timeutil import local_today
from app.models.ai import AiUsage
from app.models.user import User

logger = logging.getLogger("ia.assistente")

TASK = "assistente"
MAX_ROUNDS = 8  # consultas por pergunta (evita loop e custo alto)
HISTORY_TURNS = 6  # mensagens anteriores enviadas como contexto (curto = barato)
MAX_TOKENS = 8000

# Descrição de cada tela, para o assistente explicar e mostrar o caminho.
PAGE_GUIDE = {
    "/": "Visão geral: vendas de hoje e do mês, o que pede atenção, gráfico de 30 dias, envios de hoje, agentes.",
    "/pedidos": "Pedidos: todos os pedidos com a situação do envio, busca, filtros, planilha e lista de separação "
                "(/pedidos/separacao) para imprimir.",
    "/produtos": "Produtos: marca, estoque, compatibilidade com motos, fornecedor, local, estoque mínimo, custo do "
                 "Onix; filtro por marca; planilha para atualizar vários de uma vez.",
    "/anuncios": "Anúncios: busca em todos os anúncios (ML e Shopee), nota de qualidade e sugestões de texto do "
                 "agente para aprovar.",
    "/estoque-precos": "Estoque: esgotados, acabando, divergências Olist × anúncio, sugestões de preço, sugestão de "
                       "compra e produtos parados.",
    "/compras": "Compras: fornecedores (/compras/fornecedores) e pedidos de compra (a caminho, recebidos).",
    "/precificacao": "Precificação: calculadora 'Saia do Prejuízo' (preço para margem/markup ou margem do preço "
                     "atual), tabela de taxas e Preços em massa (/precificacao/massa).",
    "/perguntas": "Perguntas: perguntas dos compradores com respostas sugeridas pela IA para aprovar.",
    "/pos-venda": "Pós-venda: mensagens dos compradores e reclamações, com respostas sugeridas para aprovar.",
    "/margem": "Lucro real: lucro de cada venda (valor recebido − custo − impostos) e 'Conferir uma venda'.",
    "/margem/recebimentos": "Recebimentos: quando o dinheiro das vendas cai.",
    "/financeiro/dre": "DRE do mês: vendas, taxas, impostos, custo, despesas e lucro líquido.",
    "/financeiro/despesas": "Despesas: lançar aluguel, salários, contador etc.",
    "/margem/graficos": "Gráficos: faturamento e lucro por dia, por marketplace.",
    "/margem/curva-abc": "Curva ABC: produtos que mais faturam/lucram.",
    "/agentes": "Agentes: ligar/pausar cada agente, modo manual/automático e regras.",
    "/relatorios": "Relatórios: relatório diário por e-mail e envio manual.",
    "/equipe": "Equipe: pessoas, papéis e permissões.",
    "/historico": "Histórico: quem fez o quê e quando (antes → depois).",
    "/integracoes": "Contas conectadas: Olist, Mercado Livre, Shopee, alíquota de impostos.",
}

SYSTEM_RULES = """Você é o Assistente do Gerente do Ano, o painel de gestão de uma loja de motopeças que vende no \
Mercado Livre e na Shopee e usa o Olist ERP. Responda sempre em português do Brasil, simples e direto, sem jargão.

O que você faz:
1. Tira dúvidas sobre o painel e explica como cada tela funciona.
2. Mostra o caminho até qualquer coisa no painel, com link Markdown para a tela, ex.: [Pedidos](/pedidos).
   Só indique telas da lista "Telas que esta pessoa pode abrir".
3. Monta relatórios com os dados da loja usando as consultas disponíveis. Mostre números em tabelas Markdown \
(| coluna | coluna |) — a pessoa pode baixar cada tabela como planilha.
4. Responde perguntas gerais (sobre vendas online, motopeças, gestão, contas etc.).

Regras:
- Números da loja: SEMPRE use as consultas; nunca invente valores. Se a consulta não trouxer o dado, diga isso.
- Você só LÊ. Não responde clientes, não muda preço, estoque, anúncio nem nada. Para agir, indique a tela onde a \
pessoa faz isso (as ações passam pela aprovação dela).
- Se uma consulta disser que a pessoa não tem permissão, explique que é preciso pedir a quem administra (Equipe).
- Estoque e notas fiscais são do Olist: o painel não altera.
- Seja breve: comece pela resposta; use listas e tabelas curtas; no máximo umas 15 linhas de tabela, salvo pedido.
- Dinheiro no formato R$ 1.234,56. Datas dd/mm/aaaa.
- Nunca peça nem mostre senhas, chaves ou tokens."""


@dataclass
class ChatResult:
    content: list[dict[str, Any]]  # blocos da resposta (text, tool_use, thinking...)
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


# (modelo, esforço, system, tools, mensagens, max_tokens) -> ChatResult
ChatTransport = Callable[[str, str, list, list, list, int], ChatResult]


def anthropic_chat(model: str, effort: str, system: list, tools: list, messages: list,
                   max_tokens: int) -> ChatResult:
    """Chamada real pelo SDK oficial (só quando há ANTHROPIC_API_KEY)."""
    import anthropic

    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=90.0, max_retries=2)
    try:
        response = client.messages.create(model=model, max_tokens=max_tokens, system=system, tools=tools,
                                          messages=messages, output_config={"effort": effort})
    except anthropic.AuthenticationError as exc:
        raise AIError("A chave da Claude API foi recusada. Confira ANTHROPIC_API_KEY no .env.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIError("A chave da Claude API não tem permissão (confira o Billing no Console).") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Muitas perguntas ao mesmo tempo. Tente de novo em instantes.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"A Claude API respondeu com erro {exc.status_code}. Tente de novo.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Sem conexão com a Claude API no momento.") from exc
    usage = response.usage
    return ChatResult(
        content=[block.to_dict() for block in response.content],
        stop_reason=response.stop_reason,
        input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def pages_for(user: User) -> list[tuple[str, str]]:
    """Telas que a pessoa pode abrir (mesmas regras do servidor)."""
    from app.core.permissions import required_permission
    return [(path, text) for path, text in PAGE_GUIDE.items() if user.can(required_permission("GET", path))]


def build_system(user: User) -> list[dict]:
    """Parte fixa (regras + telas permitidas), marcada para o cache de prompt da API."""
    pages = "\n".join(f"- {path}: {text}" for path, text in pages_for(user))
    text = f"{SYSTEM_RULES}\n\nTelas que esta pessoa pode abrir:\n{pages}"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


@dataclass
class Answer:
    text: str
    tools_used: list[str] = field(default_factory=list)
    cost_brl_millicents: int = 0


def ask(db: Session, user: User, history: list[tuple[str, str]], question: str,
        transport: ChatTransport | None = None) -> Answer:
    """Responde uma pergunta. `history` = [(papel, texto)] das mensagens anteriores (mais antigas primeiro)."""
    settings = get_settings()
    if transport is None:
        if not settings.anthropic_api_key:
            raise AINotConfigured("O assistente precisa da chave da Claude API (ANTHROPIC_API_KEY no .env).")
        transport = anthropic_chat
    model, effort = model_for(TASK)
    system = build_system(user)
    messages: list[dict[str, Any]] = [{"role": role, "content": text} for role, text in history[-HISTORY_TURNS:]]
    # O primeiro item precisa ser da pessoa; data de hoje vai na pergunta (fora do trecho em cache).
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": f"(Hoje é {local_today():%d/%m/%Y}.)\n{question}"})

    answer = Answer(text="")
    for _round in range(MAX_ROUNDS):
        if spent_today_brl(db) >= settings.ai_daily_budget_brl:
            raise BudgetExceeded(f"O teto diário de IA (R$ {settings.ai_daily_budget_brl:.2f}) foi atingido. "
                                 "O assistente volta amanhã — ou aumente AI_DAILY_BUDGET_BRL no .env.")
        result = transport(model, effort, system, TOOLS, messages, MAX_TOKENS)
        cost = estimate_cost_usd(model, result.input_tokens + result.cache_write_tokens, result.output_tokens,
                                 result.cache_read_tokens)
        millicents = round(cost * settings.usd_brl_rate * 100_000)
        answer.cost_brl_millicents += millicents
        db.add(AiUsage(agent_slug="assistente", task=TASK, model=model, input_tokens=result.input_tokens,
                       output_tokens=result.output_tokens, cached_tokens=result.cache_read_tokens,
                       cost_brl_millicents=millicents))
        db.flush()
        if result.stop_reason == "refusal":
            answer.text = "Prefiro não responder essa pergunta. Posso ajudar com outra coisa sobre a loja ou o painel?"
            return answer
        tool_calls = [b for b in result.content if b.get("type") == "tool_use"]
        if result.stop_reason == "tool_use" and tool_calls:
            messages.append({"role": "assistant", "content": result.content})  # inclui o raciocínio, sem mexer
            results = []
            for call in tool_calls:
                output, is_error = run_tool(db, user, call["name"], call.get("input") or {})
                answer.tools_used.append(call["name"])
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output,
                                **({"is_error": True} if is_error else {})})
            messages.append({"role": "user", "content": results})
            continue
        if result.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": result.content})
            continue
        text = "\n".join(b.get("text", "") for b in result.content if b.get("type") == "text").strip()
        if result.stop_reason == "max_tokens":
            text += "\n\n_(A resposta ficou longa e foi cortada. Peça uma parte de cada vez.)_"
        answer.text = text or "Não consegui montar uma resposta. Pode reformular a pergunta?"
        return answer
    answer.text = ("Essa pergunta precisou de consultas demais. Tente dividir em partes menores "
                   "(por exemplo, um período ou um marketplace por vez).")
    logger.warning("Assistente parou após %s rodadas de consulta.", MAX_ROUNDS)
    return answer


def preview_tools_used(names: list[str]) -> str:
    """Texto curto com as consultas feitas (mostrado embaixo da resposta)."""
    labels = {"resumo_vendas": "vendas", "buscar_produtos": "produtos", "buscar_pedidos": "pedidos",
              "estoque": "estoque", "dre_do_mes": "DRE", "pendencias": "pendências",
              "buscar_anuncios": "anúncios", "compras": "compras"}
    seen = list(dict.fromkeys(labels.get(n, n) for n in names))
    return ", ".join(seen)
