@echo off
chcp 65001 >nul
title GoldAlert 实时监控
cd /d "%~dp0"
echo.
echo   正在启动 GoldAlert 实时监控...（按 Ctrl+C 可停止）
echo.
".venv\Scripts\python.exe" run.py %*
echo.
echo   已退出。
pause