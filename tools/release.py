#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一鍵同步發佈：打包 exe → 同步使用端（dist）→ 重建乾淨版（release）→ 壓 zip → 發佈紀錄 → git commit。

用法（在專案根目錄）：
    python tools/release.py                  # 完整流程（版號取 VERSION）
    python tools/release.py --skip-build     # 不重新打包，只重建使用端與乾淨版（用現有 dist 的 exe）
    python tools/release.py --version 0.9.1  # 不改 VERSION，本次標記成 0.9.1
    python tools/release.py --allow-dirty    # git 有未提交變更時仍繼續（預設只警告，也會繼續）
    python tools/release.py --no-commit      # 最後不做 git commit
    python tools/release.py --no-zip         # 跳過壓 zip（快速驗證用）

安全規則（寫死在程式裡，不提供關閉選項）：
    * 永不刪除、永不覆寫 `dist/StoryVideoStudio/projects/` 內任何東西
    * 永不刪除根目錄 `projects/`
    * 乾淨版 projects 只放 SEED_PROJECT 這一個專案

Windows 上呼叫 pyinstaller 用的是專案自己的 `.venv`（Python 3.14 + pywebview / pyinstaller）；
本腳本可以用任何有標準庫的 Python 3.9+ 執行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "StoryVideoStudio"
SPEC = ROOT / f"{NAME}.spec"
BUILD_DIR = ROOT / "dist" / NAME          # 使用端（實際在跑的 exe，資料在此）
WORK_DIR = ROOT / "build" / "release-work"  # pyinstaller 的暫存輸出（絕不能指到 dist：--noconfirm 會整個刪掉重寫）
RELEASE_DIR = ROOT / "release"
CLEAN_DIR = RELEASE_DIR / NAME            # 乾淨版資料夾
SEED_PROJECT = "project-20261118-022129"  # 《雨夜的便利商店》＝品質基準專案
VERSION_FILE = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"
NOTES_DIR = ROOT / "docs"
REQUIRED_CLEAN_TOP = ["StoryVideoStudio.exe", "_internal", "projects", "使用說明.txt"]

VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / "Scripts" / "python.exe" if os.name == "nt" else VENV_DIR / "bin" / "python"
VENV_PYI = VENV_DIR / "Scripts" / "pyinstaller.exe" if os.name == "nt" else VENV_DIR / "bin" / "pyinstaller"


# ── 輸出小工具 ────────────────────────────────────────────────
def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}", flush=True)


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def fail(msg: str, code: int = 1):
    print(f"\n✗ 失敗：{msg}", flush=True)
    sys.exit(code)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_dirs(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for p in path.iterdir() if p.is_dir() and not p.name.startswith("_"))


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def read_version() -> str:
    if not VERSION_FILE.is_file():
        fail(f"找不到 {rel(VERSION_FILE)}（內容應為一行版本號，例如 0.9.0）")
    v = VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        fail(f"VERSION 格式錯誤：{v!r}（應為 主.次.修，例如 0.9.0）")
    return v


# ── git ───────────────────────────────────────────────────────
def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        fail(f"git {' '.join(args)} 失敗：{(r.stderr or r.stdout).strip()}")
    return r


def git_repo() -> bool:
    r = git("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def git_dirty_files() -> list[str]:
    r = git("status", "--porcelain")
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def git_head_subject() -> str:
    r = git("log", "-1", "--pretty=%h %s")
    return r.stdout.strip() if r.returncode == 0 else "（無 commit）"


# ── 步驟 2：打包 ──────────────────────────────────────────────
def build() -> None:
    if not SPEC.is_file():
        fail(f"找不到 spec：{rel(SPEC)}")
    pyi = VENV_PYI if VENV_PYI.is_file() else shutil.which("pyinstaller")
    if not pyi:
        fail("找不到 pyinstaller：預期在 .venv\\Scripts\\pyinstaller.exe，或 PATH 中")
    info(f"pyinstaller：{pyi}")
    info(f"輸出到暫存 {rel(WORK_DIR)}（不碰 {rel(BUILD_DIR)}）")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    # 注意 --distpath：spec 預設把產出丟到 dist/StoryVideoStudio，而 --noconfirm 會把整個目標資料夾刪掉重寫
    # → 使用端的 projects/ 會被帶走。所以一定導到 build/release-work，由步驟 3 只覆寫 exe 與 _internal。
    r = subprocess.run([str(pyi), "--noconfirm", "--distpath", str(WORK_DIR), SPEC.name],
                       cwd=str(ROOT), text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        fail(f"pyinstaller 失敗（exit {r.returncode}），請看上方輸出")
    exe = WORK_DIR / NAME / f"{NAME}.exe"
    if not exe.is_file():
        fail(f"打包自稱成功但找不到 exe：{rel(exe)}")
    if not (WORK_DIR / NAME / "_internal").is_dir():
        fail(f"打包自稱成功但找不到 {rel(WORK_DIR / NAME / '_internal')}")
    info(f"build 完成 {time.time() - t0:.1f}s → {rel(exe)}（{exe.stat().st_size:,} B）")


# ── 步驟 3：同步使用端 dist（絕不動 projects/）────────────────
def sync_user_install(version: str) -> str:
    src = WORK_DIR / NAME
    exe_src = src / f"{NAME}.exe"
    int_src = src / "_internal"
    if not exe_src.is_file() or not int_src.is_dir():
        fail(f"{rel(src)} 裡沒有新的 exe/_internal，無法同步（先跑完整打包，或別加 --skip-build）")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    projects_dir = BUILD_DIR / "projects"
    before = count_dirs(projects_dir)
    info(f"使用端 projects 目前 {before} 個專案（同步過程不會動它）")

    # 舊 exe 備份（可回滾）；第一次發佈還沒有 exe 時不做備份
    exe_dst = BUILD_DIR / f"{NAME}.exe"
    bak = BUILD_DIR / f"{NAME}.exe.bak-{version}"
    if exe_dst.is_file():
        shutil.copy2(exe_dst, bak)
        info(f"備份舊 exe → {rel(bak)}（{bak.stat().st_size:,} B）")
    else:
        info(f"（首次發佈，沒有舊 exe 可備份）")

    # exe 是執行中的話覆寫會失敗，先檢查
    if os.name == "nt":
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-Process -Name {NAME} -ErrorAction SilentlyContinue | Measure-Object).Count"],
                           capture_output=True, text=True)
        if r.stdout.strip() not in ("", "0"):
            fail(f"{NAME}.exe 正在執行中，請先關閉程式再發佈（避免覆寫到執行中的檔案）")

    shutil.copy2(exe_src, exe_dst)
    info(f"新 exe → {rel(exe_dst)}（{exe_dst.stat().st_size:,} B）")

    # _internal：整包替換（先搬到暫存再刪，避免半新半舊）
    int_dst = BUILD_DIR / "_internal"
    tmp = BUILD_DIR / "_internal.old-release-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    if int_dst.exists():
        int_dst.rename(tmp)
    shutil.copytree(int_src, int_dst)
    info(f"_internal 已替換（{sum(1 for _ in int_dst.rglob('*'))} 個項目）")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    after = count_dirs(projects_dir)
    if after != before:
        fail(f"使用端 projects 數量異常：發佈前 {before} → 發佈後 {after}（不應改變）")
    info(f"使用端 projects 仍是 {after} 個專案 ✓（未動）")
    return str(bak)


# ── 步驟 4：重建乾淨版 ────────────────────────────────────────
def rebuild_clean(version: str) -> Path:
    seed_src = ROOT / "projects" / SEED_PROJECT
    if not seed_src.is_dir():
        fail(f"找不到成果專案 {rel(seed_src)}，乾淨版需要它")
    if not (seed_src / "project.json").is_file():
        fail(f"{rel(seed_src)} 內沒有 project.json")
    src = WORK_DIR / NAME
    exe_src = src / f"{NAME}.exe"

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if CLEAN_DIR.exists():
        info(f"清空 {rel(CLEAN_DIR)}（只清乾淨發佈包，不動 dist 與開發資料）")
        shutil.rmtree(CLEAN_DIR)
    CLEAN_DIR.mkdir(parents=True)

    shutil.copy2(exe_src, CLEAN_DIR / f"{NAME}.exe")
    info(f"exe → {rel(CLEAN_DIR / f'{NAME}.exe')}")
    shutil.copytree(src / "_internal", CLEAN_DIR / "_internal")
    info(f"_internal → {rel(CLEAN_DIR / '_internal')}")

    (CLEAN_DIR / "projects").mkdir()
    shutil.copytree(seed_src, CLEAN_DIR / "projects" / SEED_PROJECT)
    n_proj = count_dirs(CLEAN_DIR / "projects")
    info(f"projects → 只有 {n_proj} 個專案（{SEED_PROJECT}）")

    usage_src = ROOT / "docs" / "使用說明.txt"
    if usage_src.is_file():
        shutil.copy2(usage_src, CLEAN_DIR / "使用說明.txt")
    elif (RELEASE_DIR / "使用說明.txt").is_file():
        shutil.copy2(RELEASE_DIR / "使用說明.txt", CLEAN_DIR / "使用說明.txt")
    else:
        usage_txt = CLEAN_DIR / "使用說明.txt"
        usage_txt.write_text(
            "Story Video Studio（乾淨版）\n"
            f"版本：v{version}\n\n"
            "1. 把整個 StoryVideoStudio 資料夾解壓到你要放的位置，雙擊 StoryVideoStudio.exe 即可。\n"
            "2. StoryVideoStudio.exe 與 _internal 是一組的，不可只複製 exe。\n"
            "3. 你的資料都在 exe 旁的 projects 資料夾，備份就是複製整個 projects。\n"
            "4. 要生成圖片或影片，請先啟動你自己的 ComfyUI（:8000）。\n"
            "5. 已知限制見程式內設定頁與 CHANGELOG.md。\n",
            encoding="utf-8")
        info("找不到 docs/使用說明.txt，已寫入最小說明檔")
    info("使用說明.txt ✓")

    # 殘留物清掉（乾淨版不能有日誌／快取）
    for junk in CLEAN_DIR.rglob("_internal/launcher.log"):
        junk.unlink(missing_ok=True)
    for p in CLEAN_DIR.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)

    missing = [x for x in REQUIRED_CLEAN_TOP if not (CLEAN_DIR / x).exists()]
    if missing:
        fail(f"乾淨版缺少必要項目：{missing}")
    top = sorted(p.name for p in CLEAN_DIR.iterdir())
    info(f"乾淨版結構：{top}")
    return CLEAN_DIR


# ── 步驟 5：壓 zip ────────────────────────────────────────────
def make_zip(version: str) -> tuple[Path, int]:
    zpath = RELEASE_DIR / f"{NAME}-v{version}-乾淨版.zip"
    if zpath.exists():
        zpath.unlink()
    t0 = time.time()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(CLEAN_DIR.rglob("*")):
            z.write(p, Path(NAME) / p.relative_to(CLEAN_DIR))
    size = zpath.stat().st_size
    info(f"zip → {rel(zpath)}（{size:,} B / {human(size)}，{time.time() - t0:.1f}s）")
    return zpath, size


# ── 步驟 6：發佈紀錄 ──────────────────────────────────────────
def changelog_section(version: str) -> str:
    if not CHANGELOG.is_file():
        return "（CHANGELOG.md 不存在）"
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else f"（CHANGELOG.md 尚無 [{version}] 段落）"


def write_notes(version: str, exe_sha: str, zip_path: Path | None, zip_sha: str,
                zip_size: int, bak_name: str, build_time: str) -> Path:
    now = datetime.now()
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    out = NOTES_DIR / f"RELEASE-NOTES-v{version}.md"
    n_proj_user = count_dirs(BUILD_DIR / "projects")
    n_proj_clean = count_dirs(CLEAN_DIR / "projects")
    body = f"""# 發佈紀錄 v{version}

| 項目 | 值 |
|---|---|
| 版本 | **{version}**（預發版） |
| 日期 | {now.strftime('%Y-%m-%d')} |
| 發佈時間 | {now.strftime('%Y-%m-%d %H:%M:%S')}（台北） |
| build 時間 | {build_time} |
| git HEAD（發佈前） | {git_head_subject() if git_repo() else '非 git repo'} |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/{NAME}/{NAME}.exe` | {human((BUILD_DIR / f'{NAME}.exe').stat().st_size)} | `{exe_sha}` |
"""
    if zip_path:
        body += f"| 乾淨版 zip | `{rel(zip_path)}` | {human(zip_size)}（{zip_size:,} B） | `{zip_sha}` |\n"
    else:
        body += "| 乾淨版 zip | （本次 --no-zip 跳過） | — | — |\n"
    body += f"""
舊 exe 備份：`dist/{NAME}/{bak_name}`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/{NAME}/                 ← 你日常使用的版本（資料在此）
├── {NAME}.exe
├── {NAME}.exe.bak-{version}  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← {n_proj_user} 個專案（發佈期間未動）

release/{NAME}/              ← 乾淨發佈包
├── {NAME}.exe
├── _internal/
├── projects/{SEED_PROJECT}/  ← 只有《雨夜的便利商店》（{n_proj_clean} 個）
└── 使用說明.txt
"""
    if zip_path:
        body += f"release/{zip_path.name}\n```\n"
    else:
        body += "```\n"
    body += f"""
## 本版變更摘要

{changelog_section(version)}

## 驗證

- 使用端 `dist/{NAME}/projects` 發佈前後皆為 {n_proj_user} 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 {n_proj_clean} 個專案：{SEED_PROJECT}
- 乾淨版頂層必要項目齊全：{', '.join(REQUIRED_CLEAN_TOP)}
- 核對雜湊：`sha256sum dist/{NAME}/{NAME}.exe`（或 `certutil -hashfile`）與上表比對
"""
    out.write_text(body, encoding="utf-8")
    info(f"發佈紀錄 → {rel(out)}")
    return out


# ── 步驟 7：git commit ───────────────────────────────────────
def commit(version: str, allow_dirty: bool) -> None:
    if not git_repo():
        fail("不是 git repo，無法 commit（先 git init）")
    git("add", "-A")
    staged = [ln for ln in git("diff", "--cached", "--name-only").stdout.splitlines() if ln.strip()]
    if not staged:
        info("沒有變更需要提交（工作區已與 HEAD 相同）")
        return
    msg = (f"release: v{version} 同步使用端 exe＋重建乾淨版\n\n"
           f"- 打包 StoryVideoStudio onedir 並同步到 dist/StoryVideoStudio（projects 未動）\n"
           f"- 重建 release 乾淨版（只含 {SEED_PROJECT}＋使用說明）並壓 zip\n"
           f"- 產生 docs/RELEASE-NOTES-v{version}.md（大小與 sha256）")
    git("-c", "core.safecrlf=false", "commit", "-m", msg, check=True)
    info(f"commit 完成：{git_head_subject()}")
    info(f"本次提交 {len(staged)} 個檔案")


# ── 主流程 ────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Story Video Studio 一鍵同步發佈")
    ap.add_argument("--version", help="本次標記的版本號（不改 VERSION 檔）")
    ap.add_argument("--skip-build", action="store_true", help="不重跑 pyinstaller，用 dist 現有 exe 重建包")
    ap.add_argument("--allow-dirty", action="store_true", help="git 工作區不乾淨時不警告（預設只警告仍繼續）")
    ap.add_argument("--no-commit", action="store_true", help="最後不做 git commit")
    ap.add_argument("--no-zip", action="store_true", help="跳過壓 zip")
    args = ap.parse_args()

    os.chdir(ROOT)
    print(f"Story Video Studio 一鍵發佈　專案根：{ROOT}")

    # 1) 前置檢查
    step(1, "前置檢查（VERSION / git）")
    version = args.version or read_version()
    if args.version and args.version != read_version():
        info(f"⚠ 本次用 --version {args.version} 標記，VERSION 檔仍為 {read_version()}（已記錄在發佈紀錄）")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"版本號格式錯誤：{version!r}")
    info(f"版本：v{version}")

    if git_repo():
        dirty = git_dirty_files()
        if dirty:
            (info if args.allow_dirty else print)(
                f"{'  ' if args.allow_dirty else '    ⚠'}git 工作區有 {len(dirty)} 個未提交變更"
                f"{'（--allow-dirty）' if args.allow_dirty else '，發佈會把它們一起提交'}")
            for ln in dirty[:8]:
                info(f"  {ln}")
            if len(dirty) > 8:
                info(f"  …其餘 {len(dirty) - 8} 項")
        else:
            info(f"git 工作區乾淨，HEAD = {git_head_subject()}")
    else:
        info("⚠ 不是 git repo，最後一步 commit 會跳過")

    if not (WORK_DIR / NAME / f"{NAME}.exe").is_file() and args.skip_build:
        fail(f"--skip-build 但 {rel(WORK_DIR / NAME / f'{NAME}.exe')} 不存在，請先完整打包一次")

    # 2) 打包
    if args.skip_build:
        step(2, "打包：略過（--skip-build，使用上一次打包的 build/release-work 產出）")
        build_time = datetime.fromtimestamp(
            (WORK_DIR / NAME / f"{NAME}.exe").stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        info(f"現有打包 mtime = {build_time}")
    else:
        step(2, "打包 PyInstaller（.venv\\Scripts\\pyinstaller --noconfirm --distpath build/release-work StoryVideoStudio.spec）")
        t0 = time.time()
        build()
        build_time = datetime.fromtimestamp(
            (WORK_DIR / NAME / f"{NAME}.exe").stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        info(f"耗時 {time.time() - t0:.1f}s")

    # 3) 同步使用端
    step(3, f"同步到使用端 {rel(BUILD_DIR)}（絕不動 projects/）")
    bak = sync_user_install(version)

    # 4) 重建乾淨版
    step(4, f"重建乾淨版 {rel(CLEAN_DIR)}")
    rebuild_clean(version)

    # 5) 壓 zip
    zip_path = zip_sha = None
    zip_size = 0
    if args.no_zip:
        step(5, "壓 zip：略過（--no-zip）")
    else:
        step(5, "壓乾淨版 zip")
        zip_path, zip_size = make_zip(version)
        zip_sha = sha256(zip_path)

    # 6) 發佈紀錄
    step(6, "寫發佈紀錄 docs/RELEASE-NOTES-v%s.md" % version)
    exe_sha = sha256(BUILD_DIR / f"{NAME}.exe")
    notes = write_notes(version, exe_sha, zip_path, zip_sha, zip_size, Path(bak).name, build_time)
    info(f"exe sha256 = {exe_sha}")
    if zip_sha:
        info(f"zip sha256 = {zip_sha}")

    # 7) commit
    if args.no_commit:
        step(7, "git commit：略過（--no-commit）")
    else:
        step(7, "git commit（release:）")
        commit(version, args.allow_dirty)

    print(f"\n✓ 發佈完成 v{version}")
    print(f"   使用端：{rel(BUILD_DIR)}（projects {count_dirs(BUILD_DIR / 'projects')} 個，未動）")
    print(f"   乾淨版：{rel(CLEAN_DIR)}")
    if zip_path:
        print(f"   zip　：{rel(zip_path)}（{human(zip_size)}）")
    print(f"   紀錄：{rel(notes)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中止")
        sys.exit(130)
