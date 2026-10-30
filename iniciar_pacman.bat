@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente virtual nao encontrado. Use:
  echo py -3.14 -m venv .venv
  echo .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" pacman.py --port COM4 %*
if errorlevel 1 (
  echo.
  echo Ocorreu um erro ao iniciar. Consulte as mensagens acima.
  pause
)
endlocal
