@echo off
cd /d "%~dp0"
py -3.13 -m venv .venv-server
if errorlevel 1 (echo Install Python 3.13 64-bit first & pause & exit /b 1)
.venv-server\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (echo Dependency install failed & pause & exit /b 1)
.venv-server\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
pause
