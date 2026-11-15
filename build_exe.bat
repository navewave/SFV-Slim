@echo off
rem Rebuilds SFV-Slim.exe from the scripts (needs: python -m pip install --user pyinstaller pycryptodome numpy)
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --console --name SFV-Slim --distpath . --workpath "%TEMP%\sfv-slim-build" --specpath "%TEMP%\sfv-slim-build" --add-data "%~dp0stub\stub.wmv;stub" --hidden-import optimize --hidden-import bcscale --hidden-import shaderlib --hidden-import texlib --exclude-module tkinter wizard.py
pause
