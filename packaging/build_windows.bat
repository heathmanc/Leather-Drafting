@echo off
REM Build a double-clickable Stitch Hero.exe on Windows.
REM
REM   cd packaging && build_windows.bat
REM
REM Result: dist\Stitch Hero\Stitch Hero.exe
REM Zip the whole dist\Stitch Hero folder and send it to anyone.
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller "PySide6>=6.5" || exit /b 1
python -m pip install -e .. || exit /b 1

pyinstaller --noconfirm leather-drafting.spec || exit /b 1

REM verify the build actually launches and exits cleanly
"dist\Stitch Hero\Stitch Hero.exe" --smoke && echo BUILD OK: dist\Stitch Hero\
