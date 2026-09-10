@echo off
REM Start Earmark in the system tray.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src"
start "" "%HERE%.venv\Scripts\pythonw.exe" -m earmark %*
