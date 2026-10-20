@echo off
setlocal
cd /d "%~dp0"
title VOID LEADS

echo Starting VOID LEADS...

where py >nul 2>nul
if errorlevel 1 goto TRY_PYTHON
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto TRY_PYTHON
py -3 "%~dp0app.py"
goto AFTER_RUN

:TRY_PYTHON
where python >nul 2>nul
if errorlevel 1 goto NO_PYTHON
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto NO_PYTHON
python "%~dp0app.py"
goto AFTER_RUN

:NO_PYTHON
echo.
echo [ERROR] Python 3.10 or newer is required.
echo Download Python from https://www.python.org/downloads/windows/
echo Select "Add Python to PATH" during setup, then try again.
pause
exit /b 1

:AFTER_RUN
if errorlevel 1 goto FAILED
exit /b 0

:FAILED
echo.
echo [ERROR] VOID LEADS did not start. Please copy the error above.
pause
exit /b 1
