#!/usr/bin/env python
"""夜間自動巡檢（零 LLM）：起一個獨立 dev 實例，對「複本資料」跑一輪唯讀檢查。

為什麼不是直接打使用者的 exe：
- exe 的 8788 是他日常在用的實例，佔著埠時新實例起不來；對他跑 POST 也會改到正式資料。
- 所以這裡固定用 8791 + SVS_DATA_DIR 指向 scratch 複本（從 dist 的 projects 複製），
  跑完就關掉、複本下次重建。

輸出：
- stdout = markdown 摘要（cron 會把這段注入 agent 的 prompt）
- docs/testing/nightly-audit/latest.json = 結構化結果（agent 要細看時讀它）

用法：
  .venv/Scripts/python.exe tools/nightly_audit.py            # 完整
  .venv/Scripts/python.exe tools/nightly_audit.py --keep-dir # 保留複本（除錯用）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / "cache" / "scratch" / "svs-nightly"
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


# ---------- HTTP helpers ----------

def _req(method: str, path: str, payload=None, timeout: float = 30.0):
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:  # 連線失敗
        return 0, f"{type(e).__name__}: {e}"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) != 0


# ---------- 準備資料複本 ----------

def prepare_data() -> Path:
    src_candidates = [
        ROOT / "dist" / "StoryVideoStudio" / "projects",   # 使用者日常那份
        ROOT / "projects",                                  # 開發資料
    ]
    src = next((p for p in src_candidates if p.is_dir() and any(p.iterdir())), None)
    if src is None:
        raise SystemExit("找不到可用的 projects/（dist 與根目錄都沒有）")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    shutil.copytree(src, SCRATCH / "projects", ignore=shutil.ignore_patterns("_trash"))
    return SCRATCH / "projects"


def start_server(data_dir: Path):
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    env = dict(os.environ)
    env["SVS_DATA_DIR"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    log = SCRATCH / "server.log"
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        time.sleep(0.7)
        st, body = _req("GET", "/api/health", timeout=2)
        if st == 200 and isinstance(body, dict) and body.get("ok"):
            return proc, body, log
        if proc.poll() is not None:
            break
    proc.terminate()
    raise SystemExit(f"伺服器起不來，見 {log}")


# ---------- 各項檢查 ----------

def check_project(slug: str, deep: bool) -> dict:
    out = {"slug": slug}
    st, body = _req("GET", f"/api/projects/{slug}")
    if st != 200 or not isinstance(body, dict):
        out["load"] = f"FAIL {st} {str(body)[:120]}"
        return out
    proj = body.get("project") or body
    out["load"] = "ok"
    out["name"] = (proj.get("meta") or {}).get("name")
    out["counts"] = {
        "characters": len(proj.get("characters") or []),
        "scenes": len(proj.get("scenes") or []),
        "shots": len(proj.get("shots") or []),
    }
    tot = sum(float(s.get("durationSec") or 0) for s in (proj.get("shots") or []))
    out["durationSec"] = round(tot, 2)

    # validate（POST、去重）
    st, v = _req("POST", f"/api/projects/{slug}/validate", {})
    if st == 200 and isinstance(v, dict):
        issues = v.get("issues") or []
        errs, warns = [], []
        for it in issues:
            msg = it.get("message") if isinstance(it, dict) else str(it)
            lvl = it.get("level") if isinstance(it, dict) else "?"
            (errs if lvl == "error" else warns).append(msg)
        out["validate"] = {
            "raw": len(issues),
            "errors": len(errs),
            "warnsDedup": len(set(warns)),
            "warnSample": sorted(set(warns))[:6],
        }
    else:
        out["validate"] = f"FAIL {st}"

    # 分鏡層進度（M14 契約：shots[].beatIds）
    shots = proj.get("shots") or []
    out["beatsCoverage"] = {
        "shotsWithBeatIds": sum(1 for s in shots if s.get("beatIds")),
        "shotsWithDialogBeatIds": sum(1 for s in shots if s.get("dialogBeatIds")),
        "shotsWithPromptPicture": sum(1 for s in shots if "<Picture" in (s.get("promptEn") or "")),
        "total": len(shots),
    }

    st, plan = _req("GET", f"/api/projects/{slug}/assemble/plan")
    if st == 200 and isinstance(plan, dict):
        segs = plan.get("segments") or []
        out["assemble"] = {
            "segments": len(segs),
            "existing": sum(1 for s in segs if s.get("exists")),
            "total": round(plan.get("targetDuration") or 0, 2),
        }

    if deep:
        st, pf = _req("POST", f"/api/projects/{slug}/preflight/image", {})
        if isinstance(pf, dict):
            items = pf.get("items") or []
            out["preflight"] = {"ok": pf.get("ok"), "blocked": sum(1 for i in items if not i.get("ok")),
                                "blockers": (items[0].get("blockers") if items else [])[:3]}
    return out


def check_static() -> dict:
    """不用伺服器的讀碼檢查：已知回歸點。"""
    out = {}
    appjs = ROOT / "static" / "app.js"
    txt = appjs.read_text(encoding="utf-8", errors="replace") if appjs.exists() else ""
    out["appjsChars"] = len(txt)

    # 已知寫死文案（修過就不該再出現）
    markers = {
        "寫死他專案設定（帽影）": "第三個人依設定保持帽影不可辨",
        "等待文案寫死本機模型": "本機模型可能要幾分鐘",
    }
    out["hardcodedText"] = {k: (v in txt) for k, v in markers.items()}

    # 佈署窗是否用設定值當模型名（應改讀實際模型）
    out["deployModalUsesSettingsModel"] = "llm.model" in txt and "dp-llm-cur" in txt

    # M14 分鏡契約是否已落地
    hits = 0
    for f in list((ROOT / "app").glob("*.py")) + [appjs]:
        try:
            hits += f.read_text(encoding="utf-8", errors="replace").count("beatIds")
        except OSError:
            pass
    out["beatIdsRefs"] = hits

    # 前端語法（有 node 就跑）
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "-e",
                            "new Function(require('fs').readFileSync(process.argv[1],'utf8'))",
                            str(appjs)], capture_output=True, text=True, timeout=60)
        out["appjsSyntax"] = "ok" if r.returncode == 0 else (r.stderr or "")[:400]
    else:
        out["appjsSyntax"] = "skip(no node)"
    return out


def check_repo() -> dict:
    def git(*args):
        try:
            r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=30)
            return r.stdout.strip()
        except Exception as e:
            return f"ERR {e}"
    return {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "head": git("log", "-1", "--format=%h %s"),
        "statusShort": git("status", "--short")[:2000],
        "aheadBehind": git("rev-list", "--left-right", "--count", "origin/main...HEAD"),
        "lastCommits": git("log", "--oneline", "-3"),
        # 前幾天未合併的夜間分支：不列出來就會每天重複修同一件事、分支越積越多
        "unmergedNightly": [b.strip().lstrip("+* ").strip()
                            for b in git("branch", "--no-merged", "main").splitlines()
                            if "fix/nightly-" in b],
        "worktrees": git("worktree", "list"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-dir", action="store_true", help="保留複本資料（除錯用）")
    args = ap.parse_args()

    if not _port_free(PORT):
        print(f"## 夜間巡檢中止：埠 {PORT} 已被佔用（請先確認沒有殘留實例）")
        return 2

    data_dir = prepare_data()
    proc, health, log_path = start_server(data_dir)
    result = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "health": {}, "projects": [], "static": {}, "repo": {}}
    try:
        result["health"] = {k: health.get(k) for k in ("version", "pid", "dataDir")}
        st, plist = _req("GET", "/api/projects")
        slugs = []
        for p in (plist.get("projects") if isinstance(plist, dict) else []) or []:
            s = p.get("slug") or p.get("meta", {}).get("slug")
            if s:
                slugs.append(s)
        # 最近更新的前 2 個做 deep（preflight 較貴）
        for i, slug in enumerate(slugs):
            result["projects"].append(check_project(slug, deep=(i < 2)))

        # 端點冒煙
        smoke = {}
        for m, path in [("GET", "/api/prompt-rules"), ("GET", "/app.js"),
                        ("GET", "/api/llm/status"), ("GET", "/api/gpu/status")]:
            st, _ = _req(m, path, timeout=15)
            smoke[f"{m} {path}"] = st
        result["smoke"] = smoke

        result["static"] = check_static()
        result["repo"] = check_repo()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    out_dir = ROOT / "docs" / "testing" / "nightly-audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(result, ensure_ascii=False, indent=2)
    (out_dir / "latest.json").write_text(blob, encoding="utf-8")
    (out_dir / f"{time.strftime('%Y-%m-%d')}.json").write_text(blob, encoding="utf-8")

    # ---- markdown（stdout，cron 注入用）----
    L = []
    L.append(f"# 夜間巡檢 {result['at']}（版本 {result['health'].get('version')}）")
    # 紅燈只看「最近更新的 3 個專案」：老專案是歷史測資，有 error 屬資料問題不是程式壞了
    recent = result["projects"][:3]
    bad, todo = [], []
    for p in recent:
        if p.get("load") != "ok":
            bad.append(f"{p['slug']}：載入失敗（{p.get('load')}）")
            continue
        v = p.get("validate") or {}
        if isinstance(v, dict) and (v.get("errors") or 0) > 0:
            bad.append(f"{p['slug']}（最近更新）：validate {v['errors']} error")
    for k, st in (result.get("smoke") or {}).items():
        if st != 200:
            bad.append(f"端點 {k} → {st}")
    sw = result["static"]
    syn = str(sw.get("appjsSyntax") or "")
    if not syn.startswith("ok") and "skip" not in syn:
        bad.append(f"app.js 語法錯誤：{syn[:200]}")
    for k, v in (sw.get("hardcodedText") or {}).items():
        if v:
            todo.append(f"已知寫死文案未修：{k}")
    if sw.get("deployModalUsesSettingsModel"):
        todo.append("佈署參數窗仍可能顯示設定值模型名（應顯示實際生效模型）")
    if sw.get("beatIdsRefs") == 0:
        todo.append("M14 分鏡契約（beatIds）未落地：validate 對『分鏡是否演完整劇本』是盲的")

    L.append("## 紅燈" if bad else "## 紅燈（無）")
    L += [f"- {b}" for b in bad] or ["- 全部通過"]
    L.append("\n## 已知待修")
    L += [f"- {t}" for t in todo] or ["- 無"]

    L.append("\n## 專案")
    L.append("| 專案 | 角色/場景/鏡 | 片長 | validate | 有 <Picture> |")
    L.append("|---|---|---|---|---|")
    for p in result["projects"]:
        if p.get("load") != "ok":
            L.append(f"| {p['slug']} | 載入失敗 | | | |")
            continue
        c = p.get("counts") or {}
        v = p.get("validate") or {}
        bc = p.get("beatsCoverage") or {}
        L.append(f"| {p.get('name') or p['slug']} | {c.get('characters')}/{c.get('scenes')}/{c.get('shots')} "
                 f"| {p.get('durationSec')}s | {v.get('errors', '?')}e/{v.get('warnsDedup', '?')}w "
                 f"| {bc.get('shotsWithPromptPicture')}/{bc.get('total')} |")

    L.append("\n## 碼況")
    L.append(f"- app.js {sw.get('appjsChars')} 字 · 語法 {sw.get('appjsSyntax')}")
    L.append(f"- beatIds 引用數 {sw.get('beatIdsRefs')}（0＝M14 未落地）")
    r = result.get("repo") or {}
    L.append(f"- git {r.get('branch')} @ {r.get('head')} · ahead/behind {r.get('aheadBehind')} · 未提交 {len((r.get('statusShort') or '').splitlines())} 檔")
    unmerged = r.get("unmergedNightly") or []
    L.append("- 未合併的夜間分支：" + ("、".join(unmerged) if unmerged else "無"))
    L.append(f"\n完整 JSON：docs/testing/nightly-audit/{time.strftime('%Y-%m-%d')}.json")

    print("\n".join(L))

    if not args.keep_dir:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
