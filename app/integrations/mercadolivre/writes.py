"""ESCRITA no Mercado Livre — responder perguntas.

ATENÇÃO: este módulo só pode ser importado pelo ActionGateway
(app/core/actions.py). Um teste automático garante isso. Nenhum agente envia
nada direto: tudo passa pela aprovação e pelo modo simulação do gateway.

Documentação oficial (via busca, 06/11/2026):
https://developers.mercadolivre.com.br/pt-br/gerenciamento-perguntas-respostas
  POST https://api.mercadolibre.com/answers  {"question_id": ..., "text": "..."}
"""

import httpx

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.http import TokenProvider

ANSWERS_URL = "https://api.mercadolibre.com/answers"
MAX_ANSWER_CHARS = 2000


class MLWriter:
    def __init__(self, token_provider: TokenProvider, http: httpx.Client | None = None) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0))
        self._owns_http = http is None

    def __enter__(self) -> "MLWriter":
        return self

    def __exit__(self, *_exc) -> None:
        if self._owns_http:
            self._http.close()

    def answer_question(self, question_id: str, text: str) -> dict:
        """Publica a resposta. Não tenta de novo sozinho (para nunca responder duas vezes)."""
        text = text.strip()
        if not text:
            raise IntegrationError("A resposta está vazia.")
        if len(text) > MAX_ANSWER_CHARS:
            raise IntegrationError(f"A resposta passa de {MAX_ANSWER_CHARS} caracteres.")
        body = {"question_id": int(question_id), "text": text}
        try:
            response = self._send(body, self._tokens.get_token())
            if response.status_code == 401:
                response = self._send(body, self._tokens.get_token(force_refresh=True))
        except httpx.HTTPError as exc:
            raise TemporaryError("Sem conexão com o Mercado Livre. A resposta NÃO foi enviada.") from exc
        if response.status_code in (200, 201):
            return response.json() if response.content else {}
        if response.status_code == 401:
            raise AuthExpired("O Mercado Livre recusou o acesso. Conecte de novo e reenvie.")
        detail = _error_text(response)
        if response.status_code >= 500:
            raise TemporaryError(f"O Mercado Livre está instável (erro {response.status_code}). Tente de novo.")
        raise IntegrationError(f"O Mercado Livre recusou a resposta: {detail}")

    def _send(self, body: dict, token: str) -> httpx.Response:
        return self._http.post(ANSWERS_URL, json=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"})


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or data)[:200]
    return str(data)[:200]
