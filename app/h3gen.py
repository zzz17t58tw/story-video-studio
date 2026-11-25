"""M5a：單鏡生影片 — 導演台 r2v timeline 組裝、送件、背景監看、下載 mp4 回填鏡頭卡。

依據 docs/spec/contracts.md §3（已實測跑通的 H3 送件路徑）：

  templates/h3_director_r2v.ui.json 是 **UI 格式**（nodes[].widgets_values 陣列），不是 API 格式；
  送件前要自己轉成 API 格式（{nodeId: {class_type, inputs}}）：

    1 UNETLoader / 2 CLIPLoader(type=minimax) / 3 VAELoader(影片) / 4 VAELoader(音訊)
    5 MiniMaxH3Director  ← 全部參數在這裡
    6 CreateVideo(images=["5",0], audio=["5",1], fps=["5",2])
    7 SaveVideo(video=["6",0])  → 輸出在 outputs["7"]["images"]（ComfyUI 的 video 輸出也掛在 images 鍵底下）

  MiniMaxH3Director 的 widgets_values 索引（contracts §3）：
    [0]task_type [1]global_prompt [2]bd_grp_sample [3]cfg [4]seed [5]control [6]frame_rate
    [7]width [8]height [9]ref_max_size [10]total_frames [11]timeline_data(巨大 JSON 字串)
    [12]bd_grp_advanced [13]steps [14]sampler [15]scheduler [16]shift_video [17]shift_audio …

  為什麼 width/height 要看 [7]/[8] 而不是想當然：模板預設是 864x480（橫），本專案是 9:16 →
  必須一起改 timeline 的 width/height 與節點 widget，兩邊不一致時導演台會用 widget 蓋掉 timeline。

timeline 內 refs 條目：{"index":0,"imageFile":"<ComfyUI input 檔名>","fileName":"","type":"input","subfolder":""}，
提示詞用 `<Picture 1>`…`<Picture 9>` 引用（index 0→Picture 1）。**index 必須等於 ordinal-1**，
因為 promptEn 的編號是 h3compose 依 referencePlan.slots 的 ordinal 寫死的。

影格：total_frames 落在 39（=17*2+5，contracts §3 實測 1.6 秒成功的最小值）。

對外函式：
  build_timeline(project, shot, opts)  -> dict（timeline，尚未 json.dumps）
  submit(project, shot, opts)          -> dict（pid / elapsed / refs / timeline，不落地）
  wait_and_collect(pid, ...)           -> Path（下載好的 mp4）
  start_job(project, slug, shot_id, opts) / get_job(job_id) / latest_job(slug)  → 背景任務
"""

from __future__ import annotations

import copy
import json
import random
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import comfy, gpu as gpulib, imagegen, storage, paths, promptrules

ROOT = paths.TEMPLATES_DIR.parent
TEMPLATE = paths.TEMPLATES_DIR / "h3_director_r2v.ui.json"

# ── r2v 的固定參數（contracts §3 實測值）───────────────────────────
TASK_TYPE_R2V = "r2v — 参考主体生视频(Reference to Video)"
FRAME_RATE = 24.0
FRAME_GRID_STEP = 17          # H3 合法格 17k+5，total_frames 對齊 17k+1
DEFAULT_FRAMES = 39           # 17*2+5 → 1.6 秒（contracts §3 實測成功）
MAX_REFS = 9
DEFAULT_WIDTH, DEFAULT_HEIGHT = 480, 864   # 9:16 低清快速（驗收用）
DEFAULT_STEPS, DEFAULT_CFG = 4, 1.0

# ── 影片 LoRA（M9）────────────────────────────────────────────────
# 掛法：走 timeline 每個 segment 的 `loras: [{name, strength, active}]`
#   （導演台 director/segment_loras.py `normalize_lora_rows` L36-74；
#    plan.py L906「editMode=global 時改讀 global_block.loras」，所以兩邊都要寫）。
# name 就是 ComfyUI models/loras 下的檔名（= LoraLoaderModelOnly 的下拉值）。
LORA_PIKA_TURBO4 = "pika_h3_flex_turbo_4step_ref2va.safetensors"
LORA_PIKA_BF16 = "pika_h3_flex_ref2va_comfy_bf16.safetensors"
LORA_CHOICES = (
    ("", "不掛 LoRA"),
    (LORA_PIKA_TURBO4, "Pika Flex Turbo 4step Ref2VA"),
    (LORA_PIKA_BF16, "Pika Flex Ref2VA bf16"),
)
# Pika 官方建議（2026-11）：strength 1.0、cfg 1.0、euler/res_multistep＋simple、
# flow shift video 12.0 / audio 3.0。⚠️ 音訊在 6 步以下會崩 → steps 至少 6。
# ⚠️ Pika Flex／LightX2V／larryvrh／DMAD 四個加速家族**永不疊加，一支為限**。
VIDEO_GEN_DEFAULTS = {
    "lora": {"name": LORA_PIKA_BF16, "strength": 1.0, "active": True},
    "steps": 6, "cfg": 1.0, "shiftVideo": 12.0, "shiftAudio": 3.0,
    "sampler": "euler", "scheduler": "simple",
}
ACCEL_LORA_FAMILIES = (
    ("pika_", "Pika Flex"),
    ("lightx2v", "LightX2V"),
    ("larryvrh", "larryvrh"),
    ("dmad", "DMAD"),
)
DIRECTOR_NODE = 5             # 模板裡的 MiniMaxH3Director 節點 id
SAVE_NODE = 7                 # 模板裡的 SaveVideo 節點 id
POLL_INTERVAL = 1.5
MAX_WAIT = 600.0
# H3 生影片實測要吃 ~15 GB：生之前若空閒 VRAM 不到這個數，自動切換會先把本機 LLM 停掉
H3_NEED_VRAM_MB = 15000

_PIC_RE = re.compile(r"<Picture\s+(\d+)>")


def _h3compose():
    """延遲取得 h3compose（promptrules → h3compose、h3gen → promptrules，模組層互相 import 時序不定）。"""
    from . import h3compose
    return h3compose

_ASPECT = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4),
           "3:2": (3, 2), "2:3": (2, 3), "21:9": (21, 9)}


class H3GenError(Exception):
    """可預期錯誤（中文，可直接顯示）。"""


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


def legal_frames(n: int) -> int:
    """吸附到最近的 17k+5（影格長度）。"""
    n = max(5, int(n))
    k = max(0, round((n - 5) / FRAME_GRID_STEP))
    return FRAME_GRID_STEP * k + 5


def _total_frames_for(legal: int) -> int:
    """節點 total_frames 要對齊 17k+1（contracts §3）；39 → 36 的坑不踩：這裡直接用合法長度本身。

    實測 39 影格送件成功（contracts 記錄），總長度與 segment.frameCount 同值即可。
    """
    return legal


def size_for_aspect(aspect: str, long_edge: int = 864) -> tuple[int, int]:
    """比例 → (width, height)。長邊對齊 32（節點 step=32），短邊也對齊 16，避免半像素。"""
    a = _ASPECT.get(aspect) or _ASPECT["9:16"]
    w_units, h_units = a
    if h_units >= w_units:      # 直式
        h = long_edge
        w = int(round(long_edge * w_units / h_units))
    else:
        w = long_edge
        h = int(round(long_edge * h_units / w_units))
    return max(32, w // 32 * 32), max(32, h // 32 * 32)


# ── 1. 組 timeline ─────────────────────────────────────────────
def _template_graph() -> dict:
    """讀模板（只讀，不改檔）→ 回 UI 格式 dict（nodes/links）。"""
    if not TEMPLATE.is_file():
        raise H3GenError(f"缺少導演台模板：{TEMPLATE.relative_to(ROOT)}")
    try:
        data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise H3GenError(f"導演台模板格式錯誤（{TEMPLATE.name}）：{type(e).__name__}") from e
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise H3GenError(f"導演台模板不是 UI 格式（缺 nodes[]）：{TEMPLATE.name}")
    return copy.deepcopy(data)


def _node(graph: dict, node_id: int) -> dict:
    for n in graph.get("nodes") or []:
        if n.get("id") == node_id:
            return n
    raise H3GenError(f"導演台模板缺少節點 {node_id}（模板被改過？）")


PLACEHOLDER_MAX_BYTES = 1024   # 小於這個大小的圖是占位圖（實測 96x96 純色 PNG = 223 bytes）


def _is_placeholder(path: Path) -> bool:
    """占位圖判定：檔案小得離譜（純色 96x96 約 0.2KB，真圖至少數十 KB）。"""
    try:
        return path.stat().st_size < PLACEHOLDER_MAX_BYTES
    except OSError:
        return True


def _first_frame_ref(project: dict, shot: dict) -> str:
    """該鏡目前選定的首幀（相對路徑）；沒有就空字串。"""
    rel = _s((shot.get("images") or {}).get("first"))
    if not rel:
        return ""
    slug = _s((project.get("meta") or {}).get("slug"))
    return rel if (storage.project_dir(slug) / rel).is_file() else ""


def collect_refs(project: dict, shot: dict, allow_first_frame: bool = True) -> tuple[list[dict], list[str], dict]:
    """取這次要送進導演台的參考圖。回 (refs, warnings, notes)。

    規則（M5a 實測補的）：
      1. 依 referencePlan.slots 的 **ordinal 順序**取圖，不重排 —— promptEn 的 `<Picture N>`
         是依 ordinal 寫死的，重排就會讓 N 指到別人的臉。
      2. 占位圖（<1KB 的純色小圖）**跳過**：送進 r2v 只會把純色塊當主體，
         反而把真正的首幀擠掉（實測：三張 96x96 純色圖 → 模型退化成 t2v，臉全崩）。
      3. 有可用首幀（shot.images.first）且沒有角色 slot 進得了 → 把首幀補成 **第一個** 參考圖。
         這時 `_picture_map` 會回 identity（<Picture N> 照原字面用），所以不能動 prompt，
         編號語意就是「按 prompt 原本的寫法」。
    """
    warnings: list[str] = []
    notes: dict = {"skipped": [], "firstFrameAsPicture": 0}
    slots = [s for s in ((shot.get("referencePlan") or {}).get("slots") or []) if isinstance(s, dict)]
    slug = _s((project.get("meta") or {}).get("slug"))
    pdir = storage.project_dir(slug)
    out: list[dict] = []
    for s in slots:
        rel = _s(s.get("path"))
        if not rel:
            warnings.append(f"槽位 {s.get('ordinal')}（{s.get('kind')}:{s.get('id')}）沒有圖檔路徑，已略過")
            continue
        p = pdir / rel
        if not p.is_file():
            warnings.append(f"槽位 {s.get('ordinal')} 的參考圖檔不存在，已略過：{rel}")
            continue
        if _is_placeholder(p):
            notes["skipped"].append(rel)
            warnings.append(f"槽位 {s.get('ordinal')}（{s.get('kind')}:{s.get('id')}）是占位圖"
                            f"（{p.stat().st_size} bytes 的純色圖），已跳過不送 —— 送它只會污染主體")
            continue
        out.append({"ordinal": s.get("ordinal"), "path": rel,
                    "kind": _s(s.get("kind")), "id": _s(s.get("id"))})

    has_char = any(r["kind"] == "character" for r in out)
    first = _first_frame_ref(project, shot) if allow_first_frame else ""
    if first:
        fp = pdir / first
        if _is_placeholder(fp):
            warnings.append(f"首幀 {first} 也是占位圖，不送")
        elif not has_char:
            had_other_ref = bool(out)   # 只有場景圖、沒有角色圖 vs 一張都沒有
            out.insert(0, {"ordinal": 0, "path": first, "kind": "firstFrame", "id": "first"})
            notes["firstFrameAsPicture"] = 1
            warnings.append(
                ("沒有角色的參考圖（角色槽位是占位圖或沒建）：改用本鏡首幀當 <Picture 1> 參考主體"
                 if had_other_ref else
                 "沒有任何角色／場景參考圖：改用本鏡首幀當 <Picture 1> 參考主體"
                 "（要鎖同一個人的臉，請先在人物頁按「生成立繪」再建參考圖槽位）"))
        else:
            notes["startImage"] = first
    if len(out) > MAX_REFS:
        warnings.append(f"參考圖超過上限，只送前 {MAX_REFS} 張（導演台 r2v 上限 <Picture 1..9>）")
        out = out[:MAX_REFS]
    if not out:
        raise H3GenError("這鏡沒有任何可用的參考圖（角色/場景都是占位圖、也沒有首幀）："
                         "請先生成首幀或上傳真的角色參考圖（r2v 沒有參考圖等於 t2v，臉鎖不住）")
    # 重編編號 1..N（送件時的順序就是 <Picture N> 的順序）
    for i, r in enumerate(out):
        r["sendIndex"] = i
    return out, warnings, notes


def lora_rows(raw: Any, default: dict | None = None) -> list[dict]:
    """把設定/覆寫值正規化成導演台 segment.loras 格式；回 [] 表示不掛。

    接受 {"name","strength","active"} 或直接字串檔名。回 [{name, strength, active}]（最多一支）。
    """
    row = raw if isinstance(raw, dict) else ({"name": raw} if isinstance(raw, str) else {})
    name = _s(row.get("name") or row.get("lora_name") or (default or {}).get("name"))
    if not name:
        return []
    strength = _clamp_float(row.get("strength", (default or {}).get("strength")), 0.0, 1.5, 1.0)
    active = row.get("active", (default or {}).get("active", True))
    return [{"name": name, "strength": strength, "active": bool(active)}]


def lora_family(name: str) -> str:
    """加速 LoRA 家族（判斷是否會疊到第二支加速）。不是加速類就回空字串。"""
    n = _s(name).lower().replace("\\", "/").split("/")[-1]
    for prefix, label in ACCEL_LORA_FAMILIES:
        if prefix in n:
            return label
    return ""


def check_lora_stack(rows: list[dict]) -> list[str]:
    """回警告清單：同一個加速家族只能掛一支（永不疊加，官方明說會壞）。"""
    fams = [lora_family(r.get("name")) for r in rows if r.get("active", True)]
    fams = [f for f in fams if f]
    out: list[str] = []
    if len(fams) > 1:
        out.append(f"偵測到多支加速 LoRA（{'、'.join(fams)}）：加速 LoRA 永不互相疊加，一支為限，"
                   "請只留一支，否則畫質／音訊會壞")
    return out


def _sendable_ordinals(refs: list[dict], notes: dict) -> list[int]:
    """這次送件時「<Picture N> 的 N 可以是哪些值」——這個集合必須跟下方的改編號邏輯一致。

    · 一般：每個 ordinal ≥1 的參考圖都送得出，N 就是它的 ordinal（送件序號不同，但下方會 remap）。
    · 首幀當 <Picture 1>：只有 1 號（其他槽位這次沒送）。
    """
    if notes.get("firstFrameAsPicture"):
        return [1]
    return sorted({int(r["ordinal"]) for r in refs if r.get("ordinal") is not None and int(r["ordinal"]) >= 1})


def _pick_prompt(project: dict, shot: dict, sendable: list[int],
                 first_frame_as_picture: bool = False) -> tuple[str, list[str], str, list[str]]:
    """決定這次要送出的提示詞（M12b：送件口硬驗）。

    規則是 promptEn 合格就用它；不合格（或空白）就改用 app/h3compose.py 的模板編譯結果。
    **一律不寫回 project.json**（送件口不偷改使用者的資料）。
    first_frame_as_picture：送件時把首幀當 <Picture 1>（collect_refs 判定），編譯稿要跟著寫。
    回 (prompt, warnings, promptSource, problems)；promptSource ∈ {"promptEn", "compiled"}。
    """
    warnings: list[str] = []
    raw = _s(shot.get("promptEn"))
    ctx = {"slots": sendable}
    if raw:
        ok, problems, notes = promptrules.check("h3_video", raw, ctx)
        if notes:
            warnings.extend(notes)
        if ok:
            return raw, warnings, "promptEn", []
        why = "、".join(problems) if problems else "格式不符"
        compiled, cw, _meta = _h3compose().compile_prompt(project, shot, first_frame_as_picture)
        compiled = _s(compiled)
        if compiled:
            warnings.append(f"promptEn 未符合 H3 官方規則（缺：{why}），已改用模板編譯"
                            "（app/h3compose.py 六段式＋<Picture N>，未寫回 project.json）")
            return compiled, warnings + list(cw), "compiled", problems
        raise H3GenError("這鏡的 promptEn 未符合 H3 官方規則（缺：" + why
                         + "），而且模板編譯也組不出提示詞：請先補人物卡／場景卡或按「⌘ 模板編譯 prompt」")
    compiled, cw, _meta = _h3compose().compile_prompt(project, shot, first_frame_as_picture)
    compiled = _s(compiled)
    if not compiled:
        raise H3GenError("這鏡沒有提示詞（promptEn 空）：請先寫提示詞或按「⌘ 模板編譯 prompt」")
    warnings.extend(cw)
    warnings.append("這鏡 promptEn 是空的，已改用「模板編譯」的即時結果（未寫回 project.json）")
    return compiled, warnings, "compiled", ["promptEn 是空的"]


def build_timeline(project: dict, shot: dict, opts: dict | None = None) -> dict:
    """組導演台 r2v timeline（還沒送件、還沒上傳圖）。

    opts: width / height / frames / seed / 以及 refImageFiles（ordinal→ComfyUI input 檔名）。
    回傳 timeline dict；refs 的 imageFile 由呼叫端上傳後補（見 prepare_and_submit）。

    opts["lora"] = {name, strength, active}（None／空 name＝不掛）：寫進 segment.loras 與
    global.loras。editMode 一律 "global"，導演台在 global 模式下只認 global.loras 當每段 LoRA 堆疊，
    segment.loras 只是同一份資料（前端卡片讀它、且換回 segment 模式時仍正確）。

    首幀（shot.images.first）的處理（實測校準後）：
      · 角色槽位有真圖時：首幀走 segment 的 startImage（i2v 式定錨），不佔 <Picture N> 編號。
      · 角色槽位只有占位圖時：首幀當 <Picture 1>，其餘 <Picture N> 全部重編號（見 collect_refs）。
    """
    opts = opts or {}
    warnings: list[str] = []

    aspect = _s(opts.get("aspect")) or _s((project.get("meta") or {}).get("aspectRatio")) or "9:16"
    dw, dh = size_for_aspect(aspect)
    width = _clamp_int(opts.get("width"), 32, 8192, dw)
    height = _clamp_int(opts.get("height"), 32, 8192, dh)
    # 影格數：opts 沒指定就跟著這一鏡的 durationSec（17k+5 吸附）。
    # 以前一律用 DEFAULT_FRAMES=39／前端寫死 124，設定 9.42 秒的鏡只會生出 5.17 秒，
    # 台詞多的鏡一定唸不完（V3 長度合法就變成裝飾）。
    frames_opt = opts.get("frames")
    if frames_opt in (None, ""):
        dsec = float(shot.get("durationSec") or 0) or 0.0
        frames_opt = round(dsec * FRAME_RATE) if dsec > 0 else DEFAULT_FRAMES
    legal = legal_frames(_clamp_int(frames_opt, 5, 100000, DEFAULT_FRAMES))
    total_frames = _total_frames_for(legal)
    duration_sec = round(legal / FRAME_RATE, 3)
    seed = _clamp_int(opts.get("seed"), 0, 2 ** 31 - 1, random.randint(0, 2 ** 31 - 1))

    refs, w, notes = collect_refs(project, shot, allow_first_frame=bool(opts.get("useFirstFrame", True)))
    warnings.extend(w)

    # M12b：先知道「這次送得出哪些槽位」，再用 app/promptrules.py 決定要送哪一份提示詞。
    # promptEn 合格 → 原樣送；不合格（缺六段／<Picture N> 對不上／沒有 <Picture）→ 改用
    # h3compose.compile_prompt 的即時結果，並在 warnings 寫清楚缺什麼（不寫回 project.json）。
    sendable = _sendable_ordinals(refs, notes)
    prompt, pw, prompt_source, prompt_problems = _pick_prompt(
        project, shot, sendable, bool(notes.get("firstFrameAsPicture")))
    warnings.extend(pw)

    ordinals = [r["ordinal"] for r in refs]
    used = sorted({int(m.group(1)) for m in _PIC_RE.finditer(prompt)})
    # <Picture N> 的對應：送件序號從 1 開始，但 prompt 的 N 是「參考圖槽位序號」。
    # 一般情況（前面沒有被跳過的圖）兩者相同；有占位圖被跳過時，後面的圖會往前補位，
    # 所以必須在 prompt 裡一起改編號，否則模型會拿錯圖當主體（M4a 踩過同一個坑）。
    pic_map = {r["ordinal"]: r["sendIndex"] + 1 for r in refs if r["ordinal"] >= 1}
    if notes.get("firstFrameAsPicture"):
        pic_map[1] = 1          # 首幀頂 <Picture 1>：prompt 裡的 1 照字面就是指「這個主體」
    bad_pics = [n for n in used if n not in pic_map]
    if bad_pics:
        # 這裡只會剩「模板編譯後仍對不上」的殘餘（合格 promptEn 已在 promptrules.check 擋過一次）
        warnings.append("提示詞引用了 " + "、".join(f"<Picture {n}>" for n in bad_pics)
                        + f"，但這次只送得出 {sorted(pic_map)} 號槽位：這些引用會保持原樣（模型會忽略）")
    if notes.get("firstFrameAsPicture"):
        # 首幀當 <Picture 1>：只有這一張圖，所以 <Picture 2..9> 的句子整段拿掉
        # （留著會讓模型以為還有別的參考圖，實測畫質會直接崩）
        dropped = sorted({int(n) for n in used if int(n) > 1})
        if dropped:
            def _drop(m):
                return "" if int(m.group(1)) > 1 else m.group(0)
            prompt = _PIC_RE.sub(_drop, prompt)
            prompt = re.sub(r"\s{2,}", " ", prompt).strip()
            warnings.append("首幀當 <Picture 1> 後，提示詞裡 " + "、".join(f"<Picture {n}>" for n in dropped)
                            + " 那段已移除（這次沒有那些圖）")
    else:
        def _remap(m):
            n = int(m.group(1))
            return f"<Picture {pic_map[n]}>" if n in pic_map else m.group(0)
        prompt = _PIC_RE.sub(_remap, prompt)
        if prompt != _s(shot.get("promptEn")):
            warnings.append("提示詞的 <Picture N> 已依實際送出的圖重新編號（" +
                            "、".join(f"{k}→{v}" for k, v in sorted(pic_map.items()) if k != v) + "）")
    unused = [n for n in pic_map if n not in used]
    if unused and not notes.get("firstFrameAsPicture"):
        warnings.append("這些槽位有圖但提示詞沒引用：" + "、".join(f"<Picture {n}>" for n in sorted(unused)))
    if not used:
        warnings.append("promptEn 沒有任何 <Picture N> 引用（模型不知道哪張圖是誰，只會當參考氛圍）")

    # refs：index = 送件序號-1（<Picture N> ↔ refs[N-1] 是導演台硬規則）
    ref_files = opts.get("refImageFiles") or {}
    timeline_refs = []
    for r in refs:
        timeline_refs.append({
            "index": r["sendIndex"],
            "imageFile": _s(ref_files.get(str(r["ordinal"])) or ref_files.get(r["ordinal"]) or ""),
            "fileName": "", "type": "input", "subfolder": "",
            "_ordinal": r["ordinal"],   # 內部用，送件前會剝掉
        })

    seg_loras = lora_rows(opts.get("lora"))
    warnings.extend(check_lora_stack(seg_loras))
    segment = {
        "id": uuid.uuid4().hex[:12],
        "start": 0,
        "length": legal,
        "frameCount": legal,
        "durationSec": duration_sec,
        "prompt": prompt,
        "taskType": "",
        "refs": [],
        "refAudios": [],
        "refVideos": [],
        "negativePrompt": "",
        "continuityFromPrev": False,
        "refImageSize": _clamp_int(opts.get("refMaxSize"), 32, 8192, min(width, height)),
        "loras": copy.deepcopy(seg_loras),
        "genImage": {"imageFile": "", "fileName": ""},
    }
    first = _s(notes.get("startImage") or "")
    if first:
        segment["startImage"] = {"imageFile": "", "width": width, "height": height,
                                 "_local": first}   # 上傳後填 imageFile

    timeline = {
        "version": 4,
        "editMode": "global",
        "timelineMode": "gen_blank",
        "totalFrames": total_frames,
        "frameRate": FRAME_RATE,
        "width": width,
        "height": height,
        "refMaxSize": min(width, height),
        "output": {
            "mode": "fixed", "longEdge": max(width, height),
            "width": width, "height": height, "maxExportFrames": 0,
            "exportMode": "all", "continuityEnabled": False, "continuityOverlapFrames": 9,
        },
        "videoClips": [],
        "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input",
                  "frames": [], "frameMap": []},
        "global": {
            "taskType": TASK_TYPE_R2V,
            "prompt": "",
            "refs": timeline_refs,
            "refAudios": [],
            "refVideos": [],
            "referenceVideo": {},
            "continuousReference": False,
            "genImage": {"imageFile": ""},
            "commonEnabled": False,
            "commonCollapsed": True,
            "loras": copy.deepcopy(seg_loras),   # editMode=global：導演台讀這裡（plan.py L906）
        },
        "segments": [segment],
        "gen": {"defaultFrameCount": legal},
        "runSelectEnabled": False,
        "runSelection": [],
        "_warnings": warnings,
        "_meta": {
            "aspect": aspect, "width": width, "height": height, "frames": legal,
            "totalFrames": total_frames, "durationSec": duration_sec, "seed": seed,
            "refs": [{"ordinal": r["ordinal"], "sendIndex": r["sendIndex"], "id": r["id"],
                      "kind": r["kind"], "path": r["path"]} for r in refs],
            "startImage": first, "promptChars": len(prompt), "picturesUsed": used,
            "promptRule": "h3_video", "promptSource": prompt_source,
            "promptProblems": list(prompt_problems),
            "picMap": pic_map, "skippedPlaceholders": notes.get("skipped") or [],
            "firstFrameAsPicture": int(notes.get("firstFrameAsPicture") or 0),
            "loras": copy.deepcopy(seg_loras),
        },
    }
    return timeline


def _strip_internal(timeline: dict) -> dict:
    """剝掉 _ 開頭與 refs 的 _ordinal（模型不吃未知鍵，但乾淨一點比較好除錯）。"""
    t = copy.deepcopy(timeline)
    t.pop("_warnings", None)
    t.pop("_meta", None)
    for r in (t.get("global") or {}).get("refs") or []:
        r.pop("_ordinal", None)
    for seg in t.get("segments") or []:
        si = seg.get("startImage")
        if isinstance(si, dict):
            si.pop("_local", None)
            if not _s(si.get("imageFile")):
                seg.pop("startImage", None)
    return t


# ── 2. UI 格式 → API 格式 ──────────────────────────────────────
# 節點欄位型別（INPUT_TYPES 的字串型別 = 「不是連線用的插槽」→ 就是 widget）
_WIDGET_TYPES_FALLBACK = {
    "STRING", "INT", "FLOAT", "BOOLEAN", "COMBO", "BDGROUP", "AUDIO_UPLOAD", "COMFY_DYNAMICCOMBO_V3",
}


def _object_info(base_url: str | None = None) -> dict:
    """抓 /object_info（快取）。抓不到就用 fallback 型別表（仍可運作，只是 widget 順序靠規則）。"""
    global _OI_CACHE
    if _OI_CACHE is not None:
        return _OI_CACHE
    try:
        with httpx.Client(timeout=20.0) as c:
            _OI_CACHE = c.get(f"{comfy._base(base_url)}/object_info").json() or {}
    except Exception:  # noqa: BLE001
        _OI_CACHE = {}
    return _OI_CACHE


_OI_CACHE: dict | None = None


def widget_names_for(class_type: str, base_url: str | None = None) -> list[str]:
    """節點 widgets_values 的欄位順序（依 INPUT_TYPES required→optional，跳過插槽型別）。

    注意 seed 這種帶 control_after_generate 的 INT 在 UI 會多存一格（randomize/fixed），
    由呼叫端處理偏移。
    """
    oi = _object_info(base_url)
    spec = ((oi.get(class_type) or {}).get("input") or {})
    names: list[str] = []
    for sec in ("required", "optional"):
        for k, v in (spec.get(sec) or {}).items():
            t0 = v[0] if isinstance(v, (list, tuple)) and v else v
            is_widget = isinstance(t0, list) or (isinstance(t0, str) and t0 in _WIDGET_TYPES_FALLBACK)
            if is_widget:
                names.append(k)
    return names


def has_control_widget(class_type: str, name: str, base_url: str | None = None) -> bool:
    """這個 widget 後面有沒有 UI 專用的 control_after_generate 格。"""
    oi = _object_info(base_url)
    spec = ((oi.get(class_type) or {}).get("input") or {})
    for sec in ("required", "optional"):
        v = (spec.get(sec) or {}).get(name)
        if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], dict):
            return bool(v[1].get("control_after_generate"))
    return False


# 對照：UI 的 control 值 → 送 API 時要換成的語意
_CONTROL_FIXED = {"fixed", "固定"}


def _map_widgets(class_type: str, wv: list, base_url: str | None = None) -> dict:
    """把 widgets_values 陣列映射成 {欄位名: 值}，正確吃掉 control_after_generate 多出來的那一格。"""
    names = widget_names_for(class_type, base_url)
    out: dict[str, Any] = {}
    i = 0
    for name in names:
        if i >= len(wv):
            break
        out[name] = wv[i]
        i += 1
        if has_control_widget(class_type, name, base_url):
            if i < len(wv):
                ctrl = wv[i]
                # 送件時把 control 值換成 "fixed"，避免 ComfyUI 幫我們重新抽 seed
                if isinstance(ctrl, str) and ctrl.strip().lower() not in _CONTROL_FIXED:
                    pass          # control 本身不是欄位，直接丟掉；seed 由 seed 欄位決定
                i += 1
    return out


def _ui_to_api(graph: dict, director_wv: list, inputs_over: dict | None = None,
               base_url: str | None = None) -> dict:
    """把 UI 格式模板轉成 API 格式。

    widget 以 **INPUT_TYPES 的欄位順序** 解析（不是靠 inputs[] 宣告序）：
      · seed 這類 control_after_generate 的 widget，UI 會在其後多存一格（fixed/randomize），必須跳過；
      · optional 輸入（steps / sampler / shift_video …）排 required 之後，順序才對得上。
    MiniMaxH3Director 用呼叫端給的 director_wv（已在記憶體改過、已按同一規則排列）。
    """
    inputs_over = inputs_over or {}
    links = {l[0]: l for l in (graph.get("links") or []) if isinstance(l, list) and len(l) >= 5}
    api: dict[str, dict] = {}
    for n in graph.get("nodes") or []:
        if n.get("mode") in (2, 4):      # 2=mute（不執行）、4=bypass → 整顆不送
            continue
        nid = n["id"]
        cls = n.get("type")
        if cls in ("MarkdownNote", "Note"):
            continue
        inputs: dict[str, Any] = _map_widgets(cls, director_wv if nid == DIRECTOR_NODE
                                              else (n.get("widgets_values") or []), base_url)
        for i in (n.get("inputs") or []):
            if i.get("link") is None:
                continue
            lk = links.get(i["link"])
            if not lk:
                continue
            inputs[i["name"]] = [str(lk[1]), lk[2]]   # lk = [linkId, srcNode, srcSlot, dst, dstSlot, type]
        inputs.update(inputs_over.get(str(nid)) or {})
        api[str(nid)] = {"class_type": cls, "inputs": inputs}
    return api


def build_graph(project: dict, shot: dict, opts: dict | None = None) -> tuple[dict, dict]:
    """回 (api_prompt_graph, info)。info 含 timeline（已剝內部鍵）、warnings、meta。

    呼叫前 timeline 內所有 imageFile 必須已填好（見 _upload_refs）。
    """
    opts = opts or {}
    timeline = opts.get("_timeline") or build_timeline(project, shot, opts)
    graph = _template_graph()
    meta = timeline.get("_meta") or {}
    base_url = imagegen._comfy_base(project)
    # 生成參數：呼叫端給的優先，否則 Pika 官方建議值（M9）。steps 在導演台是 widget，shift/sampler/scheduler 也是。
    d = VIDEO_GEN_DEFAULTS
    steps = _clamp_int(opts.get("steps"), 1, 60, int(d["steps"]))
    cfg = _clamp_float(opts.get("cfg"), 0.0, 30.0, float(d["cfg"]))
    shift_video = _clamp_float(opts.get("shiftVideo"), 0.01, 100.0, float(d["shiftVideo"]))
    shift_audio = _clamp_float(opts.get("shiftAudio"), 0.01, 100.0, float(d["shiftAudio"]))
    sampler = _s(opts.get("sampler")) or _s(d["sampler"])
    scheduler = _s(opts.get("scheduler")) or _s(d["scheduler"])
    loras = list((timeline.get("_meta") or {}).get("loras") or [])
    dnode = _node(graph, DIRECTOR_NODE)
    wv = list(dnode.get("widgets_values") or [])
    if len(wv) < 12:
        raise H3GenError(f"導演台節點的 widgets_values 只有 {len(wv)} 格（預期 ≥12），模板格式不符")
    # 先求出「索引 → 欄位名」，才知道 seed 後面那格 control 在哪
    idx_by_name: dict[str, int] = {}
    names = widget_names_for("MiniMaxH3Director", base_url)
    if not names:
        raise H3GenError("抓不到 MiniMaxH3Director 的欄位表（ComfyUI /object_info 異常），無法安全組 widget")
    i = 0
    for nm in names:
        if i >= len(wv):
            break
        idx_by_name[nm] = i
        i += 1
        if has_control_widget("MiniMaxH3Director", nm, base_url):
            i += 1
    if "timeline_data" not in idx_by_name:
        raise H3GenError("MiniMaxH3Director 的欄位表裡沒有 timeline_data（節點版本不符）")

    width = meta.get("width") or DEFAULT_WIDTH
    height = meta.get("height") or DEFAULT_HEIGHT

    def put(name: str, val: Any) -> None:
        if name in idx_by_name:
            wv[idx_by_name[name]] = val

    put("task_type", TASK_TYPE_R2V)
    put("global_prompt", "")          # 提示詞寫在 segment；global_prompt 留空才不會被蓋掉
    put("cfg", cfg)
    put("seed", int(meta.get("seed") or 0))
    if "seed" in idx_by_name and idx_by_name["seed"] + 1 < len(wv):
        wv[idx_by_name["seed"] + 1] = "fixed"   # control_after_generate：保住 seed，不要 randomize
    put("frame_rate", FRAME_RATE)
    put("width", width)
    put("height", height)
    put("ref_max_size", min(width, height))
    put("total_frames", int(meta.get("totalFrames") or DEFAULT_FRAMES))
    put("timeline_data", json.dumps(_strip_internal(timeline), ensure_ascii=False))
    put("steps", steps)
    put("sampler", sampler)
    put("scheduler", scheduler)
    put("shift_video", shift_video)
    put("shift_audio", shift_audio)

    over = {"6": {"fps": ["5", 2]}}   # CreateVideo 的 fps 直接吃導演台輸出（24），不另寫死
    api = _ui_to_api(graph, wv, over, base_url)
    chain = api.get(str(DIRECTOR_NODE)) or {}
    info = {"meta": meta, "warnings": list(timeline.get("_warnings") or []),
            "steps": steps, "cfg": cfg, "seed": int(meta.get("seed") or 0),
            "shiftVideo": shift_video, "shiftAudio": shift_audio,
            "sampler": sampler, "scheduler": scheduler, "loras": loras,
            "timeline": _strip_internal(timeline),
            "widgetMap": idx_by_name,
            "directorInputs": {k: (str(v)[:80] if k == "timeline_data" else v)
                               for k, v in (chain.get("inputs") or {}).items()}}
    return api, info


# ── 3. 上傳參考圖 ─────────────────────────────────────────────
def _upload_refs(project: dict, shot: dict, timeline: dict, base_url: str) -> tuple[dict, list[str]]:
    """把 timeline 裡的參考圖／首幀上傳到 ComfyUI input，就地填 imageFile。回 (ordinal→檔名, warnings)。"""
    slug = _s((project.get("meta") or {}).get("slug"))
    pdir = storage.project_dir(slug)
    files: dict[int, str] = {}
    warnings: list[str] = []
    for r in (timeline.get("global") or {}).get("refs") or []:
        if _s(r.get("imageFile")):
            files[r["_ordinal"]] = r["imageFile"]
    refs = timeline.get("_refs_local") or []
    for r, local_rel in refs:
        try:
            files[r["_ordinal"]] = comfy.upload_image(pdir / local_rel, base_url)
        except comfy.ComfyError as e:
            raise H3GenError(str(e)) from e
        r["imageFile"] = files[r["_ordinal"]]
    for seg in timeline.get("segments") or []:
        si = seg.get("startImage")
        if isinstance(si, dict) and not _s(si.get("imageFile")):
            rel = _s(si.get("_local"))
            if rel:
                try:
                    si["imageFile"] = comfy.upload_image(pdir / rel, base_url)
                except comfy.ComfyError as e:
                    warnings.append(f"首幀上傳失敗，改用純 r2v（不帶 startImage）：{e}")
                    seg.pop("startImage", None)
    return files, warnings


def with_video_settings(project: dict, opts: dict | None = None) -> dict:
    """把專案 settings.video（不掛 LoRA 以外的參數）當作基底，opts 有的欄位優先（單鏡覆寫用）。"""
    out = dict(VIDEO_GEN_DEFAULTS)
    try:
        sv = storage.video_settings(project)
    except Exception:  # noqa: BLE001 — 設定壞掉不該擋生成
        sv = {}
    for k in ("steps", "cfg", "shiftVideo", "shiftAudio", "sampler", "scheduler"):
        if sv.get(k) not in (None, ""):
            out[k] = sv[k]
    if "lora" in sv:
        out["lora"] = sv["lora"]
    for k, v in (opts or {}).items():
        if v is not None and v != "":
            out[k] = v
    # lora 給空 dict／"none" 也代表「明確指定不掛」
    if isinstance((opts or {}).get("lora"), (dict, str)) and not lora_rows(opts.get("lora")):
        out["lora"] = None
    return out


# ── 4. 送件 ────────────────────────────────────────────────────
def submit(project: dict, shot: dict, opts: dict | None = None) -> dict:
    """組 timeline → 上傳參考圖 → 送 ComfyUI。回 {pid, info, timeline, refFiles, prompt}。不落地。

    例外一律轉成 H3GenError（中文）。
    """
    opts = with_video_settings(project, opts)
    base_url = imagegen._comfy_base(project)
    ok, msg, _info = comfy.comfy_available(base_url)
    if not ok:
        raise H3GenError(msg)
    shot_id = _s(shot.get("id"))
    if not shot_id:
        raise H3GenError("鏡頭沒有 id，無法命名輸出檔")

    timeline = build_timeline(project, shot, opts)
    # 讓 _upload_refs 能把 imageFile 就地寫回 global.refs：[({"_ordinal": N}, 本機相對路徑), …]
    timeline["_refs_local"] = [
        (g, r["path"]) for g, r in zip(
            (timeline.get("global") or {}).get("refs") or [],
            (timeline.get("_meta") or {}).get("refs") or [])
    ]
    files, warnings = _upload_refs(project, shot, timeline, base_url)
    timeline["_warnings"] = list(timeline.get("_warnings") or []) + warnings

    opts["_timeline"] = timeline
    api, info = build_graph(project, shot, opts)
    try:
        pid = comfy.submit(api, base_url)
    except comfy.ComfyError as e:
        raise H3GenError(str(e)) from e
    return {"pid": pid, "info": info, "timeline": timeline, "refFiles": files,
            "prompt": (info["timeline"].get("segments") or [{}])[0].get("prompt") or ""}


# ── 5. 輪詢佇列位置（選配；拿不到不影響）───────────────────────────
def queue_position(pid: str, base_url: str | None = None) -> dict:
    """回 {state: 'running'|'pending'|'', position: int}。拿不到就全空，不拋。"""
    try:
        with httpx.Client(timeout=5.0) as c:
            q = c.get(f"{comfy._base(base_url)}/queue").json()
    except Exception:  # noqa: BLE001
        return {"state": "", "position": 0}
    run = q.get("queue_running") or []
    for item in run:
        if isinstance(item, list) and len(item) > 1 and item[1] == pid:
            return {"state": "running", "position": 1, "runningCount": len(run)}
    pend = q.get("queue_pending") or []
    for idx, item in enumerate(pend):
        if isinstance(item, list) and len(item) > 1 and item[1] == pid:
            return {"state": "pending", "position": idx + 1, "pendingCount": len(pend)}
    return {"state": "", "position": 0}


# ── 6. 輪詢 ＋ 下載 ────────────────────────────────────────────
def find_video(record: dict) -> dict | None:
    """從 /history 記錄找影片輸出。ComfyUI 的 SaveVideo 輸出掛在 outputs[node]["images"]，
    裡面是 {filename, subfolder, type}；也接受 "videos"/"gifs" 鍵（不同版本叫法不同）。"""
    outs = (record or {}).get("outputs") or {}
    if not isinstance(outs, dict):
        return None
    for nid, node_out in outs.items():
        if not isinstance(node_out, dict):
            continue
        for key in ("images", "videos", "gifs", "audio"):
            for f in (node_out.get(key) or []):
                if not isinstance(f, dict):
                    continue
                name = _s(f.get("filename"))
                if name.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                    return {**f, "nodeId": nid, "key": key}
    return None


def wait_and_collect(pid: str, project: dict, shot: dict, opts: dict | None = None,
                     on_tick=None) -> dict:
    """輪詢 /history（每 1.5 秒，最多 MAX_WAIT 秒）→ 下載 mp4 到 media/clips/。

    on_tick(elapsed, queue) 可傳進度回呼（背景任務用來更新狀態）。
    回 {path, elapsed, width, height, record}；失敗拋 H3GenError。
    """
    opts = opts or {}
    base_url = imagegen._comfy_base(project)
    slug = _s((project.get("meta") or {}).get("slug"))
    shot_id = _s(shot.get("id"))
    timeout = float(_clamp_int(opts.get("timeout"), 30, 1800, int(MAX_WAIT)))
    t0 = time.time()
    deadline = t0 + timeout
    last_err = ""
    record: dict | None = None
    with httpx.Client(timeout=20.0) as c:
        while time.time() < deadline:
            try:
                r = c.get(f"{base_url}/history/{pid}")
                if r.status_code == 200:
                    hist = r.json() or {}
                    rec = hist.get(pid) if isinstance(hist, dict) else None
                    if isinstance(rec, dict):
                        status = rec.get("status") or {}
                        if status.get("status_str") == "error":
                            raise H3GenError("ComfyUI 生成失敗：" + comfy._history_error(status))
                        if rec.get("outputs"):
                            record = rec
                            break
                else:
                    last_err = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                last_err = f"{type(e).__name__}"
            if on_tick:
                try:
                    on_tick(round(time.time() - t0, 1), queue_position(pid, base_url))
                except Exception:  # noqa: BLE001 — 回呼壞掉不該中斷等待
                    pass
            time.sleep(POLL_INTERVAL)
    elapsed = round(time.time() - t0, 1)
    if record is None:
        msg = (f"等 ComfyUI 生成影片逾時（{int(timeout)} 秒，prompt_id={pid}）。"
               f"GPU 若還在跑，完成後檔案會在 ComfyUI 的 output 目錄")
        if last_err:
            msg += f"（最後一次輪詢錯誤：{last_err}）"
        raise H3GenError(msg)

    f = find_video(record)
    if not f:
        raise H3GenError("ComfyUI 完成了，但 outputs 裡沒有影片檔（工作流少了 SaveVideo？）"
                         f"｜outputs 節點：{list((record.get('outputs') or {}).keys())}")
    params = {"filename": f["filename"], "type": f.get("type") or "output"}
    if _s(f.get("subfolder")):
        params["subfolder"] = f["subfolder"]
    try:
        with httpx.Client(timeout=300.0) as c:
            rv = c.get(f"{base_url}/view", params=params)
    except Exception as e:  # noqa: BLE001
        raise H3GenError(f"下載影片失敗（{f['filename']}）：{type(e).__name__}: {e}") from e
    if rv.status_code != 200 or not rv.content:
        raise H3GenError(f"下載影片失敗（{f['filename']}）：HTTP {rv.status_code}")

    dest_dir = storage.project_dir(slug) / "media" / "clips"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{shot_id}_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    tmp = target.with_suffix(".mp4.tmp")
    tmp.write_bytes(rv.content)
    tmp.replace(target)
    rel = f"media/clips/{target.name}"
    probe = probe_video(target)
    return {"path": rel, "abs": str(target), "elapsed": elapsed, "comfyFile": f["filename"],
            "bytes": len(rv.content), **probe}


def probe_video(path: str | Path) -> dict:
    """用 ffprobe 讀實際規格（best-effort：沒有 ffprobe 就回空值，不讓整個流程失敗）。"""
    import shutil
    import subprocess
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,r_frame_rate,duration,nb_frames",
             "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        info = json.loads(out.stdout or "{}")
    except Exception:  # noqa: BLE001
        return {}
    v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams") or [] if s.get("codec_type") == "audio"), {})
    fr = _s(v.get("r_frame_rate")) or "0/1"
    try:
        num, den = fr.split("/")
        fps = round(float(num) / float(den or 1), 3)
    except Exception:  # noqa: BLE001
        fps = None
    try:
        dur = round(float((info.get("format") or {}).get("duration") or v.get("duration") or 0), 3)
    except (TypeError, ValueError):
        dur = None
    return {"vcodec": _s(v.get("codec_name")), "acodec": _s(a.get("codec_name")),
            "width": v.get("width"), "height": v.get("height"), "fps": fps,
            "duration": dur, "hasAudio": bool(a), "frames": v.get("nb_frames")}


# ── 7. 背景任務（H3 要數十秒到數分鐘，不可同步卡 HTTP）─────────────────
JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()
_JOB_MAX_KEEP = 30


class H3JobError(Exception):
    """送件前的可預期錯誤（找不到鏡頭／已在跑）。"""


def _lora_note(info: dict) -> str:
    """影片備註／log 用的 LoRA 片段（沒有就空字串）。"""
    rows = info.get("loras") or []
    if not rows:
        return ""
    r = rows[0]
    return f"｜LoRA {r.get('name')}@{r.get('strength')}"


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _set(job: dict, **kw) -> None:
    with _JOB_LOCK:
        job.update(kw)


def get_job(job_id: str) -> dict | None:
    with _JOB_LOCK:
        j = JOBS.get(job_id)
        return copy.deepcopy(_public(j)) if j else None


def latest_job(slug: str) -> dict | None:
    with _JOB_LOCK:
        cands = [j for j in JOBS.values() if j.get("slug") == slug]
        if not cands:
            return None
        return copy.deepcopy(_public(max(cands, key=lambda j: j.get("createdAt") or "")))


def start_job(project: dict, slug: str, shot_id: str, opts: dict | None = None) -> dict:
    """建立背景任務並回狀態（含 jobId／pollUrl 由 API 層補）。"""
    opts = opts or {}
    shot = imagegen.find_shot(project, shot_id)
    if shot is None:
        raise H3JobError(f"找不到鏡頭 {shot_id}")
    with _JOB_LOCK:
        for j in JOBS.values():
            if j.get("slug") == slug and j.get("status") in ("queued", "running"):
                raise H3JobError(f"這個專案已經有一個影片任務在跑（{j['jobId']}，鏡頭 {j.get('shotId')}），"
                                 f"等它結束再送（ComfyUI 只有一張 GPU，並行只會互相排隊）")
        job_id = f"m5a-{uuid.uuid4().hex[:10]}"
        job = {
            "jobId": job_id, "slug": slug, "shotId": shot_id, "shotNo": shot.get("no"),
            "status": "queued", "phase": "排隊中", "createdAt": storage.now_iso(),
            "startedAt": "", "finishedAt": "", "elapsed": 0.0,
            "pid": "", "position": 0, "queueState": "",
            "path": "", "url": "", "error": "", "warnings": [],
            "frames": None, "width": None, "height": None, "steps": None, "seed": None, "loras": [],
            "duration": None, "timeout": _clamp_int(opts.get("timeout"), 30, 1800, int(MAX_WAIT)),
            "_opts": dict(opts),
        }
        JOBS[job_id] = job
        if len(JOBS) > _JOB_MAX_KEEP:
            for k in sorted(JOBS, key=lambda x: JOBS[x]["createdAt"]):
                if k != job_id and JOBS[k].get("status") in ("done", "failed"):
                    JOBS.pop(k, None)
                    break
    th = threading.Thread(target=_run, args=(job_id,), name=f"m5a-video-{job_id}", daemon=True)
    th.start()
    return copy.deepcopy(_public(job))


def _run(job_id: str) -> None:
    """背景單鏡生影片：最外層切換 GPU（必要時停本機 LLM），結束後 finally 一律啟回。"""
    t_all = time.time()
    with _JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        slug, shot_id = job["slug"], job["shotId"]
        opts = dict(job.pop("_opts") or {})
        job["status"] = "running"
        job["startedAt"] = storage.now_iso()
        job["phase"] = "檢查 GPU（必要時暫停本機 LLM）…"
    try:
        with imagegen.gpu_for_comfy(H3_NEED_VRAM_MB, slug, "生影片（單鏡 H3）") as gbox:
            _run_inner(job_id, slug, shot_id, opts, t_all)
        # with 結束後才寫 gpu 欄位（box['restore'] 是 finally 才填的）
        rs = gbox["restore"]
        _set(job, gpu=imagegen.gpu_public(gbox["switch"], rs),
             restoreNote=rs.get("note") or "")
    except imagegen.ImageGenError as e:
        # 含 GpuBusyError：本機 LLM 正在運算 → 這一鏡不送件
        msg = str(e)[:500]
        _set(job, status="failed",
             phase="失敗（沒有送件：本機 LLM 運算中）" if getattr(e, "busy", False) else "失敗",
             error=msg, finishedAt=storage.now_iso(), elapsed=round(time.time() - t_all, 1), position=0,
             gpu={"busy": bool(getattr(e, "busy", False)), "error": msg, "switched": False,
                  "stoppedLlama": False, "restored": False, "restartWaitedS": 0.0, "freedMB": None,
                  "autoSwitch": gpulib.auto_switch_enabled()})


def _run_inner(job_id: str, slug: str, shot_id: str, opts: dict, t_all: float) -> None:
    """真正送件＋收檔（GPU 已由外層切換好）。失敗自己記進 job，不往外拋。"""
    with _JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
    try:
        data = storage.load_project(slug)      # 每次重讀：使用者可能剛改過 prompt
        shot = imagegen.find_shot(data, shot_id)
        if shot is None:
            raise H3GenError(f"找不到鏡頭 {shot_id}（可能在任務途中被刪了）")
        sub = submit(data, shot, opts)
        info = sub["info"]
        meta = info.get("meta") or {}
        _set(job, pid=sub["pid"], phase="ComfyUI 生成中…", warnings=info.get("warnings") or [],
             promptRule=meta.get("promptRule") or "h3_video",
             promptSource=meta.get("promptSource") or "",
             promptProblems=meta.get("promptProblems") or [],
             frames=meta.get("frames"), width=meta.get("width"), height=meta.get("height"),
             steps=info.get("steps"), seed=info.get("seed"), loras=info.get("loras") or [],
             cfg=info.get("cfg"), shiftVideo=info.get("shiftVideo"), shiftAudio=info.get("shiftAudio"),
             sampler=info.get("sampler"), scheduler=info.get("scheduler"))

        def on_tick(elapsed: float, q: dict) -> None:
            _set(job, elapsed=elapsed, position=int(q.get("position") or 0),
                 queueState=_s(q.get("state")),
                 phase=("ComfyUI 生成中…" if q.get("state") == "running"
                        else (f"排隊中（第 {q.get('position')} 位）" if q.get("state") == "pending"
                              else "ComfyUI 生成中…")))

        res = wait_and_collect(sub["pid"], data, shot, opts, on_tick=on_tick)
        elapsed = round(time.time() - t_all, 1)

        # 回填 shot.video（重讀一次再寫，避免蓋掉任務途中別人的修改）
        fresh = storage.load_project(slug)
        fshot = imagegen.find_shot(fresh, shot_id)
        if fshot is None:
            raise H3GenError(f"影片已生成（{res['path']}），但寫回時找不到鏡頭 {shot_id}")
        note = (f"選用時間 {time.strftime('%H:%M:%S')}｜pid {sub['pid']}｜{res.get('vcodec') or '?'}"
                f"+{res.get('acodec') or '無音'}{_lora_note(info)}")
        fshot["video"] = {"path": res["path"], "status": "done", "taskType": "r2v",
                          "seed": info.get("seed"), "note": note,
                          "lora": (info.get("loras") or [{}])[0].get("name", ""),
                          "steps": info.get("steps"), "cfg": info.get("cfg")}
        fshot["updatedAt"] = storage.now_iso()
        pipeline = fresh.setdefault("pipeline", {})
        logs = pipeline.setdefault("logs", [])
        logs.append({
            "at": storage.now_iso(), "step": "videogen", "shotId": shot_id,
            "msg": (f"H3 r2v 影片完成：{res['path']}｜{elapsed} 秒（ComfyUI 內 {res['elapsed']} 秒）"
                    f"｜pid {sub['pid']}｜{meta.get('width')}x{meta.get('height')}"
                    f"｜frames={meta.get('frames')}（{meta.get('durationSec')}s）｜steps={info.get('steps')}"
                    f"｜seed={info.get('seed')}{_lora_note(info)}"),
            "pid": sub["pid"], "elapsed": elapsed, "path": res["path"], "lora": (info.get("loras") or [{}])[0].get("name", ""),
            "taskType": "r2v", "seed": info.get("seed"), "frames": meta.get("frames"),
            "width": meta.get("width"), "height": meta.get("height"), "steps": info.get("steps"),
            "refs": [r["path"] for r in meta.get("refs") or []],
            "promptLen": meta.get("promptChars"),
        })
        if len(logs) > 200:
            del logs[:-200]
        storage.save_project(slug, fresh)
        _set(job, status="done", phase="完成", finished_at=storage.now_iso(),
             finishedAt=storage.now_iso(), elapsed=elapsed, path=res["path"],
             url=f"/files/{slug}/{res['path']}", duration=res.get("duration"),
             vcodec=res.get("vcodec"), acodec=res.get("acodec"), hasAudio=res.get("hasAudio"),
             comfyFile=res.get("comfyFile"), bytes=res.get("bytes"), error="", position=0)
    except (H3GenError, storage.StorageError) as e:
        _set(job, status="failed", phase="失敗", error=str(e)[:500],
             finishedAt=storage.now_iso(), elapsed=round(time.time() - t_all, 1), position=0)
    except Exception as e:  # noqa: BLE001 — 背景任務不能讓整個行程炸掉
        _set(job, status="failed", phase="失敗",
             error=f"未預期錯誤：{type(e).__name__}: {e}"[:500],
             finishedAt=storage.now_iso(), elapsed=round(time.time() - t_all, 1), position=0)


# ══════════════════════════════════════════════════════════════════
# M5b：多鏡批次生影片（依序、不並行、每鏡完成即寫回、失敗不中斷）
#
# 為什麼另開一份 VIDEO_BATCH_JOBS 而不是重用 start_job：start_job 的規則是「同專案同時間只能
# 有一個影片任務」，批次要自己排隊跑 N 鏡；重用會讓第 2 鏡直接被自己擋掉。
# 兩者共用 submit() / wait_and_collect()，所以單鏡與批次送出的 payload 完全一致。
# ══════════════════════════════════════════════════════════════════
VIDEO_BATCH_JOBS: dict[str, dict] = {}
_VB_LOCK = threading.Lock()
_VB_MAX_KEEP = 20


def video_preset(opts: dict | None = None) -> dict:
    """批次預設參數：720x1280／121 幀（5 秒）／steps 12 —— 品質對照後挑的實用值。

    opts 給的欄位優先；None/空字串一律視為「用預設」。
    """
    opts = opts or {}
    width = _clamp_int(opts.get("width"), 32, 8192, 720)
    height = _clamp_int(opts.get("height"), 32, 8192, 1280)
    steps = _clamp_int(opts.get("steps"), 1, 60, int(VIDEO_GEN_DEFAULTS["steps"]))
    cfg = _clamp_float(opts.get("cfg"), 0.0, 30.0, float(VIDEO_GEN_DEFAULTS["cfg"]))
    out = {"width": width, "height": height, "steps": steps, "cfg": cfg}
    # v0.9.22：使用者沒指定 frames 時**不要塞預設值**——讓 build_timeline 依 shot.durationSec
    # 推算（12 秒的鏡才是 12 秒）。以前這裡一律塞 121 幀＝5 秒，分鏡表寫 12 秒的鏡
    # 生出來只有 5 秒，台詞一定唸不完（V4 變成裝飾）。
    frames = opts.get("frames")
    if frames not in (None, ""):
        legal = legal_frames(_clamp_int(frames, 5, 100000, 121))
        out["frames"] = legal
        out["durationSec"] = round(legal / FRAME_RATE, 3)
    for k in ("shiftVideo", "shiftAudio", "sampler", "scheduler"):
        if opts.get(k) not in (None, ""):
            out[k] = opts[k]
    rows = lora_rows(opts.get("lora"))
    out["lora"] = rows[0] if rows else None
    for k in ("seed", "timeout"):
        if opts.get(k) not in (None, ""):
            out[k] = opts[k]
    if "useFirstFrame" in opts:
        out["useFirstFrame"] = bool(opts.get("useFirstFrame"))
    return out


def _vb_public(job: dict) -> dict:
    return copy.deepcopy({k: v for k, v in job.items() if not k.startswith("_")})


def get_video_batch(job_id: str) -> dict | None:
    with _VB_LOCK:
        j = VIDEO_BATCH_JOBS.get(job_id)
        return _vb_public(j) if j else None


def latest_video_batch(slug: str) -> dict | None:
    with _VB_LOCK:
        cands = [j for j in VIDEO_BATCH_JOBS.values() if j.get("slug") == slug]
        if not cands:
            return None
        return _vb_public(max(cands, key=lambda j: (j.get("createdAt") or "", j.get("jobId") or "")))


def _vb_precheck(project: dict, shot_id: str, opts: dict) -> tuple[bool, str, list[str]]:
    """不看 GPU 的前置檢查：有沒有提示詞、有沒有可用參考圖（占位圖不算）。

    回 (ok, 擋下的原因, 警告清單)。真正送件時 submit() 會再檢查一次（那時才上傳圖）。
    """
    shot = imagegen.find_shot(project, shot_id)
    if shot is None:
        return False, f"找不到鏡頭 {shot_id}", []
    if not _s(shot.get("promptEn")):
        # v0.9.21：promptEn 空不等於送不出——送件口會自動用模板編譯（使用者定調：不擋人）
        try:
            _compiled, _cw, _meta = _h3compose().compile_prompt(project, shot)
        except Exception:  # noqa: BLE001
            _compiled = ""
        if not _s(_compiled):
            return False, ("這鏡沒有可用的提示詞：promptEn 空白，模板編譯也產不出來"
                           "（通常是人物卡與場景卡都沒內容）"), []
    try:
        refs, warns, _notes = collect_refs(project, shot, allow_first_frame=bool(opts.get("useFirstFrame", True)))
    except H3GenError as e:
        return False, str(e), []
    if not refs:
        return False, "這鏡沒有可用參考圖（角色/場景都是占位圖、也沒有首幀）", warns
    return True, "", warns


def start_video_batch(project: dict, slug: str, shot_ids: list[str],
                      opts: dict | None = None) -> dict:
    """建立批次生影片任務並在背景依序跑。回 job（含 jobId）。

    opts: preset{width,height,frames,steps,cfg,seed} / trialCount / useFirstFrame / timeout
    """
    opts = opts or {}
    ids = [_s(x) for x in (shot_ids or []) if _s(x)]
    if not ids:
        raise H3JobError("沒有選任何鏡頭：請先勾選鏡頭，或用「本專案全部」範圍")
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    all_shots = [s for s in (project.get("shots") or []) if isinstance(s, dict)]
    unknown = [i for i in ordered if not any(s.get("id") == i for s in all_shots)]
    if unknown:
        raise H3JobError("找不到這些鏡頭：" + "、".join(unknown))
    ordered.sort(key=lambda i: next(k for k, s in enumerate(all_shots) if s.get("id") == i))

    pre = opts.get("preset") if isinstance(opts.get("preset"), dict) else opts
    preset = video_preset({**(pre or {}), "useFirstFrame": opts.get("useFirstFrame", True)})
    trial = _clamp_int(opts.get("trialCount"), 0, 99, 0)   # 0＝不限制（全部照排）
    requested = list(ordered)
    if trial and len(ordered) > trial:
        ordered = ordered[:trial]        # 「先試 N 鏡」：只排前 N 鏡

    items: list[dict] = []
    preblocked: list[dict] = []
    for i in ordered:
        sh = next(s for s in all_shots if s.get("id") == i)
        ok, why, warns = _vb_precheck(project, i, preset)
        items.append({
            "shotId": i, "no": sh.get("no"), "status": "pending" if ok else "blocked",
            "phase": "", "path": "", "url": "", "pid": "", "elapsed": 0.0,
            "queuePosition": 0, "error": "" if ok else why,
            "warnings": warns if ok else ([why] + warns),
            "width": None, "height": None, "frames": None, "steps": None,
            "duration": None, "bytes": None,
        })
        if not ok:
            preblocked.append({"shotId": i, "no": sh.get("no"), "error": why})

    run_ids = [it["shotId"] for it in items if it["status"] == "pending"]
    with _VB_LOCK:
        for j in VIDEO_BATCH_JOBS.values():
            if j.get("slug") == slug and j.get("status") in ("queued", "running"):
                raise H3JobError(f"這個專案已經有一個批次生影片在跑（{j.get('jobId')}，"
                                 f"進度 {j.get('done')}/{j.get('total')}），等它跑完再送"
                                 f"（ComfyUI 只有一張 GPU，並行只會互相排隊）")
        job_id = f"m5b-{uuid.uuid4().hex[:10]}"
        job = {
            "jobId": job_id, "slug": slug, "status": "queued",
            "createdAt": storage.now_iso(), "startedAt": "", "finishedAt": "",
            "preset": preset, "trialCount": trial,
            "requested": len(requested), "total": len(items),
            "skippedByTrial": len(requested) - len(ordered) if trial else 0,
            "done": 0, "okCount": 0, "failCount": 0, "blockedCount": len(preblocked),
            "elapsed": 0.0, "current": "", "currentIndex": 0, "remaining": len(run_ids),
            "runnable": len(run_ids), "phase": "", "pid": "",
            "items": items, "preblocked": preblocked, "_runIds": run_ids,
        }
        VIDEO_BATCH_JOBS[job_id] = job
        if len(VIDEO_BATCH_JOBS) > _VB_MAX_KEEP:
            for k in sorted(VIDEO_BATCH_JOBS, key=lambda x: VIDEO_BATCH_JOBS[x]["createdAt"]):
                if k != job_id and VIDEO_BATCH_JOBS[k].get("status") in ("done", "failed", "partial", "cancelled"):
                    VIDEO_BATCH_JOBS.pop(k, None)
                    break
    th = threading.Thread(target=_run_video_batch, args=(job_id,), name=f"m5b-batch-{job_id}",
                          daemon=True)
    th.start()
    return _vb_public(job)


def _vb_set(job_id: str, fn) -> None:
    """在鎖內就地改任務（fn(job)）。"""
    with _VB_LOCK:
        j = VIDEO_BATCH_JOBS.get(job_id)
        if j is not None:
            fn(j)


def _vb_status(job_id: str) -> str:
    with _VB_LOCK:
        j = VIDEO_BATCH_JOBS.get(job_id)
        return _s((j or {}).get("status"))


def _vb_item(job: dict, sid: str) -> dict | None:
    return next((x for x in job.get("items") or [] if x.get("shotId") == sid), None)


def _run_video_batch(job_id: str) -> None:
    """多鏡批次生影片：整個批次只在最外層切換一次 GPU（不逐鏡停啟本機 LLM）。"""
    t_all = time.time()
    with _VB_LOCK:
        job = VIDEO_BATCH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["startedAt"] = storage.now_iso()
        job["phase"] = "檢查 GPU（必要時暫停本機 LLM）…"
        slug = job["slug"]
    try:
        with imagegen.gpu_for_comfy(H3_NEED_VRAM_MB, slug, "批次生影片（H3）") as gbox:
            _run_video_batch_items(job_id, t_all)
        rs = gbox["restore"]
        _vb_set(job_id, lambda j: j.update(gpu=imagegen.gpu_public(gbox["switch"], rs),
                                           restoreNote=rs.get("note") or ""))
    except imagegen.ImageGenError as e:
        msg = str(e)[:500]
        busy = bool(getattr(e, "busy", False))

        def _blocked(j: dict):
            j["status"] = "failed"
            j["phase"] = "沒有送件（本機 LLM 運算中）" if busy else "GPU 切換失敗"
            j["error"] = msg
            j["finishedAt"] = storage.now_iso()
            j["elapsed"] = round(time.time() - t_all, 1)
            j["gpu"] = {"busy": busy, "error": msg, "switched": False, "stoppedLlama": False,
                        "restored": False, "restartWaitedS": 0.0, "freedMB": None,
                        "autoSwitch": gpulib.auto_switch_enabled()}
            for it in j.get("items") or []:
                if it.get("status") in ("pending", None):
                    it["status"] = "skipped"
                    it["error"] = "本機 LLM 運算中，整批未送件"
            j.pop("_runIds", None)
        _vb_set(job_id, _blocked)


def _run_video_batch_items(job_id: str, t_all: float) -> None:
    with _VB_LOCK:
        job = VIDEO_BATCH_JOBS.get(job_id)
        if not job:
            return
        job["phase"] = "批次執行中"
        slug = job["slug"]
        preset = dict(job["preset"])
        run_ids = list(job.get("_runIds") or [])
    if not run_ids and job.get("status") == "running":
        # 空批次也要收尾，否則前端 poll 永遠停在「檢查 GPU」
        with _VB_LOCK:
            job["status"] = "failed" if job.get("failCount") else "done"
            job["finishedAt"] = storage.now_iso()
            job["phase"] = "沒有要跑的鏡頭"
            job["elapsed"] = round(time.time() - t_all, 1)
        return
    total_run = len(run_ids)

    for idx, shot_id in enumerate(run_ids):
        if _vb_status(job_id) == "cancelled":
            break

        def _mark(j: dict, sid=shot_id, i=idx):
            it = _vb_item(j, sid)
            if it is not None:
                it["status"] = "running"
                it["phase"] = "送件中…"
            j["current"] = sid
            j["currentIndex"] = i + 1
            j["remaining"] = max(0, total_run - i - 1)
            j["phase"] = f"第 {i + 1}/{total_run} 鏡：{sid}"
        _vb_set(job_id, _mark)

        t0 = time.time()
        try:
            data = storage.load_project(slug)          # 每鏡重讀：使用者可能剛改過 prompt
            shot = imagegen.find_shot(data, shot_id)
            if shot is None:
                raise H3GenError(f"找不到鏡頭 {shot_id}（可能在批次途中被刪了）")
            sub = submit(data, shot, dict(preset))
            info = sub["info"]
            meta = info.get("meta") or {}

            def _sent(j: dict, sid=shot_id, info=info, meta=meta, sub=sub):
                it = _vb_item(j, sid)
                if it is not None:
                    it.update({"pid": sub["pid"], "phase": "ComfyUI 生成中…",
                               "width": meta.get("width"), "height": meta.get("height"),
                               "frames": meta.get("frames"), "steps": info.get("steps"),
                               "loras": info.get("loras") or [],
                               "warnings": info.get("warnings") or []})
                j["pid"] = sub["pid"]
            _vb_set(job_id, _sent)

            def on_tick(elapsed: float, q: dict, sid=shot_id) -> None:
                state = _s(q.get("state"))
                phase = ("ComfyUI 生成中…" if state == "running"
                         else (f"排隊中（第 {q.get('position')} 位）" if state == "pending"
                               else "ComfyUI 生成中…"))
                pos = int(q.get("position") or 0)

                def _tick(j: dict):
                    it = _vb_item(j, sid)
                    if it is not None:
                        it["phase"] = phase
                        it["queuePosition"] = pos
                _vb_set(job_id, _tick)

            res = wait_and_collect(sub["pid"], data, shot, dict(preset), on_tick=on_tick)
            elapsed = round(time.time() - t0, 1)

            # 回填 shot.video（重讀再寫，避免蓋掉批次途中別人的修改），並記 pipeline.log
            fresh = storage.load_project(slug)
            fshot = imagegen.find_shot(fresh, shot_id)
            if fshot is None:
                raise H3GenError(f"影片已生成（{res['path']}），但寫回時找不到鏡頭 {shot_id}")
            fshot["video"] = {
                "path": res["path"], "status": "done", "taskType": "r2v", "seed": info.get("seed"),
                "note": (f"選用時間 {time.strftime('%H:%M:%S')}｜pid {sub['pid']}｜"
                         f"{res.get('vcodec') or '?'}+{res.get('acodec') or '無音'}{_lora_note(info)}｜batch {job_id}"),
                "lora": (info.get("loras") or [{}])[0].get("name", ""),
                "steps": info.get("steps"), "cfg": info.get("cfg"),
            }
            fshot["updatedAt"] = storage.now_iso()
            logs = fresh.setdefault("pipeline", {}).setdefault("logs", [])
            logs.append({
                "at": storage.now_iso(), "step": "videogen", "shotId": shot_id, "batchJob": job_id,
                "msg": (f"H3 r2v 影片完成（批次 {job_id}）：{res['path']}｜{elapsed} 秒"
                        f"（ComfyUI 內 {res['elapsed']} 秒）｜pid {sub['pid']}"
                        f"｜{meta.get('width')}x{meta.get('height')}"
                        f"｜frames={meta.get('frames')}（{meta.get('durationSec')}s）"
                        f"｜steps={info.get('steps')}｜seed={info.get('seed')}{_lora_note(info)}"),
                "pid": sub["pid"], "elapsed": elapsed, "path": res["path"], "lora": (info.get("loras") or [{}])[0].get("name", ""),
                "taskType": "r2v", "seed": info.get("seed"), "frames": meta.get("frames"),
                "width": meta.get("width"), "height": meta.get("height"), "steps": info.get("steps"),
                "refs": [r["path"] for r in meta.get("refs") or []],
                "promptLen": meta.get("promptChars"),
            })
            if len(logs) > 200:
                del logs[:-200]
            storage.save_project(slug, fresh)

            def _done(j: dict, sid=shot_id, res=res, elapsed=elapsed):
                it = _vb_item(j, sid)
                if it is not None:
                    it.update({"status": "done", "phase": "完成", "path": res["path"],
                               "url": f"/files/{slug}/{res['path']}", "elapsed": elapsed,
                               "duration": res.get("duration"), "bytes": res.get("bytes"),
                               "queuePosition": 0, "error": ""})
                j["okCount"] += 1
                j["done"] += 1
                j["elapsed"] = round(time.time() - t_all, 1)
            _vb_set(job_id, _done)
        except (H3GenError, storage.StorageError) as e:
            msg = str(e)[:500]
            _vb_fail(job_id, shot_id, msg, t0, t_all)
            _vb_log_fail(slug, shot_id, msg, job_id)
        except Exception as e:  # noqa: BLE001 — 單鏡未預期錯誤不該讓整批停下
            msg = f"未預期錯誤：{type(e).__name__}: {e}"[:500]
            _vb_fail(job_id, shot_id, msg, t0, t_all)
            _vb_log_fail(slug, shot_id, msg, job_id)

    def _finish(j: dict):
        if j.get("status") != "cancelled":
            if j["failCount"] == 0 and j["okCount"] > 0:
                j["status"] = "done"
            elif j["okCount"] == 0:
                j["status"] = "failed"
            else:
                j["status"] = "partial"
        j["finishedAt"] = storage.now_iso()
        j["current"] = ""
        j["remaining"] = 0
        j["phase"] = "已結束"
        j["elapsed"] = round(time.time() - t_all, 1)
        j.pop("_runIds", None)
    _vb_set(job_id, _finish)


def _vb_fail(job_id: str, shot_id: str, msg: str, t0: float, t_all: float) -> None:
    def _f(j: dict):
        it = _vb_item(j, shot_id)
        if it is not None:
            it.update({"status": "failed", "phase": "失敗", "error": msg,
                       "elapsed": round(time.time() - t0, 1), "queuePosition": 0})
        j["failCount"] += 1
        j["done"] += 1
        j["elapsed"] = round(time.time() - t_all, 1)
    _vb_set(job_id, _f)


def _vb_log_fail(slug: str, shot_id: str, msg: str, job_id: str) -> None:
    try:
        fresh = storage.load_project(slug)
        fresh.setdefault("pipeline", {}).setdefault("logs", []).append({
            "at": storage.now_iso(), "step": "videogen", "shotId": shot_id,
            "msg": f"H3 r2v 影片失敗（批次 {job_id}）：{msg}", "error": msg, "batchJob": job_id,
        })
        storage.save_project(slug, fresh)
    except (storage.StorageError, OSError):
        pass


def cancel_video_batch(job_id: str) -> dict | None:
    """取消：只停止「還沒開始」的鏡頭（正在跑的那鏡讓它跑完，避免佔著 GPU 白跑、留半成品）。"""
    def _c(j: dict):
        for it in j["items"]:
            if it["status"] == "pending":
                it["status"] = "skipped"
                it["phase"] = "已取消"
        if j.get("status") in ("queued", "running"):
            j["status"] = "cancelled"
            j["phase"] = "已取消（正在跑的那鏡會跑完）"
    _vb_set(job_id, _c)
    return get_video_batch(job_id)
