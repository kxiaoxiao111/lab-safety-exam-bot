@echo off
cd /d "%~dp0"
"%~dp0python\python.exe" "%~dp0exam_gui.py"
if errorlevel 1 (
    echo.
    echo   Program exited with error %errorlevel%
    pause
)
exit /b
