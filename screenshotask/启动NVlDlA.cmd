@echo off
cd /d "%~dp0"
set "FAKER_PATH=%~dp0..\faker"
set "VENV_PYW=%~dp0..\faker\.venv\Scripts\pythonw.exe"
if not exist "%VENV_PYW%" (
    echo [NVlDlA] faker\.venv not found. Run start.cmd in the repo root first.
    pause
    exit /b 1
)
start "" "%VENV_PYW%" "%~dp0nvidia.py"
