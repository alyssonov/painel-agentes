#!/bin/bash
# Liga o Gerente do Ano no Mac com dois cliques (o equivalente do "Iniciar painel.bat").
# Toda a lógica fica no tarefas.py. As chaves { } fazem o bash ler o arquivo
# inteiro antes de rodar: assim a atualização automática (git pull) pode trocar
# este arquivo sem atrapalhar o que já está rodando.
{
  cd "$(dirname "$0")" || exit 1
  if ! command -v python3 > /dev/null; then
    echo "Python não encontrado. Instale pelo site python.org/downloads (versão 3.12 ou mais nova)."
  else
    python3 tarefas.py iniciar
  fi
  echo
  read -r -n 1 -p "Painel desligado. Aperte qualquer tecla para fechar esta janela."
  exit 0
}
