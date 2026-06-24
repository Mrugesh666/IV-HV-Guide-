@echo off
REM ═══════════════════════════════════════════════════════════════════
REM Options Manager v5.4 - Single Click Launcher (Advanced)
REM ═══════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

REM Navigate to script directory
cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ═══════════════════════════════════════════════════════════════════
    echo ERROR: Python is not installed or not in PATH
    echo ═══════════════════════════════════════════════════════════════════
    echo.
    echo Please install Python from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)

REM Check if the Python script exists
if not exist "options_manager.py" (
    echo.
    echo ═══════════════════════════════════════════════════════════════════
    echo ERROR: options_manager.py not found in current directory
    echo ═══════════════════════════════════════════════════════════════════
    echo.
    pause
    exit /b 1
)

REM Run the Python script
echo.
echo Starting OPTIONS MANAGER v5.4...
echo.
python options_manager.py

REM Capture exit code
set EXIT_CODE=!errorlevel!

if not !EXIT_CODE! equ 0 (
    echo.
    echo ═══════════════════════════════════════════════════════════════════
    echo Application exited with code: !EXIT_CODE!
    echo ═══════════════════════════════════════════════════════════════════
    echo.
)

pause
endlocal
