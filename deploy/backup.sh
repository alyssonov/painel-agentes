#!/bin/sh
# Cópia de segurança do banco: uma ao ligar e depois uma por dia (03:30 UTC = 00:30 de Brasília).
# Guarda em ./backups no servidor os últimos KEEP_DAYS dias. Para ter uma cópia
# FORA do servidor, baixe esta pasta de vez em quando (ver docs/IMPLANTACAO.md).
set -eu
KEEP_DAYS="${KEEP_DAYS:-14}"

dump() {
  name="/backups/painel-$(date +%Y%m%d-%H%M%S).dump"
  if pg_dump -Fc -f "$name.tmp"; then
    mv "$name.tmp" "$name"
    echo "Cópia do banco salva: $name"
  else
    rm -f "$name.tmp"
    echo "ERRO: não foi possível copiar o banco" >&2
  fi
  find /backups -name 'painel-*.dump' -mtime +"$KEEP_DAYS" -delete
}

dump
while true; do
  now=$(date +%s)
  next=$(date -d "$(date +%Y-%m-%d) 03:30" +%s 2>/dev/null || echo $((now + 86400)))
  [ "$next" -le "$now" ] && next=$((next + 86400))
  sleep $((next - now))
  dump
done
