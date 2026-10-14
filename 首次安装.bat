@echo off
chcp 65001 >nul
title GoldAlert 首次安装
cd /d "%~dp0"

echo ============================================================
echo    GoldAlert 现货黄金监控 - 首次安装
echo ============================================================
echo.

rem ---------- 1. 找 Python ----------
set "PY="
where py >nul 2>&1
if %errorlevel%==0 set "PY=py -3"
if defined PY goto :got_python
where python >nul 2>&1
if %errorlevel%==0 set "PY=python"
if defined PY goto :got_python

echo   没有找到 Python，装不了。
echo.
echo   请先安装 Python 3.10 或更高版本：
echo     打开 https://www.python.org/downloads/
echo     下载 Windows 安装包，安装时务必勾选 "Add python.exe to PATH"
echo   装完后重新双击本文件。
echo.
pause
exit /b 1

:got_python
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :bad_version
goto :ask

:bad_version
echo   检测到 Python 版本太老，需要 3.10 或更高。
%PY% -V
echo.
echo   请到 https://www.python.org/downloads/ 装一个新版本再回来。
echo.
pause
exit /b 1

rem ---------- 2. 填写邮箱和密钥 ----------
:ask
echo   第一步：填写邮箱和密钥（不写进代码，只存在你本机的配置文件里）
echo.
%PY% -X utf8 "安装向导.py"
if not exist "config.json" goto :no_config
if not exist ".env" goto :no_config
goto :venv

:no_config
echo.
echo   配置没有生成，安装中断了。重新双击本文件可以再来一次。
echo.
pause
exit /b 1

rem ---------- 3. 建虚拟环境 ----------
:venv
echo.
echo   第二步：准备运行环境
if exist ".venv\Scripts\python.exe" goto :pip
echo   正在创建虚拟环境，大概十几秒...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto :venv_fail
echo   虚拟环境已创建。

:venv_fail
if not exist ".venv\Scripts\python.exe" (
    echo   创建虚拟环境失败。
    echo   如果提示找不到 venv 模块，请重新安装 Python 并勾选全部组件。
    echo.
    pause
    exit /b 1
)

rem ---------- 4. 装依赖 ----------
:pip
echo   正在安装依赖，第一次大约需要 1 分钟...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 goto :pip_fail
echo   依赖安装完成。
goto :doctor

:pip_fail
echo.
echo   依赖安装失败，通常是网络问题。
echo   可以换个网络，或者挂上代理之后重新双击本文件。
echo.
pause
exit /b 1

rem ---------- 5. 自检 ----------
:doctor
echo.
echo   第三步：自检（检查代理、数据源、邮件、AI 能不能用）
echo.
".venv\Scripts\python.exe" -X utf8 run.py doctor
echo.
echo ============================================================
echo    安装完成
echo.
echo    下一步：双击同目录下的「启动(后台常驻).bat」就开始盯盘。
echo    想随时改关键价位，双击「设置目标价位.bat」。
echo    想让它开机自动跑，双击「安装开机自启.bat」。
echo ============================================================
echo.
pause
exit /b 0