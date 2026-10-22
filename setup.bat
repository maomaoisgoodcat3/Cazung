@echo off
rem Cazung - one-time setup (everything is installed inside this folder)
cd /d "%~dp0"

echo [1/3] Python environment (backend\.venv)...
if not exist "backend\.venv\Scripts\python.exe" (
    python -m venv backend\.venv
    if errorlevel 1 goto :err
)
backend\.venv\Scripts\python.exe -m pip install --no-cache-dir -q -r backend\requirements.txt
if errorlevel 1 goto :err

echo [2/3] Electron (frontend\node_modules)...
set "electron_config_cache=%~dp0frontend\.electron-cache"
pushd frontend
call npm install --no-audit --no-fund
set NPMERR=%errorlevel%
popd
if not "%NPMERR%"=="0" goto :err

echo [3/3] AI models (Ollama)...
where ollama >nul 2>nul
if errorlevel 1 (
    echo   Chua co Ollama. Tai tai https://ollama.com/download roi chay lai setup.bat
    goto :done
)
ollama pull dolphin3
ollama pull nomic-embed-text

:done
echo.
echo Xong! Chay start.bat de goi Cazung.
pause
exit /b 0

:err
echo.
echo Co loi, xem thong bao o tren.
pause
exit /b 1
