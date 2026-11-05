@echo off
cd /d "%~dp0"
title Lab Safety Exam - Setup
echo.
echo   Checking environment, please wait...
echo.
"%~dp0python\python.exe" "%~dp0setup_env.py"
if errorlevel 1 (
    echo.
    pause
)
exit /b
