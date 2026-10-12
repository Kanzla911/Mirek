@echo off
rem Mirek starten (Windows). Doppelklick genuegt.  Optional: start.bat chat
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python fehlt. Bitte installieren: https://www.python.org/downloads/  ^(Haken bei "Add to PATH" setzen^)
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama ^(lokale KI^) fehlt. Versuche Installation per winget ...
  winget install -e --id Ollama.Ollama
  if errorlevel 1 (
    echo Bitte manuell installieren: https://ollama.com/download
    start https://ollama.com/download
    pause
    exit /b 1
  )
  echo Ollama installiert. Bitte dieses Fenster schliessen und start.bat erneut starten.
  pause
  exit /b 0
)

python -m mirek %*
pause
