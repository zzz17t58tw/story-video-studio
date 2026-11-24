"""鏡頭頁即時連動 v1（M16a）驗收腳本：T1–T12，逐條印 PASS/FAIL 與實測數字。

規格：docs/shots-live-linkage.md §6／§8.3。**只在複本上跑**，絕不寫 dist/StoryVideoStudio/projects 正本。

用法（Windows git-bash，repo 根目錄）：
    python scripts/test_live_linkage.py                 # 全部（含 T6 前端）
    python scripts/test_live_linkage.py --no-ui         # 跳過 T6（沒有 Chrome / 不想開瀏覽器）
    python scripts/test_live_linkage.py --only T3,T5
    SVS_TEST_PORT=8795 python scripts/test_live_linkage.py   # 8790 被占時換埠

它會自己：
  1. 把 dist 的 28 鏡母本複製到 %TMP%\\svs-m16-live\\projects\\<slug>（每次重跑前刪掉重複）
  2. 用 repo 的 .venv python 起一個 dev 實例（埠 8790，SVS_DATA_DIR 指向複本）
  3. 走真的 HTTP API 做 T1–T5、T8–T12；T7 跑 compileall／node --check；
     T6 起 headless Chrome（CDP）切到鏡頭頁，逐卡比對顯示的 prompt 長度與 GET 回的值。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)
SRC_PROJECTS = Path(os.environ.get("SVS_SRC_PROJECTS")
                    or str(REPO.parent / "1117" / "dist" / "StoryVideoStudio" / "projects"))  # 只讀
SLUG = "project-20261122-121207"
PORT = int(os.environ.get("SVS_TEST_PORT", "8790"))
BASE = f"http://127.0.0.1:{PORT}"
WORKROOT = Path(os.environ.get("TMP") or os.environ.get("TEMP") or "/tmp") / "svs-m16-live"
WORK_PROJECTS = WORKROOT / "projects"

RESULTS: list[tuple[str, bool, str]] = []


# ── 小工具 ────────────────────────────────────────────────────
def http(method: str, path: str, body=None, timeout: float = 120.0):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def project() -> dict:
    code, res = http("GET", f"/api/projects/{SLUG}")
    assert code == 200, f"GET 專案失敗 {code}: {res}"
    return res["project"]


def disk_shots() -> dict:
    """**磁碟原值**的 shots（繞過 GET 的衍生重算），給需要「存檔前真相」的斷言用。

    GET 會在 deepcopy 上跑 refresh_derived，手改稿在那裡就已經被補上旗標——要驗「無旗標原值
    存檔也不流失」，只能比對存檔前的磁碟值。
    """
    pj = json.loads((WORK_PROJECTS / SLUG / "project.json").read_text(encoding="utf-8"))
    return {s.get("id"): s for s in (pj.get("shots") or []) if isinstance(s, dict)}


def shots_by_id(p: dict) -> dict:
    return {s.get("id"): s for s in (p.get("shots") or [])}


SHOT_KEYS = ("id", "no", "episodeSceneId", "role", "beatIds", "padSec", "durationSec",
             "dialogBeatIds", "characterIds", "fillReason", "derivedFrom")


def slim_project(p: dict) -> dict:
    """只留「分鏡決策＋劇本」的瘦身副本（給 PUT 用）。

    為什麼：GET 回的 referencesPlan／promptEn 是讀端**即時重算**的衍生值，原樣 PUT 回去等於
    把衍生值當真相寫入；而且母本 28 鏡帶著 media 路徑整份送會很肥。測試只改真相（劇本／
    人物／分鏡決策），衍生值一律交給 save_project 重算——這也正是契約要的行為。
    """
    q = json.loads(json.dumps(p, ensure_ascii=False))
    q.pop("_dialogMigration", None)
    q["shots"] = [{k: s.get(k) for k in SHOT_KEYS if s.get(k) is not None}
                  | {"promptEn": s.get("promptEn") or "", "promptEnManual": s.get("promptEnManual") is True}
                  for s in q.get("shots") or []]
    for s in q["shots"]:
        if s.get("promptEnManual"):
            s["promptEn"] = next(x.get("promptEn") or "" for x in p["shots"] if x.get("id") == s["id"])
    return q


def save(p: dict):
    """PUT 瘦身副本（見 slim_project）。回後端回應。"""
    code, res = http("PUT", f"/api/projects/{SLUG}", slim_project(p))
    assert code == 200, f"PUT 專案失敗 {code}: {str(res)[:400]}"
    return res


def fresh_copy() -> None:
    """每次都從母本重新複製一份，測試之間互不污染。"""
    WORK_PROJECTS.mkdir(parents=True, exist_ok=True)
    dst = WORK_PROJECTS / SLUG
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SRC_PROJECTS / SLUG, dst)
    # 母本不該被動到（寫入前後比對 mtime）——這裡只讀它的 project.json 當指紋
    src_meta = (SRC_PROJECTS / SLUG / "project.json").stat().st_mtime
    return src_meta


def _shot_matches_recompile(p: dict, shot: dict, h3c) -> bool:
    """存檔值 == 用當下真相現算的值（compile_prompt 的編號要看可送出的槽位）。"""
    try:
        refs, _w, notes = h3gen.collect_refs(p, shot)
        sendable = h3gen._sendable_ordinals(refs, notes)
    except Exception:  # noqa: BLE001 — 沒有可用參考圖時仍可比對「不含首幀」的編譯稿
        sendable = [s["ordinal"] for s in (shot.get("referencePlan") or {}).get("slots") or []
                    if isinstance(s, dict) and s.get("ordinal")]
        notes = {}
    compiled, _w, _meta = h3c.compile_prompt(p, shot, bool((notes or {}).get("firstFrameAsPicture")))
    return (shot.get("promptEn") or "") == compiled


def portrait_ref(ch: dict) -> str:
    refs = [r for r in (ch.get("refImages") or []) if isinstance(r, dict)]
    return refs[0]["path"] if refs else ""


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}：{detail}", flush=True)


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_server() -> subprocess.Popen:
    if not _port_free(PORT):
        raise SystemExit(f"埠 {PORT} 已被占：先關掉舊的 dev 實例（本腳本不幫你殺別人的行程）")
    env = dict(os.environ, SVS_DATA_DIR=str(WORK_PROJECTS), PYTHONIOENCODING="utf-8")
    log = open(WORKROOT / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(PY), "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1",
                             "--port", str(PORT)], cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit(f"dev 實例 30 秒內沒起來，看 {WORKROOT / 'server.log'}")


# ── T1：改角色外型 → 引用該角色的非手動鏡 promptEn 跟著變 ─────────
def t1() -> None:
    fresh_copy()
    p = project()
    # 改「會進編譯稿」的欄位：compile_prompt 的主體定義讀 identityAnchors.hairStyle／
    # appearance.promptEn（外型中文欄位 face 目前只影響人物頁顯示，不進 prompt）。
    new_hair = "MARKS-T1 shoulder-length crimson hair with a single braid"
    # 母本 c01 的規則式鏡頭存著舊版手改稿（無旗標）→ refresh_derived 會把它們採認為手寫稿
    # （F1 手改稿保護），於是「非手動鏡」會歸零、T1 失去比較對象。這裡把 c01 的規則式鏡頭
    # 明確設定回「非手動稿」（清旗標＋清空 promptEn，讓它們走正常編譯路徑）——只調
    # 「怎麼造出測試對象」，斷言一條不放寬。
    cands = [s for s in (p.get("shots") or [])
             if "c01" in (s.get("characterIds") or [])
             and str(s.get("derivedFrom")) in ("storyboard", "deploy")]
    assert cands, "母本沒有引用 c01 的規則式鏡頭，T1 無法成立"
    for seed in cands:
        seed.pop("promptEnManual", None)
        seed["promptEn"] = ""
    before = {sid: (s.get("promptEn") or "") for sid, s in shots_by_id(p).items()}
    c01 = next(c for c in p["characters"] if c["id"] == "c01")
    c01.setdefault("identityAnchors", {})["hairStyle"] = new_hair
    c01.setdefault("appearance", {})["face"] = "MARKS-T1 angular cheekbones and a deep scar through the right brow"
    c01["appearance"]["promptEn"] = (c01["appearance"].get("promptEn") or "") + ", MARKS-T1 angular cheekbones"
    # 明確的非手動鏡（derivedFrom 是規則式）才是契約要驗的對象
    save(p)
    p2 = project()   # 讀端重算（save_project 也會算，這裡驗「讀」也一致）
    shs = shots_by_id(p2)
    targets = [sid for sid, s in shs.items()
               if "c01" in (s.get("characterIds") or [])
               and s.get("derivedFrom") in ("storyboard", "deploy")
               and s.get("promptEnManual") is not True]
    changed = [sid for sid in targets if (shs[sid].get("promptEn") or "") != before.get(sid)]
    has_new = [sid for sid in targets if "MARKS-T1" in (shs[sid].get("promptEn") or "")]
    chars_ok = all((shs[sid].get("derived") or {}).get("promptChars")
                   == len(shs[sid].get("promptEn") or "") for sid in targets)
    ok = bool(targets) and len(changed) == len(targets) and len(has_new) == len(targets) and chars_ok
    record("T1 改角色外型 → 引用該角色的非手動鏡 promptEn 變且含新字串", ok,
           f"c01 的非手動鏡 {len(targets)} 支、promptEn 變了 {len(changed)} 支、含新字串 {len(has_new)} 支、"
           f"derived.promptChars 與實際長度相符={chars_ok}")


# ── T2：移除角色一張 refImages → 相關鏡 slots 不再含該 path ────────
def t2() -> None:
    fresh_copy()
    p = project()
    c01 = next(c for c in p["characters"] if c["id"] == "c01")
    removed = portrait_ref(c01)
    assert removed, "母本 c01 沒有參考圖，T2 無法成立"
    before = {sid: len(((s.get("referencePlan") or {}).get("slots")) or []) for sid, s in shots_by_id(p).items()}
    c01["refImages"] = [r for r in c01["refImages"] if r.get("path") != removed]
    save(p)
    shs = shots_by_id(project())
    rel = [sid for sid, s in shs.items() if "c01" in (s.get("characterIds") or [])]
    still = [sid for sid in rel
             if any((sl.get("path") == removed) for sl in ((shs[sid].get("referencePlan") or {}).get("slots") or []))]
    dropped = [sid for sid in rel
               if len(((shs[sid].get("referencePlan") or {}).get("slots")) or []) == before.get(sid, 0) - 1]
    pics_ok = all(len((shs[sid].get("derived") or {}).get("pictures") or [])
                  == len(((shs[sid].get("referencePlan") or {}).get("slots")) or []) for sid in rel)
    ok = bool(rel) and not still and len(dropped) == len(rel) and pics_ok
    record("T2 移除角色參考圖 → 相關鏡 slots 不再含該 path（pictures 少 1）", ok,
           f"移除 {removed}；c01 相關鏡 {len(rel)} 支、仍含舊 path {len(still)} 支、slots 少 1 的 {len(dropped)} 支、"
           f"derived.pictures 與 slots 數相符={pics_ok}")


# ── T3：e01s03 中間插一個 beat → /storyboard/build 只補鏡 ──────────
def t3() -> None:
    fresh_copy()
    # 步驟①：只補既有的洞（b05）——驗「單洞補鏡」
    code0, r0 = http("POST", f"/api/projects/{SLUG}/storyboard/build",
                     {"scope": "scene", "sceneId": "e01s03"})
    assert code0 == 200, f"先補 b05 失敗 {code0}: {str(r0)[:300]}"
    p = project()
    cov0 = next(x for x in http("GET", f"/api/projects/{SLUG}/storyboard")[1]["scenes"]
                if x["episodeSceneId"] == "e01s03")
    ok_b05 = (r0.get("created", 0) >= 1 and "b05" in str(r0) and r0.get("scenes", {})
              .get("e01s03", {}).get("missing") == ["b05"])
    # 步驟②：在 e01s03 第 2 句位置插一個新 beat → 再打一次 /storyboard/build
    es = next(e for ep in p["script"]["episodes"] for e in ep["scenes"] if e["id"] == "e01s03")
    new_beat = {"id": "b99", "type": "dialogue", "characterId": "c01",
                "line": "T3 新增的一句台詞，插在第 2 句的位置。", "emotion": "calm", "notes": "T3 測試"}
    if "b99" not in {str(b.get("id")) for b in es.get("beats") or []}:
        es.setdefault("beats", []).insert(1, new_beat)     # 第 2 句位置
    save(p)
    before = {sid: list(s.get("beatIds") or []) for sid, s in shots_by_id(project()).items()}
    cov_before = next(x for x in http("GET", f"/api/projects/{SLUG}/storyboard")[1]["scenes"]
                      if x["episodeSceneId"] == "e01s03")
    code, res = http("POST", f"/api/projects/{SLUG}/storyboard/build",
                     {"scope": "scene", "sceneId": "e01s03"})
    assert code == 200, f"/storyboard/build 失敗 {code}: {str(res)[:400]}"
    after_p = project()
    after = {sid: list(s.get("beatIds") or []) for sid, s in shots_by_id(after_p).items()}
    # 既有鏡頭的 beatIds 一字不動（同 id 的鏡頭一定還在，順序也一致）。
    # 唯一允許的既有欄位變動：補鏡搶下「場次第一鏡」位置時，原 establish 降級 role（合約 V5），
    # 降級必須在 warnings 裡講明。
    kept_same = all(after.get(sid) == ids for sid, ids in before.items())
    v5_role = [w for w in (res.get("warnings") or []) if "場次第一鏡" in w and "establish" in w]
    cov_after = next(x for x in http("GET", f"/api/projects/{SLUG}/storyboard")[1]["scenes"]
                     if x["episodeSceneId"] == "e01s03")
    all_ids = [x for s in (after_p.get("shots") or []) if s.get("episodeSceneId") == "e01s03"
               for x in (s.get("beatIds") or [])]
    dup = sorted({x for x in all_ids if all_ids.count(x) > 1})
    ok = (ok_b05 and res.get("mode") == "backfill" and res.get("created", 0) > 0
          and not cov_after.get("uncoveredBeats")
          and kept_same and not dup and "b99" in all_ids)
    record("T3 插 beat → /storyboard/build 只補新鏡", ok,
           f"步驟① 補 b05：created={r0.get('created')} {r0.get('createdIds')}、"
           f"補前 uncovered={cov0.get('uncoveredBeats')} → {r0.get('scenes', {}).get('e01s03', {}).get('missing')}；"
           f"步驟② 插 b99 於第 2 句：mode={res.get('mode')} created={res.get('created')} {res.get('createdIds')}；"
           f"uncoveredBeats {cov_before.get('uncoveredBeats')} → {cov_after.get('uncoveredBeats')}；"
           f"既有 {len(before)} 鏡 beatIds 不動={kept_same}；重複引用={dup or '無'}；"
           f"V5 role 降級說明={len(v5_role)} 筆；e01s03 beat 串={all_ids}")


# ── T4：手動稿不被覆蓋，且標 promptEnStale ────────────────────────
def t4() -> None:
    fresh_copy()
    p = project()
    shs = shots_by_id(p)
    sid = "sh11" if "sh11" in shs else next(iter(shs))
    manual_text = "T4 MANUAL HAND-WRITTEN PROMPT — 手寫稿，衍生流程不得覆蓋。"
    target = shs[sid]
    target["promptEn"] = manual_text
    target["promptEnManual"] = True
    # 同場次（e01s03）的非手動鏡，等一下要比對它有沒有更新
    same_scene = [s for s in p["shots"] if s.get("episodeSceneId") == target.get("episodeSceneId")
                  and s.get("id") != sid and s.get("promptEnManual") is not True]
    before_other = {s["id"]: s.get("promptEn") or "" for s in same_scene}
    save(p)

    # 改同場次的 beats（改一句台詞的字）→ save_project
    es = next(e for ep in p["script"]["episodes"] for e in ep["scenes"] if e["id"] == target.get("episodeSceneId"))
    es["beats"][0]["line"] = str(es["beats"][0].get("line") or "") + "（T4 改字）"
    save(p)

    p2 = project()
    shs2 = shots_by_id(p2)
    t = shs2[sid]
    kept = (t.get("promptEn") or "") == manual_text
    stale = (t.get("derived") or {}).get("promptEnStale") is True
    src = (t.get("derived") or {}).get("promptSource")
    others_changed = [s for s in same_scene if (shs2[s["id"]].get("promptEn") or "") != before_other.get(s["id"])]
    # 「同場非手動鏡有更新」的機械判準：經過 save 的非手動鏡，存檔值 == 同樣真相現算的結果
    # （編譯是確定性的；真相沒變就不會變字，變了才會變字。這裡驗「存檔值就是現算值」）
    h3c = importlib.import_module("app.h3compose")
    others_in_sync = all(_shot_matches_recompile(p2, s, h3c) for s in same_scene)
    ok = (kept and stale and src == "manual" and bool(same_scene) and others_in_sync)
    record("T4 手動稿不被覆蓋＋promptEnStale=true，同場非手動鏡有更新", ok,
           f"{sid} 手動稿原封不動={kept}、promptEnStale={stale}、promptSource={src}；"
           f"同場非手動鏡 {len(others_changed)}/{len(same_scene)} 支字面變了、"
           f"全部等於現算結果={others_in_sync}")


# ── T5：真實 28 鏡複本存一次 → error 不增加、孤兒判定正確 ───────────
def t5() -> None:
    fresh_copy()
    # 基準：讀端（GET）已在 deepcopy 上重算衍生值，而 validate 端點驗的是存檔原值。
    # 正確的「不退化」問法是：原值驗一次、存一次（refresh_derived 全量重算後寫檔）再驗一次，
    # error 不得增加。母本本身帶著既有契約錯誤（V1 漏句等），不是本輪造成的。
    code, v0 = http("POST", f"/api/projects/{SLUG}/validate")
    assert code == 200, f"/validate 失敗 {code}: {v0}"
    e0 = (v0.get("counts") or {}).get("error", 0)
    p_saved = save(project())    # 存一次（storage.save_project 會呼叫 refresh_derived）
    code, v1 = http("POST", f"/api/projects/{SLUG}/validate")
    assert code == 200, f"/validate 失敗 {code}: {v1}"
    e1 = (v1.get("counts") or {}).get("error", 0)
    save(project())              # 再存一次：衍生值必須收斂（第二次不得再冒出新的 error）
    code, v2 = http("POST", f"/api/projects/{SLUG}/validate")
    e2 = (v2.get("counts") or {}).get("error", 0)
    p = project()
    orph = {s["id"]: bool((s.get("derived") or {}).get("orphan")) for s in p.get("shots") or []}
    want_orphans = {"sh25", "sh26", "sh27", "sh28"}
    orphan_hit = {k for k, v in orph.items() if v}
    ok = e1 <= e0 and e2 <= e1 and len(p.get("shots") or []) == 28 and orphan_hit == want_orphans
    record("T5 28 鏡複本存一次 error 不增加、sh25–sh28 為孤兒、其餘 24 鏡不是", ok,
           f"validate error 原值 {e0} → 存一次 {e1} → 再存一次 {e2}；鏡數 {len(p.get('shots') or [])}；"
           f"判孤兒的鏡 {sorted(orphan_hit)}（應為 {sorted(want_orphans)}）")


# ── T6：前端鏡頭卡顯示的 prompt 長度 == GET 回的值、console 錯誤 0 ──
def t6() -> None:
    """起 Chrome（headless）開 8790，切到鏡頭頁，逐卡比對資料長度。"""
    chrome = _find_chrome()
    if not chrome:
        record("T6 前端 prompt 長度 == GET 回的值、console 錯誤 0", False,
               "找不到 Chrome／Edge 執行檔，無法跑前端驗收（用 --no-ui 可略過）")
        return
    import tempfile
    profile = tempfile.mkdtemp(prefix="svs-cdp-")
    port = 9333
    proc = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={port}",
                             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                             "--disable-gpu", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                if targets:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        assert targets, "Chrome CDP 沒起來"
        page = next(t for t in targets if t.get("type") == "page")
        ws = page["webSocketDebuggerUrl"]
        res = _cdp_run(ws)
        record("T6 前端鏡頭卡顯示的 prompt 長度 == GET 回的值、console 錯誤 0", res[0], res[1])
    except Exception as e:  # noqa: BLE001
        record("T6 前端 prompt 長度 == GET 回的值、console 錯誤 0", False, f"{type(e).__name__}: {e}")
    finally:
        proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def _find_chrome() -> str | None:
    cands = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((c for c in cands if c and Path(c).is_file()), None)


def _cdp_run(ws_url: str):
    """用最小 WebSocket 客戶端（不依賴第三方套件）驅動頁面。"""
    import base64
    import hashlib
    import struct

    class WS:
        def __init__(self, url: str):
            m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
            host, port, path = m.group(1), int(m.group(2)), m.group(3)
            self.s = socket.create_connection((host, port), timeout=60)
            key = base64.b64encode(os.urandom(16)).decode()
            self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                            f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += self.s.recv(4096)
            head, self.buf = buf.split(b"\r\n\r\n", 1)
            assert b"101" in head.split(b"\r\n")[0], head[:200]
            self.n = 0

        def _send(self, text: str):
            payload = text.encode("utf-8")
            mask = os.urandom(4)
            n = len(payload)
            hdr = bytes([0x81])
            if n < 126:
                hdr += bytes([0x80 | n])
            elif n < 65536:
                hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
            else:
                hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
            self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

        def _recv_exact(self, k: int) -> bytes:
            while len(self.buf) < k:
                chunk = self.s.recv(65536)
                if not chunk:
                    raise ConnectionError("ws closed")
                self.buf += chunk
            out, self.buf = self.buf[:k], self.buf[k:]
            return out

        def recv(self) -> str:
            while True:
                b0, b1 = self._recv_exact(2)
                opcode = b0 & 0x0F
                ln = b1 & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._recv_exact(2))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._recv_exact(8))[0]
                data = self._recv_exact(ln)
                if opcode == 0x8:
                    raise ConnectionError("ws closed by peer")
                if opcode == 0x9:
                    continue
                if opcode in (0x1, 0x2):
                    return data.decode("utf-8")

        def call(self, method: str, **params):
            self.n += 1
            mid = self.n
            self._send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(self.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result") or {}

    ws = WS(ws_url)
    errors: list[str] = []
    ws.call("Runtime.enable")
    ws.call("Page.enable")

    def js(expr: str, await_promise: bool = False):
        r = ws.call("Runtime.evaluate", expression=expr, returnByValue=True,
                    awaitPromise=await_promise, userGesture=True)
        return (r.get("result") or {}).get("value")

    js("window.__errors = []; window.addEventListener('error', e => window.__errors.push(String(e.message)));"
       "window.__consoleErr = []; const _ce = console.error; console.error = (...a) => "
       "{ window.__consoleErr.push(a.map(String).join(' ')); _ce.apply(console, a); };")
    ws.call("Page.navigate", url=f"{BASE}/")
    for _ in range(120):
        if js("document.readyState") == "complete" and js("!!(window.state && document.querySelector('.nav-btn'))"):
            break
        time.sleep(0.5)
    time.sleep(1.5)
    # 開專案 → 切到鏡頭頁（setView 會先存再整份重抓）
    if os.environ.get("SVS_T6_ORPHANS"):
        # 額外驗「場次標題的移除未連動鏡頭」按鈕存在且列出正確 id（不真的按下去）
        js("(async () => { await openProject('%s'); await setView('shots'); })()" % SLUG, await_promise=True)
        time.sleep(2.0)
        got = js("""(() => { const b = document.querySelector('.shot-group-title .sb-rm-orphans');
            return JSON.stringify(b ? {text: b.textContent, ids: b.dataset.orphans, scene: b.dataset.sceneId} : null); })()""")
        print("  孤兒移除按鈕：", got, flush=True)
        return (True, f"孤兒按鈕 {got}")
    js("(async () => { await openProject('%s'); await setView('shots'); })()" % SLUG, await_promise=True)
    time.sleep(2.0)
    # app.js 是傳統 script（頂層 const 不掛到 window），所以用 eval 取同一個頂層 scope 的 state
    dump = js("""(() => {
        const st = (0, eval)('typeof state !== "undefined" ? state : null');
        return JSON.stringify({
        cards: [...document.querySelectorAll('#view-shots .shot-card')].map(c => ({
          id: c.dataset.shotId,
          promptLens: [...c.querySelectorAll('.sb-derived')].map(n => n.dataset.promptChars || ''),
          derivedText: [...c.querySelectorAll('.sb-derived')].map(n => n.textContent),
          taLens: [...c.querySelectorAll('textarea[data-prompt-for]')].map(n => (n.value || '').length),
        })),
        errors: window.__errors || [], consoleErr: window.__consoleErr || [],
        view: st && st.view,
        shotCount: (st && st.project && st.project.shots || []).length,
      }); })()""")
    if not dump:
        probe = js("JSON.stringify({ready: document.readyState, title: document.title, stateType: typeof window.state, cards: document.querySelectorAll('.shot-card').length, errors: window.__errors, cerr: window.__consoleErr})")
        raise RuntimeError("頁面評估回空（JS 例外或頁面還沒就緒）：" + str(probe)[:600])
    info = json.loads(dump)
    p = project()
    shs = shots_by_id(p)
    bad = []
    shown = 0
    ta_bad = 0
    for c in info["cards"]:
        sid = c["id"]
        want = len((shs.get(sid) or {}).get("promptEn") or "")
        lens = [int(x) for x in c["promptLens"] if str(x).isdigit()]
        if not lens:
            bad.append(f"{sid} 卡片沒有顯示 derived 摘要")
            continue
        shown += 1
        if lens[0] != want:
            bad.append(f"{sid} 卡片摘要 {lens[0]} 字 ≠ GET {want} 字")
        # 卡片上的 promptEn 輸入框內容也必須逐字等於 GET 回的值（畫面上的字＝API 回的字）
        tas = [int(x) for x in c.get("taLens") or []]
        if not tas or tas[0] != want:
            ta_bad += 1
            bad.append(f"{sid} promptEn 輸入框 {tas} 字 ≠ GET {want} 字")
    errs = [e for e in (info["errors"] + info["consoleErr"]) if e]
    ok = (not bad and not errs and shown == len(shs) and info.get("view") == "shots"
          and info.get("shotCount") == len(shs))
    detail = (f"鏡頭卡 {shown}/{len(shs)} 張有衍生摘要、摘要字數全等={not bad}、"
              f"promptEn 輸入框字數全等={not ta_bad}；view={info.get('view')}；console/JS 錯誤 {len(errs)} 筆"
              + (f"；不一致：{'; '.join(bad[:3])}" if bad else "")
              + (f"；錯誤樣本：{errs[:2]}" if errs else ""))
    return ok, detail


# ── T7：編譯與語法檢查 ────────────────────────────────────────────
def t7() -> None:
    r1 = subprocess.run([str(PY), "-m", "compileall", "-q", "app", "tools"], cwd=str(REPO),
                        capture_output=True, text=True)
    r2 = subprocess.run(["node", "--check", "static/app.js"], cwd=str(REPO), capture_output=True, text=True,
                        shell=(os.name == "nt"))
    ok = r1.returncode == 0 and r2.returncode == 0
    record("T7 compileall app tools ＋ node --check static/app.js 全過", ok,
           f"compileall rc={r1.returncode}、node --check rc={r2.returncode}"
           + (f"；{r1.stdout[-200:]} {r2.stderr[-200:]}" if not ok else ""))


# ── T8：不退化（批次生影片 precheck 全放行、預覽字 == 送件字）────────
def t8() -> None:
    """不退化：28 鏡複本，批次生影片 precheck 全放行、預覽字 == 送件字。

    P4（測試不穩定）：T8 以前靠 `fresh_copy()` 共用複本、再 `save(project())` 存的**瘦身副本**。
    瘦身 PUT 只帶分鏡決策欄位（`slim_project`），`referencePlan` 等衍生值全靠 save 端重算——
    只要某次 `referencePlan` 沒被重算出來（殘留空 `slots`），28 鏡就全被判「沒有可用參考圖」，
    `build_timeline` 全拋 H3GenError → 預覽與送件 0/28、第一次跑必 FAIL、第二次才過。
    現在 T8 自己建**獨立複本**並先把槽位重建好（`build-reference-plan` 逐鏡寫回），
    前置不成立就直接斷言失敗（不偷偷放寬），所以連跑兩次結果一致。
    """
    fresh_copy()
    save(project())              # 先存一次：讓 derived 重算生效（等同使用者做完編輯的狀態）
    # 槽位前置：逐鏡重建 referencePlan（等同使用者在鏡頭頁按過一輪「建槽位」）
    shs = list(shots_by_id(project()).keys())
    zero = []
    for sid in shs:
        code_r, rr = http("POST", f"/api/projects/{SLUG}/shots/{sid}/build-reference-plan")
        if code_r != 200:
            zero.append(f"{sid} build-reference-plan {code_r}")
            continue
        if not (rr.get("referencePlan") or {}).get("slots"):
            zero.append(f"{sid} 槽位 0")
    if zero:
        record("T8 批次生影片 precheck 全放行＋prompt-preview == build_timeline segments[0].prompt", False,
               f"前置不成立：逐鏡重建槽位後仍有 {len(zero)} 鏡沒有槽位 → {zero[:4]}")
        return
    env = dict(os.environ, SVS_DATA_DIR=str(WORK_PROJECTS), PYTHONIOENCODING="utf-8")
    code = r"""import json, sys
from app import storage, h3gen
p = storage.load_project(%r)
ids = [s["id"] for s in p["shots"]]
blocked = []
for i in ids:
    ok, why, w = h3gen._vb_precheck(p, i, {})
    if not ok:
        blocked.append((i, why))
print(json.dumps({"blocked": blocked, "total": len(ids)}, ensure_ascii=False))
""" % SLUG
    r = subprocess.run([str(PY), "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"precheck 子行程失敗：{r.stderr[-500:]}"
    pre = json.loads(r.stdout.strip().splitlines()[-1])
    p = project()
    cmp_bad = []
    for sh in p.get("shots") or []:
        code_pv, pv = http("GET", f"/api/projects/{SLUG}/shots/{sh['id']}/prompt-preview?engine=h3_video")
        if code_pv != 200:
            cmp_bad.append(f"{sh['id']} prompt-preview {code_pv}")
            continue
        seg = None
        try:
            timeline = h3gen_timeline(p, sh["id"])
            seg = ((timeline.get("segments") or [{}])[0]).get("prompt")
        except Exception as e:  # noqa: BLE001
            cmp_bad.append(f"{sh['id']} build_timeline 失敗 {type(e).__name__}: {e}")
            continue
        if (pv.get("prompt") or "") != (seg or ""):
            cmp_bad.append(f"{sh['id']} 預覽 {len(pv.get('prompt') or '')} 字 ≠ 送件 {len(seg or '')} 字")
    ok = not pre["blocked"] and not cmp_bad and pre["total"] == 28
    record("T8 批次生影片 precheck 全放行＋prompt-preview == build_timeline segments[0].prompt", ok,
           f"槽位前置 28/28 鏡重建成功；precheck 放行 {pre['total'] - len(pre['blocked'])}/{pre['total']} 鏡；"
           f"預覽與送件逐字相等 {pre['total'] - len(cmp_bad)}/{pre['total']} 鏡"
           + (f"；問題：{pre['blocked'][:2]} {cmp_bad[:3]}" if (pre["blocked"] or cmp_bad) else ""))


def h3gen_timeline(p: dict, shot_id: str) -> dict:
    """同一支程序裡算 build_timeline（不碰 GPU、不上傳圖）。"""
    h3gen = importlib.import_module("app.h3gen")
    shot = next(s for s in p["shots"] if s["id"] == shot_id)
    return h3gen.build_timeline(p, shot, {})


# ── T9：舊版手改稿（非空、無旗標、與 compiled 不同）存檔後原封不動 ──────
def t9() -> None:
    """舊版前端改 promptEn 不打 promptEnManual 旗標 → 第一次存檔就被 refresh_derived 吃掉。

    F1 的修法：存檔時若「非空、與這次算出的 compiled 不同、且沒有旗標」就採認為手寫稿。
    驗兩件事：(a) 那種稿子存一次後一字不差、旗標補上、derived 標 manual/adoptedManual/stale；
    (b) 「非空、無旗標、與 compiled 相同」的鏡頭存檔後維持**非**手寫稿（它本來就是衍生值，
    該繼續跟著真相走，不能被誤凍結）。

    用裸存檔（storage.save_project 直呼）來驗，不走 PUT 的瘦身副本：save 端看到的就是磁碟
    原值，這正是「舊版前端留下無旗標改字」的真實形狀。瘦身 PUT 會剔掉真相欄位、讓某些鏡頭的
    編譯稿改變，那是 PUT 本身的語意，不是這條要驗的東西。
    """
    fresh_copy()
    env = dict(os.environ, SVS_DATA_DIR=str(WORK_PROJECTS), PYTHONIOENCODING="utf-8")
    code = r"""import json
from app import storage, h3compose
SLUG = %r
p = storage.load_project(SLUG)
h3c = h3compose
def recompiles(pr, sh):
    from app import h3gen
    try:
        refs, _w, notes = h3gen.collect_refs(pr, sh)
        sendable = h3gen._sendable_ordinals(refs, notes)
    except Exception:
        notes = {}
    c, _w2, _m = h3c.compile_prompt(pr, sh, bool((notes or {}).get("firstFrameAsPicture")))
    return (sh.get("promptEn") or "") == c

raw = {s["id"]: s for s in p["shots"]}
manual_sid = next((sid for sid, s in sorted(raw.items())
                   if s.get("promptEnManual") is not True
                   and str(s.get("promptEn") or "").strip()
                   and not recompiles(p, s)), "")
same_sid = next((sid for sid, s in sorted(raw.items())
                 if s.get("promptEnManual") is not True
                 and str(s.get("promptEn") or "").strip()
                 and recompiles(p, s)), "")
out = {"manualSid": manual_sid, "sameSid": same_sid,
       "manualLen": len(str((raw.get(manual_sid) or {}).get("promptEn") or "")),
       "sameLen": len(str((raw.get(same_sid) or {}).get("promptEn") or ""))}
if not manual_sid:
    print(json.dumps(out, ensure_ascii=False)); raise SystemExit(0)
# 規格點名的實測鏡（sh10）：非空、無旗標、與 compiled 不同、主體是「塞蕾娜」→ 存檔後必須一字不動
out["sh10Len"] = len(str((raw.get("sh10") or {}).get("promptEn") or ""))
out["sh10FlagBefore"] = (raw.get("sh10") or {}).get("promptEnManual")
out["sh10IsManualCand"] = ("sh10" in raw and out["sh10FlagBefore"] is not True
                           and out["sh10Len"] > 0 and not recompiles(p, raw["sh10"]))
mtext = str(raw[manual_sid].get("promptEn") or "")
stext = str((raw.get(same_sid) or {}).get("promptEn") or "")
storage.save_project(SLUG, p)
q = storage.load_project(SLUG)
shs = {s["id"]: s for s in q["shots"]}
t = shs.get(manual_sid) or {}
d = t.get("derived") or {}
out["manualAfter"] = len(str(t.get("promptEn") or ""))
out["manualKept"] = str(t.get("promptEn") or "") == mtext
out["manualFlag"] = t.get("promptEnManual") is True
out["adopted"] = d.get("adoptedManual") is True
out["source"] = d.get("promptSource")
out["stale"] = d.get("promptEnStale") is True
out["chars"] = d.get("promptChars")
out["sh10After"] = len(str((shs.get("sh10") or {}).get("promptEn") or ""))
out["sh10Kept"] = bool(out["sh10IsManualCand"]) and str((shs.get("sh10") or {}).get("promptEn") or "") \
    == str((raw.get("sh10") or {}).get("promptEn") or "")
if same_sid:
    s2 = shs.get(same_sid) or {}
    out["sameAfter"] = len(str(s2.get("promptEn") or ""))
    out["sameKept"] = str(s2.get("promptEn") or "") == stext
    out["sameFlag"] = s2.get("promptEnManual") is True
    out["sameAdopted"] = (s2.get("derived") or {}).get("adoptedManual") is True
# 再存兩次（連存三次）：已保全的稿子每一輪都不得被改、adoptedManual 不得掉（P2）
saves = [{"n": 1, "manual": str(t.get("promptEn") or ""), "len": len(str(t.get("promptEn") or "")),
          "flag": t.get("promptEnManual") is True, "adopted": d.get("adoptedManual") is True,
          "sh10": str((shs.get("sh10") or {}).get("promptEn") or ""),
          "sh07flag": (shs.get("sh07") or {}).get("promptEnManual") is True,
          "sh07adopted": ((shs.get("sh07") or {}).get("derived") or {}).get("adoptedManual") is True}]
for i in (2, 3):
    storage.save_project(SLUG, storage.load_project(SLUG))
    cur = {s["id"]: s for s in storage.load_project(SLUG)["shots"]}
    ct, c10, c07 = cur.get(manual_sid) or {}, cur.get("sh10") or {}, cur.get("sh07") or {}
    saves.append({"n": i, "manual": str(ct.get("promptEn") or ""), "len": len(str(ct.get("promptEn") or "")),
                  "flag": ct.get("promptEnManual") is True,
                  "adopted": (ct.get("derived") or {}).get("adoptedManual") is True,
                  "sh10": str(c10.get("promptEn") or ""),
                  "sh07flag": c07.get("promptEnManual") is True,
                  "sh07adopted": (c07.get("derived") or {}).get("adoptedManual") is True})
out["saves"] = [{"n": x["n"], "len": x["len"], "flag": x["flag"], "adopted": x["adopted"],
                 "sh10len": len(x["sh10"]), "sh07flag": x["sh07flag"], "sh07adopted": x["sh07adopted"]}
                for x in saves]
out["threeStable"] = all(x["len"] == out["manualLen"] and x["flag"] and x["adopted"]
                         and x["sh10"] == str((raw.get("sh10") or {}).get("promptEn") or "")
                         and not x["sh07flag"] and not x["sh07adopted"] for x in saves)
out["again"] = saves[-1]["manual"]
print(json.dumps(out, ensure_ascii=False))
""" % SLUG
    r = subprocess.run([str(PY), "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"T9 子行程失敗：{r.stderr[-600:]}"
    o = json.loads(r.stdout.strip().splitlines()[-1])
    ok = (bool(o["manualSid"]) and o.get("manualKept") and o.get("manualFlag") and o.get("adopted")
          and o.get("source") == "manual" and o.get("stale") and o.get("chars") == o["manualLen"]
          and o.get("sh10IsManualCand") and o.get("sh10Kept") and o.get("threeStable")
          and o.get("again") == str((disk_shots().get(o["manualSid"]) or {}).get("promptEn") or "")
          and (not o["sameSid"] or (o.get("sameKept") and not o.get("sameFlag")
                                    and not o.get("sameAdopted"))))
    sv = "；".join(f"第{x['n']}次 手改鏡 {x['len']} 字/flag={x['flag']}/adopted={x['adopted']}、"
                  f"sh10 {x['sh10len']} 字、sh07 flag={x['sh07flag']}/adopted={x['sh07adopted']}"
                  for x in o.get("saves") or [])
    record("T9 舊版手改稿（非空／無旗標／≠compiled）存檔後一字不動，同值非手動（連存三次穩定）", ok,
           f"手改稿鏡 {o['manualSid']}：{o['manualLen']} 字 → {o.get('manualAfter')} 字、逐字相同={o.get('manualKept')}、"
           f"promptEnManual={o.get('manualFlag')}、adoptedManual={o.get('adopted')}、"
           f"promptSource={o.get('source')}、promptEnStale={o.get('stale')}、"
           f"derived.promptChars={o.get('chars')}；連存三次穩定={o.get('threeStable')}（{sv}）；"
           f"sh10 {o.get('sh10Len')} 字 → {o.get('sh10After')} 字、逐字相同={o.get('sh10Kept')}；"
           f"對照鏡（非空＝compiled）{o['sameSid'] or '（母本無此鏡）'}：{o['sameLen']} 字 → {o.get('sameAfter')} 字、"
           f"維持原值={o.get('sameKept')}、未被標 manual={not o.get('sameFlag')}、adoptedManual={o.get('sameAdopted')}")


# ── T10：backfill 後 V1 不得是 warn，且 chain == want ────────────────
def t10() -> None:
    fresh_copy()
    env = dict(os.environ, SVS_DATA_DIR=str(WORK_PROJECTS), PYTHONIOENCODING="utf-8")
    code = r"""import json
from app import storage, storyboard, h3compose
SLUG = %r
p = storage.load_project(SLUG)
r = storyboard.backfill_shots(p)
storage.save_project(SLUG, p)
p = storage.load_project(SLUG)
v = h3compose.validate_project(p)
chains = {}
for ep in (p.get("script") or {}).get("episodes") or []:
    for es in ep.get("scenes") or []:
        want = [str(b.get("id")) for b in (es.get("beats") or []) if isinstance(b, dict) and not b.get("_skipped")]
        ss = sorted([s for s in p["shots"] if s.get("episodeSceneId") == es.get("id")], key=lambda s: int(s.get("no") or 0))
        chain = [x for s in ss for x in (s.get("beatIds") or [])]
        chains[es.get("id")] = {"chain": chain, "want": want, "equal": chain == want}
v1 = [{"level": i["level"], "msg": i["message"][:160]} for i in v["issues"] if "（合約 V1）" in i["message"]]
print(json.dumps({"created": r.get("created"), "chains": chains, "v1": v1,
                  "counts": v["counts"]}, ensure_ascii=False))
""" % SLUG
    r = subprocess.run([str(PY), "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"backfill 子行程失敗：{r.stderr[-600:]}"
    info = json.loads(r.stdout.strip().splitlines()[-1])
    v1 = info["v1"]
    v1_warn = [x for x in v1 if x["level"] != "error"]
    all_equal = all(c["equal"] for c in info["chains"].values())
    ok = bool(info["created"]) and not v1_warn and all_equal
    detail = (f"backfill created={info['created']}；"
              + "；".join(f"{k} {'=' if c['equal'] else '≠'}" for k, c in info["chains"].items())
              + f"；V1 問題 {len(v1)} 筆（warn {len(v1_warn)} 筆，必須為 0）"
              + (f"；{v1_warn[:2]}" if v1_warn else "")
              + ("" if all_equal else f"；不等的場次：{[k for k, c in info['chains'].items() if not c['equal']]}"
                 + str([c for c in info["chains"].values() if not c["equal"]][:1])))
    record("T10 backfill 後 V1 不得是 warn、chain == want", ok, detail)


# ── T11：backfill 前後既有鏡相對順序不變、無 beatIds 的手動鏡排最後 ──────────
def t11() -> None:
    """§8.2 排序鐵律的機械驗收（T3 被 /storyboard/build 回 400 擋下的真因）。

    對複本的 e01s03 跑 backfill_shots()（會新建 sh29 補 b05），斷言：
    ① 既有鏡頭在陣列中的相對順序不變（排除新建鏡後取 index 序列比對）；
    ② beatIds 為空的既有鏡（實測 sh25–sh28）全部排在 e01s01 有 beatIds 的鏡之後；
    ③ sh01 仍是 e01s01 的第一支有 beatIds 的鏡（合約 V5 才有機會成立）。
    """
    fresh_copy()
    env = dict(os.environ, SVS_DATA_DIR=str(WORK_PROJECTS), PYTHONIOENCODING="utf-8")
    code = r"""import json
from app import storage, storyboard
SLUG = %r
p = storage.load_project(SLUG)
before = [s["id"] for s in p["shots"]]
r = storyboard.backfill_shots(p, ["e01s03"])
after = [s["id"] for s in p["shots"]]
made = list(r.get("created") or [])
kept = [x for x in after if x not in set(made)]
s01 = [s for s in p["shots"] if s.get("episodeSceneId") == "e01s01"]
withb = [s["id"] for s in s01 if [x for x in (s.get("beatIds") or []) if isinstance(x, str)]]
without = [s["id"] for s in s01 if not [x for x in (s.get("beatIds") or []) if isinstance(x, str)]]
idx_w = {s["id"]: i for i, s in enumerate(s01)}
last_with = max((idx_w[x] for x in withb), default=-1)
first_without = min((idx_w[x] for x in without), default=10 ** 6)
print(json.dumps({
    "created": made,
    "orderKept": kept == before,
    "before": before, "afterKept": kept,
    "s01": [[s["id"], s["no"], s.get("beatIds"), s.get("role")] for s in s01],
    "withBeatIds": withb, "withoutBeatIds": without,
    "noBeatIdsAfter": first_without > last_with,
    "firstWithBeatIds": withb[0] if withb else None,
}, ensure_ascii=False))
""" % SLUG
    r = subprocess.run([str(PY), "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"T11 子行程失敗：{r.stderr[-600:]}"
    o = json.loads(r.stdout.strip().splitlines()[-1])
    ok1 = o["orderKept"]
    ok2 = bool(o["withoutBeatIds"]) and o["noBeatIdsAfter"]
    ok3 = o["firstWithBeatIds"] == "sh01"
    ok = bool(o["created"]) and ok1 and ok2 and ok3
    s01_txt = "、".join(f"{sid}(no={no},{'∅' if not bids else '/'.join(bids)},{role})"
                        for sid, no, bids, role in o["s01"])
    record("T11 backfill 後既有鏡相對順序不變＋無 beatIds 手動鏡排在最後", ok,
           f"created={o['created']}；既有 {len(o['before'])} 鏡相對順序不變={ok1}"
           f"（{'同序' if ok1 else '前：' + ','.join(o['before']) + ' 後：' + ','.join(o['afterKept'])}）；"
           f"e01s01 有 beatIds {o['withBeatIds']} vs 無 beatIds {o['withoutBeatIds']} → "
           f"無的全部在後={o['noBeatIdsAfter']}；第一支有 beatIds={o['firstWithBeatIds']}；"
           f"e01s01 (id,no,beatIds,role)={s01_txt}")


# ── T12：磁碟沒有 derived 的乾淨複本 → /storyboard 的 orphanShotIds 仍正確 ──────
def t12() -> None:
    """P1 的機械驗收：`/storyboard` 不得只讀磁碟快照 `shot.derived`。

    乾淨複本（母本本來就沒有 `derived`）打 `GET /api/projects/{slug}/storyboard`，
    e01s01 的 `orphanShotIds` 必須是 sh25–sh28（集合相等，排序不比），其餘場次為空；
    而且要與 `GET /api/projects/{slug}` 的 `derived.orphan` 判定一致。
    """
    fresh_copy()
    disk = json.loads((WORK_PROJECTS / SLUG / "project.json").read_text(encoding="utf-8"))
    n_derived = sum(1 for s in (disk.get("shots") or []) if isinstance(s, dict) and "derived" in s)
    code, sb = http("GET", f"/api/projects/{SLUG}/storyboard")
    assert code == 200, f"/storyboard 失敗 {code}: {str(sb)[:300]}"
    got = {x.get("episodeSceneId"): list(x.get("orphanShotIds") or []) for x in sb["scenes"]}
    want_e1 = {"sh25", "sh26", "sh27", "sh28"}
    hits = {k: set(v) for k, v in got.items()}
    others_empty = all(not ids for k, ids in hits.items() if k != "e01s01")
    e1_ok = hits.get("e01s01") == want_e1
    # 與整份專案的判定一致
    code2, pj = http("GET", f"/api/projects/{SLUG}")
    assert code2 == 200, f"GET 專案失敗 {code2}"
    from_proj: dict[str, set] = {}
    for sh in pj["project"]["shots"]:
        if (sh.get("derived") or {}).get("orphan"):
            from_proj.setdefault(sh.get("episodeSceneId"), set()).add(sh.get("id"))
    same = all({k: v for k, v in from_proj.items()}.get(k, set()) == hits.get(k, set())
               for k in set(from_proj) | set(hits))
    ok = n_derived == 0 and e1_ok and others_empty and same
    record("T12 乾淨複本（無 derived）的 /storyboard orphanShotIds == sh25–sh28 且與 GET 專案一致", ok,
           f"磁碟帶 derived 的鏡 {n_derived} 支（應 0）；e01s01={sorted(hits.get('e01s01') or [])}"
           f"（應 {sorted(want_e1)}）；其他場次皆空={others_empty}；與 GET /api/projects 判定一致={same}；"
           f"全部場次={ {k: sorted(v) for k, v in hits.items()} }")


# ── T13：按「模板編譯」後手寫旗標消失、再存檔不翻回 manual（P3）────────────
def t13() -> None:
    """P3：`POST /shots/{id}/compile-prompt` 成功寫回後，`promptEnManual`／`promptAdoptedManual`
    都要消失（使用者選擇了編譯稿），`derived.promptSource == "compiled"`，且之後再存檔一次
    不得被翻回 manual（＝欄位不再出現、source 仍是 compiled、字仍是編譯稿）。
    """
    fresh_copy()
    from app import storage  # 需要「裸存檔」驗 P3 的存檔端行為（不經 PUT 瘦身副本）
    sid = "sh10"      # 母本非空、無旗標、≠compiled → 存檔後會被採認為手寫稿（有 adopted/手寫旗標）
    storage.save_project(SLUG, storage.load_project(SLUG))   # 先存一次：讓 sh10 被採認成手寫稿
    pre = next(x for x in storage.load_project(SLUG)["shots"] if x["id"] == sid)
    pre_manual = pre.get("promptEnManual") is True
    pre_adopted = pre.get("promptAdoptedManual") is True
    code, r0 = http("POST", f"/api/projects/{SLUG}/shots/{sid}/compile-prompt")
    assert code == 200, f"compile-prompt 失敗 {code}: {str(r0)[:300]}"
    cleared_flag = r0.get("promptManualCleared") is True
    src0 = r0.get("promptSource")
    compiled_len = len(r0.get("promptEn") or "")
    states = []
    for i in (1, 2):
        q = json.loads((WORK_PROJECTS / SLUG / "project.json").read_text(encoding="utf-8"))
        sh = next(x for x in q["shots"] if x["id"] == sid)
        states.append({"i": i,
                       "manual": sh.get("promptEnManual") is True,
                       "adoptedFlag": sh.get("promptAdoptedManual") is True,
                       "source": (sh.get("derived") or {}).get("promptSource"),
                       "chars": len(sh.get("promptEn") or ""),
                       "adopted": (sh.get("derived") or {}).get("adoptedManual") is True})
        if i == 1:   # 再存一次檔：不得被 refresh_derived 翻回 manual
            p = storage.load_project(SLUG)
            storage.save_project(SLUG, p)
    ok = (pre_manual and pre_adopted and cleared_flag and src0 == "compiled"
          and all(not s["manual"] and not s["adoptedFlag"] and s["source"] == "compiled"
                  and s["chars"] == compiled_len and not s["adopted"] for s in states))
    record("T13 模板編譯後手寫旗標消失、promptSource=compiled、再存檔不翻回 manual", ok,
           f"{sid} 編譯前 promptEnManual={pre_manual}／promptAdoptedManual={pre_adopted}；"
           f"promptManualCleared={cleared_flag}、回應 promptSource={src0}、編譯稿 {compiled_len} 字；"
           + "；".join(f"存檔第{s['i']}次 promptEnManual={s['manual']}、promptAdoptedManual={s['adoptedFlag']}、"
                      f"source={s['source']}、{s['chars']} 字、derived.adoptedManual={s['adopted']}"
                      for s in states))


# ── main ──────────────────────────────────────────────────────────
ALL = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12", "T13"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑這幾條，例：T1,T5")
    ap.add_argument("--no-ui", action="store_true", help="跳過 T6（前端）")
    ap.add_argument("--keep", action="store_true", help="跑完保留複本與 server log（預設也保留）")
    args = ap.parse_args()
    want = [x.strip().upper() for x in args.only.split(",") if x.strip()] or ALL
    if args.no_ui:
        want = [x for x in want if x != "T6"]

    os.environ.setdefault("SVS_DATA_DIR", str(WORK_PROJECTS))
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    WORKROOT.mkdir(parents=True, exist_ok=True)
    src_before = (SRC_PROJECTS / SLUG / "project.json").stat().st_mtime
    proc = start_server()
    print(f"dev 實例：{BASE}（資料：{WORK_PROJECTS}）", flush=True)
    try:
        for name in want:
            fn = globals().get(name.lower())
            if fn is None:
                record(name, False, "沒有這條測試")
                continue
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                record(name, False, f"例外：{type(e).__name__}: {e}")
                if os.environ.get("SVS_TEST_TRACE"):
                    traceback.print_exc()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        # 母本沒被寫到（正本保護）
        src_after = (SRC_PROJECTS / SLUG / "project.json").stat().st_mtime
        print(f"\n母本 project.json mtime 未變：{src_before == src_after}", flush=True)

    npass = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n結果：{npass}/{len(RESULTS)} PASS")
    for name, ok, detail in RESULTS:
        print(f"  {'✓' if ok else '✗'} {name}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
