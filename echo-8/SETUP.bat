@echo off
setlocal
cd /d "%~dp0"
title Echo setup
echo ============================================================
echo   Echo setup - gets everything Echo needs, step by step.
echo   Big downloads: Echo's brain about 5 GB, speech about 1.6 GB.
echo   Safe to run again any time - it skips what's already done.
echo ============================================================
echo.

echo [1/7] Python 3.12
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto python_ok
echo    Python 3.12 is not installed.
where winget >nul 2>&1
if errorlevel 1 goto python_manual
choice /m "   Install Python 3.12 now with winget"
if errorlevel 2 goto python_manual
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto python_ok
:python_manual
echo    Install Python 3.12 from python.org, then run SETUP.bat again.
pause
exit /b 1
:python_ok
echo    ok

echo [2/7] Ollama - runs the AI models
where ollama >nul 2>&1
if not errorlevel 1 goto ollama_ok
echo    Ollama is not installed.
where winget >nul 2>&1
if errorlevel 1 goto ollama_manual
choice /m "   Install Ollama now with winget"
if errorlevel 2 goto ollama_manual
winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
where ollama >nul 2>&1
if not errorlevel 1 goto ollama_ok
echo    Ollama was installed - close this window, open a new one and run SETUP.bat again.
pause
exit /b 1
:ollama_manual
echo    Install Ollama from ollama.com, then run SETUP.bat again.
pause
exit /b 1
:ollama_ok
ollama list >nul 2>&1
if errorlevel 1 start "Ollama" /min ollama serve
timeout /t 3 /nobreak >nul
echo    ok

echo [3/7] Echo's Python packages
if not exist venv py -3.12 -m venv venv
call venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r backend\requirements.txt
if errorlevel 1 echo    Some packages failed to install - check the messages above.
echo    ok

echo [4/7] Echo's brain - hermes3 plus Echo's personality
ollama list | findstr /i /b "echo:" >nul
if not errorlevel 1 goto brain_ok
ollama pull hermes3
python -c "import sys; sys.path.insert(0, 'backend'); import ollama_control as o; from plugins import pc_stats; g = pc_stats._gpu(); n = o.size_for_card((g or {}).get('vram_total_gb')); o.write_context_tokens(n); print('   context window: %%d tokens, to fit this graphics card' %% n)"
ollama create echo -f backend\Modelfile
:brain_ok
ollama list | findstr /i /b "echo-mini:" >nul
if not errorlevel 1 goto mini_ok
echo    Echo Mini - a small helper brain for a second caller, about 2 GB
python -c "import sys; sys.path.insert(0, 'backend'); import brain; brain.install_helper(); print('   ' + (brain.status['error'] or 'Echo Mini ready'))"
:mini_ok
echo    ok

echo [5/7] Speech recognition - Whisper large-v3-turbo
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8'); print('   ok')"

echo [6/7] Sandbox Python - where Echo runs code it writes
if exist tools\sandbox-python\python.exe goto sandbox_ok
powershell -NoProfile -Command "$d='tools\_dl'; New-Item -ItemType Directory -Force $d | Out-Null; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile \"$d\py.zip\"; Expand-Archive \"$d\py.zip\" -DestinationPath 'tools\sandbox-python' -Force; Remove-Item $d -Recurse -Force"
:sandbox_ok
echo    ok

echo [7/7] Optional extras
ollama list | findstr /i "qwen2.5vl" >nul
if not errorlevel 1 goto eyes_done
choice /m "   Download Echo's eyes - screen, camera, TV screenshots - about 6 GB"
if errorlevel 2 goto eyes_done
ollama pull qwen2.5vl:7b
:eyes_done
if exist tools\platform-tools\adb.exe goto tv_done
choice /m "   Download the Google TV control tool - for Netflix and apps on a Google TV"
if errorlevel 2 goto tv_done
powershell -NoProfile -Command "$d='tools\_dl'; New-Item -ItemType Directory -Force $d | Out-Null; Invoke-WebRequest -UseBasicParsing 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile \"$d\pt.zip\"; Expand-Archive \"$d\pt.zip\" -DestinationPath 'tools' -Force; Remove-Item $d -Recurse -Force"
:tv_done
where node >nul 2>&1
if not errorlevel 1 goto node_done
where winget >nul 2>&1
if errorlevel 1 goto node_done
choice /m "   Install Node.js - lets Echo check the apps it builds"
if errorlevel 2 goto node_done
winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
:node_done

echo.
echo ============================================================
echo   Done. Start Echo with ECHO.bat.
echo   Then open the dashboard's Setup tab to check everything,
echo   and set your secret passcode next to the Shut Down PC toggle.
echo ============================================================
pause
endlocal
