# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包設定：Story Video Studio 桌面版。

產出：dist/StoryVideoStudio/StoryVideoStudio.exe（onedir）
- --noconsole：不跳黑窗
- static/、templates/、assets/（icon）一併打包到 _internal（由 app/paths.py 的 sys._MEIPASS 解析）
- projects/ **不打包**：exe 旁自建 projects/，或 --data-dir 指向既有資料夾

重建：
    .venv\\Scripts\\pyinstaller --noconfirm --clean StoryVideoStudio.spec
"""
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    ("static", "static"),
    ("templates", "templates"),
]
import os

if os.path.isdir("assets"):
    datas.append(("assets", "assets"))

# uvicorn 的 loop/http/protocol 實作是動態 import，PyInstaller 抓不到 → 明確收
hiddenimports = (
    collect_submodules("uvicorn")
    + [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "websockets",
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",
        "app.server",
        "app.storage",
        "app.paths",
        "app.comfy",
        "app.assemble",
        "app.h3gen",
        "app.h3compose",
        "app.imagegen",
        "app.llm",
        "app.prompts",
        "app.storygen",
        "app.gpu",
    ]
)

excludes = ["tkinter", "matplotlib", "numpy", "pandas", "scipy", "PIL", "PyQt5", "PySide2", "PySide6"]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StoryVideoStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不跳 console 黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico" if os.path.isfile("assets/app.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StoryVideoStudio",
)
