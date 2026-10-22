@echo off
rem Run only the backend (then open http://127.0.0.1:8765 in a browser)
cd /d "%~dp0backend"
.venv\Scripts\python.exe -m cazung
pause
