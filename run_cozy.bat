@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
start "Cozy server" /min python -m app.main
timeout /t 6 /nobreak >nul
python -m app.voice