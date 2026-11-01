@echo off
setlocal
chcp 65001 >nul
title FPGA眼控 + 双目 + 真实PL UART云台
cd /d "%~dp0"
"%~dp0..\..\.venv\Scripts\python.exe" -B -u "%~dp0main_fpga_gimbal.py" --physical-uart --arm-gimbal --seed-pan-steps 0 --seed-tilt-steps 0 --interface-profile-file "%~dp0physical_launch\interface_profile.json"
set "eye_physical_exit=%errorlevel%"
if not "%eye_physical_exit%"=="0" (
    echo.
    echo 完整项目启动或运行失败，请查看上方错误。
    pause
)
exit /b %eye_physical_exit%
