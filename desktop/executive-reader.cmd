@echo off
REM Start Executive Reader in the system tray.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src"
start "" "%HERE%.venv\Scripts\pythonw.exe" -m executive_reader %*
