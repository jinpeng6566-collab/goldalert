@echo off
chcp 65001 >nul
title GoldAlert 停止
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_agent.ps1"
echo.
pause