@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Установка ForLabs...
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>nul
if errorlevel 1 (
  echo Нужен Python 3.11 или новее: https://www.python.org/downloads/ ^(при установке отметь "Add python.exe to PATH"^)
  pause
  exit /b 1
)
if not exist .venv %PY% -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e .[windows]
if errorlevel 1 (
  echo Не получилось установить библиотеки. Проверь интернет и запусти ещё раз.
  pause
  exit /b 1
)
.venv\Scripts\python -m forlabs.selfcheck
echo.
echo Готово. Запускай ForLabs файлом «Запуск.bat».
pause
