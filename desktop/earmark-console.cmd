@echo off
REM Start Earmark with a console window, for reading errors.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src"
"%HERE%.venv\Scripts\python.exe" -m earmark %*
