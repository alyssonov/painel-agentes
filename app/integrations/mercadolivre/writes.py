"""ESCRITA no Mercado Livre — responder perguntas e mensagens pós-venda.

ATENÇÃO: este módulo só pode ser importado pelo ActionGateway
(app/core/actions.py). Um teste automático garante isso. Nenhum agente envia
nada direto: tudo passa pela aprovação e pelo modo simulação do gateway.

Documentação oficial (via busca, 06/11/2026):
https://developers.mercadolivre.com.br/pt-br/gerenciamento-perguntas-respostas
  POST https://api.mercadolibre.com/answers  {"question_id": ..., "text": "..."}

Mensagens pós-venda (documentação oficial consultada em 10/11/2026):
https://developers.mercadolivre.com.br/pt_br/mensagens-post-venda
  POST /messages/packs/{pack}/sellers/{seller}?tag=post_sale
  {"from": {"user_id": vendedor}, "to": {"user_id": AGENTE_MLB}, "text": "..."}
  Desde 02/02/2026, no Brasil o "to" é o ID do Agente de Mensageria do ML (não o
  comprador); até 350 caracteres; sem CRLF nem caracteres de controle; o vendedor
  nunca inicia a conversa. Resposta 202 = mensagem em revisão (pending_review).
"""

import httpx

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.http import TokenProvider

import re

ANSWERS_URL = "https://api.mercadolibre.com/answers"
MESSAGES_URL = "https://api.mercadolibre.com/messages/packs/{pack}/sellers/{seller}"
MAX_ANSWER_CHARS = 2000
MAX_MESSAGE_CHARS = 350
MLB_MESSAGING_AGENT_ID = "3037675074"  # tabela "IDs dos Agentes por país" da documentação
_FORBIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ufdd0-\ufdef]")


def clean_message(text: str) -> str:
    """Deixa o texto no formato aceito: sem CRLF e sem caracteres de controle."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return _FORBIDDEN.sub("", text).strip()


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

    def send_message(self, pack_id: str, seller_id: str, text: str, max_chars: int = MAX_MESSAGE_CHARS) -> dict:
        """Envia a mensagem pós-venda. Não tenta de novo sozinho (para nunca mandar duas vezes)."""
        text = clean_message(text)
        if not text:
            raise IntegrationError("A mensagem está vazia.")
        if len(text) > max_chars:
            raise IntegrationError(f"A mensagem passa de {max_chars} caracteres (limite do Mercado Livre).")
        url = MESSAGES_URL.format(pack=pack_id, seller=seller_id)
        body = {"from": {"user_id": str(seller_id)}, "to": {"user_id": MLB_MESSAGING_AGENT_ID}, "text": text}
        try:
            response = self._send(body, self._tokens.get_token(), url=url)
            if response.status_code == 401:
                response = self._send(body, self._tokens.get_token(force_refresh=True), url=url)
        except httpx.HTTPError as exc:
            raise TemporaryError("Sem conexão com o Mercado Livre. A mensagem NÃO foi enviada.") from exc
        if response.status_code in (200, 201, 202):
            return response.json() if response.content else {}
        if response.status_code == 401:
            raise AuthExpired("O Mercado Livre recusou o acesso. Conecte de novo e reenvie.")
        if response.status_code >= 500:
            raise TemporaryError(f"O Mercado Livre está instável (erro {response.status_code}). Tente de novo.")
        raise IntegrationError(f"O Mercado Livre recusou a mensagem: {_error_text(response)}")

    def _send(self, body: dict, token: str, url: str = ANSWERS_URL) -> httpx.Response:
        params = {"tag": "post_sale"} if url != ANSWERS_URL else None
        return self._http.post(url, json=body, params=params, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"})


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or data)[:200]
    return str(data)[:200]
