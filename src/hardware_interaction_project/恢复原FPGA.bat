@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -B "%~dp0恢复原FPGA.py"
if errorlevel 1 pause
