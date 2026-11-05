@echo off
cd /d "%~dp0"
title Setup - Lab Safety Exam
echo.
echo   Checking environment...
echo.
"%~dp0python\python.exe" "%~dp0setup_env.py"
if errorlevel 1 pause
exit /b
