@echo off
chcp 65001 >nul
title GoldAlert 目标价位设置
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m goldalert.levels_ui
echo.
echo   正在打开「目标价位设置」页面（会自动弹出浏览器）...
echo   如果没反应，把 logs\levels_ui_error.log 发给我。
echo.
ping -n 5 127.0.0.1 >nul