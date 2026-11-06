@echo off
rem Double-click to run Kash. Close this window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Python environment, first run only...
  python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)
rem already running (e.g. auto-started at login)? just open it
curl -s -o nul --max-time 3 http://localhost:8050/ && (
  start "" http://localhost:8050
  exit /b
)
echo Starting Kash at http://localhost:8050  (close this window to stop)
rem open the browser after a few seconds, once the server is up
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8050"
".venv\Scripts\python.exe" -m uvicorn app:app --port 8050 --reload
pause
exit /b

:fail
echo Setup failed. Make sure Python 3 is installed and on PATH.
pause
