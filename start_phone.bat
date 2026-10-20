@echo off
REM Start Cozy with authenticated phone access on the local Wi-Fi.
cd /d "%~dp0"

set "COZY_HOST=0.0.0.0"

call start_cozy.bat --phone