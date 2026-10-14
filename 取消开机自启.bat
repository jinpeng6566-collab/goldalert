@echo off
chcp 65001 >nul
title GoldAlert 取消开机自启
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\startup.ps1" -Remove
echo.
pause