@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Сначала запусти «Установка.bat».
  pause
  exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m forlabs
