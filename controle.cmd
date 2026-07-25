@echo off
cd /d "%~dp0"
title TofuBot - Controle via Telegram

:start
py -3 -u telegram_controle.py
if %errorlevel%==0 (
  echo.
  echo ==== O controle via Telegram foi encerrado. Pode fechar esta janela. ====
  pause
  exit
)

echo.
echo ==================================================
echo   O controle caiu com um erro - reiniciando...
echo ==================================================
timeout /t 3 >nul
goto start
