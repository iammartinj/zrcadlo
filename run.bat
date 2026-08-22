@echo off
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo [Zrcadlo] Zakladam virtualni prostredi...
    py -3.11 -c "" >nul 2>&1
    if not errorlevel 1 (
        py -3.11 -m venv "%VENV%"
    ) else (
        py -3 -m venv "%VENV%"
    )
    if not exist "%PYEXE%" (
        echo [Zrcadlo] Nepodarilo se zalozit venv. Zkontroluj instalaci Pythonu.
        pause
        exit /b 1
    )
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install -r requirements.txt
)

"%PYEXE%" -c "import fastapi, ebooklib, bs4, httpx, webview" >nul 2>&1
if errorlevel 1 (
    echo [Zrcadlo] Doplnuji chybejici baliky...
    "%PYEXE%" -m pip install -r requirements.txt
)

"%PYEXE%" -m app.serve
endlocal
