"""Cliente da API do Mercado Pago — SOMENTE LEITURA.

O dinheiro das vendas do Mercado Livre cai no Mercado Pago. A documentação do
Mercado Livre (developers.mercadolivre.com.br/pt_br/gerenciamento-de-pagamentos,
consultada em 11/11/2026) indica a API do Mercado Pago para os pagamentos.

Documentação oficial (consultada via busca em 11/11/2026):
  https://www.mercadopago.com.br/developers/pt/reference/online-payments/checkout-pro/get-payment/get.md
  GET /v1/payments/{id} → status, money_release_date (quando o dinheiro fica
  disponível), transaction_details.net_received_amount (valor líquido).

Usa o mesmo acesso (token) do Mercado Livre conectado.
"""

from app.integrations.http import ReadOnlyApiClient


class MercadoPagoClient(ReadOnlyApiClient):
    label = "Mercado Pago"
    base_url = "https://api.mercadopago.com"

    def get_payment(self, payment_id: str) -> dict | None:
        """GET /v1/payments/{id} — situação do pagamento e data de liberação do dinheiro."""
        return self._get(f"/v1/payments/{payment_id}")
