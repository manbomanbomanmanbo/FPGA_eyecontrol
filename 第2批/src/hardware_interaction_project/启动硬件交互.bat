@echo off
setlocal
chcp 65001 >nul
title 硬件交互
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -B -u "%~dp0launch_hardware_interaction.py"
if errorlevel 1 pause
exit /b %errorlevel%
