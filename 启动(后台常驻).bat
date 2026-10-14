@echo off
chcp 65001 >nul
title GoldAlert 后台启动
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_background.ps1"
echo.
pause