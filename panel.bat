@echo off
rem Audio Enhancer control panel: starts the local server and opens it in the browser
cd /d "%~dp0"
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" panel\server.py
