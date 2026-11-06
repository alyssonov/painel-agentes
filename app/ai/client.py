"""Chamadas à Claude API com controle de custo.

Toda chamada passa por aqui:
1. confere o teto diário (AI_DAILY_BUDGET_BRL) — estourou, não chama;
2. procura a mesma pergunta no cache (ai_cache) — achou, não paga de novo;
3. chama o modelo da tarefa (app/ai/models.py) pedindo JSON num formato fixo;
4. registra tokens e custo estimado (ai_usage), mostrado no painel.

Referência: SDK oficial `anthropic` (Python); saída estruturada com
output_config.format (json_schema); prompt caching com cache_control.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import estimate_cost_usd, model_for
from app.config import get_settings
from app.core.db import utcnow
from app.core.timeutil import local_day_bounds_utc, local_today
from app.models.ai import AiCache, AiUsage

logger = logging.getLogger("ia")

CACHE_TTL = timedelta(days=7)


class AIError(Exception):
    """Problema ao usar a IA. A mensagem pode ser mostrada no painel."""


class AINotConfigured(AIError):
    pass


class BudgetExceeded(AIError):
    pass


class AIRefused(AIError):
    """O modelo recusou responder (filtro de segurança)."""


@dataclass
class RawResult:
    text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


# Função que de fato chama a API: (modelo, esforço, system, mensagem, schema, max_tokens) -> RawResult.
Transport = Callable[[str, str, str, str, dict, int], RawResult]


def anthropic_transport(model: str, effort: str, system: str, user: str, schema: dict,
                        max_tokens: int) -> RawResult:
    """Chamada real pelo SDK oficial. Só é usada quando há ANTHROPIC_API_KEY."""
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # Parte fixa (regras) marcada para o cache de prompt da API.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("A chave da Claude API foi recusada. Confira ANTHROPIC_API_KEY no .env.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIError("A chave da Claude API não tem permissão (confira o Billing no Console).") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Limite de uso da Claude API atingido. Tentaremos de novo mais tarde.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"A Claude API respondeu com erro {exc.status_code}.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Sem conexão com a Claude API no momento.") from exc
    text = next((b.text for b in response.content if b.type == "text"), "")
    usage = response.usage
    return RawResult(
        text=text,
        stop_reason=response.stop_reason,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
    )


def spent_today_brl(db: Session) -> float:
    start, end = local_day_bounds_utc(local_today())
    millicents = db.scalar(select(func.coalesce(func.sum(AiUsage.cost_brl_millicents), 0)).where(
        AiUsage.at >= start, AiUsage.at < end))
    return (millicents or 0) / 100_000


class AIClient:
    def __init__(self, db: Session, transport: Transport | None = None) -> None:
        self.db = db
        self._transport = transport

    def _get_transport(self) -> Transport:
        if self._transport is not None:
            return self._transport
        if not get_settings().anthropic_api_key:
            raise AINotConfigured("Falta a chave da Claude API (ANTHROPIC_API_KEY no .env).")
        return anthropic_transport

    def complete_json(self, *, task: str, agent_slug: str, system: str, user: str, schema: dict,
                      cache_key_parts: list[str] | None = None, max_tokens: int = 1024) -> dict[str, Any]:
        """Pede ao modelo uma resposta em JSON no formato `schema` e devolve o dicionário."""
        settings = get_settings()
        model, effort = model_for(task)

        key = None
        if cache_key_parts is not None:
            raw_key = json.dumps([model, task, system, *cache_key_parts], ensure_ascii=False)
            key = hashlib.sha256(raw_key.encode()).hexdigest()
            cached = self.db.scalar(select(AiCache).where(AiCache.key == key, AiCache.expires_at > utcnow()))
            if cached:
                self.db.add(AiUsage(agent_slug=agent_slug, task=task, model=model, cache_hit=True))
                return json.loads(cached.response)

        if spent_today_brl(self.db) >= settings.ai_daily_budget_brl:
            raise BudgetExceeded(
                f"Teto diário de IA atingido (R$ {settings.ai_daily_budget_brl:.2f}). "
                "Os agentes de IA voltam amanhã — ou aumente AI_DAILY_BUDGET_BRL no .env.")

        result = self._get_transport()(model, effort, system, user, schema, max_tokens)
        cost_usd = estimate_cost_usd(model, result.input_tokens, result.output_tokens, result.cache_read_tokens)
        self.db.add(AiUsage(
            agent_slug=agent_slug, task=task, model=model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cached_tokens=result.cache_read_tokens,
            cost_brl_millicents=round(cost_usd * settings.usd_brl_rate * 100_000),
        ))
        if result.stop_reason == "refusal":
            raise AIRefused("A IA preferiu não responder esta pergunta.")
        if result.stop_reason == "max_tokens":
            raise AIError("A resposta da IA ficou longa demais e foi cortada.")
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise AIError("A IA devolveu uma resposta em formato inesperado.") from exc

        if key is not None:
            existing = self.db.scalar(select(AiCache).where(AiCache.key == key))
            if existing:
                existing.response, existing.expires_at = result.text, utcnow() + CACHE_TTL
            else:
                self.db.add(AiCache(key=key, task=task, response=result.text, expires_at=utcnow() + CACHE_TTL))
        return data
