@echo off
rem Liga o painel: atualiza pelo GitHub, prepara tudo e abre o navegador.
rem Toda a logica fica no tarefas.py (este arquivo nao deve mudar).
title Painel de Agentes
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python tarefas.py iniciar) else (py tarefas.py iniciar)
echo.
echo O painel foi desligado. Pode fechar esta janela.
pause
