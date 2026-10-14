@echo off
chcp 65001 >nul
title CorteCerto
cd /d "%~dp0"

rem Se o CorteCerto ja estiver rodando (em outra janela), so abre o navegador.
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo O CorteCerto ja esta aberto em outra janela. Abrindo o navegador...
  start "" http://localhost:8000
  timeout /t 3 /nobreak >nul
  exit /b 0
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo O ffmpeg nao foi encontrado.
  echo Abra o PowerShell, rode:  winget install Gyan.FFmpeg
  echo Depois feche e abra esta janela de novo.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparando o ambiente pela primeira vez...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Nao foi possivel usar o Python. Instale o Python 3.12 em https://www.python.org/downloads/
    echo e marque a opcao "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
  )
)

echo Instalando ou atualizando dependencias...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo Falha ao instalar as dependencias. Verifique sua internet e tente de novo.
  pause
  exit /b 1
)

echo.
echo CorteCerto rodando em http://localhost:8000  (feche esta janela para parar)
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m app
pause
