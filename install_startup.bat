@echo off
REM Makes Cozy start (minimised) when you sign in to Windows. Remove it by deleting the "Cozy" shortcut from shell:startup.
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Cozy.lnk'); $s.TargetPath='%~dp0start_cozy.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Cozy will start with Windows.
pause
