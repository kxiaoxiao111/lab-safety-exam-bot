@echo off
cd /d "%~dp0"
title Lab Safety Exam
"%~dp0python\python.exe" "%~dp0auto_exam_hybrid.py"
if errorlevel 1 pause
exit /b
