@echo off
setlocal
cd /d "%~dp0"

rem Not set up yet - or something essential is missing: run the step-by-step setup first.
if not exist venv goto needs_setup
ollama list >nul 2>&1
if errorlevel 1 (
    echo Starting Ollama...
    start "Ollama" /min ollama serve
    timeout /t 3 /nobreak >nul
)
ollama list | findstr /i /b "echo:" >nul
if errorlevel 1 goto needs_setup

call venv\Scripts\activate.bat
echo Checking dependencies...
pip install -q -r backend\requirements.txt

rem Echo does not register itself to start at login. To make it do that, put a shortcut
rem to venv\Scripts\pythonw.exe ECHO_TRAY.pyw in the Windows Startup folder - shell:startup.

echo Starting Echo...
start "" venv\Scripts\pythonw.exe ECHO_TRAY.pyw
endlocal
exit /b 0

:needs_setup
echo Echo isn't fully set up yet - running SETUP.bat first.
call "%~dp0SETUP.bat"
endlocal
