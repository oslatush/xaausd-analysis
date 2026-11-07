@echo off
cd /d "%~dp0"
py -3.13 -m venv .venv-bridge
if errorlevel 1 (echo Install Python 3.13 64-bit first & pause & exit /b 1)
.venv-bridge\Scripts\python.exe -m pip install -r requirements-bridge.txt
if errorlevel 1 (echo Dependency install failed & pause & exit /b 1)
.venv-bridge\Scripts\python.exe mt5_bridge.py
pause
