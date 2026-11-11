"""Lista oficial dos agentes do sistema.

Cada agente ganha uma linha na tabela `agents` (criada automaticamente na
inicialização). A lógica de cada agente chega na fase indicada.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent


@dataclass(frozen=True)
class AgentDef:
    slug: str
    name: str
    description: str
    phase: int  # fase do projeto em que o agente passa a funcionar


AGENTS: list[AgentDef] = [
    AgentDef("perguntas", "Respondedor de perguntas",
             "Sugere respostas para as perguntas dos anúncios usando a ficha do produto e o estoque. "
             "Casos difíceis vão para uma pessoa.", 3),
    AgentDef("anuncios", "Criador e otimizador de anúncios",
             "Usa a nota de qualidade do Mercado Livre e sugere descrições (e títulos, sem vendas) melhores "
             "para os anúncios fracos que mais vendem. Nunca publica sem aprovação.", 7),
    AgentDef("estoque", "Vigia de estoque",
             "Lê o estoque do Olist, prevê quando cada produto acaba e avisa divergências com os anúncios.", 4),
    AgentDef("precos", "Monitor de preços",
             "Compara com as referências de preço oficiais do Mercado Livre e sugere ajustes sem passar "
             "da margem mínima. Nunca muda preço sem aprovação.", 7),
    AgentDef("posvenda", "Pós-venda",
             "Responde dúvidas simples de pedidos (rastreio, prazo, nota) e avisa reclamações abertas.", 6),
    AgentDef("avaliacoes", "Avaliações e devoluções",
             "Junta avaliações negativas e reclamações por produto, mostra a taxa de problema e resume com IA "
             "o que se repete (uma vez por semana). Só lê e avisa.", 7),
    AgentDef("margem", "Calculadora de margem real",
             "Calcula o lucro de cada venda (taxas, frete, custo e impostos) e destaca prejuízos.", 4),
    AgentDef("relatorio", "Relatório diário",
             "Envia todo dia às 19h um resumo simples por WhatsApp e/ou e-mail.", 5),
    AgentDef("nfe", "Conferência de NF-e",
             "Confere se todo pedido pronto para envio tem nota emitida no Olist. Não emite nem altera notas.", 4),
    AgentDef("despacho", "Alerta de despacho",
             "Avisa os pedidos do Mercado Livre que precisam ser despachados hoje e os que já passaram do prazo.", 4),
    AgentDef("reputacao", "Termômetro da reputação",
             "Acompanha reclamações, atrasos e cancelamentos do Mercado Livre e avisa antes de a reputação cair.", 4),
]


def ensure_agents(db: Session) -> None:
    """Cria no banco os agentes que ainda não existem (não altera os existentes)."""
    existing = set(db.scalars(select(Agent.slug)))
    for position, definition in enumerate(AGENTS):
        if definition.slug not in existing:
            db.add(Agent(
                slug=definition.slug,
                name=definition.name,
                description=definition.description,
                position=position,
            ))
    db.commit()


def agent_phase(slug: str) -> int | None:
    return next((a.phase for a in AGENTS if a.slug == slug), None)
