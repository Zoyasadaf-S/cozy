@echo off
REM Optional: builds a single-folder Windows app with PyInstaller (dist\cozy\cozy.exe). Untested template; see README.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --name cozy --add-data "cozy\static;cozy\static" --add-data "plugins;plugins" --hidden-import pycaw --hidden-import comtypes run_cozy.py
pause
