@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV_DIR=%LOCALAPPDATA%\SecurityScanDatasetTool\venv"
set "LOG_FILE=%~dp0startup.log"
set "PYTHON_EXE="
set "PYTHON_ARGS="

echo Security Scan Dataset Tool startup > "%LOG_FILE%"
echo Project: %~dp0 >> "%LOG_FILE%"

py.exe -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if errorlevel 1 goto TRY_PYTHON
set "PYTHON_EXE=py.exe"
set "PYTHON_ARGS=-3"
goto PYTHON_FOUND

:TRY_PYTHON
python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if errorlevel 1 goto TRY_313
set "PYTHON_EXE=python.exe"
goto PYTHON_FOUND

:TRY_313
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" goto TRY_312
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if not errorlevel 1 goto PYTHON_FOUND

:TRY_312
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" goto TRY_311
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if not errorlevel 1 goto PYTHON_FOUND

:TRY_311
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" goto TRY_310
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if not errorlevel 1 goto PYTHON_FOUND

:TRY_310
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not exist "%PYTHON_EXE%" goto PYTHON_MISSING
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>> "%LOG_FILE%"
if not errorlevel 1 goto PYTHON_FOUND

:PYTHON_MISSING
set "PYTHON_EXE="
echo.
echo Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo During installation, select "Add Python to PATH", then run this file again.
echo.
echo ERROR: Python was not found. >> "%LOG_FILE%"
pause
exit /b 1

:PYTHON_FOUND
echo Python command: %PYTHON_EXE% %PYTHON_ARGS% >> "%LOG_FILE%"

if exist "%VENV_DIR%\Scripts\python.exe" goto INSTALL
echo Creating the Python environment. Please wait...
"%PYTHON_EXE%" %PYTHON_ARGS% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto FAILED

:INSTALL
echo Installing required packages. This may take several minutes on first run...
"%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 300 -r "%~dp0requirements.txt" >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto FAILED

echo Starting the application...
"%VENV_DIR%\Scripts\python.exe" "%~dp0app.py" >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto FAILED
exit /b 0

:FAILED
echo.
echo Setup or startup failed. Error details are in:
echo %LOG_FILE%
echo.
type "%LOG_FILE%"
echo.
pause
exit /b 1
