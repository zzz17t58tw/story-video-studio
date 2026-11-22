"""一鍵佈署：故事 → 角色卡 / 場景卡 / 劇本（集·場·對白）/ 分鏡（六段式），**只建文字、不產任何圖片**。

四步各自呼叫一次模型（json_object 模式），每步都驗結構、失敗把錯誤回饋給模型重試一次：

  1. cast      角色卡（name/role/appearance 六欄/features/promptEn/identityAnchors 六鍵/outfits/background/voice）
  2. locations 場景卡（promptEn 必含「畫面不得有人」禁人語）
  3. script    集 → 場次（引用第 1、2 步的真實 id）→ beats（dialogue/action）
  4. shots     每場次拆鏡（spec/angle/cameraMove/durationSec/promptZh/promptEn 六段式/dialogLineRefs 全集覆蓋）

寫入一律走 storage（原子替換 + validate_project）；本模組自己只做「內容層」驗證。
圖片欄位（characters.refImages、scenes.refImages、shots.images、shots.video、shots.stages）一律留空。
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid

from . import h3compose, llm, prompts, storage, storygen

STEPS = ("cast", "locations", "script", "shots")
STEP_LABEL = {"cast": "角色卡", "locations": "場景卡", "script": "劇本", "shots": "分鏡",
              "done": "完成", "failed": "失敗", "queued": "排隊中"}
MAX_TOKENS = 8192          # storygen 實測：4096 會被思考內容吃光（finish_reason=length）
ATTEMPTS = 3               # 每個步驟最多 3 次（本機模型截斷是機率問題，2 次不夠穩）
FRAME_MIN, FRAME_MAX = 124, 362   # H3 訓練域（17k+5）
SCENE_NO_HUMAN_HINT = "no people in silhouette / in reflection"

_CAMERA_ON_PERSON = ("static", "push_in", "pull_out", "handheld")
_ANGLE_BY_SCALE = {"close_up": ("eye_level", "front"), "medium": ("eye_level", "front_left"),
                   "wide": ("high", "front"), "over_shoulder": ("eye_level", "front_right"),
                   "pov": ("eye_level", "front")}


class DeployError(Exception):
    """可預期的佈署錯誤 → API 轉中文 400/502。"""

    def __init__(self, message: str, status: int = 400, step: str = "", raw: str = ""):
        super().__init__(message)
        self.status = status
        self.step = step
        self.raw = raw or ""


# ── 參數 ───────────────────────────────────────────────────────
def norm_opts(opts: dict | None) -> dict:
    """夾住使用者參數（前端給什麼都不會讓後端爆掉）。"""
    o = dict(opts or {})

    def _int(key, default, lo, hi):
        try:
            v = int(float(o.get(key)))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(v, hi))

    def _float(key, default, lo, hi):
        try:
            v = float(o.get(key))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(v, hi))

    return {
        "characterCount": _int("characterCount", 3, 1, 8),
        "sceneCount": _int("sceneCount", 2, 1, 8),
        "shotCount": _int("shotCount", 6, 1, 24),   # 語意＝整個劇本的**總**鏡數（由 allocate_shots 分配到各場）
        "shotSeconds": round(_float("shotSeconds", 5, 3.0, 15.0), 2),
        "overwrite": bool(o.get("overwrite", False)),
        "dryRun": bool(o.get("dryRun", False)),
    }


def allocate_shots(total: int, n_scene: int) -> tuple[list[int], list[str]]:
    """把「總鏡數」平均分配到 n_scene 場：除不盡的餘數從第一場開始各多 1。

    回 (每場鏡數, warnings)。總數 < 場次數時，每場至少 1 鏡（實際總數會大於使用者填的數字）。
    """
    n = max(1, int(n_scene))
    total = max(1, int(total))
    warns: list[str] = []
    if total < n:
        warns.append(f"總鏡數 {total} 少於場次數 {n}：已改成每場 1 鏡（實際 {n} 鏡）")
        total = n
    base, rem = divmod(total, n)
    alloc = [base + (1 if i < rem else 0) for i in range(n)]
    return alloc, warns


def _image_blockers(slug: str, data: dict) -> list[str]:
    """overwrite=True 會連 media 檔一起讓位；先檢查有沒有正在跑的生成任務。"""
    try:
        from . import imagegen
        job = imagegen.latest_batch(slug)
    except Exception:  # noqa: BLE001 —— 生圖模組匯入失敗不該擋佈署
        job = None
    if job and job.get("status") in ("queued", "running"):
        return [f"這個專案有批次生圖在跑（{job.get('jobId')}，{job.get('done')}/{job.get('total')}），先等它跑完再覆蓋"]
    return []


# ── 清理（overwrite）───────────────────────────────────────────
def _purge(project: dict) -> dict:
    """清空角色／場景／劇本／鏡頭（就地改），回被清掉的舊資料（給呼叫端刪 media 檔）。"""
    old = {
        "characters": [c for c in (project.get("characters") or []) if isinstance(c, dict)],
        "scenes": [s for s in (project.get("scenes") or []) if isinstance(s, dict)],
        "episodes": [e for e in ((project.get("script") or {}).get("episodes") or []) if isinstance(e, dict)],
        "shots": [s for s in (project.get("shots") or []) if isinstance(s, dict)],
    }
    project["characters"] = []
    project["scenes"] = []
    project["script"] = {"episodes": []}
    project["shots"] = []
    return old


def _media_paths(old: dict) -> list[str]:
    out: list[str] = []
    for c in old.get("characters") or []:
        out += [r.get("path") for r in (c.get("refImages") or []) if isinstance(r, dict) and r.get("path")]
        out += [st.get("characterSheet") for st in (c.get("stages") or []) if isinstance(st, dict) and st.get("characterSheet")]
    for s in old.get("scenes") or []:
        out += [r.get("path") for r in (s.get("refImages") or []) if isinstance(r, dict) and r.get("path")]
    for sh in old.get("shots") or []:
        img = sh.get("images") or {}
        out += [img.get("first"), img.get("last")]
        out.append((sh.get("video") or {}).get("path"))
    return [p for p in out if isinstance(p, str) and p.strip()]


def _delete_media(slug: str, paths: list[str]) -> int:
    """只刪「本專案 media/ 底下」的舊檔（防路徑穿越）；回刪掉幾個。"""
    root = storage.project_dir(slug).resolve()
    n = 0
    for rel in paths:
        try:
            target = (root / rel).resolve()
            if not str(target).startswith(str(root)) or not target.is_file():
                continue
            target.unlink()
            n += 1
        except OSError:
            continue
    return n


# ── 共用驗證小工具 ─────────────────────────────────────────────
def _hex_ok(v) -> str:
    s = str(v or "").strip().upper()
    return s if re.fullmatch(r"#[0-9A-F]{6}", s) else ""


_GENDERS = ("男", "女", "其他")


def _norm_gender(raw) -> str:
    """性別只留枚舉值。模型常回「男性，喉結明顯」這種長句，會讓前端下拉選單顯示成空白。"""
    t = str(raw or "").strip()
    if t in _GENDERS:
        return t
    if t.startswith("男"):
        return "男"
    if t.startswith("女"):
        return "女"
    return "其他"


# 中文／英文色彩詞 → 近似 Hex（只當模型給了非法值時的保底，優先仍用模型給的合法值）
_COLOR_WORDS = (
    (("黑", "black"), "#1A1614"), (("深褐", "dark brown"), "#2E1E14"), (("褐", "棕", "brown"), "#5A3A22"),
    (("金", "blond", "golden"), "#C9A24B"), (("紅", "red", "crimson"), "#8C2B2B"),
    (("藍", "blue"), "#243E5C"), (("深藍", "navy"), "#1C2A44"), (("灰", "grey", "gray"), "#4A4A4A"),
    (("白", "white"), "#E6E2DA"), (("米", "beige", "cream"), "#D8C7A8"), (("綠", "green"), "#2F4A32"),
    (("紫", "purple"), "#4A2E52"), (("橘", "橙", "orange"), "#B4642A"), (("黃", "yellow"), "#C8B24A"),
)
_SKIN_WORDS = ((("蒼白", "白皙", "pale", "fair"), "#EBCDB0"), (("偏黃", "黃", "tan", "olive"), "#D9B48C"),
               (("小麥", "古銅", "bronze", "dark skin"), "#A9764C"), (("黑", "deep", "dark"), "#7A5230"))
_DEFAULT_COLORS = {"hair": "#1A1410", "eyes": "#3A2A20", "skin": "#E8C9A8", "primaryOutfit": "#2B2B2B"}


def guess_hex(text: str, default: str, table=_COLOR_WORDS) -> str:
    """從描述文字猜一個 Hex（先比長詞再比短詞，避免「深藍」被「藍」搶先）。"""
    t = str(text or "").lower()
    best = None
    for words, hexv in table:
        for w in words:
            if w and w in t and (best is None or len(w) > best[0]):
                best = (len(w), hexv)
    return best[1] if best else default


def _fill_hex_anchors(anchors: dict, appearance: dict, outfit_prompt: str) -> tuple[dict, list[str]]:
    """四個 Hex 錨點不合格時從外型文字推一個相近色（只確保格式合法，未填的才補）。"""
    ca = anchors.get("colorAnchors") if isinstance(anchors.get("colorAnchors"), dict) else {}
    fallback = {
        "hair": guess_hex(appearance.get("hair"), _DEFAULT_COLORS["hair"]),
        "eyes": guess_hex(appearance.get("face"), _DEFAULT_COLORS["eyes"]),
        "skin": guess_hex(appearance.get("skin"), _DEFAULT_COLORS["skin"], _SKIN_WORDS + _COLOR_WORDS),
        "primaryOutfit": guess_hex(outfit_prompt, _DEFAULT_COLORS["primaryOutfit"]),
    }
    warns: list[str] = []
    out = {}
    for k in ("hair", "eyes", "skin", "primaryOutfit"):
        got = _hex_ok(ca.get(k))
        if got:
            out[k] = got
        else:
            out[k] = fallback.get(k) or _DEFAULT_COLORS[k]
            warns.append(f"colorAnchors.{k} 不是合法 #RRGGBB，已由外型描述推算為 {out[k]}")
    return out, warns


def _norm_angle(raw) -> dict:
    """spec.angle：dict 取三鍵、字串塞 h、其餘留空（storage.fill_project_defaults 的相容規則）。"""
    if isinstance(raw, dict):
        return {k: str(raw.get(k) or "") for k in ("h", "v", "s")}
    if isinstance(raw, str) and raw.strip():
        return {"h": raw.strip(), "v": "", "s": ""}
    return {"h": "", "v": "", "s": ""}


# ── 步驟 1：角色卡 ─────────────────────────────────────────────
def _cast_prompt(project: dict, opts: dict, next_id: str) -> tuple[str, str]:
    story = project.get("story") or {}
    style = story.get("style") or {}
    chapters = [ch for ch in story.get("chapters") or [] if isinstance(ch, dict)]
    chap_txt = "\n".join(
        f"  {ch.get('id')}｜{ch.get('title') or '（無標題）'}｜{(ch.get('summary') or (ch.get('content') or ''))[:200]}"
        for ch in chapters) or "  （尚未寫章回，請只依大綱）"
    n = opts["characterCount"]
    system = f"""你是短劇的選角與美術總監。要依故事設計 {n} 張「人物卡」，讓後續生圖／生影片能鎖住同一張臉。

【故事概念】{story.get('premise') or '（未填）'}
【故事大綱】{(story.get('synopsis') or '（未填）')[:1200]}
【章回】\n{chap_txt}
【風格】類型 {style.get('genre') or '—'}／基調 {style.get('tone') or '—'}／視覺 {style.get('visualStyle') or '—'}
【畫面比例】{(project.get('meta') or {}).get('aspectRatio') or '9:16'}

【規則】
- 恰好 {n} 張人物卡，覆蓋大綱裡真正有戲的人（主角 1 位＋其餘配角）；不要湊人數。
- appearance 的 age/skin/face/hair/body 各 10–30 字中文，**具體可拍**（不要「很美」「帥氣」這類空話）。
- appearance.gender **只能填「男」「女」「其他」其中一個**（不准加任何描述；體態細節寫進 body）。
- appearance.features 2–3 條辨識特徵（疤、痣、飾品、慣用動作造成的痕跡），之後每個鏡頭都要靠它認人。
- appearance.promptEn 一整段英文（逗號分隔、不換行、60–120 字），順序 age/gender → face → hair → body → 辨識特徵 → 光線與風格。
- identityAnchors 六個鍵**齊全且非空**（每項 1 句英文或中文短語即可）；colorAnchors 四個值必須是 #RRGGBB（例：#1A1A1A），不得留白或寫顏色名。
- outfits 只給 1 套：name 中文、desc 中文 15–40 字（材質、顏色、配件）、promptEn 英文服裝描述（30–60 字）。
- voice.desc 一句音色建議（含語速與音域）。
- 不要寫英文以外的 promptEn；不要在 promptEn 寫中文名。
- **輸出要精簡**：整份 JSON 不要超過 3000 字，不要重複同一句描述。

【輸出】只回純 JSON 物件（不要 markdown 圍欄、不要說明）：
{{"characters":[
  {{"name":"姓名","aliases":["別名"],"role":"主角或配角",
    "appearance":{{"age":"","gender":"","skin":"","face":"","hair":"","body":"","features":[""],"promptEn":""}},
    "identityAnchors":{{"faceShape":"","facialFeatures":"","uniqueMarks":"",
      "colorAnchors":{{"hair":"#RRGGBB","eyes":"#RRGGBB","skin":"#RRGGBB","primaryOutfit":"#RRGGBB"}},
      "skinTexture":"","hairStyle":""}},
    "outfits":[{{"name":"","desc":"","promptEn":""}}],
    "voice":{{"desc":""}}}}]}}
（id 由程式編，不要自己填 id；第一張會是 {next_id} 起算）"""
    user = f"請依上面的故事產出 {n} 張人物卡 JSON。"
    return system, user


def gen_cast(project: dict, cfg: dict, opts: dict, first_seq: int = 1) -> dict:
    """回 {characters:[…(未寫檔，id 已編好)], warnings}。"""
    n = opts["characterCount"]
    sysmsg, user = _cast_prompt(project, opts, f"c{first_seq:02d}")

    def validate(data) -> tuple[dict, list]:
        if isinstance(data, list):
            data = {"characters": data}
        if not isinstance(data, dict):
            raise storygen.GenError("最外層必須是 JSON 物件（{characters:[…]}）")
        chars = data.get("characters")
        if not isinstance(chars, list) or not chars:
            raise storygen.GenError("缺少 characters 陣列或為空")
        warns: list[str] = []
        out: list[dict] = []
        seen_names: set[str] = set()
        for i, c in enumerate(chars):
            if not isinstance(c, dict):
                raise storygen.GenError(f"第 {i + 1} 張人物卡不是物件")
            name = str(c.get("name") or "").strip()
            if not name:
                raise storygen.GenError(f"第 {i + 1} 張人物卡缺 name")
            if name in seen_names:
                raise storygen.GenError(f"人物姓名重複：{name}（每位角色要有獨立姓名）")
            seen_names.add(name)
            app_in = c.get("appearance") if isinstance(c.get("appearance"), dict) else {}
            app = {}
            for k in ("age", "gender", "skin", "face", "hair", "body"):
                app[k] = str(app_in.get(k) or "").strip()
                if not app[k]:
                    raise storygen.GenError(f"「{name}」的 appearance.{k} 是空的（六欄都要填）")
            raw_gender = app["gender"]
            app["gender"] = _norm_gender(raw_gender)
            if app["gender"] != raw_gender:
                warns.append(f"{name}：性別「{raw_gender[:20]}」不是枚舉值，已收斂為「{app['gender']}」")
            feats = [str(x).strip() for x in (app_in.get("features") or []) if str(x or "").strip()]
            if not feats:
                raise storygen.GenError(f"「{name}」的 appearance.features 至少要有 1 條辨識特徵")
            app["features"] = feats[:6]
            app["promptEn"] = str(app_in.get("promptEn") or "").strip()
            if not app["promptEn"]:
                raise storygen.GenError(f"「{name}」缺 appearance.promptEn（英文外型描述）")
            if re.search(r"[\u4e00-\u9fff]", app["promptEn"]):
                raise storygen.GenError(f"「{name}」的 appearance.promptEn 含中文，必須是純英文")

            outfits_in = c.get("outfits") if isinstance(c.get("outfits"), list) else []
            outfits = []
            for j, o in enumerate(outfits_in[:2]):
                if not isinstance(o, dict):
                    continue
                outfits.append({"id": f"o{j + 1:02d}", "name": str(o.get("name") or f"造型 {j + 1}").strip(),
                                "desc": str(o.get("desc") or "").strip(),
                                "promptEn": str(o.get("promptEn") or "").strip()})
            if not outfits or not outfits[0]["promptEn"]:
                raise storygen.GenError(f"「{name}」至少要有 1 套服裝，且 outfits[0].promptEn（英文）不可為空")

            anchors_in = c.get("identityAnchors") if isinstance(c.get("identityAnchors"), dict) else {}
            for k in ("faceShape", "facialFeatures", "uniqueMarks", "skinTexture", "hairStyle"):
                if not str(anchors_in.get(k) or "").strip():
                    raise storygen.GenError(f"「{name}」的 identityAnchors.{k} 是空的（六鍵都要填）")
            colors, cwarn = _fill_hex_anchors(anchors_in, app, outfits[0]["promptEn"])
            warns += [f"{name}：{w}" for w in cwarn]
            anchors = {
                "faceShape": str(anchors_in.get("faceShape")).strip(),
                "facialFeatures": str(anchors_in.get("facialFeatures")).strip(),
                "uniqueMarks": str(anchors_in.get("uniqueMarks")).strip(),
                "colorAnchors": colors,
                "skinTexture": str(anchors_in.get("skinTexture")).strip(),
                "hairStyle": str(anchors_in.get("hairStyle")).strip(),
            }
            voice_in = c.get("voice") if isinstance(c.get("voice"), dict) else {}
            out.append({
                "name": name,
                "aliases": [str(a).strip() for a in (c.get("aliases") or []) if str(a or "").strip()][:4],
                "role": str(c.get("role") or "配角").strip(),
                "appearance": app, "outfits": outfits,
                "voice": {"desc": str(voice_in.get("desc") or "").strip(), "refAudio": "", "engine": "", "note": ""},
                "identityAnchors": anchors,
                "refImages": [],          # ← 圖片一律留空（等使用者上傳或按生成）
                "lockedFace": False,
                "stages": [],             # ← 分階段造型圖也留空
            })
        if len(out) != n:
            warns.append(f"要求 {n} 位角色，模型給了 {len(out)} 位")
        for k, c in enumerate(out):
            c["id"] = f"c{first_seq + k:02d}"
        return ({"characters": out}, warns)

    # 本機小模型偶爾會把輸出截在字串中間（取樣問題）→ 一鍵佈署給 3 次機會，別讓 4 步裡任一步白跑
    return storygen._run(cfg, sysmsg, user, validate, attempts=ATTEMPTS)


# ── 步驟 2：場景卡 ─────────────────────────────────────────────
def _locations_prompt(project: dict, opts: dict, chars: list[dict], first_seq: int) -> tuple[str, str]:
    story = project.get("story") or {}
    style = story.get("style") or {}
    cast = "、".join(f"{c.get('name')}（{c.get('id')}，{c.get('role')}）" for c in chars) or "（無）"
    n = opts["sceneCount"]
    system = f"""你是短劇的美術指導。要依故事設計 {n} 張「場景卡」（之後每張會單獨生一張空景參考圖）。

【故事概念】{story.get('premise') or '（未填）'}
【故事大綱】{(story.get('synopsis') or '（未填）')[:1000]}
【主要人物】{cast}
【風格】類型 {style.get('genre') or '—'}／基調 {style.get('tone') or '—'}／視覺 {style.get('visualStyle') or '—'}
【畫面比例】{(project.get('meta') or {}).get('aspectRatio') or '9:16'}

【規則】
- 恰好 {n} 個場景，必須是大綱裡人物真的會待的地方（不要為了湊數加無關場景）。
- desc 中文 120–260 字：時間、光線、氣氛、可拍到的具體物件與材質、聲音線索；不要條列。
- timeOfDay（如「深夜」「清晨」）、lighting（光源方向＋色溫）、mood（一句氛圍）都要具體。
- promptEn：一整段英文 comma-separated（location → time of day → lighting → atmosphere → key props/materials → camera framing），
  **必須包含**這句禁人語："{SCENE_NO_HUMAN_HINT}"（場景參考圖要空景，不能有路人、剪影、倒影、玻璃反射裡的人）。
- ambience.note：一句環境音描述（例：雨刷聲、日光燈嗡鳴）。
- 不要在 promptEn 寫角色中文名或 id。

【輸出】只回純 JSON 物件（不要 markdown 圍欄、不要說明）：
{{"scenes":[
  {{"name":"場景名（含時段）","desc":"","timeOfDay":"","lighting":"","mood":"","promptEn":"",
    "ambience":{{"note":""}}}}]}}
（id 由程式編，第一張會是 sc{first_seq:02d} 起算，不要自己填 id）"""
    user = f"請依上面的故事產出 {n} 個場景卡 JSON。"
    return system, user


def gen_locations(project: dict, cfg: dict, opts: dict, chars: list[dict], first_seq: int = 1) -> dict:
    n = opts["sceneCount"]
    sysmsg, user = _locations_prompt(project, opts, chars, first_seq)

    def validate(data) -> tuple[dict, list]:
        if isinstance(data, list):
            data = {"scenes": data}
        if not isinstance(data, dict):
            raise storygen.GenError("最外層必須是 JSON 物件（{scenes:[…]}）")
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise storygen.GenError("缺少 scenes 陣列或為空")
        warns: list[str] = []
        out = []
        for i, s in enumerate(scenes):
            if not isinstance(s, dict):
                raise storygen.GenError(f"第 {i + 1} 個場景不是物件")
            name = str(s.get("name") or "").strip()
            if not name:
                raise storygen.GenError(f"第 {i + 1} 個場景缺 name")
            fields = {k: str(s.get(k) or "").strip() for k in ("desc", "timeOfDay", "lighting", "mood", "promptEn")}
            for k in ("desc", "timeOfDay", "lighting", "promptEn"):
                if not fields[k]:
                    raise storygen.GenError(f"「{name}」的 {k} 是空的")
            if re.search(r"[\u4e00-\u9fff]", fields["promptEn"]):
                raise storygen.GenError(f"「{name}」的 promptEn 含中文，必須是純英文")
            low = fields["promptEn"].lower()
            if not (("no people" in low or "no person" in low or "empty" in low) and ("silhouette" in low) and ("reflection" in low)):
                # 不讓模型偷懶：缺禁人語直接回饋重試（validate 第 5 條的硬要求）
                raise storygen.GenError(
                    f"「{name}」的 promptEn 缺禁人語，必須包含「no people in silhouette / in reflection」"
                    "（空景參考圖要明寫不得有人，含剪影與倒影）")
            amb = s.get("ambience") if isinstance(s.get("ambience"), dict) else {}
            out.append({
                "name": name, **fields,
                "ambience": {"refAudio": "", "note": str(amb.get("note") or "").strip()},
                "refImages": [],          # ← 圖片一律留空
            })
        if len(out) != n:
            warns.append(f"要求 {n} 個場景，模型給了 {len(out)} 個")
        for k, s in enumerate(out):
            s["id"] = f"sc{first_seq + k:02d}"
        return ({"scenes": out}, warns)

    # 本機小模型偶爾會把輸出截在字串中間（取樣問題）→ 一鍵佈署給 3 次機會，別讓 4 步裡任一步白跑
    return storygen._run(cfg, sysmsg, user, validate, attempts=ATTEMPTS)


# ── 步驟 3：劇本（集 → 場次 → beats）────────────────────────────
def gen_script(project: dict, cfg: dict, opts: dict, chars: list[dict], scenes: list[dict],
               episode_no: int) -> dict:
    """用剛產生的角色／場景（尚未寫檔）寫一集劇本；id 由呼叫端之後寫進專案。"""
    story = project.get("story") or {}
    style = story.get("style") or {}
    n_scene = len(scenes)
    # shotCount 是**總鏡數** → 每場約分到 總數/場次 個鏡頭，劇本長度依此估算（真正配額在 build_preview 分配）
    per_scene_shots = max(1, round(opts["shotCount"] / max(1, n_scene)))
    per_scene_target = max(10, round(per_scene_shots * opts["shotSeconds"]))
    total_target = per_scene_target * n_scene
    chars_txt = json.dumps([{
        "id": c["id"], "name": c.get("name"), "role": c.get("role"),
        "外型": (c.get("appearance") or {}).get("promptEn", "")[:160],
        "辨識特徵": (c.get("appearance") or {}).get("features"),
    } for c in chars], ensure_ascii=False, indent=1)
    scenes_txt = json.dumps([{"id": s["id"], "name": s.get("name"), "時間": s.get("timeOfDay"),
                              "光線": s.get("lighting"), "氛圍": s.get("mood"),
                              "描述": (s.get("desc") or "")[:140]} for s in scenes], ensure_ascii=False, indent=1)
    chapters = [ch for ch in story.get("chapters") or [] if isinstance(ch, dict)]
    chap_txt = "\n".join(
        f"  {ch.get('id')}｜{ch.get('title') or ''}｜{(ch.get('summary') or (ch.get('content') or ''))[:260]}"
        for ch in chapters) or "  （無章回，只用大綱）"
    scene_ids = "、".join(s["id"] for s in scenes)
    char_ids = "、".join(c["id"] for c in chars)

    sysmsg = f"""你是短劇編劇。要把故事寫成「一集」可直接分鏡的劇本（繁體中文）。

【故事概念】{story.get('premise') or '（未填）'}
【故事大綱】{(story.get('synopsis') or '（未填）')[:1200]}
【章回】\n{chap_txt}
【風格】類型 {style.get('genre') or '—'}／基調 {style.get('tone') or '—'}／視覺 {style.get('visualStyle') or '—'}

【人物卡（只能用這些 id，不准發明新角色）】
{chars_txt}
【場景卡（只能用這些 id，不准發明新場景）】
{scenes_txt}

【規則】
- 恰好 {n_scene} 場，每場各自對應一個場景卡（sceneId 只能是 {scene_ids}）；同一個場景卡可以用在兩場。
- characterId 只能是 {char_ids}；characterIds（出場人物）要與 beats 裡真的說話的人一致。
- 這一集總長約 {total_target} 秒（每場約 {per_scene_target} 秒）；台詞每句 5–40 字、能自然說出口，不要旁白式解釋。
- beats 依演出順序涵蓋全場：dialogue 必填 characterId 與 line（＋emotion）；action 的 characterId 可為 null，line 寫鏡頭拍得到的具體行為。
- 每場 5–12 個 beats（要夠拆成鏡頭），最後一場要把衝突推到一個明確的懸念或收束。
- 若故事有章回，sourceChapterId 填這一集主要改編的章回 id（ch01…），沒有就給 null。
- 回「純 JSON 物件」（不要 markdown 圍欄、不要說明），id 欄位不要填：
{{"title":"集名（10–20 字）","logline":"一句話","sourceChapterId":"ch01 或 null",
 "scenes":[{{"title":"場次標題","sceneId":"{scenes[0]['id']}","characterIds":["{chars[0]['id']}"],
   "summary":"60–150 字","targetSeconds":{per_scene_target},
   "beats":[{{"type":"dialogue","characterId":"{chars[0]['id']}","line":"台詞","emotion":"情緒","notes":""}},
            {{"type":"action","characterId":null,"line":"動作","emotion":"","notes":""}}]}}]}}"""
    user = f"請寫第 {episode_no} 集的完整劇本 JSON：{n_scene} 場，約 {total_target} 秒。"

    allowed_chars = {c["id"] for c in chars}
    allowed_scenes = {s["id"] for s in scenes}
    allowed_chapters = {ch.get("id") for ch in chapters}

    def validate(data) -> tuple[dict, list]:
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        if not isinstance(data, dict):
            raise storygen.GenError("最外層必須是 JSON 物件")
        scenes_in = data.get("scenes")
        if not isinstance(scenes_in, list) or not scenes_in:
            raise storygen.GenError("缺少 scenes 陣列或為空")
        warns: list[str] = []
        out_scenes = []
        for i, sc in enumerate(scenes_in):
            if not isinstance(sc, dict):
                raise storygen.GenError(f"第 {i + 1} 場不是物件")
            sid = sc.get("sceneId")
            if sid not in allowed_scenes:
                raise storygen.GenError(f"第 {i + 1} 場的 sceneId {sid!r} 不存在（可用：{'、'.join(sorted(allowed_scenes))}）")
            be = sc.get("beats")
            if not isinstance(be, list) or not be:
                raise storygen.GenError(f"第 {i + 1} 場沒有 beats")
            chars_used = []
            for j, b in enumerate(be):
                if not isinstance(b, dict):
                    raise storygen.GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 不是物件")
                if not str(b.get("line") or "").strip():
                    raise storygen.GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 的 line 是空的")
                b["type"] = "action" if str(b.get("type")) == "action" else "dialogue"
                cid = b.get("characterId") or None
                if cid and cid not in allowed_chars:
                    raise storygen.GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 的 characterId {cid!r} 不存在")
                if b["type"] == "dialogue" and not cid:
                    raise storygen.GenError(f"第 {i + 1} 場第 {j + 1} 個對白 beat 缺 characterId")
                if cid and cid not in chars_used:
                    chars_used.append(cid)
            declared = [c for c in (sc.get("characterIds") or []) if isinstance(c, str)]
            for cid in declared:
                if cid not in allowed_chars:
                    raise storygen.GenError(f"第 {i + 1} 場的 characterIds 含不存在的角色 {cid!r}")
            for cid in chars_used:
                if cid not in declared:
                    declared.append(cid)
            if not declared:
                raise storygen.GenError(f"第 {i + 1} 場沒有任何角色出場")
            out_scenes.append({
                "title": str(sc.get("title") or "").strip() or f"場次 {i + 1}",
                "sceneId": sid, "characterIds": declared,
                "summary": str(sc.get("summary") or "").strip(),
                "beats": [{"type": b["type"], "characterId": b.get("characterId") or None,
                           "line": str(b["line"]).strip(),
                           "emotion": str(b.get("emotion") or "").strip(),
                           "notes": str(b.get("notes") or "").strip()} for b in be],
            })
        if len(out_scenes) != n_scene:
            warns.append(f"要求 {n_scene} 場，模型給了 {len(out_scenes)} 場")
        src = data.get("sourceChapterId")
        if src and src not in allowed_chapters:
            warns.append(f"sourceChapterId {src!r} 不是現有章回，已設為 null")
            src = None
        return ({"title": str(data.get("title") or "").strip() or f"第 {episode_no} 集",
                 "logline": str(data.get("logline") or "").strip(),
                 "sourceChapterId": src or None,
                 "scenes": out_scenes}, warns)

    # 本機小模型偶爾會把輸出截在字串中間（取樣問題）→ 一鍵佈署給 3 次機會，別讓 4 步裡任一步白跑
    return storygen._run(cfg, sysmsg, user, validate, attempts=ATTEMPTS)


# ── 步驟 4：分鏡 ───────────────────────────────────────────────
def _legal_frames_floor() -> tuple[int, int]:
    """H3 訓練域內最小的 17k+5 與對應 k（124–362 → k=7..21）。"""
    k_min = -(-(FRAME_MIN - 5) // 17)   # ceil，避免 119
    k_max = (FRAME_MAX - 5) // 17
    return 17 * k_min + 5, k_max


def _fit_frames(val, fps: int) -> float:
    """把 durationSec 調成 H3 合法格（17k+5 落在 124–362），回調好的秒數。"""
    try:
        sec = float(val or 0)
    except (TypeError, ValueError):
        sec = 5.0
    floor_frames, k_max = _legal_frames_floor()
    k_min = (floor_frames - 5) // 17
    fr = h3compose.frames_for(sec, fps)
    k = min(max(fr["k"], k_min), k_max)
    return round((17 * k + 5) / fps, 2)


def frames_in_range(sec: float, fps: int) -> bool:
    return h3compose.frames_for(sec, fps)["inRange"]


def shot_budget(beats: list, shot_seconds: float, shot_count: int, fps: int) -> tuple[list, list[str]]:
    """每場的鏡數配額（把 shot_count 平均分給 beats，餘數給前面幾句）＋ 分配合理性警告。

    台詞時長下限沿用 storygen 的算式（字數÷4.5 + 2 秒，單鏡上限 15 秒）；
    真正的時長調整與 coverage 檢查在 gen_shots_for_scene 的 validate 裡逐鏡做。
    """
    # 台詞量只算 dialogue：action beat 沒有台詞、不佔說話時間。
    # （曾把兩者一起算，導致「8 句台詞塞不下」的誤報，還把鏡頭時長拉長。）
    said = [b for b in beats if (b.get("type") or "dialogue") != "action"]
    per = storygen.estimate_durations(said)["perBeat"] if said else []
    sec = float(shot_seconds)
    warns: list[str] = []
    if per and sec * shot_count + 0.01 < sum(p["sec"] for p in per):
        warns.append(f"{len(said)} 句台詞共需約 {sum(p['sec'] for p in per):g}s，"
                     f"但 {shot_count} 鏡 × {sec:g}s = {sec * shot_count:g}s：台詞會塞不下（已在可行範圍內平均分配）")
    if len(beats) > shot_count:
        warns.append(f"這一場有 {len(beats)} 個演出點（{len(said)} 句台詞＋{len(beats) - len(said)} 個動作）、"
                     f"卻只分到 {shot_count} 鏡：部分鏡頭會連續演多個演出點")
    quota = [1] * len(beats) if beats else []
    for k in range(shot_count - len(quota)):
        if quota:
            quota[k % len(quota)] += 1
    if any(abs(_fit_frames(sec, fps) - sec) > 0.15 for _ in range(1)) and not frames_in_range(sec, fps):
        warns.append(f"每鏡 {sec:g}s 不是 H3 合法格，已自動改用 {_fit_frames(sec, fps):g}s（17k+5 幀）")
    return quota, warns


def gen_shots_for_scene(project: dict, cfg: dict, chars: list[dict], scenes: list[dict],
                        es: dict, count: int, shot_seconds: float, fps: int,
                        ch_map: dict[str, str]) -> dict:
    """為「一場」產出 count 個鏡頭（尚未寫檔）。es 需含 id/title/sceneId/characterIds/beats。"""
    beats = [b for b in (es.get("beats") or []) if isinstance(b, dict)]
    # dialogLineRefs 的第幾句＝**該場 dialogue beat 的序號**（action 不佔號；驗證器也是這樣算的）
    dlg_idx = [i + 1 for i, b in enumerate(beats) if (b.get("type") or "dialogue") != "action"]
    dlg_n = len(dlg_idx)
    # M12a：台詞引用同時存 beat id（新增時實際寫入的 id）。模型這裡給的 beat 可能還沒有 id，
    # 先留 null，寫入時（_land_shots）再用實際 beat id 補上。
    dlg_beat_id = {n: beats[idx - 1].get("id") for n, idx in enumerate(dlg_idx, start=1)}
    scene = next((s for s in scenes if s.get("id") == es.get("sceneId")), None)
    char_by_id = {c["id"]: c for c in chars}
    cast = [char_by_id[cid] for cid in (es.get("characterIds") or []) if cid in char_by_id]
    est = storygen.estimate_durations(beats)
    beat_lines = "\n".join(
        (f"  第 {dlg_idx.index(i + 1) + 1} 句台詞" if (i + 1) in dlg_idx else "  （動作，不編號）")
        + f"｜{b.get('type')}｜{prompts._char_brief(project, b.get('characterId')) if b.get('characterId') else '（無角色）'}"
        f"｜{b.get('line')}｜{b.get('emotion') or ''}｜估 {est['perBeat'][i]['sec']}s"
        for i, b in enumerate(beats))
    cast_txt = json.dumps([{
        "id": c["id"], "name": c.get("name"),
        "外型": (c.get("appearance") or {}).get("promptEn", ""),
        "辨識特徵": (c.get("appearance") or {}).get("features"),
        "服裝": ((c.get("outfits") or [{}])[0].get("promptEn") if c.get("outfits") else ""),
        "identityAnchors": c.get("identityAnchors"),
    } for c in cast], ensure_ascii=False, indent=1)
    dur = _fit_frames(shot_seconds, fps)
    has_person = bool(es.get("characterIds"))
    move_pool = "/".join(_CAMERA_ON_PERSON if has_person else storage.V02_CAMERA_MOVES)
    type_pool = "close_up/medium/over_shoulder/pov（禁 wide）" if has_person else "wide/medium"

    sysmsg = f"""你是分鏡師。把這一場戲拆成 {count} 個可拍鏡頭，輸出繁體中文描述＋英文六段式 prompt。

【這一場】
場次 {es.get('id')}｜{es.get('title') or ''}｜場景卡：{scene.get('name') if scene else '（無）'}（{es.get('sceneId')}）
時間 {scene.get('timeOfDay') if scene else ''}／光線 {scene.get('lighting') if scene else ''}／氛圍 {scene.get('mood') if scene else ''}
場景英文描述：{(scene.get('promptEn') if scene else '') or ''}
出場人物（JSON，promptEn 要逐字沿用外型特徵，不要改寫）：
{cast_txt}
台詞與動作（1-based 編號＝dialogLineRefs 的寫法）：
{beat_lines}

【規則】
- 恰好 {count} 鏡，順序就是剪接順序；合起來要涵蓋全部 {len(beats)} 句（其中 {dlg_n} 句台詞），不重複演同一句。
- dialogLineRefs 填「上面編號清單的句號」（1–{len(beats)} 就是那份清單的編號），純動作鏡填 []。
  **每一句台詞（標 dialogue 的那幾句）都要剛好被某一鏡引用一次**；不要引用動作句。
- durationSec 一律填 {dur}（H3 合法格：5 秒級）；台詞很長的鏡頭可以到 15。
- spec.shotType：{type_pool}；spec.angle 的 h 用 front/front_left/left/back_left/back/back_right/right/front_right，
  v 用 worm/low/eye_level/high，s 用 close_up/medium/wide；spec.cameraMove：{move_pool}。
- 有人的鏡頭禁止 wide（MSB 取景規則）；wide 只能用在純環境／純動作鏡。
- promptEn 用六段式，每段一行、段首固定寫「1. Subject:」「2. Action:」「3. Camera:」「4. Lighting:」「5. Atmosphere:」「6. Audio:」；
  全英文、不要中文名、不要 <Picture N> 或 {{{{c01}}}} 佔位符（參考圖槽位由程式產生）。
- **每段 1 句、20–35 個英文單字**（不要長串逗號堆疊）。1 Subject 只需帶 2–3 個最關鍵的辨識特徵（疤／痣／飾品／髮型之一），
  不要把整段人物卡貼進來；6 Audio 有對白就寫誰說話與聲音質感，沒有就寫環境音。
- 每鏡 beats 只要 1–2 條；composition／lighting 各 25–45 字中文。**整份輸出控制在 6000 字以內**。

【輸出】只回純 JSON 物件（不要 markdown 圍欄、不要說明）：
{{"shots":[
  {{"durationSec":{dur},
    "spec":{{"shotType":"medium","angle":{{"h":"front","v":"eye_level","s":"medium"}},"cameraMove":"static",
             "composition":"中文構圖說明","lighting":"中文光線"}},
    "beats":[{{"start":0,"end":2.5,"cut":false,"action":"中文子鏡頭動作","camera":"中文運鏡","locationId":"{es.get('sceneId') or ''}"}}],
    "dialogLineRefs":[1],
    "promptZh":"繁體中文畫面描述（給人看）",
    "promptEn":"1. Subject: …\\n2. Action: …\\n3. Camera: …\\n4. Lighting: …\\n5. Atmosphere: …\\n6. Audio: …"}}]}}"""
    user = f"請把場次 {es.get('id')} 拆成 {count} 個鏡頭。"

    allowed_chars = set(es.get("characterIds") or [])

    def validate(data) -> tuple[dict, list]:
        shots = data.get("shots") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        if not isinstance(shots, list) or not shots:
            raise storygen.GenError("缺少 shots 陣列或為空")
        warns: list[str] = []
        dup_warns: list[str] = []
        seen: dict[int, int] = {}
        out = []
        for i, sh in enumerate(shots):
            if not isinstance(sh, dict):
                raise storygen.GenError(f"第 {i + 1} 鏡不是物件")
            spec = sh.get("spec") if isinstance(sh.get("spec"), dict) else {}
            st = storage.shot_type_en(spec.get("shotType"))
            if not st:
                raise storygen.GenError(f"第 {i + 1} 鏡的 shotType 不合法（可用：{'/'.join(storage.V02_SHOT_TYPES)}）")
            angle = _norm_angle(spec.get("angle"))
            for key, allowed in (("h", storage.V02_ANGLES_H), ("v", storage.V02_ANGLES_V), ("s", storage.V02_ANGLES_S)):
                if angle[key] and angle[key] not in allowed:
                    raise storygen.GenError(f"第 {i + 1} 鏡 angle.{key}={angle[key]!r} 不合法（可用：{'/'.join(allowed)}）")
            move = str(spec.get("cameraMove") or "").strip()
            if move and move not in storage.V02_CAMERA_MOVES:
                raise storygen.GenError(f"第 {i + 1} 鏡 cameraMove={move!r} 不合法（可用：{'/'.join(storage.V02_CAMERA_MOVES)}）")
            refs_in = sh.get("dialogLineRefs") or []
            if not isinstance(refs_in, list):
                raise storygen.GenError(f"第 {i + 1} 鏡的 dialogLineRefs 必須是陣列")
            # 模型可能用「beat 序號」或「台詞序號」編號（兩種都合理）→ 一律先換成 beat 序號再轉台詞序號。
            # 資料模型／驗證器要的是「台詞序號」（1-based，只算 dialogue）。
            # 模型看到的是「這一場的 beat 編號表」→ 一律把 dialogLineRefs 當 **beat 序號**解讀，
            # 再換算成資料模型要的「台詞序號」（1-based，只算 dialogue）。指到動作 beat 的就略過。
            valid_int = [r for r in refs_in if isinstance(r, int) and not isinstance(r, bool)]
            if len(valid_int) != len(refs_in):
                raise storygen.GenError(f"第 {i + 1} 鏡的 dialogLineRefs 含非整數：{refs_in!r}")
            if any(not (1 <= r <= len(beats)) for r in valid_int):
                raise storygen.GenError(f"第 {i + 1} 鏡 dialogLineRefs 超出這一場的 beat 數（1–{len(beats)}）")
            refs: list[int] = []
            for r in valid_int:
                beat_no = r if r in dlg_idx else (dlg_idx[r - 1] if r <= dlg_n else None)
                if beat_no is None:
                    continue                      # 指向動作 beat：不佔台詞號，也無法演出
                line_no = dlg_idx.index(beat_no) + 1
                if line_no in seen:
                    dup_warns.append(f"第 {line_no} 句台詞同時被第 {seen[line_no] + 1} 鏡與第 {i + 1} 鏡引用（已保留前者）")
                    continue
                seen[line_no] = i
                refs.append(line_no)
            refs = sorted(set(refs))
            try:
                dur_i = float(sh.get("durationSec"))
            except (TypeError, ValueError):
                dur_i = shot_seconds
            dur_i = _fit_frames(dur_i, fps)
            need_raw = sum(storygen._nonspace_len(beats[dlg_idx[r - 1] - 1].get("line")) / 4.5 + 2.0
                           for r in refs) if refs else 0
            need = min(need_raw, 15.0)
            if need and dur_i + 0.01 < need:
                new_dur = _fit_frames(need, fps)
                while new_dur + 0.01 < need and new_dur < 15.0:
                    new_dur = _fit_frames(new_dur + 0.5, fps)
                warns.append(f"第 {i + 1} 鏡 {dur_i:g}s 少於台詞所需 {need:.1f}s，已調為 {new_dur:g}s")
                dur_i = new_dur
            if need_raw > 15.0:
                warns.append(f"第 {i + 1} 鏡台詞需 {need_raw:.1f}s 超過單鏡上限 15s（建議拆句）")
            sb = []
            for b in sh.get("beats") or []:
                if not isinstance(b, dict):
                    continue
                try:
                    s0 = float(b.get("start") or 0)
                    e0 = float(b.get("end") or dur_i)
                except (TypeError, ValueError):
                    s0, e0 = 0.0, dur_i
                loc = str(b.get("locationId") or "")
                if loc and loc != (es.get("sceneId") or ""):
                    raise storygen.GenError(f"第 {i + 1} 鏡 beat 的 locationId {loc!r} 與場次場景不符")
                sb.append({"start": round(max(0.0, min(s0, dur_i)), 2),
                           "end": round(max(0.0, min(e0, dur_i)), 2), "cut": bool(b.get("cut")),
                           "action": str(b.get("action") or ""), "camera": str(b.get("camera") or ""),
                           "locationId": loc})
            if not sb:
                sb = [{"start": 0.0, "end": round(dur_i, 2), "cut": False, "action": "（單一連續動作）",
                       "camera": "", "locationId": es.get("sceneId") or ""}]
            cids = [c for c in (sh.get("characterIds") or list(allowed_chars)) if c in allowed_chars]
            if st == "wide" and cids:
                raise storygen.GenError(
                    f"第 {i + 1} 鏡含人物（{'、'.join(cids)}）卻用 wide；有人的鏡頭只能用 medium／MCU／CU")
            prompt_en = str(sh.get("promptEn") or "").strip()
            for k in (1, 2, 3, 4, 5, 6):
                if f"{k}. " not in prompt_en:
                    raise storygen.GenError(f"第 {i + 1} 鏡的 promptEn 不是六段式（缺第 {k} 段標頭）")
            if re.search(r"[\u4e00-\u9fff]", prompt_en):
                raise storygen.GenError(f"第 {i + 1} 鏡的 promptEn 含中文（必須全英文）")
            out.append({
                "durationSec": dur_i,
                "characterIds": cids,
                "spec": {"shotType": st, "angle": angle, "cameraMove": move,
                         "composition": str(spec.get("composition") or "").strip(),
                         "lighting": str(spec.get("lighting") or "").strip(),
                         "framingRule": storage.V02_FRAMING_RULE},
                "beats": sb, "dialogLineRefs": refs,
                "dialogBeatIds": [dlg_beat_id[n] for n in refs if dlg_beat_id.get(n)],
                "promptZh": str(sh.get("promptZh") or "").strip(),
                "promptEn": prompt_en,
            })
        if len(out) != count:
            warns.append(f"要求 {count} 鏡，模型給了 {len(out)} 鏡")
        warns += dup_warns
        missing = [r for r in range(1, dlg_n + 1) if r not in seen]
        if missing:
            # 模型漏引用了台詞 → 補到「台詞最少」的那一鏡（保住 100% 覆蓋），並明確記錄補了什麼
            for r in missing:
                target = min(range(len(out)), key=lambda k: len(out[k]["dialogLineRefs"]))
                out[target]["dialogLineRefs"] = sorted([*out[target]["dialogLineRefs"], r])
                warns.append(f"第 {r} 句台詞模型沒有安排鏡頭，已補到第 {target + 1} 鏡（請確認那個鏡頭的畫面是否合理）")
            warns.append(f"模型漏了 {len(missing)} 句台詞的鏡頭安排，已自動補齊使覆蓋率 100%")
        over = len(out) - count
        if over > 0:
            # 總鏡數是硬上限：多的從尾巴砍（前面的鏡頭已涵蓋主戲與台詞）
            warns.append(f"模型多給了 {over} 鏡，已截掉最後 {over} 鏡（總鏡數維持 {count}）")
            out = out[:count]
            gone = [r for r in range(1, dlg_n + 1) if r not in {x for sh in out for x in sh["dialogLineRefs"]}]
            if gone:
                warns.append(f"截掉後有 {len(gone)} 句台詞沒有鏡頭：{gone}（可把總鏡數調大或重新佈署）")
        return ({"shots": out}, warns)

    # 本機小模型偶爾會把輸出截在字串中間（取樣問題）→ 一鍵佈署給 3 次機會，別讓 4 步裡任一步白跑
    return storygen._run(cfg, sysmsg, user, validate, attempts=ATTEMPTS)


# ── 寫入 ───────────────────────────────────────────────────────
def _merge_in_place(target: dict, new: dict) -> None:
    """同 id 就地換內容（保留順序與位置），新欄位的圖片欄位一律留空。"""
    keep = {"refImages": [], "lockedFace": False, "stages": []}
    target.clear()
    target.update(new)
    target.update(keep)


def apply_preview(slug: str, preview: dict, overwrite: bool) -> dict:
    """把預覽一次寫進專案（overwrite 先清空角色／場景／劇本／鏡頭，並移走舊 media 檔）。"""
    with storage._lock:
        project = storage.load_project(slug)
        old_images: list[str] = []
        if overwrite:
            old = _purge(project)
            old_images = media_paths_of(old)

        # 角色
        chars_now = project.get("characters")
        if not isinstance(chars_now, list):
            chars_now = project["characters"] = []
        existing_c = {c.get("id"): c for c in chars_now if isinstance(c, dict)}
        for ch in preview.get("characters") or []:
            cid = ch["id"]
            if cid in existing_c:
                _merge_in_place(existing_c[cid], ch)
            else:
                chars_now.append(ch)

        # 場景
        scenes_now = project.get("scenes")
        if not isinstance(scenes_now, list):
            scenes_now = project["scenes"] = []
        existing_s = {s.get("id"): s for s in scenes_now if isinstance(s, dict)}
        for sc in preview.get("scenes") or []:
            sid = sc["id"]
            if sid in existing_s:
                _merge_in_place(existing_s[sid], sc)
            else:
                scenes_now.append(sc)

        # 劇本（append 時編新的集 id，場次沿用預覽算好的 eNNsMM）
        script = project.setdefault("script", {"episodes": []})
        eps = script.get("episodes")
        if not isinstance(eps, list):
            eps = script["episodes"] = []
        sp = preview.get("script") or {}
        ep_id = storage._next_seq([e.get("id") for e in eps if isinstance(e, dict)], "e")
        ep_out = {"id": ep_id, "title": sp.get("title") or "未命名集", "scenes": []}
        if sp.get("sourceChapterId"):
            ep_out["sourceChapterId"] = sp["sourceChapterId"]
        for i, es in enumerate(sp.get("scenes") or []):
            es_id = f"{ep_id}s{i + 1:02d}"
            beats = []
            for b in es.get("beats") or []:
                beats.append({"id": storage.next_id(beats, "b"), "type": b.get("type") or "dialogue",
                              "characterId": b.get("characterId") or None, "line": b.get("line") or "",
                              "emotion": b.get("emotion") or "", "notes": b.get("notes") or ""})
            ep_out["scenes"].append({"id": es_id, "title": es.get("title") or "",
                                     "sceneId": es.get("sceneId") or None,
                                     "characterIds": list(es.get("characterIds") or []),
                                     "summary": es.get("summary") or "", "beats": beats})
        eps.append(ep_out)
        # 預覽的場次 id（eNNsMM）→ 實際寫入的 id（eMMsMM），鏡頭跟著改
        id_map = {es["id"]: ep_out["scenes"][i]["id"] for i, es in enumerate(sp.get("scenes") or [])}
        # M12a：台詞引用改存 beat id，舊的序號引用在這條同步路徑也要換成「實際寫入後的 beat id」
        beat_id_map: dict[str, str] = {}
        for i, es in enumerate(sp.get("scenes") or []):
            raw = [b for b in (es.get("beats") or []) if isinstance(b, dict)]
            for j, b in enumerate(raw):
                if b.get("id") and j < len(ep_out["scenes"][i]["beats"]):
                    beat_id_map[str(b["id"])] = ep_out["scenes"][i]["beats"][j]["id"]

        shots_now = project.get("shots")
        if not isinstance(shots_now, list):
            shots_now = project["shots"] = []
        ts = storage.now_iso()
        created_ids = []
        for sh in preview.get("shots") or []:
            es_id = id_map.get(sh.get("episodeSceneId"), sh.get("episodeSceneId"))
            es = next((s for s in ep_out["scenes"] if s.get("id") == es_id), None)
            if es is None:
                raise storage.StorageError(f"鏡頭引用不存在的場次 {es_id}")
            new_shot = {
                "id": storage.next_id(shots_now, "sh"), "no": 0,
                "episodeSceneId": es_id, "sceneId": es.get("sceneId") or None,
                "characterIds": list(sh.get("characterIds") or []),
                "spec": sh.get("spec") or {},
                "durationSec": sh.get("durationSec") or 5,
                "promptZh": sh.get("promptZh") or "", "promptEn": sh.get("promptEn") or "",
                "dialogue": [], "beats": sh.get("beats") or [],
                "dialogLineRefs": list(sh.get("dialogLineRefs") or []),
                # M12a：同步寫入 beat id（驗證器與 compile_prompt 優先讀這個）
                "dialogBeatIds": [beat_id_map[b] for b in (sh.get("dialogBeatIds") or []) if b in beat_id_map],
                "stageIds": [],
                "sourceChapterId": sp.get("sourceChapterId") or None,
                "referencePlan": {"slots": [], "weakReferences": [], "promptPlaceholders": {}},
                "images": {"first": "", "last": ""},               # ← 首尾幀留空
                "video": {"path": "", "status": "none", "taskType": "r2v", "seed": 0, "note": ""},
                "voice": {"path": "", "engine": ""},
                "durationFrames": h3compose.frames_for(sh.get("durationSec") or 5,
                                                       int((project.get("meta") or {}).get("fps") or 24))["legal"],
                "updatedAt": ts,
            }
            shots_now.append(new_shot)
            created_ids.append(new_shot["id"])
        for j, s in enumerate(shots_now):
            if isinstance(s, dict):
                s["no"] = j + 1

        errors = storage.validate_project(project)
        if errors:
            raise storage.StorageError("寫入後結構不合法：" + "；".join(errors[:5]))
        project["meta"]["updatedAt"] = ts
        storage.ensure_media_dirs(slug)
        storage._write_json_atomic(storage.project_file(slug), project)
        deleted = _delete_media(slug, old_images) if old_images else 0

    return {
        "characters": len(preview.get("characters") or []),
        "scenes": len(preview.get("scenes") or []),
        "episodes": 1,
        "episodeSceneIds": [s["id"] for s in ep_out["scenes"]],
        "shots": len(created_ids),
        "ids": {"characters": [c["id"] for c in preview.get("characters") or []],
                "scenes": [s["id"] for s in preview.get("scenes") or []],
                "episodeId": ep_id, "shots": created_ids},
        "mediaDeleted": deleted,
    }


# ── 逐步落地（每完成一層就寫檔：模型一次只算一小塊，失敗時前面的成果不會白做）──
def _save_now(slug: str, project: dict) -> None:
    """驗證後原子寫入（逐步落地用；不動 media 檔）。"""
    errors = storage.validate_project(project)
    if errors:
        raise storage.StorageError("寫入後結構不合法：" + "；".join(errors[:5]))
    project.setdefault("meta", {})["updatedAt"] = storage.now_iso()
    storage.ensure_media_dirs(slug)
    storage._write_json_atomic(storage.project_file(slug), project)


def _land_chars(project: dict, chars: list[dict]) -> list[str]:
    now = project.get("characters")
    if not isinstance(now, list):
        now = project["characters"] = []
    existing = {c.get("id"): c for c in now if isinstance(c, dict)}
    out: list[str] = []
    for ch in chars:
        cid = ch["id"]
        if cid in existing:
            _merge_in_place(existing[cid], ch)
        else:
            now.append(ch)
        out.append(cid)
    return out


def _land_scenes(project: dict, scenes: list[dict]) -> list[str]:
    now = project.get("scenes")
    if not isinstance(now, list):
        now = project["scenes"] = []
    existing = {s.get("id"): s for s in now if isinstance(s, dict)}
    out: list[str] = []
    for sc in scenes:
        sid = sc["id"]
        if sid in existing:
            _merge_in_place(existing[sid], sc)
        else:
            now.append(sc)
        out.append(sid)
    return out


def _land_script(project: dict, sp: dict) -> tuple[dict, dict, dict]:
    """把一集（場次＋beats）寫進 project，回 (ep_out, 預覽場次 id → 實際 id, 預覽 beat id → 實際 beat id)。"""
    script = project.setdefault("script", {"episodes": []})
    eps = script.get("episodes")
    if not isinstance(eps, list):
        eps = script["episodes"] = []
    ep_id = storage._next_seq([e.get("id") for e in eps if isinstance(e, dict)], "e")
    ep_out: dict = {"id": ep_id, "title": sp.get("title") or "未命名集", "scenes": []}
    if sp.get("sourceChapterId"):
        ep_out["sourceChapterId"] = sp["sourceChapterId"]
    id_map: dict[str, str] = {}
    beat_id_map: dict[str, str] = {}
    for i, es in enumerate(sp.get("scenes") or []):
        es_id = f"{ep_id}s{i + 1:02d}"
        beats = []
        # M12a：preview 內的臨時 beat id → 實際寫入後的 beat id（_land_shots 靠它寫 dialogBeatIds）
        for b in es.get("beats") or []:
            new_bid = storage.next_id(beats, "b")
            beats.append({"id": new_bid, "type": b.get("type") or "dialogue",
                          "characterId": b.get("characterId") or None, "line": b.get("line") or "",
                          "emotion": b.get("emotion") or "", "notes": b.get("notes") or ""})
            if b.get("id"):
                beat_id_map[str(b["id"])] = new_bid
        ep_out["scenes"].append({"id": es_id, "title": es.get("title") or "",
                                 "sceneId": es.get("sceneId") or None,
                                 "characterIds": list(es.get("characterIds") or []),
                                 "summary": es.get("summary") or "", "beats": beats})
        if es.get("id"):
            id_map[es["id"]] = es_id
    eps.append(ep_out)
    return ep_out, id_map, beat_id_map


def _land_shots(project: dict, ep_out: dict, sp: dict, shots: list[dict], id_map: dict,
                beat_id_map: dict | None = None) -> list[str]:
    """把某一場的鏡頭寫進 project（no 重排）。"""
    shots_now = project.get("shots")
    if not isinstance(shots_now, list):
        shots_now = project["shots"] = []
    ts = storage.now_iso()
    fps = int((project.get("meta") or {}).get("fps") or 24)
    created: list[str] = []
    for sh in shots:
        es_id = id_map.get(sh.get("episodeSceneId"), sh.get("episodeSceneId"))
        es = next((s for s in ep_out["scenes"] if s.get("id") == es_id), None)
        if es is None:
            raise storage.StorageError(f"鏡頭引用不存在的場次 {es_id}")
        # M12a：dialogBeatIds 用「實際寫入後的 beat id」——模型回的 beat 常常沒 id，
        # 場次 beat 寫入時才由 _land_script 補；這裡按 beat id 對，對不到就退回台詞序號換算。
        dlg_beats = [b for b in (es.get("beats") or []) if isinstance(b, dict) and b.get("type") == "dialogue"]
        by_id = {str(b.get("id")): n for n, b in enumerate(dlg_beats, start=1) if b.get("id")}
        bmap = beat_id_map or {}
        refs = [r for r in (sh.get("dialogLineRefs") or []) if isinstance(r, int)]
        beat_ids: list[str] = []
        for bid in (sh.get("dialogBeatIds") or []):
            real = bmap.get(str(bid), str(bid))
            n = by_id.get(real)
            if n and n not in refs:
                refs.append(n)
            if n:
                beat_ids.append(real)
        if not beat_ids:
            beat_ids = [dlg_beats[n - 1].get("id") for n in sorted(set(refs)) if 1 <= n <= len(dlg_beats)]
        new_shot = {
            "id": storage.next_id(shots_now, "sh"), "no": 0,
            "episodeSceneId": es_id, "sceneId": es.get("sceneId") or None,
            "characterIds": list(sh.get("characterIds") or []),
            "spec": sh.get("spec") or {},
            # M14a：長度吸附到 H3 合法格（124–362 幀），超出域就夾回域內
            "durationSec": _fit_frames(sh.get("durationSec") or 5, fps),
            "promptZh": sh.get("promptZh") or "", "promptEn": sh.get("promptEn") or "",
            "dialogue": [], "beats": [],
            "beatIds": [b for b in (sh.get("beatIds") or beat_ids) if b],
            "role": sh.get("role") or ("dialogue" if beat_ids else "action"),
            "derivedFrom": "deploy",
            "padSec": round(max(0.0, _fit_frames(sh.get("durationSec") or 5, fps)
                                - float(sh.get("durationSec") or 5)), 2),
            "dialogLineRefs": sorted(set(refs)),
            "dialogBeatIds": [b for b in beat_ids if b],
            "stageIds": [],
            "sourceChapterId": sp.get("sourceChapterId") or None,
            "referencePlan": {"slots": [], "weakReferences": [], "promptPlaceholders": {}},
            "images": {"first": "", "last": ""},
            "video": {"path": "", "status": "none", "taskType": "r2v", "seed": 0, "note": ""},
            "voice": {"path": "", "engine": ""},
            "durationFrames": h3compose.frames_for(sh.get("durationSec") or 5, fps)["legal"],
            "updatedAt": ts,
        }
        shots_now.append(new_shot)
        created.append(new_shot["id"])
    for j, s in enumerate(shots_now):
        if isinstance(s, dict):
            s["no"] = j + 1
    return created


def run_steps_landing(job_id: str, slug: str, cfg: dict, opts: dict,
                      progress, done_steps: list) -> dict:
    """一鍵佈署的逐步落地版：cast→寫檔、locations→寫檔、script→寫檔、每場 shots→寫檔。

    為什麼要這樣做（使用者 2026-11-20 明確要求）：
    - 一次要模型算完四層、再一次寫入，輸出越大越容易「算完結果是空的」（配額被吃光）；
      而且任何一步失敗＝十幾分鐘全白做。
    - 改成每層各自生成、驗證通過立刻落地，模型每次只扛一小包，失敗時前面的資料留著。
    """
    t_start = time.time()
    project = storage.load_project(slug)
    fps = int((project.get("meta") or {}).get("fps") or 24)
    timings: dict[str, float] = {}
    warn_by: dict[str, list] = {}
    written = {"characters": 0, "scenes": 0, "episodeScenes": 0, "shots": 0}
    agg = {"characters": [], "scenes": [], "episodes": 0, "shots": [], "episodeId": "", "mediaDeleted": 0}
    old_images: list[str] = []
    purged = {"done": False}

    def flush_job() -> None:
        warnings = []
        for k in ("cast", "locations", "script", "shots"):
            warnings += [w for w in (warn_by.get(k) or [])]
        _set(job_id, written=dict(written), warnings=warnings, warningsByStep=dict(warn_by),
             timings=dict(timings))

    try:
        # 1) 角色卡 → 落地
        progress("cast", "產生角色卡…")
        t = time.time()
        first_c = 1 if opts["overwrite"] else int(
            re.sub(r"\D", "", storage.next_id(project.get("characters") or [], "c")) or 1)
        cast = gen_cast(project, cfg, opts, first_seq=first_c)
        chars = cast["preview"]["characters"]
        warn_by["cast"] = cast["warnings"]
        timings["cast"] = round(time.time() - t, 1)
        done_steps.append("cast")
        # 覆蓋：**等第一步成功後才清空**，這樣 cast 就失敗時舊資料還在
        if opts["overwrite"] and not purged["done"]:
            old = _purge(project)
            old_images = _media_paths(old)
            purged["done"] = True
        _land_chars(project, chars)
        _save_now(slug, project)
        written["characters"] = len(chars)
        agg["characters"] = [c["id"] for c in chars]
        flush_job()
        _check_cancel(job_id)

        # 2) 場景卡 → 落地
        progress("locations", "產生場景卡…")
        t = time.time()
        first_sc = 1 if opts["overwrite"] else int(
            re.sub(r"\D", "", storage.next_id(project.get("scenes") or [], "sc")) or 1)
        locs = gen_locations(project, cfg, opts, chars, first_seq=first_sc)
        scns = locs["preview"]["scenes"]
        warn_by["locations"] = locs["warnings"]
        timings["locations"] = round(time.time() - t, 1)
        done_steps.append("locations")
        _land_scenes(project, scns)
        _save_now(slug, project)
        written["scenes"] = len(scns)
        agg["scenes"] = [s["id"] for s in scns]
        flush_job()
        _check_cancel(job_id)

        # 3) 劇本（集／場／beats）→ 落地
        progress("script", "寫劇本（集／場／對白）…")
        t = time.time()
        ep_no = 1 + len(((project.get("script") or {}).get("episodes") or []))
        script = gen_script(project, cfg, opts, chars, scns, episode_no=ep_no)
        warn_by["script"] = script["warnings"]
        timings["script"] = round(time.time() - t, 1)
        done_steps.append("script")
        sp = dict(script["preview"])
        ep_out, id_map, beat_id_map = _land_script(project, sp)
        _save_now(slug, project)
        written["episodeScenes"] = len(ep_out["scenes"])
        agg["episodes"] = 1
        agg["episodeId"] = ep_out["id"]
        flush_job()
        _check_cancel(job_id)

        # 4) 分鏡：**逐場生成、每場完成立刻落地**
        progress("shots", "拆分鏡（六段式 prompt）…")
        t = time.time()
        # 注意順序：預覽的 sc 裡也有 id（eNNsMM），必須最後覆蓋成實際寫入的場次 id，
        # 否則鏡頭會引用到不存在的場次（實測 error：鏡頭引用不存在的場次 None）。
        es_list = [{**sc, "id": ep_out["scenes"][i]["id"]} for i, sc in enumerate(sp.get("scenes") or [])]
        shot_warns: list[str] = []
        sp_total = int(opts["shotCount"])
        if len(es_list) != int(opts["sceneCount"]):
            sp_total = max(sp_total, len(es_list))
        alloc, alloc_warns = allocate_shots(sp_total, len(es_list))
        shot_warns += alloc_warns
        job_cfg = dict(cfg or {})
        if job_cfg.get("provider"):
            job_cfg["providerLabel"] = "線上" if job_cfg["provider"] == "online" else (
                "自訂" if job_cfg["provider"] == "custom" else "本機")
        try:
            st = llm.status(cfg or {})
            if st.get("ok") and st.get("model"):
                job_cfg["model"] = st["model"]
        except Exception:  # noqa: BLE001
            pass
        _set(job_id, params={**opts, "shotPlan": alloc,
                             "provider": job_cfg.get("provider") or "",
                             "providerLabel": job_cfg.get("providerLabel") or "",
                             "model": job_cfg.get("model") or ""})
        n_es = max(1, len(es_list))
        for k, es in enumerate(es_list):
            _check_cancel(job_id)
            # 進度條要在逐場之間真的前進（0.77 → 0.99），否則使用者會以為卡住
            _set(job_id, step="shots", stepLabel=STEP_LABEL["shots"],
                 progress=round(0.77 + 0.22 * (k / n_es), 3),
                 message=f"拆分鏡…（{k + 1}/{n_es} 場）", elapsed=round(time.time() - t_start, 1))
            beats = [b for b in (es.get("beats") or []) if isinstance(b, dict)]
            per = alloc[k] if k < len(alloc) else 1
            for w in shot_budget(beats, opts["shotSeconds"], per, fps)[1]:
                shot_warns.append(f"{es['id']}：{w}")
            res = gen_shots_for_scene(project, cfg, chars, scns, es, per,
                                      opts["shotSeconds"], fps, {})
            shot_warns += [f"{es['id']}：{w}" for w in res["warnings"]]
            # gen_shots_for_scene 回傳的鏡頭不帶所屬場次（舊版由 build_preview 統一補），
            # 逐步落地要在這裡補，否則寫入時找不到場次（實測：鏡頭引用不存在的場次 None）。
            shots_for_es = [{**sh, "episodeSceneId": es["id"], "sceneId": es.get("sceneId")}
                            for sh in res["preview"]["shots"]]
            es["shots"] = shots_for_es
            ids = _land_shots(project, ep_out, sp, shots_for_es, id_map, beat_id_map)
            _save_now(slug, project)
            written["shots"] += len(ids)
            agg["shots"] += ids
            warn_by["shots"] = list(shot_warns)
            flush_job()
        warn_by["shots"] = shot_warns
        timings["shots"] = round(time.time() - t, 1)

        # 舊 media 檔最後才刪（前面任何一步失敗都還能還原）
        if old_images:
            agg["mediaDeleted"] = _delete_media(slug, old_images)
        flush_job()
        return {"result": {**{k: written[k] if k != "episodeScenes" else len(ep_out["scenes"])
                              for k in ("characters", "scenes", "episodeScenes", "shots")},
                           "episodes": 1, "mediaDeleted": agg["mediaDeleted"],
                           "ids": {"characters": agg["characters"], "scenes": agg["scenes"],
                                   "episodeId": agg["episodeId"], "shots": agg["shots"]}},
                "warnings": [w for k in ("cast", "locations", "script", "shots") for w in (warn_by.get(k) or [])],
                "warningsByStep": warn_by, "timings": timings}
    finally:
        # 無論成敗都把已完成的部分寫回去（避免 job 卡在舊 written）
        pass


def media_paths_of(old: dict) -> list[str]:
    return _media_paths(old)


def existing_counts(project: dict) -> dict:
    """給前端「已有內容」警告用。"""
    return {
        "characters": len([c for c in project.get("characters") or [] if isinstance(c, dict)]),
        "scenes": len([s for s in project.get("scenes") or [] if isinstance(s, dict)]),
        "episodes": len(((project.get("script") or {}).get("episodes") or [])),
        "shots": len([s for s in project.get("shots") or [] if isinstance(s, dict)]),
    }


def preview(slug: str, opts: dict | None = None) -> dict:
    """同步跑四步但**完全不寫檔**（給「先預覽」用；前端要等 4 次模型，屬慢路徑）。"""
    o = norm_opts(opts)
    project = storage.load_project(slug)
    if not str((project.get("story") or {}).get("synopsis") or "").strip() and \
            not str((project.get("story") or {}).get("premise") or "").strip():
        raise DeployError("故事頁還是空的：請先寫故事概念或大綱，再按一鍵佈署", step="cast")
    cfg = storage.llm_settings(slug)
    pack = build_preview_with_progress(project, cfg, o, lambda step, msg: None, [], "", 0.0)
    return {"preview": pack["preview"], "warnings": pack["warnings"],
            "warningsByStep": pack["warningsByStep"], "timings": pack["timings"],
            "params": o, "existing": existing_counts(project)}


# ── 背景任務（4 次模型呼叫，1–3 分鐘；不可同步卡 HTTP）────────────
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_MAX_KEEP = 20


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return json.loads(json.dumps(_public(job), ensure_ascii=False)) if job else None


def latest_job(slug: str) -> dict | None:
    with _LOCK:
        cands = [j for j in _JOBS.values() if j.get("slug") == slug]
        if not cands:
            return None
        job = max(cands, key=lambda j: (j.get("createdAt") or "", j.get("jobId") or ""))
        return json.loads(json.dumps(_public(job), ensure_ascii=False))


def start(slug: str, opts: dict | None = None) -> dict:
    """建任務並在背景 thread 跑四步；立刻回 job 狀態（前端 poll）。"""
    o = norm_opts(opts)
    project = storage.load_project(slug)
    if not str((project.get("story") or {}).get("synopsis") or "").strip() and \
            not str((project.get("story") or {}).get("premise") or "").strip():
        raise DeployError("故事頁還是空的：請先寫故事概念或大綱，再按一鍵佈署", step="cast")
    blockers = _image_blockers(slug, project) if o["overwrite"] else []
    if blockers:
        raise DeployError("；".join(blockers), step="cast")
    with _LOCK:
        for j in _JOBS.values():
            if j.get("slug") == slug and j.get("status") in ("queued", "running"):
                raise DeployError(f"這個專案已經有一個佈署在跑（{j.get('jobId')}，{STEP_LABEL.get(j.get('step'), j.get('step'))}），等它完成再送")
        job_id = f"dp-{uuid.uuid4().hex[:10]}"
        job = {
            "jobId": job_id, "slug": slug, "status": "queued", "step": "queued", "stepLabel": "排隊中",
            "progress": 0.0, "params": o, "existing": existing_counts(project),
            "warnings": [], "warningsByStep": {}, "timings": {},
            "written": {"characters": 0, "scenes": 0, "episodeScenes": 0, "shots": 0},
            "result": {"characters": 0, "scenes": 0, "episodes": 0, "shots": 0},
            "error": "", "failedStep": "", "createdAt": storage.now_iso(),
            "startedAt": "", "finishedAt": "", "elapsed": 0.0,
            "_cancel": threading.Event(),
        }
        _JOBS[job_id] = job
        if len(_JOBS) > _MAX_KEEP:
            for k in sorted(_JOBS, key=lambda x: _JOBS[x]["createdAt"]):
                if k != job_id and _JOBS[k].get("status") in ("done", "failed", "cancelled"):
                    _JOBS.pop(k, None)
                    break
    th = threading.Thread(target=_run, args=(job_id,), name=f"deploy-{job_id}", daemon=True)
    th.start()
    return get_job(job_id)


def cancel(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        if job.get("status") in ("queued", "running"):
            job["_cancel"].set()
    return get_job(job_id)


def _set(job_id: str, **kw) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.update(kw)


def _written_of(job_id: str) -> dict:
    with _LOCK:
        return dict((_JOBS.get(job_id) or {}).get("written") or {})


def _kept_note(job_id: str) -> str:
    """失敗／取消時的「已寫入」說明（逐步落地的重點：前面的成果不會白做）。"""
    w = _written_of(job_id)
    bits = [f"{w.get(n)} {label}" for n, label in (("characters", "角色"), ("scenes", "場景"),
                                                    ("episodeScenes", "場劇本"), ("shots", "鏡頭")) if w.get(n)]
    return ("\n已保留在專案裡：" + "、".join(bits)) if bits else "\n（這一輪還沒有任何資料寫入）"


def _run(job_id: str) -> None:
    with _LOCK:
        job = _JOBS[job_id]
        slug, opts = job["slug"], job["params"]
    t0 = time.time()
    _set(job_id, status="running", startedAt=storage.now_iso(), step="cast", stepLabel=STEP_LABEL["cast"],
         progress=0.02)
    done_steps: list[str] = []

    def progress(step: str, msg: str) -> None:
        base = len(done_steps) / 4.0
        _set(job_id, step=step, stepLabel=STEP_LABEL.get(step, step), progress=round(base + 0.02, 3),
             message=msg, elapsed=round(time.time() - t0, 1))

    try:
        cfg = storage.llm_settings(slug)
        # 逐步落地：每層生成完就寫檔（失敗時前面的成果保留，模型每次只算一小包）
        pack = run_steps_landing(job_id, slug, cfg, opts, progress, done_steps)
        r = pack["result"]
        _set(job_id, status="done", step="done", stepLabel="完成", progress=1.0,
             result={**{k: r[k] for k in ("characters", "scenes", "episodes", "shots")},
                     "mediaDeleted": r["mediaDeleted"], "ids": r["ids"]},
             warnings=pack["warnings"], warningsByStep=pack["warningsByStep"],
             timings=pack["timings"], finishedAt=storage.now_iso(),
             elapsed=round(time.time() - t0, 1), message="")
    except DeployError as e:
        with _LOCK:
            cur_written = dict(_JOBS[job_id].get("written") or {})
        w = cur_written or {}
        kept = ("；已保留在專案裡：" + "、".join(
            f"{w.get(n)} {label}" for n, label in (("characters", "角色"), ("scenes", "場景"),
                                                   ("episodeScenes", "場劇本"), ("shots", "鏡頭")) if w.get(n)))
        if e.step == "cancelled":
            _set(job_id, status="cancelled", step="cancelled", failedStep="cancelled",
                 error=str(e) + kept, written=w,
                 finishedAt=storage.now_iso(), elapsed=round(time.time() - t0, 1))
            return
        _set(job_id, status="failed", step=e.step or "failed", failedStep=e.step,
             error=str(e) + kept, written=w,
             finishedAt=storage.now_iso(), elapsed=round(time.time() - t0, 1))
    except storygen.GenError as e:
        with _LOCK:
            cur = _JOBS[job_id].get("step")
        raw = (e.raw or "")[:400]
        _set(job_id, status="failed", step=cur or "failed", failedStep=cur,
             error=f"{STEP_LABEL.get(cur, cur)}步驟失敗：{e}" + (f"\n模型原始回覆（前 400 字）：{raw}" if raw else "")
                   + _kept_note(job_id), written=_written_of(job_id),
             finishedAt=storage.now_iso(), elapsed=round(time.time() - t0, 1))
    except llm.LLMError as e:
        with _LOCK:
            cur = _JOBS[job_id].get("step")
        _set(job_id, status="failed", step=cur or "failed", failedStep=cur,
             error=f"模型服務錯誤（{STEP_LABEL.get(cur, cur)}步驟）：{e}" + _kept_note(job_id),
             written=_written_of(job_id), finishedAt=storage.now_iso(),
             elapsed=round(time.time() - t0, 1))
    except storage.StorageError as e:
        _set(job_id, status="failed", step="write", failedStep="write",
             error=f"寫入專案失敗：{e}" + _kept_note(job_id),
             written=_written_of(job_id), finishedAt=storage.now_iso(), elapsed=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001 —— 背景 thread 不能靜默死掉
        traceback.print_exc()
        _set(job_id, status="failed", step="failed", failedStep="failed",
             error=f"未預期的錯誤：{type(e).__name__}: {e}", finishedAt=storage.now_iso(),
             elapsed=round(time.time() - t0, 1))


def build_preview_with_progress(project: dict, cfg: dict, opts: dict, progress, done_steps: list,
                               job_id: str, t0: float) -> dict:
    """與 build_preview 同邏輯，但每完成一步就更新 job 進度（背景任務用）。"""
    fps = int((project.get("meta") or {}).get("fps") or 24)
    timings: dict[str, float] = {}
    warn_by: dict[str, list] = {}

    progress("cast", "產生角色卡…")
    t = time.time()
    first_c = 1 if opts["overwrite"] else int(re.sub(r"\D", "", storage.next_id(project.get("characters") or [], "c")) or 1)
    cast = gen_cast(project, cfg, opts, first_seq=first_c)
    chars = cast["preview"]["characters"]
    warn_by["cast"] = cast["warnings"]
    timings["cast"] = round(time.time() - t, 1)
    done_steps.append("cast")
    _check_cancel(job_id)

    progress("locations", "產生場景卡…")
    t = time.time()
    first_sc = 1 if opts["overwrite"] else int(re.sub(r"\D", "", storage.next_id(project.get("scenes") or [], "sc")) or 1)
    locs = gen_locations(project, cfg, opts, chars, first_seq=first_sc)
    scns = locs["preview"]["scenes"]
    warn_by["locations"] = locs["warnings"]
    timings["locations"] = round(time.time() - t, 1)
    done_steps.append("locations")
    _check_cancel(job_id)

    progress("script", "寫劇本（集／場／對白）…")
    t = time.time()
    ep_no = 1 + len(((project.get("script") or {}).get("episodes") or []))
    script = gen_script(project, cfg, opts, chars, scns, episode_no=ep_no)
    warn_by["script"] = script["warnings"]
    timings["script"] = round(time.time() - t, 1)
    done_steps.append("script")
    _check_cancel(job_id)

    progress("shots", "拆分鏡（六段式 prompt）…")
    t = time.time()
    sp = dict(script["preview"])
    es_list = [{"id": f"e{ep_no:02d}s{i + 1:02d}", **sc} for i, sc in enumerate(sp["scenes"])]
    sp["scenes"] = es_list
    shot_warns: list[str] = []
    sp_total = int(opts["shotCount"])
    if len(es_list) != int(opts["sceneCount"]):
        # 模型回傳的場數與要的不一致（validate 會提醒），配額照實際場數重新平均
        sp_total = max(sp_total, len(es_list))
    alloc, alloc_warns = allocate_shots(sp_total, len(es_list))
    shot_warns += alloc_warns
    job_cfg = dict(cfg or {})
    if job_cfg.get("provider"):
        job_cfg["providerLabel"] = "線上" if job_cfg["provider"] == "online" else (
            "自訂" if job_cfg["provider"] == "custom" else "本機")
    # 摘要要顯示「實際會用的模型」而不是設定值：設定寫 deepseek-chat 時後端會自動改用
    # 帳號清單裡存在的模型（例如 deepseek-flash），照設定值顯示等於騙人。
    try:
        st = llm.status(cfg or {})
        if st.get("ok") and st.get("model"):
            job_cfg["model"] = st["model"]
    except Exception:  # noqa: BLE001 —— 拿不到就退回設定值，不影響佈署
        pass
    _set(job_id, params={**opts, "shotPlan": alloc,
                         "provider": job_cfg.get("provider") or "", "providerLabel": job_cfg.get("providerLabel") or "",
                         "model": job_cfg.get("model") or ""})
    for k, es in enumerate(es_list):
        _check_cancel(job_id)
        if k:
            progress("shots", f"拆分鏡…（{k + 1}/{len(es_list)} 場）")
        beats = [b for b in (es.get("beats") or []) if isinstance(b, dict)]
        per = alloc[k] if k < len(alloc) else 1
        for w in shot_budget(beats, opts["shotSeconds"], per, fps)[1]:
            shot_warns.append(f"{es['id']}：{w}")
        res = gen_shots_for_scene(project, cfg, chars, scns, es, per,
                                  opts["shotSeconds"], fps, {})
        shot_warns += [f"{es['id']}：{w}" for w in res["warnings"]]
        es["shots"] = res["preview"]["shots"]
    warn_by["shots"] = shot_warns
    timings["shots"] = round(time.time() - t, 1)

    shots_out = []
    for es in es_list:
        for sh in es.pop("shots", []):
            shots_out.append({**sh, "episodeSceneId": es["id"], "sceneId": es.get("sceneId")})
    return {
        "preview": {"characters": chars, "scenes": scns,
                    "script": {"title": sp.get("title"), "logline": sp.get("logline"),
                               "sourceChapterId": sp.get("sourceChapterId"), "scenes": es_list},
                    "shots": shots_out},
        "warnings": [f"{STEP_LABEL[s]}：{w}" for s in STEPS for w in warn_by.get(s, [])],
        "warningsByStep": warn_by, "timings": timings,
    }


def _check_cancel(job_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        cancelled = bool(job and job["_cancel"].is_set())
    if cancelled:
        raise DeployError("使用者取消（已完成的層級會保留在專案裡）", step="cancelled", raw="")
