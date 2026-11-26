#!/bin/sh
# Busca a versão nova no GitHub e, se houver, faz a cópia do banco e religa.
# Roda sozinho a cada 5 minutos (cron, ver docs/IMPLANTACAO.md) ou à mão:
#   sh deploy/atualizar.sh
set -eu
cd "$(dirname "$0")/.."

git fetch --quiet
if [ "$(git rev-parse HEAD)" = "$(git rev-parse @{u})" ] && [ "${1:-}" != "--forcar" ]; then
  exit 0  # nada novo
fi
echo "$(date '+%d/%m %H:%M') - versão nova encontrada, atualizando..."
git pull --ff-only
# Cópia do banco antes das migrações (fica em ./backups, com o nome "antes-da-atualizacao").
docker compose exec -T db pg_dump -U painel -Fc painel > "backups/painel-$(date +%Y%m%d-%H%M%S)-antes-da-atualizacao.dump"
docker compose up -d --build --remove-orphans
docker image prune -f > /dev/null
echo "$(date '+%d/%m %H:%M') - pronto."
