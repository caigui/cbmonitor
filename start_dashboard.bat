@echo off
setlocal EnableExtensions
chcp 936 >nul
title CBMonitor Launcher (8100 + 5174)

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BACKEND=%ROOT%\backend"

echo ========================================
echo   CBMonitor 可转债监控
echo   后端 8100 / 前端 5174 · FastAPI + Vite 双进程
echo ========================================
echo.

REM ---------- [1/4] Python 解释器：项目 .venv 优先，否则用 PATH 里的 python ----------
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM ---------- [2/4] 清理环境变量（照抄 lofarb-master/start_dashboard.bat 的踩坑经验） ----------
REM PYTHONPATH：防止继承 QMT / WorkBuddy 等外部路径，导致 import 串味或旧版 defusedxml 覆盖 venv
set "PYTHONPATH="
REM 代理：本机 VPN 若设了 HTTP(S)_PROXY，requests 会被劫持，行情抓取全部 ProxyError 失败
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "ALL_PROXY="
set "all_proxy="

REM ---------- [2.5/4] 依赖自检：FastAPI 全套不在标准库，缺了直接起不来 ----------
"%PY%" -c "import fastapi, uvicorn, sqlalchemy, pydantic" >nul 2>&1
if errorlevel 1 (
  echo [FATAL] 当前 Python 缺少依赖: fastapi / uvicorn / sqlalchemy / pydantic
  echo [FATAL] 当前解释器: %PY%
  echo.
  echo 建议先建独立虚拟环境再装（requirements 里钉了 numpy==1.26，直接装进系统 Python 会降级冲突）:
  echo     cd /d "%BACKEND%"
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo 装好后重新双击本脚本（脚本会自动优先用 %ROOT%\.venv）
  echo.
  pause
  exit /b 1
)

REM ---------- [2.6/4] 前端依赖：node_modules 缺失时先 npm install ----------
where npm >nul 2>&1
if errorlevel 1 (
  echo [FATAL] 未找到 npm，请先安装 Node.js（前端需要 Vite 开发服务器）
  pause
  exit /b 1
)
if not exist "%ROOT%\frontend\node_modules" (
  echo [首次运行] 未检测到 frontend\node_modules，开始 npm install（约 1~3 分钟）...
  pushd "%ROOT%\frontend"
  call npm install
  popd
)


REM ---------- [3/4] 释放端口 8100：上次没关干净的残留进程先强杀 ----------
REM 用 Get-NetTCPConnection 取 OwningProcess 比 netstat 解析稳，且不会误杀无关进程
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "for($i=0;$i -lt 5;$i++){ if(-not (Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue)){ break }; Start-Sleep -Seconds 1 }"

REM ---------- [3.5/4] 顺带释放前端端口 5174 ----------
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5174 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [1/2] 启动后端 (8100) ...
start "CBMonitor Backend" cmd /k "cd /d %BACKEND% && %PY% main.py"
timeout /t 5 /nobreak >nul

echo [2/2] 启动前端 (5174) ...
start "CBMonitor Frontend" cmd /k "cd /d %ROOT%\frontend && npm run dev"
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:5174/"

echo.
echo ========================================
echo   后端 API : http://127.0.0.1:8100
echo   API 文档 : http://127.0.0.1:8100/docs
echo   前端页面 : http://127.0.0.1:5174
echo   停止：分别在前端/后端窗口按 Ctrl+C
echo ========================================
timeout /t 3 /nobreak >nul
