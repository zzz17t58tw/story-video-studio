"""集中處理路徑：開發模式（python 直接跑）與打包模式（PyInstaller）兩種情況。

規則：
- **程式資源**（static/、templates/）→ 唯讀，跟著程式走
  * dev    : <repo>/static、<repo>/templates
  * frozen : PyInstaller 解壓目錄（sys._MEIPASS，onedir 時是 _internal/）
- **使用者資料**（projects/）→ 可寫，跟著 exe／專案根走，**不打包進 exe**
  * 由 launcher 用環境變數 SVS_DATA_DIR 指定（--data-dir）
  * 未指定時：frozen → exe 所在目錄/projects；dev → repo/projects

其他模組一律 `from . import paths` 後用 paths.STATIC_DIR / paths.TEMPLATES_DIR / paths.PROJECTS_DIR，
不要各自 `Path(__file__)`（打包後 __file__ 指到 _internal/app/paths.py，會解析錯）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否跑在 PyInstaller 打包後的 exe 裡。"""
    return bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """唯讀資源根目錄（static/templates 所在）。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        # onedir 沒有 _MEIPASS 時的備援：exe 所在目錄
        return Path(sys.executable).resolve().parent
    # dev：app/paths.py → repo 根
    return Path(__file__).resolve().parent.parent


def _app_dir() -> Path:
    """可寫資料根目錄（projects/ 的父層）。"""
    env = os.environ.get("SVS_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _bundle_dir()


BUNDLE_DIR = _bundle_dir()
APP_DIR = _app_dir()  # 可寫資料根（projects/ 的父層）

STATIC_DIR = BUNDLE_DIR / "static"
TEMPLATES_DIR = BUNDLE_DIR / "templates"

# SVS_DATA_DIR / --data-dir 的語義＝**projects 資料夾本身**；
# 但為了容忍有人傳了它的父層，這裡做一次智慧判斷。
_env_data = (os.environ.get("SVS_DATA_DIR") or "").strip()
if _env_data:
    _d = Path(_env_data).expanduser()
    PROJECTS_DIR = _d if _d.name.lower() == "projects" else _d / "projects"
else:
    PROJECTS_DIR = APP_DIR / "projects"

# 舊程式（M1）用 storage.ROOT 的地方都改指這裡：dev 時等於 repo 根
ROOT = BUNDLE_DIR


def describe() -> dict:
    return {
        "frozen": is_frozen(),
        "bundle": str(BUNDLE_DIR),
        "data": str(APP_DIR),
        "static": str(STATIC_DIR),
        "templates": str(TEMPLATES_DIR),
        "projects": str(PROJECTS_DIR),
    }
