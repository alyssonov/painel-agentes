#!/bin/sh
# Primeira instalação (ou reinstalação) do Gerente do Ano no servidor.
# Uso, dentro da pasta do projeto:   sh deploy/instalar.sh app.gerentedoano.com
#
# Antes: copie o .env do computador da loja para esta pasta (o ENCRYPTION_KEY
# precisa ser o mesmo, senão as contas conectadas não são lidas).
# Pode rodar de novo sem problema: só acerta o que faltar.
set -eu
cd "$(dirname "$0")/.."
DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "Uso: sh deploy/instalar.sh seudominio.com"; exit 1; }
[ -f .env ] || { echo "Falta o arquivo .env (copie o do computador da loja para esta pasta)."; exit 1; }
command -v docker > /dev/null || { echo "Docker não instalado (ver docs/IMPLANTACAO.md, passo 3)."; exit 1; }

# Troca (ou acrescenta) uma linha CHAVE=valor no .env.
set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '\n%s=%s\n' "$1" "$2" >> .env
  fi
}

set_env DOMAIN "$DOMAIN"
if ! grep -q '^POSTGRES_PASSWORD=.' .env; then
  set_env POSTGRES_PASSWORD "$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
# Endereços de retorno do login: agora direto no domínio (HTTPS), sem a página de repasse.
set_env OLIST_REDIRECT_URI "https://$DOMAIN/integracoes/olist/retorno"
set_env ML_REDIRECT_URI "https://$DOMAIN/integracoes/mercadolivre/retorno"
set_env SHOPEE_REDIRECT_URI "https://$DOMAIN/integracoes/shopee/retorno"
chmod 600 .env

mkdir -p data backups
chown 1000:1000 data  # o painel roda como usuário 1000 dentro do contêiner

docker compose up -d --build

# Atualização automática: confere o GitHub a cada 5 minutos.
CRON="*/5 * * * * cd $(pwd) && sh deploy/atualizar.sh >> backups/atualizacoes.log 2>&1"
{ crontab -l 2>/dev/null | grep -v 'deploy/atualizar.sh' || true; echo "$CRON"; } | crontab -

echo
echo "Pronto. Em 1 ou 2 minutos o painel abre em https://$DOMAIN"
echo "(o certificado HTTPS sai na primeira visita; o domínio precisa já apontar para este servidor)."
