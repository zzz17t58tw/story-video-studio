@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Story Video Studio 啟動器

if not exist ".venv\Scripts\pythonw.exe" (
  echo [錯誤] 找不到 .venv，請先在這個資料夾執行：
  echo     python -m venv .venv
  echo     .venv\Scripts\python -m pip install fastapi uvicorn httpx pywebview pyinstaller python-multipart tzdata
  pause
  exit /b 1
)

REM 開發模式：用專屬 venv 的 pythonw 跑桌面視窗（不會跳 console 黑窗）
start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher.py" %*
exit /b 0
