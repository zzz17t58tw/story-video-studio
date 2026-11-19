@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Story Video Studio

REM 若伺服器已在跑，直接開瀏覽器，不重複啟動
curl -s -m 2 http://127.0.0.1:8788/api/health >nul 2>nul
if not errorlevel 1 (
  echo Story Video Studio 已在執行，開啟瀏覽器...
  start http://127.0.0.1:8788
  exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
  echo [錯誤] 找不到 python，請先安裝 Python 3.11 並加入 PATH。
  pause
  exit /b 1
)

python -c "import uvicorn" >nul 2>nul
if errorlevel 1 (
  echo 正在安裝 uvicorn（僅此一套）...
  python -m pip install uvicorn
)

echo Story Video Studio 啟動中...（關掉這個視窗就會停止程式）
start "" /b python -m uvicorn app.server:app --host 127.0.0.1 --port 8788
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8788
