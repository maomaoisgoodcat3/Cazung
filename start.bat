@echo off
rem Start Cazung (the desktop app starts the backend by itself)
cd /d "%~dp0"
set ELECTRON_RUN_AS_NODE=
if not exist "frontend\node_modules\electron\dist\electron.exe" (
    echo Chua cai dat. Hay chay setup.bat truoc.
    pause
    exit /b 1
)
start "" "frontend\node_modules\electron\dist\electron.exe" "frontend"
