@echo off
REM Build a double-clickable Leather-Drafting.exe on Windows.
REM
REM   cd packaging && build_windows.bat
REM
REM Result: dist\Leather-Drafting\Leather-Drafting.exe
REM Zip the whole dist\Leather-Drafting folder and send it to anyone.
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller "PySide6>=6.5" || exit /b 1
python -m pip install -e .. || exit /b 1

pyinstaller --noconfirm leather-drafting.spec || exit /b 1

REM verify the build actually launches and exits cleanly
"dist\Leather-Drafting\Leather-Drafting.exe" --smoke && echo BUILD OK: dist\Leather-Drafting\
