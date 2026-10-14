@echo off
chcp 65001 >nul
title GoldAlert 状态
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\status_agent.ps1"
echo.
pause