@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=%~dp0faker\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"

rem ---------- 1. locate system python ----------
set "PY=python"
py -3 -c "pass" >nul 2>&1
if %errorlevel%==0 set "PY=py -3"

rem ---------- 2. first run: create venv and install deps ----------
if not exist "%VENV_PY%" (
    echo [NVlDlA] First run: creating virtualenv and installing dependencies...
    %PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [NVlDlA] Failed to create venv. Please install Python 3.10+ and add it to PATH.
        pause
        exit /b 1
    )
    "%VENV_DIR%\Scripts\pip.exe" install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [NVlDlA] Failed to install dependencies. Check your network and retry.
        pause
        exit /b 1
    )
)

rem ---------- 3. optional login hint (login is available in app settings) ----------
if not exist "%~dp0faker\webchat\sessions\doubao.json" if not exist "%~dp0faker\config.json" (
    echo [NVlDlA] Not logged in yet. Log in later from the app: tray icon - Settings - Account Login.
)

rem ---------- 4. launch NVlDlA ----------
start "" "%VENV_PYW%" "%~dp0screenshotask\nvidia.py"
endlocal
