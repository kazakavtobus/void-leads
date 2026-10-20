@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto NOPYTHON
py -3 -m pip install --upgrade pyinstaller
if errorlevel 1 goto FAILED
py -3 -m PyInstaller --clean --noconfirm --onefile --windowed --name VOID-Leads --add-data "static;static" app.py
if errorlevel 1 goto FAILED
py -3 -m PyInstaller --clean --noconfirm --onefile --windowed --name VOID-Updater updater.py
if errorlevel 1 goto FAILED
echo.
echo EXE compiled: dist\VOID-Leads.exe
echo Note: The installer requires Inno Setup or GitHub Actions.
pause
exit /b 0
:NOPYTHON
echo Install Python 3.10+ from python.org
pause
exit /b 1
:FAILED
echo Build failed. Copy the error above.
pause
exit /b 1
