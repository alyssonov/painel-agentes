"""Camada de IA: teto diário, cache, custo e recusa."""

import json

import pytest

from app.ai.client import AIClient, AINotConfigured, AIRefused, BudgetExceeded, RawResult
from app.ai.models import HAIKU, estimate_cost_usd, model_for
from app.models import AiUsage

SCHEMA = {"type": "object"}


def fake_transport(calls, text='{"ok": 1}', stop="end_turn"):
    def transport(model, effort, system, user, schema, max_tokens):
        calls.append((model, effort))
        return RawResult(text=text, stop_reason=stop, input_tokens=1000, output_tokens=200)
    return transport


def test_respostas_usam_o_modelo_mais_barato():
    assert model_for("responder_pergunta") == (HAIKU, "low")


def test_custo_estimado():
    # 1000 de entrada × 0,10 + 200 de saída × 0,50, por milhão
    assert estimate_cost_usd(HAIKU, 1000, 200) == pytest.approx(0.0002)


def test_registra_uso_e_custo(db):
    calls = []
    data = AIClient(db, fake_transport(calls)).complete_json(
        task="responder_pergunta", agent_slug="perguntas", system="s", user="u", schema=SCHEMA)
    db.commit()
    assert data == {"ok": 1} and calls == [(HAIKU, "low")]
    usage = db.query(AiUsage).one()
    assert usage.input_tokens == 1000 and usage.cost_brl_millicents > 0


def test_cache_evita_pagar_de_novo(db):
    calls = []
    ai = AIClient(db, fake_transport(calls))
    for _ in range(3):
        ai.complete_json(task="responder_pergunta", agent_slug="p", system="s", user="u", schema=SCHEMA,
                         cache_key_parts=["item", "pergunta igual"])
        db.commit()
    assert len(calls) == 1
    assert db.query(AiUsage).filter(AiUsage.cache_hit.is_(True)).count() == 2


def test_teto_diario(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("AI_DAILY_BUDGET_BRL", "0.01")
    get_settings.cache_clear()
    db.add(AiUsage(agent_slug="p", task="t", model=HAIKU, cost_brl_millicents=5000))  # R$ 0,05
    db.commit()
    with pytest.raises(BudgetExceeded):
        AIClient(db, fake_transport([])).complete_json(task="responder_pergunta", agent_slug="p",
                                                       system="s", user="u", schema=SCHEMA)
    get_settings.cache_clear()


def test_recusa_do_modelo(db):
    with pytest.raises(AIRefused):
        AIClient(db, fake_transport([], text="", stop="refusal")).complete_json(
            task="responder_pergunta", agent_slug="p", system="s", user="u", schema=SCHEMA)


def test_sem_chave(db):
    with pytest.raises(AINotConfigured):
        AIClient(db).complete_json(task="responder_pergunta", agent_slug="p", system="s", user="u", schema=SCHEMA)


def test_prompt_nao_tem_data_variavel():
    """O texto de instruções é fixo (bom para o cache de prompt)."""
    from app.agents.questions import PROMPT
    assert "{loja}" in PROMPT and "datetime" not in PROMPT
    json.dumps(PROMPT)
