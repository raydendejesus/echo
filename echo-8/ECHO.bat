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
rem Only run pip when requirements.txt changed since the last successful install - about a second
rem saved on every start. The stamp is the file's size and date.
for %%F in (backend\requirements.txt) do set REQ_STAMP=%%~zF-%%~tF
set OLD_STAMP=
if exist venv\.requirements_stamp set /p OLD_STAMP=<venv\.requirements_stamp
if "%REQ_STAMP%"=="%OLD_STAMP%" goto deps_ok
echo Checking dependencies...
pip install -q -r backend\requirements.txt
if not errorlevel 1 >venv\.requirements_stamp echo %REQ_STAMP%
:deps_ok

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
