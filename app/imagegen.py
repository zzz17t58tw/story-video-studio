"""M4a／M6：單鏡生圖（Qwen Image 2.1 i2i·t2i｜MOSS Image SFT t2i）——把專案的鏡頭卡接上本機 ComfyUI。

設計原則：
- templates/ 內的原檔只讀不改；要調參一律 copy 到記憶體 dict 再改（見 _load_template）。
- 一次只送一張、解析度預設 1024（VRAM 有限，見本輪任務說明）。
- 與 h3compose 的分工：h3compose 產生 H3 用的六段式 promptEn（含 `<Picture N>`）；
  這裡只把它當「給生圖模型的文字」用，機械把 `<Picture N>` 換成 Qwen 的 `<imageN>`，
  N 對應 referencePlan.slots 的 ordinal → 送進去的那張圖。
- 寫回：shots[].images.first（舊值記在 pipeline.logs 的 prevFirst，不另建欄位）。

M6：engine=moss
- MOSS 是純文字模型（本機只有 t2i 底模，沒有編輯 LoRA）→ **不吃參考圖**，
  身份靠 caption 裡的 identityAnchors 逐字描述（build_moss_caption），不靠 i2i。
- 工作流是「前端 UI 格式」（templates/moss_image_sft.ui.json，21 節點）→ 送件前轉成 API 格式；
  提示詞擴寫 LLM 輔助（節點 32-37）不接，直接忽略。
- 前端 combo「載入即回退」：UI 檔的 CLIPLoader 寫死檔名可能被前端換成清單第一個；
  轉格式時一律用 OUR 指定的檔名，並用 loader 的實際值回報 user 用的是哪顆 encoder。
"""

from __future__ import annotations

import copy
import contextlib
import json
import random
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import comfy, gpu, h3compose, storage, paths

ROOT = paths.TEMPLATES_DIR.parent
TEMPLATE_I2I = paths.TEMPLATES_DIR / "qwen21_i2i_edit.api.json"
TEMPLATE_T2I = paths.TEMPLATES_DIR / "qwen21_t2i.api.json"
TEMPLATE_MOSS = paths.TEMPLATES_DIR / "moss_image_sft.ui.json"

MAX_REF_IMAGES = 4                 # Qwen 2.1 編輯模板 images 是 COMFY_AUTOGROW_V3，實測可接 4 張（見 M4b 驗收 pid）
MAX_RESOLUTION = 1536
DEFAULT_RESOLUTION = 1024
DEFAULT_TIMEOUT = 180
MOSS_TIMEOUT = 420                 # MOSS 是 5B DiT + 9B 語言塔，第一次載模型 + 30 步實測遠超過 180 秒
MODELS = {
    "i2i": "qwen_image_2.1_int8_convrot（i2i 編輯）",
    "t2i": "qwen_image_2.1_int8_convrot（t2i）",
    "moss": "moss_image_sft_int8_convrot（t2i）",
}
ENGINES = ("qwen", "moss")

# MOSS 的模型三件套（同系列 int8_convrot）；CLIPLoader 一定要是官方量化，不是 uncensored
MOSS_FILES = {
    "unet": "moss_image_sft_int8_convrot.safetensors",
    "clip": "qwen3.5_9b_int8_convrot.safetensors",
    "clipType": "moss_image",
    "vae": "flux2-vae.safetensors",
}
MOSS_NEGATIVE = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"
MOSS_STEPS = 30
MOSS_CFG = 3.0                     # 官方建議 3.0–6.0（短 prompt 可調高）
MOSS_SAMPLER = "euler"
MOSS_SCHEDULER = "sgm_uniform"
# 9:16 的尺寸階梯：OOM 就往下退一階（都要是 16 的倍數；見 MOSS 官方 bbox/尺寸規則）
MOSS_SIZES_9X16 = ((768, 1344), (704, 1216), (640, 1152), (576, 1024))


# referencePlan.slots 的 role/kind → 生圖提示詞裡對這張圖的說明
_ROLE_LABEL = {
    "face": "the character's face reference",
    "stage": "the character's costume/look reference",
    "sheet": "the character's costume/look reference",
    "full": "the character's full-body reference",
    "outfit": "the costume reference",
    "wide": "the location reference (environment only, no people)",
    "detail": "the location detail reference",
}
_KIND_LABEL = {"character": "character", "scene": "location", "prop": "prop"}


class ImageGenError(Exception):
    """可預期的生圖錯誤 → API 回 200 + ok:false（前端直接顯示中文原因）。"""


# ── GPU 自動切換（生圖前停本機 LLM、跑完一定在 finally 啟回）──────────
# 為什麼放在 imagegen 而不是 server：單張／批次／立繪三條路都要用，且批次只切一次。
# 為什麼用 contextmanager + finally：生圖失敗（OOM／逾時／使用者取消）也一定要把 LLM 啟回來，
# 不能讓「停 LLM」變成沒人還原的副作用。
class GpuBusyError(ImageGenError):
    """本機 LLM 正在運算中 → 整批不送件（不是生圖設定錯誤，前端要顯示成紅色橫幅）。"""

    busy = True


def gpu_public(sw: dict | None, rs: dict | None = None) -> dict:
    """給 API／前端看的 GPU 欄位（不含內部的 vramBefore/vramAfter 全物件）。"""
    sw = sw or {}
    rs = rs or {}
    vb = (sw.get("vramBefore") or {}).get("freeMB")
    va = (sw.get("vramAfter") or {}).get("freeMB")
    return {
        "switched": bool(sw.get("switched")),
        "stoppedLlama": bool(sw.get("stoppedLlama")),
        "freedMB": sw.get("freedMB"),
        "restored": bool(rs.get("restarted")),
        "restartWaitedS": rs.get("waitedS") or 0.0,
        "beforeFreeMB": vb,
        "afterFreeMB": va,
        "note": (sw.get("note") or "") + ("　" + rs["note"] if rs.get("note") else ""),
        "error": rs.get("error") or "",
        "autoSwitch": gpu.auto_switch_enabled(),
    }


@contextlib.contextmanager
def gpu_for_comfy(need_mb: int = 12000, slug: str = "", scope: str = "",
                  project: dict | None = None):
    """`with gpu_for_comfy() as box:` → box['switch']／box['restore']（結束後才填）。

    進入時：不足以生圖就停掉閒置的本機 LLM（LLM 正在運算 → GpuBusyError，不送件）。
    離開時（含例外）：只要是自己停的，一律 finally 啟回並寫進「已載入專案」的 pipeline.logs。

    參數 `project`：呼叫端手上那份（記憶體）專案 dict。有給就把 log 直接 append 到它身上、
    不另外 load+save —— 否則會用磁碟上的舊資料覆蓋呼叫端剛寫回的內容（實測：立繪的
    refImages 就是這樣被蓋掉的）。給 None 才走 load+save。
    """
    try:
        sw = gpu.ensure_comfy_gpu(need_mb)
    except gpu.GpuError as e:
        # 停不掉 LLM（含正在運算）→ 直接擋，不要在半殘狀態下生圖
        raise GpuBusyError(str(e)) if getattr(e, "status", 0) == 409 else ImageGenError(str(e)) from e
    box: dict = {"switch": sw, "restore": {}}
    try:
        yield box
    finally:
        try:
            rs = gpu.restore_llm_after(sw)
        except Exception as e:  # noqa: BLE001 — 還原層已包好，這裡是最後一道保險
            rs = {"restarted": False, "pid": None, "waitedS": 0.0,
                  "error": f"{type(e).__name__}: {e}", "note": f"本機 LLM 還原失敗：{e}"}
        box["restore"] = rs
        if sw.get("stoppedLlama"):
            if project is not None:
                _log_gpu_into(project, scope, sw, rs)
            elif slug:
                _log_gpu(slug, scope, sw, rs)


def _log_gpu_into(project: dict, scope: str, sw: dict, rs: dict) -> None:
    """把 GPU 自動切換記到「呼叫端手上這份」專案的 pipeline.logs（就地 append，不寫檔）。"""
    try:
        pipeline = project.get("pipeline")
        if not isinstance(pipeline, dict):
            pipeline = project["pipeline"] = {}
        logs = pipeline.get("logs")
        if not isinstance(logs, list):
            logs = []
            pipeline["logs"] = logs
        freed = sw.get("freedMB")
        msg = (f"GPU 自動切換（{scope or '生圖'}）："
               + (f"停止本機 LLM 釋放 {freed / 1024:.1f} GB" if freed is not None else "停止本機 LLM")
               + "；" + (rs.get("note") or ""))
        step = "videogen" if ("video" in (scope or "") or "影片" in (scope or "")) else "imagegen"
        logs.append({"at": storage.now_iso(), "step": step, "shotId": "", "gpu": True, "msg": msg})
        if len(logs) > 200:
            del logs[:-200]
    except Exception:  # noqa: BLE001
        pass


def _log_gpu(slug: str, scope: str, sw: dict, rs: dict) -> None:
    """把 GPU 自動切換記進專案檔的 pipeline.logs（呼叫端手上沒有 project 物件時用）。

    一律「就地 append」到 load 出來的 dict 上，不要重新指派 pipeline["logs"]
    —— 呼叫端常與這裡共用同一份記憶體，重新指派會讓另一邊的陣列被孤立。
    """
    if not slug:
        return
    try:
        data = storage.load_project(slug)
        _log_gpu_into(data, scope, sw, rs)
        storage.save_project(slug, data)
    except Exception:  # noqa: BLE001 — log 寫不進去不能影響生圖結果
        pass


# ── 小工具 ─────────────────────────────────────────────────────
def _s(v: Any) -> str:
    return str(v or "").strip()


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _load_template(path: Path) -> dict:
    """讀模板 API JSON（只讀，不改檔）。模板可能包成 {"prompt": {...}} 也可能直接是節點表。"""
    if not path.is_file():
        raise ImageGenError(f"缺少模板檔：{path.relative_to(ROOT)}（請確認 templates/ 未被搬動）")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ImageGenError(f"模板檔格式錯誤（{path.name}）：{type(e).__name__}") from e
    graph = raw.get("prompt") if isinstance(raw, dict) and isinstance(raw.get("prompt"), dict) else raw
    if not isinstance(graph, dict) or not graph:
        raise ImageGenError(f"模板檔沒有任何節點（{path.name}）")
    return copy.deepcopy(graph)


def _find_node_id(graph: dict, class_type: str) -> str | None:
    for nid, node in graph.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return nid
    return None


def _comfy_base(project: dict) -> str:
    comfy_cfg = (project.get("settings") or {}).get("comfy") or {}
    return _s(comfy_cfg.get("baseUrl")) or comfy.DEFAULT_BASE_URL


def find_shot(project: dict, shot_id: str) -> dict | None:
    return next((s for s in (project.get("shots") or []) if isinstance(s, dict) and s.get("id") == shot_id), None)


# ── 1. 收集參考圖（referencePlan → 最多 2 張）─────────────────────
def _local_media_path(project: dict, rel: str) -> Path:
    slug = project.get("meta", {}).get("slug") or ""
    return storage.project_dir(slug) / rel


def _char_face_ref(project: dict, cid: str) -> dict | None:
    ch = next((c for c in (project.get("characters") or []) if isinstance(c, dict) and c.get("id") == cid), None)
    if not ch:
        return None
    return h3compose._pick_char_ref(ch)


def _scene_ref(project: dict, sid: str) -> dict | None:
    sc = next((s for s in (project.get("scenes") or []) if isinstance(s, dict) and s.get("id") == sid), None)
    if not sc:
        return None
    return next((r for r in (sc.get("refImages") or [])
                 if isinstance(r, dict) and _s(r.get("path"))), None)


def collect_refs(project: dict, shot: dict) -> tuple[list[dict], list[str]]:
    """回 (refs, warnings)。refs = [{path, label, source, kind, ids}]，最多 MAX_REF_IMAGES（4）張。

    來源優先序：
      1. referencePlan.slots（角色 → 場景 的順序，同類照 ordinal）
      2. 沒有 plan 時：該鏡角色卡的鎖臉圖，再場景卡參考圖
      缺檔一律略過並回 warning（不中斷）。
    排序刻意「角色 > 場景」：4 個槽位時角色全部進得去，場景只會在超過 4 個角色時被擠掉。
    被丟掉的槽位會回 warning，呼叫端（preflight）再把「提示詞引用到被丟的圖」升級成拒送。
    """
    warnings: list[str] = []
    plan_slots = [s for s in ((shot.get("referencePlan") or {}).get("slots") or []) if isinstance(s, dict)]
    if plan_slots:
        picked: list[dict] = []
        for s in plan_slots:
            p = _s(s.get("path"))
            if not p:
                continue
            role = _s(s.get("role"))
            kind = _s(s.get("kind"))
            label = _ROLE_LABEL.get(role) or _KIND_LABEL.get(kind, "reference image")
            picked.append({"path": p, "label": label, "source": f"{kind}:{_s(s.get('id'))}",
                           "kind": kind, "ids": [_s(s.get("id"))], "ordinal": s.get("ordinal")})
        # 角色 > 場景（同類保留原 ordinal 順序；sorted 是穩定排序）
        picked.sort(key=lambda r: 0 if r["kind"] == "character" else 1)
    else:
        picked = []
        warnings.append("這鏡還沒有 referencePlan（可先按「▣ 建參考圖槽位」），改用角色/場景卡上的參考圖")
        for cid in (shot.get("characterIds") or []):
            ref = _char_face_ref(project, cid)
            if ref:
                picked.append({"path": ref["path"],
                               "label": _ROLE_LABEL.get(_s(ref.get("kind")), "the character reference"),
                               "source": f"character:{cid}", "kind": "character", "ids": [cid], "ordinal": None})
        sref = _scene_ref(project, _s(shot.get("sceneId")))
        if sref:
            picked.append({"path": sref["path"],
                           "label": _ROLE_LABEL.get(_s(sref.get("kind")), "the location reference"),
                           "source": f"scene:{_s(shot.get('sceneId'))}", "kind": "scene",
                           "ids": [_s(shot.get("sceneId"))], "ordinal": None})

    # 去重（同檔案只送一次）＋ 檢查檔案存在
    uniq: list[dict] = []
    seen: set[str] = set()
    slug = project.get("meta", {}).get("slug") or ""
    for r in picked:
        rel = r["path"]
        if rel in seen:
            continue
        seen.add(rel)
        if not (storage.project_dir(slug) / rel).is_file():
            warnings.append(f"參考圖檔不存在，已略過：{rel}")
            continue
        uniq.append(r)
    if len(uniq) > MAX_REF_IMAGES:
        dropped = uniq[MAX_REF_IMAGES:]
        warnings.append(f"參考圖超過上限，只送前 {MAX_REF_IMAGES} 張（Qwen 編輯模板槽位上限）；未送："
                        + "、".join(f"{r['path']}（{r['label']}）" for r in dropped))
        uniq = uniq[:MAX_REF_IMAGES]
    # 讓提示詞端知道哪些 slot 這輪真的送到（含被丟的）
    for r in uniq:
        r["sent"] = True
    return uniq, warnings


# ── 2. 提示詞（promptEn → Qwen 文字；<Picture N> → <imageN>）────
_PIC_RE = re.compile(r"<Picture\s+(\d+)>")
_NEGATIVE = "blurry, low quality, deformed hands, extra limbs, watermark, text, caption, logo, jpeg artifacts"


def _image_map(shot: dict, refs: list[dict]) -> dict[int, int]:
    """<Picture N> → <imageK>：N 是 slot ordinal，K 是「第幾張真的送進去」。"""
    ord_to_ref_idx: dict[int, int] = {}
    slots = [s for s in ((shot.get("referencePlan") or {}).get("slots") or []) if isinstance(s, dict)]
    for i, r in enumerate(refs):
        slot = next((s for s in slots if _s(s.get("path")) == r["path"]), None)
        if slot and isinstance(slot.get("ordinal"), int):
            ord_to_ref_idx[slot["ordinal"]] = i + 1
        else:
            # 沒有 plan 時：characterIds 依序對齊參考圖
            ord_to_ref_idx[i + 1] = i + 1
    return ord_to_ref_idx


def _prompt_base(shot: dict) -> str:
    """提示詞原文（不加參考圖說明）；空字串代表這鏡沒有可用提示詞。"""
    return _s(shot.get("promptEn")) or _s(shot.get("promptZh"))


def build_prompt(project: dict, shot: dict, refs: list[dict]) -> tuple[str, list[str]]:
    """回 (prompt, warnings)。規則：

    - promptEn 內有 `<Picture N>` 時，**N 一律照 referencePlan 的 ordinal 對應**；
      對不到實際送出的圖（N 超過張數／被丟掉的槽位）→ 直接拋 ImageGenError 而不是默默刪掉
      （默默刪掉會讓模型拿錯圖當主體，M4a 的 sh04 就是這樣白跑一趟 GPU）。
    - promptEn 沒有 `<Picture N>`（H3 六段式自然語言）時不硬塞編號：改在開頭用
      「<imageK> is …」說明每張圖是什麼，讓模型自己對。
    """
    warnings: list[str] = []
    base = _s(shot.get("promptEn"))
    if not base:
        base, cw, _meta = h3compose.compile_prompt(project, shot)
        base = _s(base)
        warnings.extend(cw)
        if base:
            warnings.append("這鏡的 promptEn 是空的，已改用「模板編譯」的即時結果生圖（未寫回 project.json）")
    if not base:
        base = _s(shot.get("promptZh"))
        if base:
            warnings.append("這鏡沒有英文 promptEn，直接用中文 promptZh（Qwen 可讀中文，但英文效果較穩）")
    if not base:
        raise ImageGenError("這鏡沒有可用的提示詞（promptEn／promptZh 都是空的）：請先寫提示詞或按「⌘ 模板編譯 prompt」")

    mapping = _image_map(shot, refs)
    pic_refs = sorted({int(m.group(1)) for m in _PIC_RE.finditer(base)})
    has_pic_refs = bool(pic_refs)
    if has_pic_refs:
        bad = [n for n in pic_refs if n not in mapping]
        if bad:
            raise ImageGenError(
                f"提示詞引用了 {', '.join(f'<Picture {n}>' for n in bad)}，但這次只送 {len(refs)} 張參考圖"
                f"（對應的槽位{'已超上限被略過' if len(refs) >= MAX_REF_IMAGES else '沒有圖／檔案不存在'}）："
                "請按「▣ 建參考圖槽位」修正槽位，或「⌘ 模板編譯 prompt」重編提示詞後再試（硬送會讓模型認錯主體）")
        prompt = _PIC_RE.sub(lambda m: f"<image{mapping[int(m.group(1))]}>", base).strip()
    else:
        prompt = base

    if not refs:
        warnings.append("這次沒有送任何參考圖（i2i 會失敗；若要純文字生圖請選 t2i）")
    elif not has_pic_refs:
        warnings.append("提示詞沒有 <Picture N> 引用（自然語言寫法）：已在開頭列出每張 <imageK> 是什麼，"
                        "但身份鎖定較弱；想鎖臉請按「⌘ 模板編譯 prompt」產生帶引用的版本")

    # 參考圖說明（固定寫在最前面，讓模型知道每張 <imageK> 是什麼）
    if refs:
        head = "; ".join(f"<image{i + 1}> is {r['label']}" for i, r in enumerate(refs))
        prompt = (f"Reference images: {head}.\n"
                  "Compose a single cinematic film still. Keep faces, hair, body shape and costume identical "
                  "to the reference images; relight and re-stage the scene as described below. No text, no watermark.\n\n"
                  + prompt)
    return prompt, warnings


# ── 2b. 前置檢查（避免白跑 GPU）──────────────────────────────────
def preflight(project: dict, shot: dict, mode: str = "ref") -> tuple[list[dict], list[str], list[str]]:
    """生圖前的機械檢查，回 (refs, warnings, blockers)。

    blockers 非空 → 呼叫端應回 ok:false 並把清單直接給使用者看，不要送 GPU。檢查：
      1. （i2i）這鏡每個角色都要有參考圖（鎖臉圖優先）；缺就列「c01（小紅帽）沒有鎖臉圖」
      2. （i2i）promptEn 的 <Picture N> 必須都對得到這輪真的送出的圖
      3. promptEn／promptZh 至少一個非空
      4. 參考圖張數不得為 0（i2i）
    t2i 模式不檢查參考圖（本來就不送）。
    """
    blockers: list[str] = []
    if mode != "ref":
        if not _prompt_base(shot):
            blockers.append("這鏡沒有可用的提示詞（promptEn／promptZh 都是空的）")
        return [], [], blockers

    char_ids = [c for c in (shot.get("characterIds") or []) if _s(c)]
    warnings: list[str] = []
    # 1. 每個角色至少要有「可送的一張參考圖」；沒有 → 擋。有但沒勾鎖臉 → 只當 warning
    #    （M4b 實測：這專案的人物卡多半是 stage 造型照而沒勾 locked，硬擋會讓批次完全跑不動；
    #     真正擋住的條件是「連一張圖都沒有」，那時臉一定不會被鎖。）
    for cid in char_ids:
        ch = next((c for c in (project.get("characters") or []) if isinstance(c, dict) and c.get("id") == cid), None)
        if ch is None:
            blockers.append(f"角色 {cid} 在人物卡裡不存在（鏡頭有引用）")
            continue
        refs_all = [r for r in (ch.get("refImages") or []) if isinstance(r, dict) and _s(r.get("path"))]
        # referencePlan 上已經有這個角色的槽位（如 stage 造型照）也算「有參考圖」
        plan_paths = [_s(s.get("path")) for s in ((shot.get("referencePlan") or {}).get("slots") or [])
                      if isinstance(s, dict) and _s(s.get("id")) == cid and _s(s.get("path"))]
        refs_all += [{"path": p, "locked": True, "kind": "plan"} for p in plan_paths]
        existing = [r for r in refs_all if (storage.project_dir(project.get("meta", {}).get("slug") or "") / r["path"]).is_file()]
        if not existing:
            blockers.append(f"{cid}（{ch.get('name') or cid}）沒有鎖臉圖，也沒有任何可用的參考圖：請先上傳一張臉部照"
                            "（人物卡 → 參考圖 → kind=face 並勾「鎖臉」），否則這鏡的臉不會被鎖住")
            continue
        if not [r for r in existing if r.get("locked") and r.get("kind") != "plan"]:
            warnings.append(f"{cid}（{ch.get('name') or cid}）的參考圖沒有勾「鎖臉」：仍會送圖，但建議在人物卡勾「鎖臉」以確保是本人的臉")

    refs, w = collect_refs(project, shot)
    warnings = [x for x in warnings if "檔案不存在" not in x] + [x for x in w if "檔案不存在" not in x]
    blockers.extend(x for x in w if "檔案不存在" in x)
    if not refs:
        blockers.append("這鏡沒有任何可送的參考圖（referencePlan 與人物/場景卡都沒有可用圖片）")
    # 每個角色都要真的出現在送出的參考圖裡（否則 identity 沒被鎖）
    if refs:
        sent_ids = {i for r in refs for i in (r.get("ids") or [])}
        for cid in char_ids:
            if cid not in sent_ids:
                ch = next((c for c in (project.get("characters") or []) if isinstance(c, dict) and c.get("id") == cid), None)
                blockers.append(f"{cid}（{(ch or {}).get('name') or cid}）沒有進到這輪送出的參考圖"
                                f"（最多 {MAX_REF_IMAGES} 張，角色超額時場景會被擠掉）：請減少這鏡的角色或建對應槽位")

    # 2. <Picture N> 是否都對得到
    base = _prompt_base(shot)
    if base and refs:
        mapping = _image_map(shot, refs)
        pic_ns = sorted({int(m.group(1)) for m in _PIC_RE.finditer(base)})
        bad = [n for n in pic_ns if n not in mapping]
        if bad:
            send_n = len(refs)
            blockers.append(
                f"promptEn 引用了 {', '.join(f'<Picture {n}>' for n in bad)}，但這輪只會送 {send_n} 張參考圖"
                "（超過模板 4 張上限或槽位沒圖）：請按「⌘ 模板編譯 prompt」重編後再試")
    # 3. 提示詞
    if not base:
        blockers.append("這鏡沒有可用的提示詞（promptEn／promptZh 都是空的）")
    return refs, warnings, blockers


# ── 3. 產生節點圖 ───────────────────────────────────────────────
def build_graph(mode: str, prompt: str, negative: str, resolution: int, steps: int,
                cfg: float, seed: int, ref_names: list[str], prefix: str,
                aspect: str = "16:9") -> dict:
    """依模板組 API 節點圖（只改記憶體副本）。ref_names = ComfyUI input 目錄內的檔名。"""
    if mode == "t2i":
        graph = _load_template(TEMPLATE_T2I)
    else:
        graph = _load_template(TEMPLATE_I2I)

    enc_id = _find_node_id(graph, "TextEncodeQwenImage21")
    ks_id = _find_node_id(graph, "KSampler")
    save_id = _find_node_id(graph, "SaveImage")
    if not enc_id or not ks_id or not save_id:
        raise ImageGenError(f"模板缺少必要節點（TextEncodeQwenImage21／KSampler／SaveImage）：{mode}")

    enc = graph[enc_id]["inputs"]
    enc["prompt"] = prompt
    enc["negative_prompt"] = negative
    enc["resolution"] = resolution

    if mode == "t2i":
        lat = _find_node_id(graph, "EmptyLatentImage")
        if lat:
            # resolution 當長邊（<=1536），依 aspect 算短邊；長寬取 16 倍數
            w, h = _aspect_size(resolution, aspect)
            graph[lat]["inputs"]["width"] = w
            graph[lat]["inputs"]["height"] = h
    else:
        # i2i：參考圖槽位（模板有 10/11 兩個 LoadImage；3、4 張時補節點 12/13）。
        # images 是 COMFY_AUTOGROW_V3，M4b 已實測 4 張可正常出圖。
        load_ids = sorted([nid for nid, n in graph.items()
                           if isinstance(n, dict) and n.get("class_type") == "LoadImage"],
                          key=lambda x: int(x) if str(x).isdigit() else 999)
        for key in [k for k in list(enc) if k.startswith("images.image_")]:
            enc.pop(key, None)
        if len(ref_names) > MAX_REF_IMAGES:
            raise ImageGenError(f"參考圖 {len(ref_names)} 張超過上限 {MAX_REF_IMAGES}")
        next_id = max([int(x) for x in graph if str(x).isdigit()] or [0]) + 1
        for i, fname in enumerate(ref_names[:MAX_REF_IMAGES]):
            if i < len(load_ids):
                lid = load_ids[i]
            else:  # 模板 LoadImage 不夠：補節點
                lid = str(next_id)
                next_id += 1
                graph[lid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
            graph[lid]["inputs"]["image"] = fname
            enc[f"images.image_{i + 1}"] = [lid, 0]
        for extra in load_ids[len(ref_names):]:
            graph.pop(extra, None)
        if not ref_names:
            raise ImageGenError("i2i 模式沒有任何參考圖可送；請改用文字生圖（mode=t2i）")

    ks = graph[ks_id]["inputs"]
    ks["seed"] = seed
    ks["steps"] = steps
    ks["cfg"] = cfg

    graph[save_id]["inputs"]["filename_prefix"] = prefix
    return graph


_ASPECT = {"9:16": (9, 16), "3:4": (3, 4), "4:3": (4, 3), "1:1": (1, 1), "16:9": (16, 9), "21:9": (21, 9)}
ASPECTS = tuple(_ASPECT)


def _aspect_size(resolution: int, aspect: str = "16:9") -> tuple[int, int]:
    """t2i 的畫布：以 resolution 為短邊（不超過），依比例算另一邊，取 16 的倍數。"""
    w_r, h_r = _ASPECT.get(aspect, (16, 9))
    if w_r >= h_r:
        h = resolution
        w = int(round(resolution * w_r / h_r))
    else:
        w = resolution
        h = int(round(resolution * h_r / w_r))
    return (max(256, w // 16 * 16), max(256, h // 16 * 16))


# ══ M6：MOSS Image（engine=moss）════════════════════════════════
# MOSS 工作流的可調節點（id 取自 templates/moss_image_sft.ui.json，21 節點 int8 版）
MOSS_NODES = {
    "unet": 1, "clip": 2, "vae": 3, "slg": 5, "pos": 8, "neg": 9, "latent": 11,
    "sched": 20, "noise": 22, "guider": 23, "sampler": 24, "save": 30, "decode": 31,
}
# 要轉進 API 圖的節點（提示詞擴寫 LLM 輔助 32-37 不在裡面＝生圖時不接）
MOSS_API_NODES = (1, 2, 3, 5, 8, 9, 11, 20, 22, 23, 24, 29, 30, 31)
# UI 節點 widgets_values 的欄位順序（照 templates/moss_image_sft.ui.json 實測）
_MOSS_WIDGET_ORDER = {
    "UNETLoader": ("unet_name", "weight_dtype"),
    "CLIPLoader": ("clip_name", "type", "device"),
    "VAELoader": ("vae_name",),
    "SkipLayerGuidanceDiTSimple": ("double_layers", "single_layers", "start_percent", "end_percent"),
    "CLIPTextEncode": ("text",),
    "EmptyFlux2LatentImage": ("width", "height", "batch_size"),
    "ResolutionSelector": ("aspect_ratio", "megapixels", "multiple"),
    "BasicScheduler": ("scheduler", "steps", "denoise"),
    "RandomNoise": ("noise_seed", "control_after_generate"),
    "CFGGuider": ("cfg",),
    "KSamplerSelect": ("sampler_name",),
    "SaveImage": ("filename_prefix",),
}


def build_moss_size(aspect: str = "9:16", resolution: int | None = None,
                    tier: int = 0) -> tuple[int, int]:
    """MOSS 的畫布。9:16 走固定的 16 倍數階梯（768×1344 → … → 576×1024），tier 越大越省 VRAM。

    其他比例沿用 _aspect_size（resolution 當短邊），再對齊 16 的倍數。
    """
    if aspect == "9:16":
        idx = max(0, min(len(MOSS_SIZES_9X16) - 1, int(tier)))
        return MOSS_SIZES_9X16[idx]
    base = resolution or 768
    return _aspect_size(base, aspect)


def _moss_load_ui(path: Path = TEMPLATE_MOSS) -> dict:
    """讀 MOSS 的 UI 格式工作流（只讀）。"""
    if not path.is_file():
        raise ImageGenError(f"缺少 MOSS 工作流：{path.name}（請確認 templates/ 未被搬動）")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ImageGenError(f"MOSS 工作流檔格式錯誤（{path.name}）：{type(e).__name__}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        raise ImageGenError(f"MOSS 工作流檔不是前端格式（沒有 nodes 陣列）：{path.name}")
    return copy.deepcopy(raw)


def _ui_widget_values(node: dict) -> dict:
    """UI 節點的 widgets_values（陣列）→ {欄位名: 值}；不在白名單的欄位不轉。"""
    order = _MOSS_WIDGET_ORDER.get(_s(node.get("type")))
    vals = node.get("widgets_values")
    out: dict[str, Any] = {}
    if not order or not isinstance(vals, list):
        return out
    for i, name in enumerate(order):
        if i < len(vals):
            out[name] = vals[i]
    return out


def _ui_to_api(ui: dict, keep_ids: set[int] | None = None) -> dict:
    """前端 UI 格式 → API 格式（只留 keep_ids 內的節點與它們之間的連線）。

    links 每筆是 [link_id, from_node, from_slot, to_node, to_slot, type]；
    轉成 API 的 {to_node: {class_type, inputs: {欄位: [from_node, from_slot] 或值}}}。
    """
    keep = keep_ids if keep_ids is not None else set(MOSS_API_NODES)
    nodes = {n["id"]: n for n in ui.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    missing = [i for i in keep if i not in nodes]
    if missing:
        raise ImageGenError(f"MOSS 工作流缺少節點 {missing}（工作流檔被改過？）")
    # 連線：to_node → {to_slot: [from_node, from_slot]}
    incoming: dict[int, dict[int, list]] = {}
    for link in ui.get("links") or []:
        if not isinstance(link, (list, tuple)) or len(link) < 5:
            continue
        _lid, src, src_slot, dst, dst_slot = link[0], link[1], link[2], link[3], link[4]
        if dst in keep and src in keep:
            incoming.setdefault(dst, {})[int(dst_slot)] = [str(src), int(src_slot)]

    graph: dict[str, dict] = {}
    for nid in sorted(keep):
        node = nodes[nid]
        ctype = _s(node.get("type"))
        inputs = _ui_widget_values(node)
        # 有連線的輸入欄位（依 inputs 宣告的順序決定 slot 編號）
        wired = incoming.get(nid, {})
        for slot_idx, inp in enumerate(node.get("inputs") or []):
            if slot_idx in wired and _s(inp.get("name")):
                inputs[_s(inp.get("name"))] = wired[slot_idx]
        # 有些節點（SamplerCustomAdvanced／VAEDecode）沒有 widget，全靠連線
        graph[str(nid)] = {"class_type": ctype, "inputs": inputs}
    return graph


def _moss_snapshot_ui(ui: dict, prompt: str, width: int, height: int, steps: int, seed: int,
                      cfg: float, prefix: str, negative: str = MOSS_NEGATIVE) -> dict:
    """在 UI 副本上寫入所有參數（原檔不動），回 UI dict。

    這裡是「使用者若在同一支工作流上手動跑」會看到的同一組值；送件走 _ui_to_api 再轉一次。
    """
    nodes = {n["id"]: n for n in ui.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    for i in MOSS_API_NODES:
        if i not in nodes:
            raise ImageGenError(f"MOSS 工作流缺少節點 {i}（工作流檔被改過？）")

    # 1) loader 三件套：一律寫死成本機實際有的 int8_convrot（不讓前端回退到 uncensored）
    nodes[MOSS_NODES["unet"]]["widgets_values"][0] = MOSS_FILES["unet"]
    nodes[MOSS_NODES["clip"]]["widgets_values"][0] = MOSS_FILES["clip"]
    nodes[MOSS_NODES["clip"]]["widgets_values"][1] = MOSS_FILES["clipType"]
    nodes[MOSS_NODES["vae"]]["widgets_values"][0] = MOSS_FILES["vae"]
    # 2) 正向／負向文字
    nodes[MOSS_NODES["pos"]]["widgets_values"][0] = prompt
    nodes[MOSS_NODES["neg"]]["widgets_values"][0] = negative
    # 3) 尺寸（節點 11）＋ 排程（節點 20）＋ 雜訊種子（節點 22）＋ CFG（節點 23）
    nodes[MOSS_NODES["latent"]]["widgets_values"][:3] = [int(width), int(height), 1]
    nodes[MOSS_NODES["sched"]]["widgets_values"][:2] = [MOSS_SCHEDULER, int(steps)]
    nodes[MOSS_NODES["noise"]]["widgets_values"][0] = int(seed)
    nodes[MOSS_NODES["guider"]]["widgets_values"][0] = float(cfg)
    nodes[MOSS_NODES["sampler"]]["widgets_values"][0] = MOSS_SAMPLER
    nodes[MOSS_NODES["save"]]["widgets_values"][0] = prefix
    return ui


def build_moss_graph(prompt: str, width: int, height: int, steps: int, cfg: float, seed: int,
                     prefix: str, negative: str = MOSS_NEGATIVE,
                     resolution_px: int = 1024) -> tuple[dict, dict]:
    """回 (api_graph, loader_report)。loader_report = 送件時「實際」寫進 loader 的檔名。

    注意：API 送件不經過前端，所以 combo 不會被回退；但工作流檔本身若被改壞、
    或我们寫錯檔名，ComfyUI 會在 validate 時回「value not in list」——那就是檔案真的不在。
    loader_report 用來讓呼叫端／使用者核對「這次跑的是哪顆 encoder」。
    """
    ui = _moss_load_ui()
    ui = _moss_snapshot_ui(ui, prompt, width, height, steps, seed, cfg, prefix, negative)
    graph = _ui_to_api(ui)
    # ResolutionSelector（節點 12）沒接進圖：寬高由 EmptyFlux2LatentImage 直接給，
    # 避免兩者同時存在時「誰蓋誰」不明確（前端 UI 版是靠 12 → 11 的連線，API 版只留值）。
    graph.pop("12", None)
    report = {
        "unet": graph[str(MOSS_NODES["unet"])]["inputs"].get("unet_name"),
        "clip": graph[str(MOSS_NODES["clip"])]["inputs"].get("clip_name"),
        "clipType": graph[str(MOSS_NODES["clip"])]["inputs"].get("type"),
        "vae": graph[str(MOSS_NODES["vae"])]["inputs"].get("vae_name"),
        "width": graph[str(MOSS_NODES["latent"])]["inputs"].get("width"),
        "height": graph[str(MOSS_NODES["latent"])]["inputs"].get("height"),
        "steps": graph[str(MOSS_NODES["sched"])]["inputs"].get("steps"),
        "cfg": graph[str(MOSS_NODES["guider"])]["inputs"].get("cfg"),
        "seed": graph[str(MOSS_NODES["noise"])]["inputs"].get("noise_seed"),
        "sampler": graph[str(MOSS_NODES["sampler"])]["inputs"].get("sampler_name"),
        "scheduler": graph[str(MOSS_NODES["sched"])]["inputs"].get("scheduler"),
        "nodeCount": len(graph),
    }
    return graph, report


def read_back_loaders(base_url: str | None = None) -> dict:
    """向 ComfyUI 讀回「真正會用的檔名」對照：把我們指定的檔名丟給 /object_info 驗證存不存在。

    /object_info 的 combo 清單就是前端 loadGraphData 回退時用的同一份清單；
    我們指定的檔若不在清單裡，送件必定失敗——先講清楚，不要等 OOM 或 404。
    """
    url = comfy._base(base_url)
    out: dict[str, Any] = {"unet": {}, "clip": {}, "vae": {}}
    try:
        import httpx  # 本檔已透過 comfy 間接依賴；這裡保持延遲匯入

        with httpx.Client(timeout=20.0) as c:
            r = c.get(f"{url}/object_info")
            r.raise_for_status()
            info = r.json()
    except Exception as e:  # noqa: BLE001
        return {"error": f"讀不到 /object_info：{type(e).__name__}: {e}"}
    specs = (("unet", "UNETLoader", "unet_name", MOSS_FILES["unet"]),
             ("clip", "CLIPLoader", "clip_name", MOSS_FILES["clip"]),
             ("vae", "VAELoader", "vae_name", MOSS_FILES["vae"]))
    for key, cls, field, want in specs:
        node_info = ((info.get(cls) or {}).get("input") or {}).get("required") or {}
        opts = node_info.get(field) or []
        choices = opts[0] if opts and isinstance(opts[0], list) else []
        out[key] = {"want": want, "exists": want in choices, "fallbackWouldBe": (choices[0] if choices else None),
                    "count": len(choices)}
    clip_types = ((((info.get("CLIPLoader") or {}).get("input") or {}).get("required") or {})
                  .get("type") or [])
    types = clip_types[0] if clip_types and isinstance(clip_types[0], list) else []
    out["clipTypeOk"] = MOSS_FILES["clipType"] in types
    return out



# ── 3b. MOSS caption（純邏輯，不呼模型）─────────────────────────
# MOSS 要的是「caption 風格」：一段連續英文描述，沒有編號段落、沒有 Audio 段、禁 quality tag。
# 身份靠 identityAnchors 逐字貼上（MOSS 是純文字模型，沒有 i2i，描述漏掉＝換人）。
_MOSS_FORBIDDEN = ("masterpiece", "8k", "best quality", "ultra detailed", "high detail",
                   "award winning", "quality", "4k", "hd", "trending on artstation")
_MOSS_NO_PEOPLE_RE = re.compile(r"[；;]?\s*畫面內不得有人[^；;。]*", re.MULTILINE)
_MOSS_NO_PEOPLE_EN = re.compile(r",?\s*(empty scene with no people in frame|no people in frame|"
                                r"empty street|no silhouettes|no reflections of people|"
                                r"empty scene)[,，]?", re.IGNORECASE)
_SHOT_SIZE_EN = {"wide": "wide shot", "medium": "medium shot", "mcu": "medium close-up",
                 "close_up": "close-up", "cu": "close-up", "ecu": "extreme close-up",
                 "over_shoulder": "over-the-shoulder shot", "ots": "over-the-shoulder shot",
                 "pov": "POV shot", "insert": "insert close-up"}
_ANGLE_H = {"front": "front-on", "front_left": "from front-left", "front_right": "from front-right",
            "left": "from the left side", "right": "from the right side",
            "back_left": "from behind-left", "back_right": "from behind-right", "back": "from behind"}
_ANGLE_V = {"eye_level": "eye level", "high": "high angle looking down", "low": "low angle looking up",
            "top": "top-down angle", "bird": "high angle looking down"}
_ANGLE_S = {"wide": "wide framing", "medium": "medium framing", "close_up": "close framing",
            "extreme_close_up": "tight framing"}


def _scene_prompt_no_people(scene: dict) -> str:
    """場景 promptEn 去掉「no people」那句（這鏡要有人，不能照抄空的場景描述）。"""
    raw = _s(scene.get("promptEn"))
    raw = _MOSS_NO_PEOPLE_RE.sub("", raw)
    raw = _MOSS_NO_PEOPLE_EN.sub(", ", raw)
    raw = re.sub(r"\s*[；;]\s*$", "", raw)
    return re.sub(r"\s{2,}", " ", raw).strip(" ,，、；;")


def _anchors_verbatim(ch: dict, compact: bool = False) -> str:
    """identityAnchors 逐字貼上（faceShape／facialFeatures／uniqueMarks／hairStyle／colorAnchors Hex）。

    compact=True（同鏡多人）時只留臉形／五官／辨識特徵／髮型＋四個色碼，省略皮膚質感，
    避免兩個人的 caption 疊到 2000+ 字元反而讓兩張臉都糊掉。
    """
    a = ch.get("identityAnchors") or {}
    bits: list[str] = []
    for key in ("faceShape", "facialFeatures", "uniqueMarks", "hairStyle"):
        v = _s(a.get(key))
        if v:
            bits.append(v.rstrip("."))
    colors = a.get("colorAnchors") or {}
    hexes = [f"{k} {colors[k]}" for k in ("hair", "eyes", "skin", "primaryOutfit")
             if _s(colors.get(k))]
    if hexes:
        bits.append("color anchors " + ", ".join(hexes))
    skin = _s(a.get("skinTexture"))
    if skin and not compact and not _s(ch.get("id")).startswith("c"):
        # （人物卡的 skinTexture 一律帶，只有專案角色會走下面的中性規則）
        bits.append(skin.rstrip("."))
    elif skin and not compact:
        bits.append(skin.rstrip(".").replace("tired dull skin", "natural skin"))
    return "; ".join(bits)


def _who_age(age: str) -> str:
    """「不可辨」「成年」這種非數字年齡 → 英文形容詞（MOSS 讀不懂「不可辨」）。"""
    a = _s(age)
    if not a:
        return ""
    if "成年" in a or "adult" in a.lower():
        return "adult figure of indeterminate age"
    return ""


def _outfit_en(ch: dict, shot: dict) -> str:
    """這鏡的服裝：stageIds 指到的 stage.characterSheet 沒文字，所以退回角色的第一套 outfit.promptEn。"""
    outs = [o for o in (ch.get("outfits") or []) if isinstance(o, dict)]
    for o in outs:
        if _s(o.get("promptEn")):
            return _s(o.get("promptEn")).rstrip(".")
    return ""


# 「疲勞」的寫法：MOSS 把 heavy dark circles / 烏青 畫成黑眼圈瘀青 → 一律換成中性說法
_TIRED_REWRITE = (
    (r"(?i)heavy dark circles under (his |her |the )?(deep-set )?eyes", "tired eyes with faint shadows"),
    (r"(?i)(deep-set eyes with )?heavy dark circles", "tired eyes with faint shadows"),
    (r"(?i)dark circles under (his |her |the )?eyes", "tired eyes with faint shadows"),
    (r"(?i)visible veins and dark eye bags", "tired eyes with faint shadows"),
)


def _soften_tired(text: str) -> str:
    out = text
    for pat, rep in _TIRED_REWRITE:
        out = re.sub(pat, rep, out)
    return out


def _strip_quality(text: str) -> str:
    out = text
    for bad in _MOSS_FORBIDDEN:
        out = re.sub(rf"(?i)[,\s]*\b{re.escape(bad)}\b", "", out)
    return re.sub(r"\s{2,}", " ", out).strip(" ,;")


def _scene_overlaps_subject(scene_desc: str, subj: str) -> bool:
    """scene 的描述是否已被鏡頭的 Shot content 涵蓋（共用 3 個以上實詞就算重複）。"""
    if not subj:
        return False
    stop = {"with", "the", "and", "of", "in", "on", "a", "an", "is", "its", "from", "to"}
    words = lambda t: {w for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in stop}  # noqa: E731
    return len(words(scene_desc) & words(subj)) >= 2


def _trim_clauses(text: str, max_chars: int) -> str:
    """按逗號切句，從前面累積到 max_chars（不切半句）；至少保留第一句。"""
    parts = [p.strip() for p in re.split(r"[,，]", text) if p.strip()]
    out: list[str] = []
    total = 0
    for p in parts:
        if out and total + len(p) + 2 > max_chars:
            break
        out.append(p)
        total += len(p) + 2
    return ", ".join(out)


def build_moss_caption(project: dict, shot: dict) -> tuple[str, list[str]]:
    """組 MOSS 用的 caption（回 (caption, warnings)）。純字串處理，不呼叫任何模型。

    結構（一段連續英文，不編號、沒有 Audio 段）：
      景別與角度 → 場景（scene.promptEn 去 no-people 句，截到第一個「空景」特徵為止）
      → 出場角色（identityAnchors **逐字**＋服裝；這部分不截斷，因為它就是身份）
      → 動作（beat 或 promptZh） → 光線與氛圍（scene.lighting/mood、shot.spec.lighting 各取一段）
    長度目標 120–260 字英文（identityAnchors 較長時會超，屬預期：身份 > 長度）；禁 quality tag。
    """
    warnings: list[str] = []
    spec = shot.get("spec") or {}
    size = _SHOT_SIZE_EN.get(_s(spec.get("shotType")), "")
    angle = spec.get("angle") if isinstance(spec.get("angle"), dict) else {}
    ang_bits = [_ANGLE_H.get(_s(angle.get("h")), ""), _ANGLE_V.get(_s(angle.get("v")), ""),
                _ANGLE_S.get(_s(angle.get("s")), "")]
    ang = ", ".join(b for b in ang_bits if b)
    head = f"{size or 'medium shot'}{(', ' + ang) if ang else ''}."

    scene = next((s for s in (project.get("scenes") or [])
                  if isinstance(s, dict) and _s(s.get("id")) == _s(shot.get("sceneId"))), None) or {}
    # 場景只留「能一眼認出這是哪裡」的前幾句（MOSS caption 越短越準；長描述會讓它分心去畫雜物）
    scene_desc = _trim_clauses(_scene_prompt_no_people(scene), 200)
    if scene_desc:
        scene_desc = scene_desc[0].upper() + scene_desc[1:] + "."
    else:
        warnings.append(f"場景 {_s(shot.get('sceneId'))} 沒有 promptEn，caption 少了場景描述")

    people: list[str] = []
    for cid in (shot.get("characterIds") or []):
        ch = next((c for c in (project.get("characters") or [])
                   if isinstance(c, dict) and _s(c.get("id")) == _s(cid)), None)
        if ch is None:
            warnings.append(f"角色 {cid} 在人物卡裡不存在，caption 略過")
            continue
        multi = len([c for c in (shot.get("characterIds") or []) if _s(c)]) > 1
        anchor = _anchors_verbatim(ch, compact=multi)
        age, gender = _s((ch.get("appearance") or {}).get("age")), _s((ch.get("appearance") or {}).get("gender"))
        who = " ".join(x for x in (f"{age}-year-old" if age.isdigit() else _who_age(age),
                                   {"男": "man", "女": "woman"}.get(gender, "")) if x).strip()
        outfit = _trim_clauses(_outfit_en(ch, shot), 110)
        parts = [f"a {who}" if who else "a person"]
        if anchor:
            parts.append(anchor)
        else:
            warnings.append(f"{cid}（{ch.get('name') or cid}）沒有 identityAnchors，caption 鎖不住長相")
        if outfit:
            parts.append("wearing " + outfit)
        people.append(", ".join(parts))
    if not people:
        warnings.append("這鏡沒有指到任何角色（characterIds 是空的）：caption 只有場景與動作")

    actions: list[str] = []
    for b in (shot.get("beats") or []):
        if isinstance(b, dict):
            for k in ("action", "actionZh", "text", "line", "desc"):
                v = _s(b.get(k))
                if v:
                    actions.append(v)
                    break
        elif isinstance(b, str) and _s(b):
            actions.append(_s(b))
    if not actions and _s(shot.get("promptZh")):
        actions.append(_s(shot.get("promptZh")))
    # promptZh 是中文；MOSS 的語言塔是 Qwen3.5，讀中文沒問題（硬翻成破英文反而更容易畫錯）
    action = "。".join(a.rstrip("。.") for a in actions[:2])[:180]

    # promptEn 是 H3 六段式（1. Subject / 2. Action / 3. Camera / 4. Lighting / 5. Atmosphere / 6. Audio）。
    # 只取 Subject 段的「開頭名詞」（人物＋場景＋關鍵道具），不整段貼：
    # 貼整段會把 H3 的鏡頭語言與 Audio 一起塞進來（MOSS 不需要），但完全不貼又會缺「畫面上有什麼」
    # （sh03 特寫被畫成雙人中景、sh05 監控畫面被畫成店內，兩次都是實測）。
    subj_head = ""
    pe = _s(shot.get("promptEn"))
    m = re.search(r"(?is)(?:^|\n)\s*1\.\s*Subject:\s*(.+?)(?:\n\s*2\.|$)", pe)
    if m:
        subj = re.sub(r"\([^)]*\)", "", m.group(1))          # 去括號註解
        subj = re.sub(r"(?i)\b(scene|location|ref|reference)\s+(sc|ch|c)\d+\b", "", subj)
        subj_head = _trim_clauses(subj, 150)

    # 光線：場景燈光與鏡頭燈光重複度高 → 只取一段並去掉句號（下面統一補）
    light = _trim_clauses(_s(spec.get("lighting")) or _s(scene.get("lighting")), 90).rstrip("。.")
    mood = _trim_clauses(_s(scene.get("mood")), 60).rstrip("。.")
    shot_light_bits = [light, mood]

    # sh03 這類特寫沒有角色在畫面裡（只有手/物件）→ 動作段那個「In frame」會讓模型硬塞人臉
    sizes_no_face = ("close_up", "cu", "insert", "ecu")
    prop_tight = _s(spec.get("shotType")) in sizes_no_face and people
    if prop_tight:
        # 特寫物件：不放「In frame」的人臉描述（放了 MOSS 就把兩人畫成中景，實測 3 次都這樣）
        chunks_note = ("（特寫：只有物件與一隻手入鏡，臉不在畫面內）")
        people = people[:1]
    else:
        chunks_note = ""

    chunks = [head]
    # 場景：promptEn 的 Subject 段已經寫了場景時就不重複貼 scene.promptEn
    # （兩段都在講「店內」，MOSS 會把它當成兩個空間；sh05「對街雨中」就是這樣被畫進店裡）
    if scene_desc and not _scene_overlaps_subject(scene_desc, subj_head):
        chunks.append(scene_desc)
    if subj_head:
        chunks.append("Shot content: " + _soften_tired(subj_head) + ".")
    if people and not prop_tight:
        chunks.append("In frame: " + "; ".join(people) + ".")
        # 店員要靠「在櫃台後面」定住位置：不寫的話 MOSS 讓他和客人在走道並排站（sh02 實測）
        if "clerk" in "; ".join(people).lower():
            chunks.append("The clerk stands behind the checkout counter.")
        # 人數要釘死：不寫「只有這幾個人」時 MOSS 會補一個路人或第二個店員（sh04 實測）
        chunks.append(f"Exactly {'one person' if len(people) == 1 else str(len(people)) + ' people'} "
                      "in the frame, no other people, no background customers.")
        # 過肩：前景那顆肩頸頭就是他自己（不寫的話 MOSS 會多畫一個背對的人站在前面）
        if "over-the-shoulder" in head or "over_shoulder" in _s(spec.get("shotType")).lower():
            chunks.append("The blurred shoulder and back of the head in the foreground belong to "
                          "the SAME man, it is his own shoulder, there is no second person.")
        # 角色卡上有疤／OK繃時，MOSS 會順手把整張臉畫成受傷 → 明講「只有這些標記，沒有其他傷」
        ple = "; ".join(people).lower()
        if any(w in ple for w in ("scar", "bandage", "callus")):
            chunks.append("Only those small marks are present; otherwise clean undamaged skin, "
                          "no black eye, no bruising, no bruise, no cut, no blood.")
        if "faint shadows" in ple or "tired eyes" in ple:
            chunks.append("Under-eye area is only slightly shadowed from tiredness, not dark, "
                          "no swollen eyelids, no redness.")
    if action:
        chunks.append("Action: " + action + (chunks_note or "") + ".")
    # 構圖錨點：caption 沒寫「誰在畫面哪裡」時，MOSS 會把所有人都塞成同一個中景（sh03／sh05 實測）
    if people and not prop_tight and _s(spec.get("composition")):
        comp = _trim_clauses(_s(spec.get("composition")), 90).rstrip("。.")
        chunks.append(f"Framing: {head.rstrip('.')} — {comp}.")
    atmos = "，".join(x for x in shot_light_bits if x)
    if atmos:
        chunks.append("Lighting and mood: " + atmos + ".")
    caption = _soften_tired(_strip_quality(" ".join(c for c in chunks if _s(c))).strip())
    caption = re.sub(r"\s{2,}", " ", caption)
    # 中文句號/逗號混在英文裡會讓 tokenizer 切得零碎 → 統一逗號
    caption = caption.replace("。", ".").replace("，", ", ")
    caption = re.sub(r"(?<=[。.])[,，]\s*", "", caption)
    caption = re.sub(r"\s{2,}", " ", caption)
    if len(caption) < 120:
        warnings.append(f"caption 只有 {len(caption)} 字元（目標 120–260）：可能是人物卡或場景卡描述太空")
    elif len(caption) > 260:
        core = len(caption) - sum(len(_anchors_verbatim(c)) for c in
                                  (project.get("characters") or [])
                                  if _s(c.get("id")) in [_s(x) for x in (shot.get("characterIds") or [])])
        if core > 160:
            warnings.append(f"caption 長度 {len(caption)} 字元（去掉 identityAnchors 後 {core}）超過建議 260："
                            "文字部分仍偏長，MOSS 可能漏細節")
    return caption, warnings


# ── 4. 主流程 ───────────────────────────────────────────────────
def _png_size(path: Path) -> tuple[int | None, int | None]:
    """只讀 PNG 標頭（IHDR 在固定位移），不引入影像套件。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _is_oom(err: Exception) -> bool:
    """判斷這次失敗是不是 VRAM 不夠（決定要不要降一階尺寸重試）。"""
    txt = str(err).lower()
    keys = ("out of memory", "outofmemory", "allocation on device", "cuda error",
            "not enough memory", "oom", "insufficient memory")
    return any(k in txt for k in keys)


def _write_back(project: dict, shot: dict, shot_id: str, rel_path: str, pid: str, elapsed: float,
                seed: int, resolution: int, steps: int, prompt: str, refs: list[dict],
                model: str, extra_msg: str = "") -> str:
    """把首幀寫回 shot（不存檔），順手記一筆 pipeline.log；回 prev_first。"""
    images = shot.get("images") if isinstance(shot.get("images"), dict) else {}
    shot["images"] = images
    prev_first = _s(images.get("first"))
    images["first"] = rel_path
    shot["updatedAt"] = storage.now_iso()
    logs = project.setdefault("pipeline", {}).setdefault("logs", [])
    logs.append({
        "at": storage.now_iso(),
        "step": "imagegen",
        "shotId": shot_id,
        "msg": (f"{model} 完成：{rel_path}｜{elapsed} 秒｜seed={seed}"
                f"｜resolution={resolution}｜steps={steps}" + extra_msg
                + (f"｜舊首幀 {prev_first}（檔案保留）" if prev_first else "")),
        "pid": pid, "model": model, "elapsed": elapsed, "path": rel_path,
        "prevFirst": prev_first, "seed": seed,
        "refs": [r["path"] for r in refs], "promptLen": len(prompt),
    })
    if len(logs) > 200:          # log 只留最近 200 筆（prompt 全文不入 log、只記長度）
        del logs[:-200]
    return prev_first


def generate_moss_image(project: dict, shot: dict, opts: dict) -> dict:
    """engine=moss：純文字（caption）→ MOSS Image SFT t2i。不吃參考圖。

    尺寸依 meta.aspectRatio；9:16 從 768×1344 起，OOM 就往下退一階（576×1024 到底）。
    降級一律寫進 warnings 回報，不默默換尺寸。

    opts["_gpuForce"]：批次外層已經切換過 → 這裡不要再停/啟一次（批次只切一次）。
    """
    if not opts.get("_gpuForce"):
        slug = project.get("meta", {}).get("slug") or ""
        with gpu_for_comfy(12000, slug, "生圖（MOSS 首幀）", project=project) as box:
            res = _generate_moss_image_impl(project, shot, opts)
        res["gpu"] = gpu_public(box["switch"], box["restore"])
        return res
    return _generate_moss_image_impl(project, shot, opts)


def _generate_moss_image_impl(project: dict, shot: dict, opts: dict) -> dict:
    shot_id = _s(shot.get("id"))
    warnings: list[str] = []
    base_url = _comfy_base(project)
    ok, msg, info = comfy.comfy_available(base_url)
    if not ok:
        raise ImageGenError(msg)
    if info.get("vramFreeMB") is not None and info["vramFreeMB"] < 1500:
        warnings.append(f"ComfyUI 可用 VRAM 只剩 {info['vramFreeMB']} MB，MOSS 可能很慢或 OOM"
                        "（ComfyUI 會自己卸載舊模型載入 MOSS，等它一下）")

    caption, cw = build_moss_caption(project, shot)
    warnings.extend(cw)
    if not caption:
        raise ImageGenError("這鏡組不出 MOSS caption（人物卡與場景卡都是空的）：請先寫 promptZh 或補人物/場景設定")

    try:
        seed = int(opts.get("seed")) if opts.get("seed") not in (None, "") else random.randint(0, 2 ** 31 - 1)
    except (TypeError, ValueError):
        seed = random.randint(0, 2 ** 31 - 1)
    steps = _clamp_int(opts.get("steps"), 1, 60, MOSS_STEPS)
    cfg = _clamp_float(opts.get("cfg"), 0.0, 20.0, MOSS_CFG)
    timeout = _clamp_int(opts.get("timeout"), 30, 900, MOSS_TIMEOUT)

    aspect = _s(opts.get("aspect")) or _s((project.get("meta") or {}).get("aspectRatio")) or "9:16"
    if aspect not in _ASPECT:
        warnings.append(f"不認得比例 {aspect}，改用 9:16")
        aspect = "9:16"
    tier = _clamp_int(opts.get("sizeTier"), 0, len(MOSS_SIZES_9X16) - 1, 0)
    if aspect == "9:16" and tier:
        warnings.append(f"起始尺寸已指定為第 {tier + 1} 階（{MOSS_SIZES_9X16[tier][0]}×{MOSS_SIZES_9X16[tier][1]}）")
    slug = project.get("meta", {}).get("slug") or ""
    project_dir = storage.project_dir(slug)
    dest_dir = project_dir / "media" / "shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    attempts: list[dict] = []
    saved: list[Path] = []
    pid = ""
    t0 = time.time()
    loaders: dict = {}
    final_w = final_h = 0
    for attempt in range(tier, len(MOSS_SIZES_9X16)):
        w, h = build_moss_size(aspect, opts.get("resolution"), attempt)
        prefix = f"svs_moss_{shot_id}_{stamp}"
        graph, report = build_moss_graph(caption, w, h, steps, cfg, seed, prefix)
        loaders = report
        try:
            pid = comfy.submit(graph, base_url)
        except comfy.ComfyError as e:
            # 送件就被拒（多半是檔名不在清單）→ 不重試，直接把原因吐出去
            raise ImageGenError(f"MOSS 送件失敗：{e}｜loader={report.get('unet')}／{report.get('clip')}") from e
        try:
            record = comfy.wait(pid, timeout=timeout, base_url=base_url)
        except comfy.ComfyError as e:
            attempts.append({"w": w, "h": h, "pid": pid, "error": str(e)[:400]})
            if _is_oom(e) and attempt + 1 < len(MOSS_SIZES_9X16):
                nw, nh = MOSS_SIZES_9X16[attempt + 1]
                warnings.append(f"{w}×{h} 爆 VRAM（{str(e)[:120]}），已降到 {nw}×{nh} 重試"
                                f"（第 {attempt + 2}／{len(MOSS_SIZES_9X16)} 階）")
                continue
            raise ImageGenError(f"{e}｜prompt_id={pid}（seed={seed}）｜MOSS {w}×{h}") from e
        target_name = f"{shot_id}_{time.strftime('%Y%m%d-%H%M%S')}.png"
        try:
            saved = comfy.collect_images(record, dest_dir, filename=target_name, base_url=base_url)
        except comfy.ComfyError as e:
            raise ImageGenError(str(e)) from e
        if not saved:
            raise ImageGenError("ComfyUI 完成了，但沒有任何圖檔落地（collect_images 回空）")
        final_w, final_h = w, h
        if attempt > tier:
            warnings.append(f"尺寸降級：由第 {tier + 1} 階降到第 {attempt + 1} 階（實際產出 {w}×{h}）")
        break
    if not saved:
        raise ImageGenError("MOSS 所有尺寸階梯都失敗：" + "；".join(
            f"{a['w']}×{a['h']}：{a['error']}" for a in attempts))

    rel_path = f"media/shots/{saved[0].name}"
    elapsed = round(time.time() - t0, 1)
    width, height = _png_size(saved[0])
    prev_first = _write_back(project, shot, shot_id, rel_path, pid, elapsed, seed,
                             final_w, steps, caption, [], MODELS["moss"],
                             extra_msg=f"｜{final_w}×{final_h}｜cfg={cfg}｜engine=moss｜encoder={loaders.get('clip')}")
    return {
        "ok": True, "path": rel_path, "pid": pid, "elapsed": elapsed, "warnings": warnings,
        "width": width, "height": height, "engine": "moss",
        "mode": "moss", "model": MODELS["moss"],
        "seed": seed, "resolution": final_w, "steps": steps, "cfg": cfg,
        "size": {"width": final_w, "height": final_h},
        "refs": [], "prompt": caption, "promptChars": len(caption), "prevFirst": prev_first,
        "loaders": loaders, "attempts": attempts,
    }


def generate_shot_image(project: dict, shot_id: str, opts: dict | None = None) -> dict:
    """生一張首幀並寫回 project（不存檔，存檔由呼叫端做一次）。

    engine：qwen（預設，i2i 編輯／t2i）｜moss（純文字 caption，不吃參考圖）
    回 dict：{ok, path, pid, elapsed, warnings, mode, model, seed, attempts, promptChars, loaders?, gpu}
    失敗一律拋 ImageGenError（中文）。

    GPU：預設自己切換（生圖前停閒置的本機 LLM、結束 finally 啟回）。批次／立繪批次的外層
    已切換時傳 opts["_gpuForce"]=True，避免每個鏡頭都停啟一次 LLM。
    """
    opts = opts or {}
    if opts.get("_gpuForce"):
        return _generate_shot_image_impl(project, shot_id, opts)
    slug = project.get("meta", {}).get("slug") or ""
    with gpu_for_comfy(12000, slug, "生圖（首幀）", project=project) as box:
        res = _generate_shot_image_impl(project, shot_id, opts)
    res["gpu"] = gpu_public(box["switch"], box["restore"])
    return res


def _generate_shot_image_impl(project: dict, shot_id: str, opts: dict) -> dict:
    shot = find_shot(project, shot_id)
    if shot is None:
        raise ImageGenError(f"找不到鏡頭 {shot_id}")

    engine = _s(opts.get("engine")) or _s((project.get("settings") or {}).get("imageEngine")) or "qwen"
    if engine not in ENGINES:
        raise ImageGenError(f"不支援的生圖引擎：{engine}（合法值：{'／'.join(ENGINES)}）")
    if engine == "moss":
        return generate_moss_image(project, shot, opts)

    mode = _s(opts.get("mode")) or "ref"
    if mode not in ("ref", "t2i"):
        raise ImageGenError(f"不支援的生成模式：{mode}（合法值：ref／t2i）")
    resolution = _clamp_int(opts.get("resolution"), 256, MAX_RESOLUTION, DEFAULT_RESOLUTION)
    steps = _clamp_int(opts.get("steps"), 1, 60, 25)
    cfg = _clamp_float(opts.get("cfg"), 0.0, 20.0, 1.0)
    warnings: list[str] = []

    # 前置檢查（M4b）：缺鎖臉圖／<Picture N> 對不上／沒有提示詞 → 直接擋，不碰 GPU
    pre_refs, pre_warn, blockers = preflight(project, shot, mode)
    if blockers:
        raise ImageGenError("生圖前置檢查沒過，已擋下（沒有送 GPU）：\n- " + "\n- ".join(blockers))
    warnings.extend(pre_warn)

    base_url = _comfy_base(project)
    ok, msg, info = comfy.comfy_available(base_url)
    if not ok:
        raise ImageGenError(msg)
    if info.get("vramFreeMB") is not None and info["vramFreeMB"] < 1500:
        warnings.append(f"ComfyUI 可用 VRAM 只剩 {info['vramFreeMB']} MB，可能很慢或失敗（可先關掉其他吃 GPU 的程式）")

    refs: list[dict] = pre_refs if mode == "ref" else []
    prompt, w = build_prompt(project, shot, refs)
    warnings.extend(w)

    try:
        seed = int(opts.get("seed")) if opts.get("seed") not in (None, "") else random.randint(0, 2 ** 31 - 1)
    except (TypeError, ValueError):
        seed = random.randint(0, 2 ** 31 - 1)

    slug = project.get("meta", {}).get("slug") or ""
    project_dir = storage.project_dir(slug)
    timeout = _clamp_int(opts.get("timeout"), 30, 900, DEFAULT_TIMEOUT)

    # 上傳參考圖 → 組圖 → 送件（每一步的例外都轉成中文帶上下文）
    ref_names: list[str] = []
    for r in refs:
        local = project_dir / r["path"]
        try:
            ref_names.append(comfy.upload_image(local, base_url))
        except comfy.ComfyError as e:
            raise ImageGenError(str(e)) from e

    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = f"svs_{shot_id}_{stamp}"
    aspect = _s(opts.get("aspect")) or _s((project.get("meta") or {}).get("aspectRatio")) or "16:9"
    if aspect not in _ASPECT:
        warnings.append(f"不認得比例 {aspect}，t2i 畫布改用 16:9")
        aspect = "16:9"
    graph = build_graph(mode, prompt, _NEGATIVE, resolution, steps, cfg, seed, ref_names, prefix, aspect)

    t0 = time.time()
    try:
        pid = comfy.submit(graph, base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(str(e)) from e
    try:
        record = comfy.wait(pid, timeout=timeout, base_url=base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(f"{e}｜prompt_id={pid}（seed={seed}）") from e

    # 落地：media/shots/<shotId>_<時間戳>.png（先寫暫存再改名，避免半張圖被抓走）
    dest_dir = project_dir / "media" / "shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{shot_id}_{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        saved = comfy.collect_images(record, dest_dir, filename=target_name, base_url=base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(str(e)) from e
    if not saved:
        raise ImageGenError("ComfyUI 完成了，但沒有任何圖檔落地（collect_images 回空）")
    rel_path = f"media/shots/{saved[0].name}"
    elapsed = round(time.time() - t0, 1)
    width, height = _png_size(saved[0])
    engine_used = "qwen"
    mode_label = "i2i 編輯" if mode == "ref" else "t2i"
    prev_first = _write_back(project, shot, shot_id, rel_path, pid, elapsed, seed,
                             resolution, steps, prompt, refs,
                             f"{mode_label}・{MODELS['i2i' if mode == 'ref' else 't2i']}")

    return {
        "ok": True, "path": rel_path, "pid": pid, "elapsed": elapsed, "warnings": warnings,
        "width": width, "height": height, "engine": engine_used,
        "mode": mode, "model": MODELS["i2i" if mode == "ref" else "t2i"],
        "seed": seed, "resolution": resolution, "steps": steps, "cfg": cfg,
        "refs": [{"path": r["path"], "label": r["label"]} for r in refs],
        "prompt": prompt, "promptChars": len(prompt), "prevFirst": prev_first,
    }


# ── 5. 角色立繪（M6）：為每個人物生「正面全身、中性淺灰背景、完整服裝、清楚臉部」─────
def find_character(project: dict, cid: str) -> dict | None:
    return next((c for c in (project.get("characters") or [])
                 if isinstance(c, dict) and _s(c.get("id")) == cid), None)


def build_character_sheet_caption(project: dict, ch: dict) -> tuple[str, list[str]]:
    """角色立繪用的 caption（純邏輯）。和鏡頭 caption 只差在：去掉景別/場景/動作，
    改成「全身站姿、正面、中性淺灰背景、完整服裝、臉部清楚可辨」。"""
    warnings: list[str] = []
    a = ch.get("identityAnchors") or {}
    age = _s((ch.get("appearance") or {}).get("age"))
    gender = _s((ch.get("appearance") or {}).get("gender"))
    who = " ".join(x for x in (f"{age}-year-old" if age.replace("-", "").isdigit() else age,
                               {"男": "man", "女": "woman"}.get(gender, "")) if x).strip()
    anchor = _anchors_verbatim(ch)
    if not anchor:
        warnings.append(f"{ch.get('id')} 沒有 identityAnchors：立繪只能靠服裝與外型欄位，長相不穩")
        app = ch.get("appearance") or {}
        anchor = "; ".join(_s(app.get(k)) for k in ("face", "hair", "body") if _s(app.get(k)))
    outfit = _outfit_en(ch, ch)
    if not outfit:
        warnings.append(f"{ch.get('id')} 沒有 outfit.promptEn：立繪的服裝會靠模型自行補")
    pose = ("full-body standing character reference sheet, facing the camera straight on, "
            "whole body from head to shoes in frame, both feet visible, relaxed neutral standing pose, "
            "arms slightly away from the body, centered")
    bg = ("plain light grey seamless studio background, soft even diffuse lighting from the front, "
          "no shadows on the background, no props, no text, no watermark")
    if _s(ch.get("id")) == "c03" or "hood" in anchor.lower() and "no visible eyes" in anchor.lower():
        # 這個角色的設定就是「臉不可辨」→ 立繪不能硬把臉畫出來
        face = ("entire face kept inside the hood shadow, features unreadable by design, "
                "no visible mouth or jaw, only a dark silhouette")
        warnings.append(f"{ch.get('id')} 的設定是「臉不可辨」，立繪刻意不畫出五官（保持剪影一致）")
    else:
        face = "face fully visible and sharply in focus, full facial features readable"
    # 中性表情：不加這句時 MOSS 會把「weary／dark circles」畫成瘀青或哭過的臉（c01 第一次試跑實測）
    calm = ("calm neutral expression, relaxed mouth, clean healthy skin without dark eye rings, "
            "no bruising, no black eye, no tears, no injury on the face")
    parts = [pose + ".", f"In frame: a {who}" if who else "In frame: a person",
             anchor + "." if anchor else "",
             ("wearing " + _trim_clauses(outfit, 150) + ".") if outfit else "",
             face + ".", calm + ".", bg + "."]
    cap = _soften_tired(_strip_quality(re.sub(r"\s{2,}", " ", " ".join(p for p in parts if p))))
    cap = cap.replace("。", ". ").replace("，", ", ")
    return cap, warnings


def generate_character_sheet(project: dict, cid: str, opts: dict | None = None) -> dict:
    """為一個人物生正面全身立繪，寫回 characters[].refImages（kind=full, lockedFace=true）。
    檔名：media/characters/<id>_sheet_<時間>.png。不存檔（由呼叫端做）。
    engine=moss 走 caption；engine=qwen 走 t2i（resolution 當短邊）。

    GPU：預設自己切換；立繪批次的外層已切換時傳 opts["_gpuForce"]=True（整批只切一次）。
    """
    opts = opts or {}
    if opts.get("_gpuForce"):
        return _generate_character_sheet_impl(project, cid, opts)
    slug = project.get("meta", {}).get("slug") or ""
    with gpu_for_comfy(12000, slug, "角色立繪", project=project) as box:
        res = _generate_character_sheet_impl(project, cid, opts)
    res["gpu"] = gpu_public(box["switch"], box["restore"])
    return res


def _generate_character_sheet_impl(project: dict, cid: str, opts: dict) -> dict:
    ch = find_character(project, cid)
    if ch is None:
        raise ImageGenError(f"找不到人物 {cid}")
    engine = _s(opts.get("engine")) or _s((project.get("settings") or {}).get("imageEngine")) or "qwen"
    if engine not in ENGINES:
        raise ImageGenError(f"不支援的生圖引擎：{engine}（合法值：{'／'.join(ENGINES)}）")
    base_url = _comfy_base(project)
    ok, msg, info = comfy.comfy_available(base_url)
    if not ok:
        raise ImageGenError(msg)

    warnings: list[str] = []
    caption, cw = build_character_sheet_caption(project, ch)
    warnings.extend(cw)
    try:
        seed = int(opts.get("seed")) if opts.get("seed") not in (None, "") else random.randint(0, 2 ** 31 - 1)
    except (TypeError, ValueError):
        seed = random.randint(0, 2 ** 31 - 1)
    aspect = _s(opts.get("aspect")) or _s((project.get("meta") or {}).get("aspectRatio")) or "9:16"
    slug = project.get("meta", {}).get("slug") or ""
    dest_dir = storage.project_dir(slug) / "media" / "characters"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_name = f"{cid}_sheet_{stamp}.png"
    loaders: dict = {}
    t0 = time.time()

    if engine == "moss":
        steps = _clamp_int(opts.get("steps"), 1, 60, MOSS_STEPS)
        cfg = _clamp_float(opts.get("cfg"), 0.0, 20.0, MOSS_CFG)
        timeout = _clamp_int(opts.get("timeout"), 30, 900, MOSS_TIMEOUT)
        tier = _clamp_int(opts.get("sizeTier"), 0, len(MOSS_SIZES_9X16) - 1, 0)
        record = None
        last_err: Exception | None = None
        final_w = final_h = 0
        for attempt in range(tier, len(MOSS_SIZES_9X16)):
            w, h = build_moss_size(aspect, opts.get("resolution"), attempt)
            graph, loaders = build_moss_graph(caption, w, h, steps, cfg, seed,
                                              f"svs_moss_sheet_{cid}_{stamp}")
            try:
                pid = comfy.submit(graph, base_url)
                record = comfy.wait(pid, timeout=timeout, base_url=base_url)
                final_w, final_h = w, h
                if attempt > tier:
                    warnings.append(f"尺寸降級到第 {attempt + 1} 階（實際 {w}×{h}）")
                break
            except comfy.ComfyError as e:
                last_err = e
                if _is_oom(e) and attempt + 1 < len(MOSS_SIZES_9X16):
                    nw, nh = MOSS_SIZES_9X16[attempt + 1]
                    warnings.append(f"{w}×{h} 爆 VRAM，降到 {nw}×{nh} 重試（{str(e)[:100]}）")
                    continue
                raise ImageGenError(f"{cid} 立繪失敗：{e}｜MOSS {w}×{h}") from e
        if record is None:
            raise ImageGenError(f"{cid} 立繪失敗（所有尺寸階梯）：{last_err}")
        resolution_used = final_w
        model = MODELS["moss"]
        size_note = f"｜{final_w}×{final_h}｜cfg={cfg}｜encoder={loaders.get('clip')}"
    else:
        steps = _clamp_int(opts.get("steps"), 1, 60, 25)
        cfg = _clamp_float(opts.get("cfg"), 0.0, 20.0, 1.0)
        timeout = _clamp_int(opts.get("timeout"), 30, 900, DEFAULT_TIMEOUT)
        resolution_used = _clamp_int(opts.get("resolution"), 256, MAX_RESOLUTION, DEFAULT_RESOLUTION)
        size_note = ""
        model = MODELS["t2i"]
        graph = build_graph("t2i", caption, _NEGATIVE, resolution_used, steps, cfg, seed, [],
                            f"svs_sheet_{cid}_{stamp}", aspect)
        try:
            pid = comfy.submit(graph, base_url)
            record = comfy.wait(pid, timeout=timeout, base_url=base_url)
        except comfy.ComfyError as e:
            raise ImageGenError(f"{cid} 立繪失敗：{e}") from e

    try:
        saved = comfy.collect_images(record, dest_dir, filename=target_name, base_url=base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(str(e)) from e
    if not saved:
        raise ImageGenError(f"{cid} 立繪：ComfyUI 完成了但沒有圖檔落地")
    rel_path = f"media/characters/{saved[0].name}"
    width, height = _png_size(saved[0])
    elapsed = round(time.time() - t0, 1)

    # 寫回：refImages 加一筆 kind=full / locked=true；lockedFace=true（front-of-card 鎖臉開關）
    refs = ch.get("refImages") if isinstance(ch.get("refImages"), list) else []
    ch["refImages"] = refs
    refs.append({"path": rel_path, "kind": "full", "locked": True,
                 "note": f"{engine} 生成立繪 {time.strftime('%Y-%m-%d %H:%M')}", "addedAt": storage.now_iso()})
    ch["lockedFace"] = True
    ch["updatedAt"] = storage.now_iso()

    logs = project.setdefault("pipeline", {}).setdefault("logs", [])
    logs.append({
        "at": storage.now_iso(), "step": "imagegen.character", "shotId": "", "characterId": cid,
        "msg": f"{ch.get('name') or cid} 立繪完成：{rel_path}｜{elapsed} 秒｜seed={seed}｜{engine}"
               + (f"｜{width}×{height}" if width else "") + size_note,
        "pid": pid, "model": model, "elapsed": elapsed, "path": rel_path, "seed": seed,
        "engine": engine, "promptLen": len(caption),
    })
    if len(logs) > 200:
        del logs[:-200]
    return {"ok": True, "characterId": cid, "name": ch.get("name"), "path": rel_path, "pid": pid,
            "elapsed": elapsed, "width": width, "height": height, "engine": engine, "model": model,
            "seed": seed, "prompt": caption, "promptChars": len(caption),
            "warnings": warnings, "loaders": loaders}


def generate_all_character_sheets(project: dict, ids: list[str] | None, opts: dict | None = None) -> dict:
    """count 不限：照 ids 或有的人物卡依序跑（一張 GPU，不能並行）。單一人物失敗不中斷整批。

    GPU：整個批次只在最外層切換一次（ensure 一次、finally 還原一次），不逐張停啟 LLM。
    本機 LLM 正在運算 → GpuBusyError（整批不送件）。
    """
    opts = opts or {}
    slug = project.get("meta", {}).get("slug") or ""
    with gpu_for_comfy(12000, slug, "角色立繪批次", project=project) as box:
        res = _generate_all_character_sheets_impl(project, ids, {**opts, "_gpuForce": True})
    # 一定要在 with 結束「之後」才組 gpu 欄位：box['restore'] 是 finally 才填的
    res["gpu"] = gpu_public(box["switch"], box["restore"])
    return res


def _generate_all_character_sheets_impl(project: dict, ids: list[str] | None, opts: dict) -> dict:
    try:
        count = int(opts.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    chars = [c for c in (project.get("characters") or []) if isinstance(c, dict) and _s(c.get("id"))]
    if ids:
        wanted = [_s(i) for i in ids if _s(i)]
        missing = [i for i in wanted if not any(c["id"] == i for c in chars)]
        if missing:
            raise ImageGenError("找不到這些人物：" + "、".join(missing))
        chars = [c for c in chars if c["id"] in wanted]
    if count > 0:
        chars = chars[:count]
    if not chars:
        raise ImageGenError("這個專案沒有任何人物卡可以生立繪")
    items: list[dict] = []
    for ch in chars:
        try:
            res = generate_character_sheet(project, ch["id"], opts)
            items.append({k: res[k] for k in ("characterId", "name", "path", "pid", "elapsed",
                                              "width", "height", "engine", "seed", "warnings")})
        except ImageGenError as e:
            items.append({"characterId": ch["id"], "name": ch.get("name"), "ok": False, "error": str(e)})
    ok_n = sum(1 for i in items if "error" not in i)
    return {"ok": ok_n == len(items), "count": len(items), "okCount": ok_n, "items": items}


# ── 6. 批次生圖（M4b）：依序送件、每鏡寫回、進度可 poll ───────────────
# 為什麼是「背景 thread + 前端每 2 秒 poll」：一次生圖 10–40 秒，多鏡同步請求會讓
# 瀏覽器／uvicorn 同時壓著好幾條連線；背景 task 只佔一條、狀態隨時可讀。
# 依序（不並行）是硬規則：ComfyUI 只有一張 GPU，並行只會互相排隊又吃掉 VRAM。
BATCH_JOBS: dict[str, dict] = {}
_BATCH_LOCK = threading.Lock()
_BATCH_MAX_KEEP = 20          # 記憶體只留最近 20 個任務狀態


class BatchError(Exception):
    """批次生圖的前置錯誤（找不到鏡頭／已在跑）→ API 回 400/200 + 中文。"""


# ── 7. 第二層：H3 Character Sheet Designer（engine=h3sheet，把立繪轉多角度設定表）──
# 使用者要的順序：第一層 MOSS t2i 生單張立繪 → 第二層（這裡）把立繪 i2i 成多角度設定表。
# 工作流：templates/h3_character_designer.ui.json（老臘肉 2026-11-16，前端 UI 格式，原檔不動）。
# 節點 68 H3CharacterSheetDesignerReferenceV13 會「自己組提示詞」並輸出 prompt/width/height，
# 所以我們只需要餵 character_image（＋ state_json 決定畫哪幾個視角），不要另外接文字提示詞。
SHEET_TEMPLATE = paths.TEMPLATES_DIR / "h3_character_designer.ui.json"
H3SHEET_FILES = {
    # 本機只有 w4a8（12.5 GB，實測在 /object_info 清單裡）；工作流原本寫的 int8_convrot 版本沒下載，
    # 照原檔名送會被 ComfyUI 判「Value not in list」（2026-11-19 實測）。有 int8 版時可在這裡換檔名。
    "unet": "minimax_h3_image_pruned_w4a8.safetensors",
    # 官方 int8_convrot 的 qwen3vl_32b 也不在本機 → 先照檔名試清單裡的 32B（NVFP4／AWQ／int8_uncensored），
    # 每個候選逐次嘗試、第一次送件成功就記住（見 _h3sheet_failed_clips）；全部不行時回中文原因＋手動指定。
    "clip": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "clipCandidates": (
        "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "qwen3vl_32b_minimax_h3_ultra_uncensored_heretic_int8_convrot.safetensors",
    ),
    "clipType": "minimax",
    "vae": "minimax_h3_image_vae_fp16.safetensors",
}
# 這一輪程序內「送件階段就被擋」的 encoder 檔名（例如這個人形權重去量化報錯）→ 換下一個候選。
# 只在 Pod 內記憶，重啟就重試；不放進專案檔（那是使用者資料）。
_h3sheet_failed_clips: set[str] = set()
# 節點 63 SamplerCustomAdvanced 需要的五個輸入，全部由節點表推導（不寫死連線清單）
H3SHEET_REQUIRED_CHAIN = {
    68: ("state_json", "character_image"),
    32: ("clip", "vae"),
    29: ("unet_name",),
    30: ("clip_name", "type"),
    31: ("vae_name",),
    63: ("noise", "guider", "sampler", "sigmas", "latent_image"),
    67: ("model", "conditioning"),
    42: ("samples",),
    9: ("filename_prefix",),
}
H3SHEET_VIEWS = ("face_front", "face_left", "face_right", "face_3q_left", "body_front", "body_back")
H3SHEET_VIEW_LABELS = {
    "face_front": "正面頭像", "face_left": "左側臉", "face_right": "右側臉",
    "face_3q_left": "左四分之三臉", "body_front": "正面全身", "body_back": "背面全身",
}
H3SHEET_DEFAULT_VIEWS = ("face_front", "face_left", "face_right", "body_front", "body_back")
H3SHEET_STYLES = ("none", "anime", "photo", "realistic_painting", "semi_realistic_anime",
                  "oil_painting", "watercolor", "gouache", "colored_pencil", "3d")
H3SHEET_TIMEOUT = 2400            # 21GB UNET + 25GB text encoder 在 16GB VRAM 會大量 RAM offload，慢是預期
H3SHEET_MP_OPTIONS = (4, 8, 16)   # 目標總像素（百萬）：預設 4MP（先求跑得完），最佳 16MP
DEFAULT_FULL_KIND = "full"


def build_h3sheet_state(view_list: list[str], body_height: int = 1120,
                        megapixels: int = 0) -> str:
    """組 state_json（節點 68 的 widgets 字串）。size.mode=auto 時節點自己算總寬，
    body_height 是單一視角的角色高度；megapixels>0 可改成手動畫布（長邊推算）。"""
    mp = _clamp_int(megapixels, 0, 32, 0)
    if mp:
        w, h = _sheet_manual_size(mp, len(view_list))
        size = {"mode": "manual", "body_height": int(body_height),
                "manual_width": w, "manual_height": h}
    else:
        size = {"mode": "auto", "body_height": int(body_height),
                "manual_width": 3168, "manual_height": 1280}
    state = {
        "hand_state": "bare",
        "clothing_mode": "garment",
        "clothing_type": "reference",
        "footwear_state": "shod",
        "schema_version": 3,
        "views": list(view_list),
        "size": size,
        "part_prompts": {},
    }
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _sheet_manual_size(megapixels: int, n_views: int) -> tuple[int, int]:
    """手動畫布：總像素 ≈ megapixels，每格寬 ≈ body_height*0.45，取 16 倍數。"""
    px = int(megapixels * 1_000_000)
    cell_w = int(1280 * 0.40)                     # 單格約 512 寬（1120 高的角色）
    width = max(1024, cell_w * max(1, n_views))
    height = max(512, px // width)
    if height > 4096:                              # 太窄長：改由高度反推寬
        height = 2048
        width = max(1024, px // height)
    return max(512, width // 16 * 16), max(512, height // 16 * 16)


def normalize_h3sheet_views(views: Any) -> tuple[list[str], list[str]]:
    """請求 → (合法 views, warnings)。不認識的一律丟掉；全空就用預設五視角。"""
    warnings: list[str] = []
    raw = [(_s(v)).lower() for v in (views or []) if _s(v)]
    bad = [v for v in raw if v not in H3SHEET_VIEWS]
    if bad:
        warnings.append("不認得的視角已略過：" + "、".join(bad)
                        + f"（合法值：{'、'.join(H3SHEET_VIEWS)}）")
    picked: list[str] = []
    for v in raw:
        if v in H3SHEET_VIEWS and v not in picked:
            picked.append(v)
    if not picked:
        picked = list(H3SHEET_DEFAULT_VIEWS)
        if raw:
            warnings.append(f"沒有可用的視角，改用預設：{'、'.join(picked)}")
    # 依固定順序排（避免使用者勾選順序影響拼版）（拼版順序影響裁切對位）
    picked.sort(key=lambda v: H3SHEET_VIEWS.index(v))
    return picked, warnings


def pick_character_full_ref(project: dict, ch: dict) -> dict | None:
    """挑「要轉多角度的那張立繪」：refImages 裡 kind=full 的最新一張（檔案存在者優先）。

    先挑檔案存在的（最新的優先）；全都找不到檔案就回 None，由呼叫端拒絕並講清楚原因。
    """
    slug = project.get("meta", {}).get("slug") or ""
    base = storage.project_dir(slug)
    cands = [r for r in (ch.get("refImages") or [])
             if isinstance(r, dict) and _s(r.get("path")) and _s(r.get("kind") or DEFAULT_FULL_KIND) == DEFAULT_FULL_KIND]
    def _stamp(r: dict) -> str:
        return _s(r.get("addedAt")) or _s(r.get("path"))
    cands.sort(key=_stamp, reverse=True)
    for r in cands:
        if (base / _s(r.get("path"))).is_file():
            return r
    return None


def build_h3sheet_graph(ref_name: str, state_json: str, style: str, prefix: str,
                        seed: int | None = None, clip_name: str | None = None) -> tuple[dict, dict]:
    """在 UI 副本上寫參數 → 轉 API 節點圖（原檔不動），回 (graph, report)。

    只留節點 9/27-33/42/48/56/63-69/75/76/78-79（MarkdownNote、Reroute 不轉）；
    連線照工作流原本的 links，但目的地不在保留清單的就丟掉——所以換掉來源節點（69→上傳檔）
    不會讓下游斷線。
    """
    ui = _load_h3sheet_ui()
    nodes = {n["id"]: n for n in ui.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    keep = {27, 28, 29, 30, 31, 32, 33, 42, 48, 56, 63, 64, 65, 66, 67, 68, 69, 75, 76, 78, 79, 9}
    missing = [i for i in keep if i not in nodes]
    if missing:
        raise ImageGenError(f"H3 設定表工作流缺少節點 {missing}（{SHEET_TEMPLATE.name} 被改過？）")

    # 1) loader 三件套寫死成本機檔名（不讓前端 combo 回退）
    nodes[29]["widgets_values"][0] = H3SHEET_FILES["unet"]
    nodes[30]["widgets_values"][0] = clip_name or H3SHEET_FILES["clip"]
    nodes[30]["widgets_values"][1] = H3SHEET_FILES["clipType"]
    nodes[31]["widgets_values"][0] = H3SHEET_FILES["vae"]
    # 2) 角色參考圖（LoadImage 69）＋ 設定表參數（節點 68）
    nodes[69]["widgets_values"][0] = ref_name
    wv = nodes[68]["widgets_values"]
    wv[0] = state_json
    wv[1] = True                                   # use_layout_image：輸出拼版圖（要存檔裁切）
    wv[2] = style if style in H3SHEET_STYLES else "none"
    # 3) 種子（節點 66）＋ 存檔前綴（節點 9）
    nodes[66]["widgets_values"][0] = int(seed if seed is not None else random.randint(0, 2 ** 63 - 1))
    nodes[9]["widgets_values"][0] = prefix

    graph = _h3sheet_ui_to_api(ui, keep)
    report = {
        "unet": graph["29"]["inputs"].get("unet_name"),
        "clip": graph["30"]["inputs"].get("clip_name"),
        "clipType": graph["30"]["inputs"].get("type"),
        "vae": graph["31"]["inputs"].get("vae_name"),
        "style": graph["68"]["inputs"].get("style"),
        "seed": graph["66"]["inputs"].get("noise_seed"),
        "nodeCount": len(graph),
    }
    for nid, fields in H3SHEET_REQUIRED_CHAIN.items():
        node = graph.get(str(nid))
        if not node:
            raise ImageGenError(f"H3 設定表工作流缺少節點 {nid}（class={nodes[nid].get('type')}）")
        lack = [f for f in fields if f not in node["inputs"]]
        if lack:
            raise ImageGenError(f"H3 設定表節點 {nid}（{node.get('class_type')}）缺輸入 {lack}"
                                f"：工作流的連線被改動過，請重新複製原檔到 templates/{SHEET_TEMPLATE.name}")
    return graph, report


def _load_h3sheet_ui() -> dict:
    if not SHEET_TEMPLATE.is_file():
        raise ImageGenError(f"缺少 H3 設定表工作流：templates/{SHEET_TEMPLATE.name}")
    try:
        raw = json.loads(SHEET_TEMPLATE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ImageGenError(f"H3 設定表工作流格式錯誤（{SHEET_TEMPLATE.name}）：{type(e).__name__}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        raise ImageGenError(f"H3 設定表工作流不是前端格式（沒有 nodes 陣列）：{SHEET_TEMPLATE.name}")
    return copy.deepcopy(raw)


def _h3sheet_ui_to_api(ui: dict, keep: set[int]) -> dict:
    """UI → API，但只看「工作流原本宣告的輸入插槽名」（節點 32 的 images 是動態展開的
    COMFY_AUTOGROW_V3，插槽名是 images.image_N，不能靠節點類型推）。

    Reroute（79）在 API 格式不存在 → 先把「來源節點輸出 → Reroute → 目的地」串接成直接連線：
    67 BasicGuider 的 model 在 UI 是從 79 接過來的，照原樣送會被 ComfyUI 判「Node 'Reroute' not found」。
    """
    nodes = {n["id"]: n for n in ui.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    links = [l for l in (ui.get("links") or []) if isinstance(l, (list, tuple)) and len(l) >= 5]
    reroutes = {nid for nid, n in nodes.items() if _s(n.get("type")) == "Reroute"}

    def resolve(src: int, slot: int, depth: int = 0) -> list | None:
        """把來源往上追到非 Reroute 的節點；回 [node_id_str, slot]。"""
        if src in reroutes:
            for l in links:                        # Reroute 只有一個輸入：找 to == src 的那條
                if int(l[3]) == int(src) and depth < 10:
                    return resolve(int(l[1]), int(l[2]), depth + 1)
            return None
        return [str(src), int(slot)]

    incoming: dict[int, dict[int, list]] = {}
    for l in links:
        _lid, src, src_slot, dst, dst_slot = l[0], l[1], l[2], l[3], l[4]
        if dst not in keep:
            continue
        resolved = resolve(int(src), int(src_slot))
        if resolved:
            incoming.setdefault(int(dst), {})[int(dst_slot)] = resolved
    graph: dict[str, dict] = {}
    for nid in sorted(keep):
        node = nodes[nid]
        ctype = _s(node.get("type"))
        if ctype in ("MarkdownNote", "Reroute", "Note", "PrimitiveNode"):
            continue            # 前端專用節點（Reroute 只是接線，API 版不存在）
        vals = node.get("widgets_values")
        inputs: dict[str, Any] = {}
        if isinstance(vals, list):
            # 只認「有名字的 widget」：H3 設定表節點的 widgets 是 state_json / use_layout_image / style
            names = {"H3CharacterSheetDesignerReferenceV13": ("state_json", "use_layout_image", "style"),
                     "UNETLoader": ("unet_name", "weight_dtype"),
                     "CLIPLoader": ("clip_name", "type", "device"),
                     "VAELoader": ("vae_name",),
                     "MiniMaxH3ImageConditioning": ("prompt", "width", "height"),
                     "ManualSigmas": ("sigmas",),
                     "KSamplerSelect": ("sampler_name",),
                     "RandomNoise": ("noise_seed", "control_after_generate"),
                     "SaveImage": ("filename_prefix",),
                     "LoadImage": ("image", "upload"),
                     "Reroute": None,
                     "ResizeImageMaskNode": None}.get(ctype)
            if names is not None:
                for i, nm in enumerate(names):
                    if i < len(vals):
                        inputs[nm] = vals[i]
            elif ctype == "ResizeImageMaskNode":
                # widgets_values 依 resize_type 而異（scale to multiple: [type, multiple, method]）
                rt = vals[0] if vals else ""
                inputs["resize_type"] = rt
                if rt == "scale to multiple" and len(vals) > 1:
                    inputs["resize_type.multiple"] = vals[1]
                elif rt == "scale total pixels" and len(vals) > 1:
                    inputs["resize_type.megapixels"] = vals[1]
                if len(vals) > 2:
                    inputs["scale_method"] = vals[2]
        wired = incoming.get(nid, {})
        for slot_idx, inp in enumerate(node.get("inputs") or []):
            nm = _s(inp.get("name"))
            if slot_idx in wired and nm:
                inputs[nm] = wired[slot_idx]
        graph[str(nid)] = {"class_type": ctype, "inputs": inputs}
    return graph


def _sheet_layout_guess(path: Path, n_views: int) -> tuple[str, int]:
    """猜拼版方向與欄數（只看尺寸，不靠模型）：回 (方向 'h'|'v', 每列格數)。"""
    w, h = _png_size(path)
    if not w or not h:
        return "h", n_views
    # 目標：每格寬高比 ≈ 0.4–0.6（全身）或 0.6–0.9（只有頭像）
    best = None
    for cols in range(1, n_views + 1):
        if n_views % cols:
            continue
        rows = n_views // cols
        cw, chh = w / cols, h / rows
        ratio = cw / chh
        score = abs(ratio - 0.48)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    _score, cols, rows = best
    return ("h" if cols >= rows else "v"), cols


def crop_sheet(path: Path, n_views: int, out_dir: Path, stem: str,
               direction: str | None = None, cols: int | None = None) -> list[Path]:
    """把拼版大圖平均切成 n_views 張（依方向／每列格數）。任何失敗都回 []（不影響交付）。"""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 — 沒裝 PIL 就只交付整張
        return []
    try:
        img = Image.open(path)
        img.load()
        w, h = img.size
        if direction is None or cols is None:
            direction, cols = _sheet_layout_guess(path, n_views)
        rows = max(1, n_views // cols)
        if cols * rows != n_views:
            return []
        cw, chh = w // cols, h // rows
        if cw < 64 or chh < 64:
            return []
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for idx in range(n_views):
            r, c = divmod(idx, cols)
            box = (c * cw, r * chh, (c + 1) * cw, (r + 1) * chh)
            target = out_dir / f"{stem}_{idx + 1:02d}.png"
            img.crop(box).save(target)
            saved.append(target)
        return saved
    except Exception:  # noqa: BLE001 — 裁切是加分項，失敗不要擋交付
        return []


def _h3sheet_missing_files(base_url: str | None = None) -> list[str]:
    """向 /object_info 核對三件套是否存在（回缺的檔名清單，空＝都在）。讀不到清單就回 [] 交給送件去判。"""
    try:
        import httpx
        with httpx.Client(timeout=30.0) as c:
            info = c.get(f"{comfy._base(base_url)}/object_info").json()
    except Exception:  # noqa: BLE001
        return []
    specs = (("UNETLoader", "unet_name", H3SHEET_FILES["unet"]),
             ("CLIPLoader", "clip_name", H3SHEET_FILES["clip"]),
             ("VAELoader", "vae_name", H3SHEET_FILES["vae"]))
    missing: list[str] = []
    for cls, field, want in specs:
        opts = (((info.get(cls) or {}).get("input") or {}).get("required") or {}).get(field) or []
        choices = opts[0] if opts and isinstance(opts[0], list) else []
        if want not in choices:
            missing.append(f"{field}={want}")
    return missing


def generate_character_sheet_angles(project: dict, cid: str, opts: dict | None = None) -> dict:
    """第二層：把角色卡上「最新的 kind=full 立繪」送進 H3 Character Sheet Designer，
    生一張多角度設定表（拼版大圖），可選裁成多張。寫回 refImages（原圖保留）。

    opts：views[]、style、crop（預設 True）、bodyHeight、megapixels、seed、timeout、_gpuForce
    GPU：預設自己切換（停本機 LLM）；背景 job 外層已切換時傳 _gpuForce=True。
    """
    opts = opts or {}
    if opts.get("_gpuForce"):
        return _generate_character_sheet_angles_impl(project, cid, opts)
    slug = project.get("meta", {}).get("slug") or ""
    with gpu_for_comfy(20000, slug, "H3 多角度設定表", project=project) as box:
        res = _generate_character_sheet_angles_impl(project, cid, opts)
    res["gpu"] = gpu_public(box["switch"], box["restore"])
    return res


def _generate_character_sheet_angles_impl(project: dict, cid: str, opts: dict) -> dict:
    ch = find_character(project, cid)
    if ch is None:
        raise ImageGenError(f"找不到人物 {cid}")
    ref = pick_character_full_ref(project, ch)
    if ref is None:
        raise ImageGenError(
            f"{cid}（{ch.get('name') or cid}）沒有可用的全身立繪（refImages 裡找不到 kind=full 且檔案存在的圖）："
            "請先在角色卡按「生成立繪」（MOSS t2i）產生第一層立繪，再用多角度設定表")
    slug = project.get("meta", {}).get("slug") or ""
    base = storage.project_dir(slug)
    src_path = base / _s(ref["path"])
    warnings: list[str] = []
    views, vw = normalize_h3sheet_views(opts.get("views"))
    warnings.extend(vw)
    style = _s(opts.get("style")) or "none"
    if style not in H3SHEET_STYLES:
        warnings.append(f"不認得的風格 {style}，改用 none")
        style = "none"
    body_height = _clamp_int(opts.get("bodyHeight"), 480, 2048, 1120)
    megapixels = _clamp_int(opts.get("megapixels"), 0, 32, 0)
    seed = opts.get("seed")
    try:
        seed = int(seed) if seed not in (None, "") else random.randint(0, 2 ** 63 - 1)
    except (TypeError, ValueError):
        seed = random.randint(0, 2 ** 63 - 1)
    crop = bool(opts.get("crop", True))
    timeout = _clamp_int(opts.get("timeout"), 120, 7200, H3SHEET_TIMEOUT)

    base_url = _comfy_base(project)
    ok, msg, info = comfy.comfy_available(base_url)
    if not ok:
        raise ImageGenError(msg)
    if info.get("vramTotalMB") and info["vramTotalMB"] < 30000:
        warnings.append(f"顯示卡 VRAM {info['vramTotalMB']} MB 小於模型總量（46 GB）：會大量 RAM offload，"
                        "這一張可能要 10–20 分鐘以上；想快一點可只勾 1–2 個視角")

    try:
        ref_name = comfy.upload_image(src_path, base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(str(e)) from e
    # 模型檔先驗（送件被 combo 擋下來時 ComfyUI 只會回「Value not in list」，看不出是哪個檔）
    missing = _h3sheet_missing_files(base_url)
    if missing:
        raise ImageGenError("H3 設定表需要的模型檔不在 ComfyUI 清單裡：" + "、".join(missing)
                            + "（請確認 models/diffusion_models、text_encoders、vae 三個資料夾，"
                            "或改 app/imagegen.py 的 H3SHEET_FILES）")
    state_json = build_h3sheet_state(views, body_height, megapixels)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = f"svs_h3sheet_{cid}_{stamp}"

    def _available(names: list[str]) -> list[str]:
        """只留 ComfyUI 清單裡真的有的檔名（順序照 candidates）。"""
        try:
            import httpx
            with httpx.Client(timeout=30.0) as c:
                info = c.get(f"{comfy._base(base_url)}/object_info/CLIPLoader").json()
            choices = ((((info.get("CLIPLoader") or {}).get("input") or {}).get("required") or {})
                       .get("clip_name") or [[]])[0]
        except Exception:  # noqa: BLE001
            return names[:1]
        return [n for n in names if n in choices]

    clip_order = _available([H3SHEET_FILES["clip"], *H3SHEET_FILES["clipCandidates"]])
    clip_order += [n for n in H3SHEET_FILES["clipCandidates"] if n not in clip_order and n not in _h3sheet_failed_clips]
    clip_order = [n for n in clip_order if n not in _h3sheet_failed_clips]
    if not clip_order:
        raise ImageGenError("H3 設定表的 text encoder 全部試過都失敗："
                            + "、".join(sorted(_h3sheet_failed_clips))
                            + "（多半是這顆權重與 w4a8 UNET 不相容）。請換一顆 qwen3vl_32b 再試，"
                            "或把官方 qwen3vl_32b_minimax_h3_int8_convrot 下載到 models/text_encoders")

    t0 = time.time()
    attempts: list[dict] = []
    pid = ""
    record = None
    report: dict = {}
    last_err = ""
    for clip_name in clip_order:
        graph, report = build_h3sheet_graph(ref_name, state_json, style, prefix, seed, clip_name)
        try:
            pid = comfy.submit(graph, base_url)
        except comfy.ComfyError as e:
            # 送件就被拒：可能是這個 encoder 不在清單（換下一個），也可能是圖／節點問題（直接報）
            last_err = str(e)
            if "clip_name" in last_err or "Value not in list" in last_err:
                _h3sheet_failed_clips.add(clip_name)
                attempts.append({"clip": clip_name, "error": last_err[:200], "stage": "submit"})
                warnings.append(f"encoder {clip_name} 送件被拒，換下一顆")
                continue
            raise ImageGenError(f"H3 設定表送件失敗：{e}｜loader={report.get('unet')}／{report.get('clip')}") from e
        try:
            record = comfy.wait(pid, timeout=timeout, base_url=base_url)
            break
        except comfy.ComfyError as e:
            last_err = str(e)
            # 「dequantize / dtype / backend can handle」＝這顆 encoder 跟 UNET 對不上 → 換下一個候選
            txt = last_err.lower()
            if any(k in txt for k in ("dequantize", "not in {", "no backend can handle", "cannot handle")):
                _h3sheet_failed_clips.add(clip_name)
                attempts.append({"clip": clip_name, "error": last_err[:300], "stage": "run"})
                warnings.append(f"encoder {clip_name} 載入後不相容（{last_err[:90]}…），換下一顆重試")
                continue
            if _is_oom(e):
                raise ImageGenError(
                    f"H3 設定表爆記憶體（prompt_id={pid}）：{e}\n"
                    "建議降級：①只勾 1–2 個視角 ②bodyHeight 調到 832 或 768 ③megapixels=4"
                    "（畫布越小越省）後重試") from e
            raise ImageGenError(f"H3 設定表失敗：{e}｜prompt_id={pid}｜views={','.join(views)}"
                                f"｜encoder={clip_name}") from e
    if record is None:
        raise ImageGenError("H3 設定表的 text encoder 全部不相容（都試過）："
                            + "；".join(f"{a['clip']}：{a['error'][:160]}" for a in attempts)
                            + "。最穩的解法：把官方 qwen3vl_32b_minimax_h3_int8_convrot.safetensors "
                            "下載到 models/text_encoders 後重試")

    dest_dir = base / "media" / "characters"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{cid}_sheetangles_{stamp}.png"
    try:
        saved = comfy.collect_images(record, dest_dir, filename=target_name, base_url=base_url)
    except comfy.ComfyError as e:
        raise ImageGenError(str(e)) from e
    if not saved:
        raise ImageGenError("H3 設定表完成了，但沒有任何圖檔落地")
    elapsed = round(time.time() - t0, 1)
    main_path = saved[0]
    width, height = _png_size(main_path)
    rel_path = f"media/characters/{main_path.name}"

    # 裁切（加分項）：判斷是不是「一張拼版」→ 依尺寸猜方向與欄數再平均切
    crops: list[dict] = []
    if crop and len(views) > 1:
        if width and height and max(width, height) / max(1, min(width, height)) < 1.45:
            direction, cols = "v", 1
        else:
            direction, cols = _sheet_layout_guess(main_path, len(views))
        outs = crop_sheet(main_path, len(views), dest_dir, main_path.stem, direction, cols)
        if len(outs) == len(views):
            for i, p in enumerate(outs):
                crops.append({"path": f"media/characters/{p.name}", "view": views[i],
                              "label": H3SHEET_VIEW_LABELS.get(views[i], views[i])})
        else:
            warnings.append("拼版裁切沒成功（版面可能是 2 欄或不平均）：只交付整張設定表，"
                            "要單張請自行在圖片檢視器裁切")

    refs = ch.get("refImages") if isinstance(ch.get("refImages"), list) else []
    ch["refImages"] = refs
    now = storage.now_iso()
    views_note = ",".join(views)
    detail = ("｜" + "、".join(c["label"] for c in crops)) if crops else ""
    refs.append({"path": rel_path, "kind": "full", "locked": False, "source": "h3sheet",
                 "note": f"h3sheet:{views_note}{detail}", "addedAt": now})
    for c in crops:
        refs.append({"path": c["path"], "kind": "full", "locked": False, "source": "h3sheet",
                     "note": f"h3sheet:{views_note}｜{c['label']}（由整張設定表裁切）", "addedAt": now})
    ch["updatedAt"] = now

    logs = project.setdefault("pipeline", {}).setdefault("logs", [])
    logs.append({
        "at": now, "step": "imagegen.sheet", "shotId": "", "characterId": cid,
        "msg": f"{ch.get('name') or cid} 多角度設定表完成：{rel_path}｜{elapsed} 秒｜views={views_note}"
               f"｜{width}×{height}｜crop={len(crops)} 張｜style={style}｜bodyHeight={body_height}",
        "pid": pid, "model": "H3 Character Sheet Designer V13", "elapsed": elapsed, "path": rel_path,
        "seed": seed, "engine": "h3sheet", "views": views, "crops": [c["path"] for c in crops],
    })
    if len(logs) > 200:
        del logs[:-200]
    return {"ok": True, "characterId": cid, "name": ch.get("name"), "path": rel_path,
            "url": f"/files/{slug}/{rel_path}", "pid": pid, "elapsed": elapsed,
            "width": width, "height": height, "views": views, "style": style,
            "bodyHeight": body_height, "megapixels": megapixels, "seed": seed,
            "crops": crops, "sourceRef": _s(ref["path"]), "warnings": warnings,
            "loaders": report}


# ── 7b. 多角度設定表的背景 job（H3 image 很慢；API 先回 jobId 讓前端 poll）──
SHEET_JOBS: dict[str, dict] = {}
_SHEET_LOCK = threading.Lock()
_SHEET_MAX_KEEP = 20


def _sheet_public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("_thread",)}


def start_sheet_job(slug: str, cid: str, opts: dict | None = None) -> dict:
    """建立背景 job 並回狀態（jobId 給前端 poll）。同專案同時只允許一個在跑。"""
    opts = opts or {}
    with _SHEET_LOCK:
        for j in SHEET_JOBS.values():
            if j.get("slug") == slug and j.get("status") in ("queued", "running"):
                raise BatchError(f"這個專案已經有一張多角度設定表在跑（{j.get('jobId')}，"
                                 f"狀態 {j.get('status')}），等它跑完再送（H3 image 一次只能跑一張）")
        job_id = f"h3s-{uuid.uuid4().hex[:10]}"
        views, _w = normalize_h3sheet_views(opts.get("views"))
        job = {
            "jobId": job_id, "slug": slug, "characterId": cid, "status": "queued",
            "phase": "排隊中…", "createdAt": storage.now_iso(), "startedAt": "", "finishedAt": "",
            "views": views, "style": _s(opts.get("style")) or "none",
            "crop": bool(opts.get("crop", True)),
            "elapsed": 0.0, "path": "", "url": "", "pid": "", "width": None, "height": None,
            "crops": [], "warnings": [], "error": "", "gpu": {},
        }
        SHEET_JOBS[job_id] = job
        if len(SHEET_JOBS) > _SHEET_MAX_KEEP:
            for k in sorted(SHEET_JOBS, key=lambda x: SHEET_JOBS[x]["createdAt"]):
                if k != job_id and SHEET_JOBS[k].get("status") in ("done", "failed"):
                    SHEET_JOBS.pop(k, None)
                    break
    th = threading.Thread(target=_run_sheet_job, args=(job_id, dict(opts)), name=f"h3sheet-{job_id}", daemon=True)
    job["_thread"] = th
    th.start()
    return _sheet_public(job)


def get_sheet_job(job_id: str) -> dict | None:
    with _SHEET_LOCK:
        job = SHEET_JOBS.get(job_id)
        return copy.deepcopy(_sheet_public(job)) if job else None


def _run_sheet_job(job_id: str, opts: dict) -> None:
    t_all = time.time()
    with _SHEET_LOCK:
        job = SHEET_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["startedAt"] = storage.now_iso()
        job["phase"] = "檢查 GPU（必要時暫停本機 LLM）…"
        slug, cid = job["slug"], job["characterId"]
    try:
        with gpu_for_comfy(20000, slug, "H3 多角度設定表") as box:
            with _SHEET_LOCK:
                job["phase"] = "H3 image 生成中（很慢：16GB VRAM 會大量 RAM offload，可能 10–25 分鐘）…"
            data = storage.load_project(slug)
            res = _generate_character_sheet_angles_impl(data, cid, {**opts, "_gpuForce": True})
            try:
                storage.save_project(slug, data)
            except Exception as e:  # StorageError 在 imagegen 沒 import；一律當寫回失敗處理
                with _SHEET_LOCK:
                    job.update({"status": "failed", "finishedAt": storage.now_iso(),
                                "error": f"圖已生成（{res['path']}）但寫回 project.json 失敗：{e}"})
                return
            # 收尾排在「LLM 已啟回」之後，前端才不會看到完成但還原狀態還沒填
            g = gpu_public(box["switch"], box["restore"])
        with _SHEET_LOCK:
            job.update({
                "status": "done", "finishedAt": storage.now_iso(), "phase": "已結束",
                "path": res["path"], "url": res["url"], "pid": res["pid"],
                "width": res.get("width"), "height": res.get("height"),
                "crops": res.get("crops") or [], "warnings": res.get("warnings") or [],
                "elapsed": res.get("elapsed"), "gpu": g,
            })
    except GpuBusyError as e:
        with _SHEET_LOCK:
            job.update({"status": "failed", "finishedAt": storage.now_iso(),
                        "phase": "沒有送件（本機 LLM 正在運算）", "error": str(e),
                        "gpu": {"busy": True, "error": str(e)},
                        "elapsed": round(time.time() - t_all, 1)})
    except ImageGenError as e:
        with _SHEET_LOCK:
            job.update({"status": "failed", "finishedAt": storage.now_iso(), "phase": "失敗",
                        "error": str(e), "elapsed": round(time.time() - t_all, 1)})
    except Exception as e:  # noqa: BLE001 — 背景 thread 的例外一定要收成 job 狀態
        detail = f"{type(e).__name__}: {e}"
        if isinstance(e, NameError):
            detail += "（這是程式內部錯誤：背景 job 用到未定義的名稱，請回報）"
        with _SHEET_LOCK:
            job.update({"status": "failed", "finishedAt": storage.now_iso(), "phase": "失敗",
                        "error": f"未預期錯誤：{detail}",
                        "elapsed": round(time.time() - t_all, 1)})


def _batch_public(job: dict) -> dict:
    """給前端看的狀態（不含內部 thread 指標）。"""
    return {k: v for k, v in job.items() if k not in ("_thread",)}


def start_batch(project: dict, slug: str, shot_ids: list[str], opts: dict | None = None) -> dict:
    """建立批次任務並在背景 thread 依序跑；回任務狀態（jobId 給前端 poll）。

    opts: mode / resolution / trialFirst / timeout / engine（qwen|moss）
    """
    opts = opts or {}
    ids = [(_s(x)) for x in (shot_ids or []) if _s(x)]
    if not ids:
        raise BatchError("沒有選任何鏡頭：請先勾選鏡頭，或用「本場次」範圍")
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:  # 去重但保序
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    all_shots = [x for x in (project.get("shots") or []) if isinstance(x, dict)]
    missing = [i for i in ordered if not any(s.get("id") == i for s in all_shots)]
    if missing:
        raise BatchError("找不到這些鏡頭：" + "、".join(missing))
    # 一律照分鏡表順序跑（no 由呼叫端 renumber 過，陣列順序就是鏡號順序）
    ordered.sort(key=lambda i: next(k for k, s in enumerate(all_shots) if s.get("id") == i))

    trial_first = bool(opts.get("trialFirst", True))
    if trial_first and len(ordered) > 1:
        ordered = ordered[:1]     # 「先試 1 鏡」：只跑第一鏡，之後由使用者確認再放量

    with _BATCH_LOCK:
        for j in BATCH_JOBS.values():
            if j.get("slug") == slug and j.get("status") in ("queued", "running"):
                raise BatchError(f"這個專案已經有一個批次生圖在跑（{j.get('jobId')}，"
                                 f"進度 {j.get('done')}/{j.get('total')}），等它跑完再送")
        job_id = f"m4b-{uuid.uuid4().hex[:10]}"
        job = {
            "jobId": job_id, "slug": slug, "status": "queued",
            "createdAt": storage.now_iso(), "startedAt": "", "finishedAt": "",
            "mode": _s(opts.get("mode")) or "ref",
            "engine": _s(opts.get("engine")) or "qwen",
            "resolution": _clamp_int(opts.get("resolution"), 256, MAX_RESOLUTION, DEFAULT_RESOLUTION),
            "trialFirst": trial_first,
            "requested": len(ids), "total": len(ordered),
            "skippedByTrial": len(ids) - len(ordered) if trial_first else 0,
            "skipped": [],
            "done": 0, "okCount": 0, "failCount": 0, "elapsed": 0.0,
            "current": "", "items": [
                {"shotId": i, "no": next((s.get("no") for s in all_shots if s.get("id") == i), None),
                 "status": "pending", "path": "", "pid": "", "elapsed": 0.0,
                 "error": "", "warnings": [], "width": None, "height": None}
                for i in ordered
            ],
        }
        BATCH_JOBS[job_id] = job
        if len(BATCH_JOBS) > _BATCH_MAX_KEEP:  # 砍最舊的（跑完的優先）
            for k in sorted(BATCH_JOBS, key=lambda x: BATCH_JOBS[x]["createdAt"]):
                if k != job_id and BATCH_JOBS[k].get("status") in ("done", "failed", "cancelled"):
                    BATCH_JOBS.pop(k, None)
                    break
    th = threading.Thread(target=_run_batch, args=(job_id,), name=f"m4b-batch-{job_id}", daemon=True)
    job["_thread"] = th
    th.start()
    return _batch_public(job)


def get_batch(job_id: str) -> dict | None:
    with _BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        return copy.deepcopy(_batch_public(job)) if job else None


def latest_batch(slug: str) -> dict | None:
    with _BATCH_LOCK:
        cands = [j for j in BATCH_JOBS.values() if j.get("slug") == slug]
        if not cands:
            return None
        job = max(cands, key=lambda j: (j.get("createdAt") or "", j.get("jobId") or ""))
        return copy.deepcopy(_batch_public(job))


def _same_project_mtime(slug: str) -> float:
    try:
        return storage.project_file(slug).stat().st_mtime
    except OSError:
        return 0.0


def _run_batch(job_id: str) -> None:
    """背景工作：每鏡重讀 project.json → 生圖 → 立刻存檔，失敗不中斷。

    GPU：整個批次只在這裡切換一次（ensure → 全部跑完 → finally 還原），不逐鏡停啟 LLM。
    LLM 忙 → 整批直接 failed（沒有任何鏡頭送件），gpu.busy 供前端顯示紅色橫幅。
    """
    t_all = time.time()
    with _BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["startedAt"] = storage.now_iso()
        job["phase"] = "檢查 GPU（必要時暫停本機 LLM）…"
        slug = job["slug"]
        mode = job["mode"]
        resolution = job["resolution"]
        engine = job.get("engine") or "qwen"

    try:
        with gpu_for_comfy(12000, slug, "批次生圖", project=job) as gbox:
            _run_batch_loop(job_id, slug, mode, resolution, engine, job, t_all)
            # 收尾排在「LLM 已啟回」之後：前端才不會看到「已結束」但還原狀態還沒填
            _run_batch_finish(job, t_all)
    except GpuBusyError as e:
        with _BATCH_LOCK:
            job["status"] = "failed"
            job["finishedAt"] = storage.now_iso()
            job["phase"] = "沒有送件（本機 LLM 正在運算）"
            job["error"] = str(e)
            job["gpu"] = {"busy": True, "error": str(e), "switched": False, "stoppedLlama": False,
                          "restored": False, "restartWaitedS": 0.0, "freedMB": None,
                          "autoSwitch": gpu.auto_switch_enabled()}
            for it in job["items"]:
                if it.get("status") == "pending":
                    it["status"] = "skipped"
                    it["error"] = "本機 LLM 運算中，整批未送件"
            job["elapsed"] = round(time.time() - t_all, 1)
        return
    except ImageGenError as e:
        with _BATCH_LOCK:
            job["status"] = "failed"
            job["finishedAt"] = storage.now_iso()
            job["phase"] = "GPU 切換失敗"
            job["error"] = str(e)
            job["elapsed"] = round(time.time() - t_all, 1)
        return


def _run_batch_finish(job: dict, t_all: float) -> None:
    """批次收尾：狀態／時間。獨立成函式，讓呼叫端能在「LLM 已啟回」之後才呼叫。"""
    with _BATCH_LOCK:
        if job.get("status") != "cancelled":
            job["status"] = "done" if job["failCount"] == 0 else ("failed" if job["okCount"] == 0 else "partial")
        job["finishedAt"] = storage.now_iso()
        job["current"] = ""
        job["elapsed"] = round(time.time() - t_all, 1)
        job["phase"] = job.get("phase") if job["status"] == "cancelled" else "已結束"


def _run_batch_loop(job_id: str, slug: str, mode: str, resolution: int, engine: str,
                    job: dict, t_all: float) -> None:
    for item in job["items"]:
        shot_id = item["shotId"]
        if job.get("status") == "cancelled":
            break
        with _BATCH_LOCK:
            item["status"] = "running"
            job["current"] = shot_id
        t0 = time.time()
        try:
            # 每次重讀：使用者可能在批次途中改了 prompt／勾了鎖臉；也避免覆蓋別人的存檔
            data = storage.load_project(slug)
            shot = find_shot(data, shot_id)
            if shot is None:
                raise ImageGenError(f"找不到鏡頭 {shot_id}（可能在批次途中被刪了）")
            res = generate_shot_image(data, shot_id, {"mode": mode, "resolution": resolution,
                                                      "engine": engine, "_gpuForce": True})
            storage.save_project(slug, data)   # 每鏡完成即寫回
            with _BATCH_LOCK:
                item.update({"status": "done", "path": res["path"], "pid": res["pid"],
                             "elapsed": res["elapsed"], "warnings": res.get("warnings") or [],
                             "width": res.get("width"), "height": res.get("height"),
                             "seed": res.get("seed"), "refs": len(res.get("refs") or []),
                             "error": ""})
                job["okCount"] += 1
        except ImageGenError as e:
            with _BATCH_LOCK:
                item.update({"status": "failed", "error": str(e), "elapsed": round(time.time() - t0, 1)})
                job["failCount"] += 1
        except Exception as e:  # noqa: BLE001 — 單鏡未預期錯誤不該讓整批停下
            with _BATCH_LOCK:
                item.update({"status": "failed", "elapsed": round(time.time() - t0, 1),
                             "error": f"未預期錯誤：{type(e).__name__}: {e}"})
                job["failCount"] += 1
        with _BATCH_LOCK:
            job["done"] += 1
            job["elapsed"] = round(time.time() - t_all, 1)
    # 收尾（status／finishedAt）由 _run_batch_finish 負責：必須排在「本機 LLM 已啟回」之後
