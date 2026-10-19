@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV_DIR=%LOCALAPPDATA%\SecurityScanDatasetTool\venv"
set "LOG_FILE=%~dp0startup.log"

if not exist "%VENV_DIR%\Scripts\python.exe" goto SETUP

"%VENV_DIR%\Scripts\python.exe" app.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto FAILED
exit /b 0

:SETUP
call "%~dp0setup_and_run.bat"
exit /b %errorlevel%

:FAILED
echo.
echo The program failed to start. Error details are in:
echo %LOG_FILE%
echo.
type "%LOG_FILE%"
echo.
pause
exit /b 1
