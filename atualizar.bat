@echo off
chcp 65001 >nul
title CorteCerto - atualizar
cd /d "%~dp0"

echo.
echo Vamos atualizar o CorteCerto. Se ele estiver aberto, sera fechado e aberto de novo.
echo Seus cortes, videos de fundo e configuracoes serao mantidos.
echo.
pause

rem Bloco entre parenteses: o cmd le tudo antes de executar, entao este arquivo
rem pode ser substituido pela versao nova durante a atualizacao.
(
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0atualizar.ps1"
  if errorlevel 1 (
    echo.
    pause
    exit /b 1
  )
  call "%~dp0iniciar.bat"
  exit /b
)
