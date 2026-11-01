@echo off
setlocal
chcp 65001 >nul
title FPGA眼控 + ZED + PL UART云台（默认未ARM）
cd /d "%~dp0"
"%~dp0..\..\.venv\Scripts\python.exe" -B -u "%~dp0main_fpga_gimbal.py"
set "eye_gimbal_exit=%errorlevel%"
if not "%eye_gimbal_exit%"=="0" (
    echo.
    echo 完整项目启动或运行失败，请查看上方错误。
    pause
)
exit /b %eye_gimbal_exit%
