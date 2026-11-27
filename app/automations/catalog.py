"""Lista das automações que a loja pode ligar (todas começam DESLIGADAS).

Cada automação escolhe alertas do painel (os mesmos da faixa "O que pede atenção")
pela origem e pela gravidade, e manda um aviso por e-mail para a própria loja.
São mensagens INTERNAS: nunca falam com cliente nem mudam preço, estoque ou anúncio.
"""

from dataclasses import dataclass, field

from app.models.enums import AlertSeverity

SEVERITY_ORDER = {AlertSeverity.INFO: 0, AlertSeverity.WARNING: 1, AlertSeverity.CRITICAL: 2}

# Origem dos alertas de perguntas esperando (criados pelo próprio runner).
QUESTIONS_SOURCE = "perguntas_espera"


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str  # "int" | "bool"
    default: int | bool
    minimum: int = 0
    maximum: int = 999
    help: str = ""


@dataclass(frozen=True)
class AutomationDef:
    slug: str
    name: str
    description: str
    sources: tuple[str, ...] | None  # None = qualquer origem
    min_severity: str
    params: tuple[Param, ...] = field(default_factory=tuple)

    def severity_for(self, params: dict) -> str:
        """Gravidade mínima, considerando a opção "avisar também o que vence em breve"."""
        if self.slug == "atrasos" and params.get("incluir_vencendo"):
            return AlertSeverity.WARNING
        return self.min_severity


AUTOMATIONS: list[AutomationDef] = [
    AutomationDef("urgente", "Algo urgente aconteceu",
                  "Qualquer alerta vermelho do painel: envio atrasado, produto esgotado, nota fiscal faltando, "
                  "reclamação aberta, conta que caiu.", None, AlertSeverity.CRITICAL),
    AutomationDef("atrasos", "Envio atrasado",
                  "Pedido que passou do prazo de despacho do Mercado Livre ou da Shopee.", ("despacho",),
                  AlertSeverity.CRITICAL,
                  (Param("incluir_vencendo", "Avisar também o que vence nas próximas horas", "bool", False),)),
    AutomationDef("estoque", "Estoque acabando",
                  "Produto esgotado, acabando em poucos dias ou com estoque diferente entre o Olist e o anúncio.",
                  ("estoque",), AlertSeverity.WARNING),
    AutomationDef("prejuizo", "Venda com prejuízo",
                  "Venda em que, depois de taxas, frete, custo e impostos, a loja perdeu dinheiro.", ("margem",),
                  AlertSeverity.WARNING),
    AutomationDef("perguntas", "Pergunta esperando resposta",
                  "Pergunta de cliente sem resposta há mais tempo do que o limite escolhido.", (QUESTIONS_SOURCE,),
                  AlertSeverity.WARNING,
                  (Param("horas", "Avisar depois de quantas horas sem resposta", "int", 2, 1, 72),)),
    AutomationDef("clientes", "Reclamações e reputação",
                  "Reclamação aberta, mensagem de pós-venda que pede atenção, avaliação ruim ou reputação caindo.",
                  ("posvenda", "avaliacoes", "reputacao"), AlertSeverity.WARNING),
    AutomationDef("publicidade", "Anúncio pago gastando sem vender",
                  "Anúncio patrocinado do Mercado Ads que gastou nos últimos 30 dias sem nenhuma venda.",
                  ("publicidade",), AlertSeverity.WARNING),
    AutomationDef("contas", "Conta conectada com problema",
                  "Olist, Mercado Livre ou Shopee pararam de responder ou pedem para conectar de novo.",
                  ("olist", "ml", "shopee"), AlertSeverity.WARNING),
]
BY_SLUG = {a.slug: a for a in AUTOMATIONS}


def default_params(defn: AutomationDef) -> dict:
    return {p.key: p.default for p in defn.params}


def clean_params(defn: AutomationDef, raw: dict) -> dict:
    """Valida o que veio do formulário (números dentro dos limites; caixas marcadas)."""
    out = default_params(defn)
    for p in defn.params:
        if p.kind == "bool":
            out[p.key] = str(raw.get(p.key, "")).lower() in ("1", "on", "true", "sim")
        else:
            try:
                out[p.key] = max(p.minimum, min(p.maximum, int(str(raw.get(p.key, p.default)).strip())))
            except ValueError:
                pass
    return out
