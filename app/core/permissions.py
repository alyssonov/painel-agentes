"""Papéis e permissões da equipe (conferidos no servidor em toda requisição).

Cada pessoa tem um PAPEL (um pacote pronto de permissões). Se o administrador
marcar permissões à mão na tela Equipe, valem as marcadas. O administrador tem
sempre todas.

As regras de qual página/ação exige qual permissão ficam TODAS aqui
(`ROUTE_RULES`), num lugar só, para ficar fácil de conferir. Página nova que não
estiver na lista exige no mínimo "ver o painel".
"""

import re
from dataclasses import dataclass

# --- Permissões ------------------------------------------------------------------

VIEW = "ver_painel"
FINANCE = "ver_financeiro"
SUPPORT = "aprovar_atendimento"
PRICES = "aprovar_precos_anuncios"
PRICING = "usar_precificacao"
PRODUCTS = "editar_produtos"
AGENTS = "configurar_agentes"
STORE = "configurar_loja"
TEAM = "gerenciar_equipe"
AUDIT = "ver_historico"


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    description: str


PERMISSIONS: list[Permission] = [
    Permission(VIEW, "Ver o painel", "Visão geral, perguntas, pós-venda, anúncios, estoque e agentes (só olhar)."),
    Permission(FINANCE, "Ver o financeiro", "Lucro real, margem, recebimentos, gráficos, curva ABC e relatórios."),
    Permission(SUPPORT, "Aprovar atendimento", "Aprovar, editar ou escrever respostas para perguntas e mensagens."),
    Permission(PRICES, "Aprovar preços e anúncios", "Aprovar mudanças de preço e melhorias de anúncio sugeridas."),
    Permission(PRICING, "Usar a precificação", "Calculadora de preços e simulações."),
    Permission(PRODUCTS, "Editar o cadastro dos produtos", "Fornecedor, estoque mínimo, local, compatibilidade e "
               "planilha (só no Gerente do Ano; o Olist não muda)."),
    Permission(AGENTS, "Configurar agentes", "Ligar/desligar agentes, modo manual/automático e regras."),
    Permission(STORE, "Configurar a loja", "Contas conectadas, impostos, tabela de taxas e relatórios por e-mail."),
    Permission(TEAM, "Gerenciar a equipe", "Adicionar pessoas, mudar papéis e permissões, redefinir senhas."),
    Permission(AUDIT, "Ver o histórico", "Quem fez o quê e quando (com o valor anterior e o novo)."),
]
ALL = frozenset(p.code for p in PERMISSIONS)
LABELS = {p.code: p.label for p in PERMISSIONS}

# --- Papéis (pacotes prontos) ----------------------------------------------------

ROLES: dict[str, tuple[str, str, frozenset[str]]] = {
    "admin": ("Administrador", "Tudo, inclusive equipe e contas conectadas.", ALL),
    "gerente": ("Gerente", "Toda a operação e o financeiro; não mexe na equipe nem nas contas.",
                frozenset({VIEW, FINANCE, SUPPORT, PRICES, PRICING, PRODUCTS, AGENTS, AUDIT})),
    "atendimento": ("Atendimento", "Responde perguntas e mensagens dos clientes.", frozenset({VIEW, SUPPORT})),
    "financeiro": ("Financeiro", "Lucro, recebimentos, relatórios e precificação.",
                   frozenset({VIEW, FINANCE, PRICING, AUDIT})),
    "operador": ("Operação", "Pedidos, envios, estoque, anúncios e cadastro dos produtos.",
                 frozenset({VIEW, PRICING, PRODUCTS})),
}
DEFAULT_ROLE = "operador"


def role_label(role: str) -> str:
    return ROLES.get(role, (role,))[0]


def permissions_of(role: str, custom: list[str] | None) -> frozenset[str]:
    """Permissões que valem para a pessoa."""
    if role == "admin":
        return ALL
    if custom is not None:
        return frozenset(custom) & ALL
    return ROLES.get(role, ROLES[DEFAULT_ROLE])[2]


# --- Qual permissão cada endereço exige --------------------------------------------
# (método ou "*", expressão do endereço, permissão ou None = basta estar logado).
# A PRIMEIRA regra que combinar vale; por isso as mais específicas vêm antes.

ROUTE_RULES: list[tuple[str, str, str | None]] = [
    ("POST", r"^/(tema|logout)$", None),
    ("*", r"^/equipe(/|$)", TEAM),
    ("*", r"^/historico(/|$)", AUDIT),
    ("*", r"^/integracoes(/|$)", STORE),
    ("*", r"^/precificacao/taxas$", STORE),
    ("*", r"^/precificacao/massa(/|$)", PRICES),
    ("GET", r"^/precificacao$", PRICING),
    ("*", r"^/margem(/|$)", FINANCE),
    ("GET", r"^/relatorios(/|$)", FINANCE),
    ("POST", r"^/relatorios/", STORE),
    ("POST", r"^/(perguntas|pos-venda)/", SUPPORT),
    ("POST", r"^/(anuncios|estoque-precos)/", PRICES),
    ("POST", r"^/agentes/", AGENTS),
    ("POST", r"^/produtos(/|$)", PRODUCTS),
    ("GET", r"^/", VIEW),
]
_COMPILED = [(m, re.compile(p), perm) for m, p, perm in ROUTE_RULES]


def required_permission(method: str, path: str) -> str | None:
    """Permissão exigida por um endereço. Sem regra: exige ver o painel."""
    for rule_method, pattern, perm in _COMPILED:
        if rule_method in ("*", method) and pattern.search(path):
            return perm
    return VIEW
