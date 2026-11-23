"""專案資料存取層：projects/<slug>/project.json 為單一真相。

所有寫入採「寫 .tmp → os.replace」的原子替換，並更新 meta.updatedAt。
規格見 docs/data-model.md（key 一律 camelCase）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── 根目錄（集中由 app/paths.py 決定：dev＝專案根；frozen＝exe 旁／SVS_DATA_DIR）──
from . import paths as _paths

ROOT = _paths.BUNDLE_DIR
PROJECTS_DIR = _paths.PROJECTS_DIR
TRASH_DIR = PROJECTS_DIR / "_trash"

MEDIA_KINDS = ("characters", "scenes", "shots", "clips", "voices")
UPLOAD_KINDS = ("characters", "scenes", "shots", "voices")
ASPECT_RATIOS = ("9:16", "3:4", "4:3", "1:1", "16:9", "21:9")

TOP_LEVEL_KEYS = (
    "meta",
    "story",
    "characters",
    "scenes",
    "script",
    "shots",
    "settings",
    "pipeline",
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_lock = threading.Lock()


class StorageError(Exception):
    """可預期的資料錯誤 → 由 API 層轉成 400/404，不該變 500。"""


def now_iso() -> str:
    """ISO8601 帶時區（台北）。"""
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


# ── slug ────────────────────────────────────────────────────────
def normalize_slug(raw: str) -> str:
    """把使用者輸入轉成安全 slug；含非英數字（例如中文）時回空字串。"""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if not _SLUG_RE.fullmatch(s):
        return ""
    return s


def generate_slug() -> str:
    """時間戳型 slug（中文專案名走這條；中文名只存 meta.name）。"""
    stamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d-%H%M%S")
    base = f"project-{stamp}"
    candidate = base
    n = 2
    while (PROJECTS_DIR / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def assert_slug(slug: str) -> str:
    """防路徑穿越：只允許 [a-z0-9_-]，拒絕 .. / 絕對路徑。"""
    if not _SLUG_RE.fullmatch(slug or ""):
        raise StorageError(f"專案代號不合法：{slug!r}（只允許小寫英數字、底線、連字號）")
    return slug


def project_dir(slug: str) -> Path:
    return PROJECTS_DIR / assert_slug(slug)


def project_file(slug: str) -> Path:
    return project_dir(slug) / "project.json"


# ── M9：影片生成參數（settings.video）───────────────────────────
# 預設值＝Pika Flex 官方建議（steps 6／cfg 1.0／shift 12-3／euler+simple／strength 1.0）。
# 不改 docs/data-model.md，舊專案由 fill_project_defaults 讀取時補上（不寫回檔）。
VIDEO_LORA_PIKA_BF16 = "pika_h3_flex_ref2va_comfy_bf16.safetensors"


def video_settings_defaults() -> dict:
    return {
        "lora": {"name": VIDEO_LORA_PIKA_BF16, "strength": 1.0, "active": True},
        "steps": 6, "cfg": 1.0, "shiftVideo": 12.0, "shiftAudio": 3.0,
        "sampler": "euler", "scheduler": "simple",
    }


def video_settings(project: dict) -> dict:
    """專案 settings.video（缺的欄位補預設）。永不拋例外。"""
    st = (project or {}).get("settings")
    got = (st or {}).get("video") if isinstance(st, dict) else None
    out = video_settings_defaults()
    if isinstance(got, dict):
        lora = got.get("lora")
        if isinstance(lora, dict):
            out["lora"] = {**out["lora"], **{k: v for k, v in lora.items() if k in ("name", "strength", "active")}}
        elif isinstance(lora, str):
            out["lora"] = {**out["lora"], "name": lora}
        for k in ("steps", "cfg", "shiftVideo", "shiftAudio", "sampler", "scheduler"):
            if got.get(k) not in (None, ""):
                out[k] = got[k]
    return out


# ── 空白骨架 ────────────────────────────────────────────────────
def blank_project(slug: str, name: str, aspect_ratio: str = "9:16") -> dict:
    """依 docs/data-model.md 產生完整空白 project.json（所有頂層 key 都在，空陣列而非缺 key）。"""
    if aspect_ratio not in ASPECT_RATIOS:
        raise StorageError(f"畫面比例不合法：{aspect_ratio!r}（合法值：{'/'.join(ASPECT_RATIOS)}）")
    ts = now_iso()
    return {
        "meta": {
            "slug": slug,
            "name": name,
            "version": 1,
            "createdAt": ts,
            "updatedAt": ts,
            "aspectRatio": aspect_ratio,
            "fps": 24,
        },
        "story": {
            "premise": "",
            "synopsis": "",
            "style": {"genre": "", "tone": "", "visualStyle": "", "negative": ""},
            "chapters": [],
        },
        "characters": [],
        "scenes": [],
        "script": {"episodes": []},
        "shots": [],
        "settings": {
            "llm": {
                "baseUrl": "http://127.0.0.1:8080/v1",
                "model": "auto",
                "temperature": 0.8,
                "provider": "local",
            },
            "comfy": {"baseUrl": "http://127.0.0.1:8000"},
            "tts": {"engine": "fish_s2", "baseUrl": ""},
            "video": video_settings_defaults(),
        },
        "pipeline": {"stage": "story", "logs": []},
    }


# ── v0.2 向後相容（M3c）──────────────────────────────────────
# 舊專案讀進來時補預設值（不寫回檔）；只在「缺就補」的前提下動，不覆寫既有值。
V02_SHOT_TYPES = ("close_up", "medium", "wide", "over_shoulder", "pov")
V02_ANGLES_H = ("front", "front_left", "left", "back_left", "back", "back_right", "right", "front_right")
V02_ANGLES_V = ("worm", "low", "eye_level", "high")
V02_ANGLES_S = ("close_up", "medium", "wide")
V02_CAMERA_MOVES = ("static", "push_in", "pull_out", "pan_left", "pan_right", "tracking", "handheld")
V02_FRAMING_RULE = "has_person→medium|mcu|cu（禁 wide）"
# 顯示層仍可存中文（M2b 既有資料）；生成時模型只准用英文枚舉
_SHOT_TYPE_ZH = {"特寫": "close_up", "中景": "medium", "全景": "wide", "過肩": "over_shoulder",
                 "POV": "pov", "pov": "pov", "其他": ""}


def blank_identity_anchors() -> dict:
    return {"faceShape": "", "facialFeatures": "", "uniqueMarks": "",
            "colorAnchors": {"hair": "", "eyes": "", "skin": "", "primaryOutfit": ""},
            "skinTexture": "", "hairStyle": ""}


def blank_angle() -> dict:
    return {"h": "", "v": "", "s": ""}


# ── M12a：鏡頭台詞引用改存 beat id（dialogBeatIds）───────────────
# 為什麼：舊欄位 dialogLineRefs 存「所屬場次第幾句台詞（1-based）」，
# 劇本頁在中間插／刪／搬一句，所有鏡頭的數字就集體錯位且畫面看不出來。
# 改存 beat id 後，句子搬家引用仍跟著走；舊資料在讀檔正規化時一次換算成 id。
def episode_scene_map(data: dict) -> dict[str, dict]:
    """場次 id → {sceneId, episodeId, title, dialogue: [dialogue beat...]}（依劇本順序）。"""
    out: dict[str, dict] = {}
    for ep in ((data.get("script") or {}).get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        for es in (ep.get("scenes") or []):
            if not isinstance(es, dict) or not es.get("id"):
                continue
            out[es["id"]] = {
                "sceneId": es.get("sceneId") or "",
                "episodeId": ep.get("id") or "",
                "title": es.get("title") or es.get("id"),
                "dialogue": [b for b in (es.get("beats") or [])
                             if isinstance(b, dict) and b.get("type") == "dialogue" and b.get("id")],
            }
    return out


def shot_dialog_beat_ids(shot: dict, data: dict) -> list[str]:
    """**唯讀換算**：鏡頭 → 台詞 beat id（不寫回）。

    優先用 dialogBeatIds；沒有才退回舊的 dialogLineRefs（場次內第 N 句台詞）。
    回傳順序照場次劇本順序（引用順序＝台詞演出順序，前端唯讀區與音訊段都用它）。
    """
    es_id = shot.get("episodeSceneId")
    dlg = (episode_scene_map(data).get(es_id) or {}).get("dialogue") or []
    by_index = {i + 1: str(b.get("id")) for i, b in enumerate(dlg)}
    ids = [x for x in (shot.get("dialogBeatIds") or []) if isinstance(x, str)]
    refs = [r for r in (shot.get("dialogLineRefs") or []) if isinstance(r, int) and not isinstance(r, bool)]
    want = set(ids) | {by_index[n] for n in refs if n in by_index}
    seen: set[str] = set()
    ordered: list[str] = []
    for b in dlg:
        bid = str(b.get("id"))
        if bid in want and bid not in seen:
            seen.add(bid)
            ordered.append(bid)
    return ordered


def dialog_beat_index(beat_ids: list[str], dialogue: list[dict]) -> list[int]:
    """beat id 清單 → 該場次台詞的 1-based 序號（舊 dialogLineRefs 的寫法）。"""
    pos = {str(b.get("id")): i + 1 for i, b in enumerate(dialogue or [])}
    return sorted({pos[b] for b in (beat_ids or []) if b in pos})


def migrate_dialog_linkage(data: dict) -> list[str]:
    """就地補 dialogBeatIds：無此欄位但有 dialogLineRefs → 依場次台詞序號換算成 beat id。

    為什麼寫在讀取正規化而不是 PUT：舊專案一打開就要看得到引用（鏡頭卡、驗證、編譯都靠它），
    不能等使用者手動存一次檔。換算不到（場次不存在／序號超範圍／beat 沒有 id）時**保留舊欄位、
    不清空、也不拋錯**，只回訊息由 API 附給前端。
    """
    notes: list[str] = []
    projects = episode_scene_map(data)
    for sh in (data.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        raw = sh.get("dialogBeatIds")
        if isinstance(raw, list):
            # 已有新欄位：只清洗型別，並用它同步舊欄位（後端舊路徑仍會讀 dialogLineRefs）
            sh["dialogBeatIds"] = [x for x in raw if isinstance(x, str)]
        else:
            refs = [r for r in (sh.get("dialogLineRefs") or []) if isinstance(r, int) and not isinstance(r, bool)]
            if not refs:
                sh["dialogBeatIds"] = []
                continue
            es_id = sh.get("episodeSceneId")
            dlg = (projects.get(es_id) or {}).get("dialogue") or []
            if not es_id or es_id not in projects:
                sh["dialogBeatIds"] = []
                notes.append(f"鏡頭 {sh.get('id')} 有 dialogLineRefs 但沒有有效的所屬場次，無法換算（舊值保留）")
                continue
            got: list[str] = []
            bad: list[int] = []
            for n in refs:
                if 1 <= n <= len(dlg) and dlg[n - 1].get("id"):
                    got.append(str(dlg[n - 1]["id"]))
                else:
                    bad.append(n)
            if bad:
                notes.append(f"鏡頭 {sh.get('id')} 的 dialogLineRefs {bad} 在場次 {es_id} 換算不到台詞（已忽略）")
            if not got and bad:
                sh["dialogBeatIds"] = []
                continue  # 一個都換算不到時不清空舊欄位，讓使用者看得到原值自己修
            sh["dialogBeatIds"] = sorted(set(got), key=got.index)
        # 與舊欄位同步（寫入 dialogBeatIds 時重算 dialogLineRefs）
        es_id = sh.get("episodeSceneId")
        if sh["dialogBeatIds"] and es_id in projects:
            idx = dialog_beat_index(sh["dialogBeatIds"], projects[es_id]["dialogue"])
            if idx:
                sh["dialogLineRefs"] = idx
                continue
        if not isinstance(sh.get("dialogLineRefs"), list):
            sh["dialogLineRefs"] = []
    return notes


def fill_project_defaults(data: dict) -> dict:
    """補 v0.2 欄位預設（就地修改並回傳同一物件）。永不拋例外、不寫檔。"""
    if not isinstance(data, dict):
        return data
    st = data.get("settings")
    if isinstance(st, dict):
        base_v = video_settings_defaults()
        cur = st.get("video")
        if not isinstance(cur, dict):
            st["video"] = base_v
        else:
            merged = dict(base_v)
            merged.update({k: v for k, v in cur.items() if v is not None})
            if isinstance(merged.get("lora"), str):
                merged["lora"] = {**base_v["lora"], "name": merged["lora"]}
            elif not isinstance(merged.get("lora"), dict):
                merged["lora"] = base_v["lora"]
            else:
                merged["lora"] = {**base_v["lora"], **merged["lora"]}
            st["video"] = merged
    data.setdefault("voiceMap", {})
    if not isinstance(data.get("voiceMap"), dict):
        data["voiceMap"] = {}
    if not isinstance(data.get("narrator"), dict):
        data["narrator"] = {"engine": "", "refAudio": "", "note": ""}
    for c in data.get("characters") or []:
        if not isinstance(c, dict):
            continue
        anchors = c.get("identityAnchors")
        if not isinstance(anchors, dict):
            c["identityAnchors"] = blank_identity_anchors()
        else:
            base = blank_identity_anchors()
            for k, v in anchors.items():
                if k == "colorAnchors" and isinstance(v, dict):
                    base["colorAnchors"].update({kk: vv for kk, vv in v.items() if kk in base["colorAnchors"]})
                elif k in base:
                    base[k] = v
            c["identityAnchors"] = base
        if not isinstance(c.get("stages"), list):
            c["stages"] = []
    for sh in data.get("shots") or []:
        if not isinstance(sh, dict):
            continue
        spec = sh.get("spec")
        if not isinstance(spec, dict):
            spec = sh["spec"] = {}
        spec.setdefault("shotType", "")
        angle = spec.get("angle")
        if isinstance(angle, str):  # 舊值可能是自由文字
            spec["angle"] = {**blank_angle(), **({"h": angle} if angle else {})}
        elif not isinstance(angle, dict):
            spec["angle"] = blank_angle()
        else:
            spec["angle"] = {**blank_angle(), **{k: angle.get(k) or "" for k in ("h", "v", "s")}}
        spec.setdefault("cameraMove", "")
        spec.setdefault("composition", "")
        spec.setdefault("lighting", "")
        spec.setdefault("framingRule", V02_FRAMING_RULE)
        if not isinstance(sh.get("beats"), list):
            sh["beats"] = []
        if not isinstance(sh.get("dialogLineRefs"), list):
            sh["dialogLineRefs"] = []
        if not isinstance(sh.get("stageIds"), list):
            sh["stageIds"] = []
        if not isinstance(sh.get("referencePlan"), dict):
            sh["referencePlan"] = {"slots": [], "weakReferences": [], "promptPlaceholders": {}}
        else:
            rp = sh["referencePlan"]
            rp.setdefault("slots", [])
            rp.setdefault("weakReferences", [])
            rp.setdefault("promptPlaceholders", {})
    # M12a：鏡頭台詞引用（dialogLineRefs → dialogBeatIds）在讀入時換算
    migrate_dialog_linkage(data)
    return data


def shot_type_en(value: object) -> str:
    """顯示層中文值 → v0.2 英文枚舉（認不得給空字串，由呼叫端決定）。"""
    s = str(value or "").strip()
    if s in V02_SHOT_TYPES:
        return s
    return _SHOT_TYPE_ZH.get(s, "")


def ensure_media_dirs(slug: str) -> None:
    for kind in MEDIA_KINDS:
        (project_dir(slug) / "media" / kind).mkdir(parents=True, exist_ok=True)


# ── ID 產生器（M2a）────────────────────────────────────────────
# 規則見 docs/data-model.md：c01 / sc01 / e01 / sh01（前綴 + 兩位流水號，建立後不可改）。
ID_PREFIXES = {"characters": "c", "scenes": "sc"}
_ID_RE = re.compile(r"^([a-z]+)(\d{2,})$")


def next_id(items: list, prefix: str) -> str:
    """掃現有 id 取「前綴相同者」的最大號 +1，回前綴+兩位流水號。空清單 → 前綴+01。"""
    biggest = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        m = _ID_RE.fullmatch(str(item.get("id") or ""))
        if m and m.group(1) == prefix:
            biggest = max(biggest, int(m.group(2)))
    n = biggest + 1
    out = f"{prefix}{n:02d}"
    existing = {str(i.get("id")) for i in items or [] if isinstance(i, dict)}
    while out in existing:  # 極端情況（例如手改成 c01b）保險
        n += 1
        out = f"{prefix}{n:02d}"
    return out


# ── 驗證 ────────────────────────────────────────────────────────
def validate_project(data: object) -> list[str]:
    """基本 schema 驗證：頂層 key 齊全、id 引用存在。回錯誤字串清單（空＝通過）。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["project.json 必須是物件"]

    for key in TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"缺少頂層欄位：{key}")
    if errors:
        return errors

    meta = data["meta"]
    if not isinstance(meta, dict):
        errors.append("meta 必須是物件")
    else:
        if not str(meta.get("slug", "")).strip():
            errors.append("meta.slug 不可為空")
        if not str(meta.get("name", "")).strip():
            errors.append("meta.name 不可為空")
        if meta.get("aspectRatio") not in ASPECT_RATIOS:
            errors.append(f"meta.aspectRatio 不合法：{meta.get('aspectRatio')!r}")

    story = data["story"]
    if not isinstance(story, dict):
        errors.append("story 必須是物件")
    else:
        if not isinstance(story.get("style"), dict):
            errors.append("story.style 必須是物件")
        if not isinstance(story.get("chapters"), list):
            errors.append("story.chapters 必須是陣列")

    for key in ("characters", "scenes", "shots"):
        if not isinstance(data[key], list):
            errors.append(f"{key} 必須是陣列")

    script = data["script"]
    if not isinstance(script, dict) or not isinstance(script.get("episodes"), list):
        errors.append("script.episodes 必須是陣列")

    if not isinstance(data["settings"], dict):
        errors.append("settings 必須是物件")
    pipeline = data["pipeline"]
    if not isinstance(pipeline, dict) or not isinstance(pipeline.get("logs"), list):
        errors.append("pipeline.logs 必須是陣列")

    if errors:
        return errors

    # id 集合
    char_ids = {c.get("id") for c in data["characters"] if isinstance(c, dict) and c.get("id")}
    scene_ids = {s.get("id") for s in data["scenes"] if isinstance(s, dict) and s.get("id")}

    # 人物 / 場景 id 唯一性與格式（M2a：新增卡片時補上）
    for key, prefix, label in (("characters", "c", "人物"), ("scenes", "sc", "場景")):
        seen_ids: set[str] = set()
        for item in data[key]:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or "")
            if not re.fullmatch(rf"{prefix}\d{{2,}}", iid):
                errors.append(f"{label} id 格式不合法：{iid!r}（應為 {prefix}01 這種形式）")
            if iid in seen_ids:
                errors.append(f"{label} id 重複：{iid}")
            seen_ids.add(iid)

    episode_scene_ids: set[str] = set()
    for ep in data["script"]["episodes"]:
        if not isinstance(ep, dict):
            errors.append("script.episodes 內每項必須是物件")
            continue
        for es in ep.get("scenes", []) or []:
            if isinstance(es, dict) and es.get("id"):
                episode_scene_ids.add(es["id"])

    def check(ref_field: str, value: object, pool: set[str], where: str) -> None:
        if value in (None, ""):
            return
        if value not in pool:
            errors.append(f"{where} 的 {ref_field} 引用不存在：{value!r}")

    # 劇本場次引用
    for ep in data["script"]["episodes"]:
        if not isinstance(ep, dict):
            continue
        for es in ep.get("scenes", []) or []:
            if not isinstance(es, dict):
                continue
            where = f"劇本場次 {es.get('id', '?')}"
            check("sceneId", es.get("sceneId"), scene_ids, where)
            for cid in es.get("characterIds", []) or []:
                check("characterIds", cid, char_ids, where)
            for beat in es.get("beats", []) or []:
                if isinstance(beat, dict) and not beat.get("_skipped"):
                    check("characterId", beat.get("characterId"), char_ids, f"{where} 的 beat {beat.get('id', '?')}")

    # 鏡頭引用
    seen_no: set[int] = set()
    # M3c：場次 → 該場 dialogue beat 數（dialogLineRefs 上界）
    beat_count = {}
    beat_id_map: dict[str, list[dict]] = {}
    for ep in data["script"]["episodes"]:
        if not isinstance(ep, dict):
            continue
        for es in ep.get("scenes", []) or []:
            if isinstance(es, dict) and es.get("id"):
                beat_count[es["id"]] = len([b for b in (es.get("beats") or [])
                                            if isinstance(b, dict) and not b.get("_skipped")])
                beat_id_map[es["id"]] = [b for b in (es.get("beats") or [])
                                         if isinstance(b, dict) and b.get("type") == "dialogue"
                                         and not b.get("_skipped")]
    for shot in data["shots"]:
        if not isinstance(shot, dict):
            errors.append("shots 內每項必須是物件")
            continue
        where = f"鏡頭 {shot.get('id', '?')}"
        check("episodeSceneId", shot.get("episodeSceneId"), episode_scene_ids, where)
        check("sceneId", shot.get("sceneId"), scene_ids, where)
        for cid in shot.get("characterIds", []) or []:
            check("characterIds", cid, char_ids, where)
        no = shot.get("no")
        if isinstance(no, int):
            if no in seen_no:
                errors.append(f"鏡頭 no 重複：{no}")
            seen_no.add(no)
        else:
            errors.append(f"{where} 的 no 必須是整數")
        # v0.2：dialogLineRefs 必須落在所屬場次的 beat 範圍內且不重複
        upper = beat_count.get(shot.get("episodeSceneId"))
        for ref in shot.get("dialogLineRefs") or []:
            if not isinstance(ref, int) or isinstance(ref, bool):
                errors.append(f"{where} 的 dialogLineRefs 含非整數：{ref!r}")
            elif upper is not None and not (1 <= ref <= upper):
                errors.append(f"{where} 的 dialogLineRefs {ref} 超出所屬場次的 beat 數（1–{upper}）")
        refs = [r for r in (shot.get("dialogLineRefs") or []) if isinstance(r, int)]
        if len(refs) != len(set(refs)):
            errors.append(f"{where} 的 dialogLineRefs 有重複")
        # M12a：新欄位 dialogBeatIds 必須是「所屬場次」存在的台詞 beat id（能存就存，
        # 但指向別場／已刪除的句子要報出來——那是引用失效，不是資料缺欄位）
        beat_ids = shot.get("dialogBeatIds")
        if beat_ids is not None:
            if not isinstance(beat_ids, list):
                errors.append(f"{where} 的 dialogBeatIds 必須是陣列")
            else:
                allowed = {str(b.get("id")) for b in (beat_id_map.get(shot.get("episodeSceneId")) or [])}
                all_ids = {str(b.get("id")) for dl in beat_id_map.values() for b in dl}
                for bid in beat_ids:
                    if not isinstance(bid, str):
                        errors.append(f"{where} 的 dialogBeatIds 含非字串值：{bid!r}")
                    elif bid not in allowed:
                        hint = "（屬於其他場次）" if bid in all_ids else ""
                        errors.append(f"{where} 的 dialogBeatIds 引用了所屬場次沒有的台詞 {bid}{hint}")
        for sid in shot.get("stageIds") or []:
            if not isinstance(sid, str) or ":" not in sid or sid.split(":", 1)[0] not in char_ids:
                errors.append(f"{where} 的 stageIds 不合法：{sid!r}（格式 c01:s1，角色必須存在）")
        for beat in shot.get("beats") or []:
            if isinstance(beat, dict):
                check("locationId", beat.get("locationId"), scene_ids, where)

    return errors


# ── CRUD ────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def list_projects() -> list[dict]:
    """掃 projects/ 回 [{slug,name,updatedAt,shotCount,coverImage}]，依 updatedAt 新到舊。"""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    if not PROJECTS_DIR.is_dir():
        return out
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue  # _trash 等內部目錄不列入
        pj = entry / "project.json"
        if not pj.is_file():
            continue
        try:
            data = _read_json(pj)
        except Exception:
            continue
        meta = data.get("meta") or {}
        cover = ""
        for shot in data.get("shots") or []:
            if isinstance(shot, dict):
                first = ((shot.get("images") or {}).get("first")) or ""
                if first:
                    cover = first
                    break
        if not cover:
            for char in data.get("characters") or []:
                if isinstance(char, dict):
                    for ref in char.get("refImages") or []:
                        if isinstance(ref, dict) and ref.get("path"):
                            cover = ref["path"]
                            break
                if cover:
                    break
        if not cover:
            for scene in data.get("scenes") or []:
                if isinstance(scene, dict):
                    for ref in scene.get("refImages") or []:
                        if isinstance(ref, dict) and ref.get("path"):
                            cover = ref["path"]
                            break
                if cover:
                    break
        out.append(
            {
                "slug": meta.get("slug") or entry.name,
                "name": meta.get("name") or entry.name,
                "updatedAt": meta.get("updatedAt") or "",
                "shotCount": len(data.get("shots") or []),
                "coverImage": cover,
            }
        )
    out.sort(key=lambda p: p["updatedAt"], reverse=True)
    return out


def create_project(name: str, aspect_ratio: str = "9:16", slug: str | None = None) -> dict:
    """建立專案：slug 一律 ASCII 小寫；中文名只存 meta.name。回完整 project.json。"""
    name = (name or "").strip()
    if not name:
        raise StorageError("專案名稱不可為空")
    with _lock:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        slug = normalize_slug(slug or "") or generate_slug()
        if (project_dir(slug) / "project.json").exists():
            raise StorageError(f"專案代號已存在：{slug}")
        data = blank_project(slug, name, aspect_ratio)
        errors = validate_project(data)
        if errors:
            raise StorageError("；".join(errors))
        d = project_dir(slug)
        d.mkdir(parents=True)
        ensure_media_dirs(slug)
        _write_json_atomic(project_file(slug), data)
    return data


def load_project(slug: str) -> dict:
    path = project_file(slug)
    if not path.is_file():
        raise StorageError(f"找不到專案：{slug}")
    data = _read_json(path)
    # M3c：舊專案缺 v0.2 欄位時在記憶體補預設（不寫回，除非使用者另存）
    before = {sh.get("id"): (sh.get("dialogBeatIds") is not None)
              for sh in (data.get("shots") or []) if isinstance(sh, dict)}
    notes = fill_project_defaults_notes(data)
    data["_dialogMigration"] = {
        "applied": [sid for sid, had in before.items() if not had],
        "notes": notes,
    }
    return data


def fill_project_defaults_notes(data: dict) -> list[str]:
    """fill_project_defaults 的旁註版（M14a 起）：回台詞引用／分鏡欄位遷移過程的訊息（給前端提示用）。"""
    notes = migrate_dialog_linkage(data)
    try:
        from . import storyboard as _sb  # 延遲匯入：storyboard 會反過來引用本模組
        notes = _sb.migrate_shot_fields(data) + notes
    except Exception:  # 遷移只是加值功能，不能讓讀檔失敗
        pass
    return notes


def save_project(slug: str, data: dict) -> dict:
    """整份寫回（先驗證，再更新 meta.updatedAt，再原子替換）。"""
    from . import storyboard  # 延遲匯入：storyboard 會反過來引用本模組（避免循環匯入）
    assert_slug(slug)
    if not isinstance(data, dict):
        raise StorageError("project.json 必須是物件")
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise StorageError("缺少 meta")
    data.pop("_dialogMigration", None)  # 讀取時附給前端的遷移訊息，不寫回檔
    meta["slug"] = slug  # 不讓 body 把 slug 改掉（避免目錄與 slug 分岔）
    strip_llm_api_key(data)  # M3b：API key 永遠不進 project.json
    # M14a：舊欄位收斂（shot.beats → legacyBeats；不碰 beatIds）——必須在任何驗證之前，
    # 否則新契約把「只有自由敘述、沒有 beatIds」的舊鏡頭當成缺覆蓋。
    storyboard.migrate_shot_fields(data)
    # M12a：先把台詞引用正規化（換算／同步 dialogLineRefs），再驗證——
    # 否則只帶舊欄位或只帶新欄位的前端版本會撞到不一致的引用而 400。
    migrate_dialog_linkage(data)
    errors = validate_project(data)
    if errors:
        raise StorageError("；".join(errors))
    path = project_file(slug)
    if not path.is_file():
        raise StorageError(f"找不到專案：{slug}")
    with _lock:
        created = meta.get("createdAt") or now_iso()
        meta["createdAt"] = created
        meta["updatedAt"] = now_iso()
        ensure_media_dirs(slug)
        _write_json_atomic(path, data)
    return data


def delete_project(slug: str) -> str:
    """移到 projects/_trash/（不直接刪，防誤刪）。回 trash 內的新目錄名。"""
    src = project_dir(slug)
    if not src.is_dir():
        raise StorageError(f"找不到專案：{slug}")
    with _lock:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d-%H%M%S")
        dest = TRASH_DIR / f"{slug}-{stamp}"
        n = 2
        while dest.exists():
            dest = TRASH_DIR / f"{slug}-{stamp}-{n}"
            n += 1
        shutil.move(str(src), str(dest))
    return dest.name


def safe_filename(filename: str) -> str:
    """去掉目錄與危險字元，只留英數點底線連字號。"""
    base = os.path.basename(filename or "").strip()
    stem, ext = os.path.splitext(base)
    stem = _SAFE_NAME_RE.sub("-", stem).strip("-.") or "file"
    ext = _SAFE_NAME_RE.sub("", ext)[:10]
    return f"{stem}{ext}"


# ── M3a：LLM 設定與「套用到欄位」（M3b 擴充到劇本／鏡頭）──────────
# 只允許改這些容器的欄位路徑（防模型/前端亂寫 meta、pipeline 等）
_APPLY_ROOT_KINDS = ("story", "character", "scene")
_PROTECTED_KEYS = {"id", "path", "refAudio"}  # 定位用或檔案路徑，不接受模型文字覆寫
_MAX_PATH_DEPTH = 6                          # dialogue.0.line / spec.lighting 都需要 3–4 層

# M3b：每個 kind 允許套用的欄位（白名單；前綴 _ 表示「該卡本身」，其餘填點分路徑）
# M13a：加入「整份結構陣列」（章回／beats／dialogue／outfits）與其元素欄位；
#       形狀由 _norm_array_items 驗，不合法就中文報錯（不寫進專案）。
APPLY_FIELD_WHITELIST = {
    "story": {"synopsis", "premise", "style.genre", "style.tone", "style.visualStyle", "style.negative",
              "chapters", "chapters.*.title", "chapters.*.body", "chapters.*.summary"},
    "character": {
        "appearance.promptEn", "appearance.face", "appearance.hair", "appearance.body", "appearance.skin",
        # background.*：UI 與生成流程已移除，但保留白名單讓舊前端／既有腳本送這個欄位不會 400
        "background.birth", "background.personality", "background.goal", "background.secret",
        "voice.desc",
        "outfits", "outfits.*.name", "outfits.*.desc", "outfits.*.promptEn",
    },
    "scene": {"desc", "promptEn", "lighting", "mood", "timeOfDay", "ambience.note"},
    "episodeScene": {"summary", "title",
                     "beats", "beats.*.id", "beats.*.line", "beats.*.action", "beats.*.emotion",
                     "beats.*.notes", "beats.*.type", "beats.*.characterId"},
    "beat": {"line", "emotion", "notes"},
    "shot": {
        "promptZh", "promptEn",
        "spec.shotType", "spec.angle", "spec.cameraMove", "spec.composition", "spec.lighting",
        "spec.angle.h", "spec.angle.v", "spec.angle.s",
        "dialogue", "dialogue.*.line", "dialogue.*.characterId",
        "beats",
        # M14a：分鏡契約欄位（分鏡表可直接改這幾項，改完仍走 validate_project 的 V1–V5）
        "beatIds", "beatPart", "role", "padSec", "durationSec",
    },
}

# 陣列索引路徑用「*」比對（outfits.*.promptEn、dialogue.0.line）
_APPLY_FIELD_PATTERNS = {
    "outfits.*.promptEn", "outfits.*.name", "dialogue.*.line", "dialogue.*.characterId",
    "chapters.*.title", "chapters.*.body", "chapters.*.summary",
    "beats.*.line", "beats.*.action", "beats.*.emotion", "beats.*.notes",
    "beats.*.type", "beats.*.characterId", "beats.*.id",
}

# ── M13a：結構陣列的元素形狀（整份寫入前逐項驗，append 時自動補 id）──────
_BEAT_TYPES = ("dialogue", "action")
# 欄位名 → (中文標籤, 是否必須是文字)
_ARRAY_ITEM_FIELDS = {
    "chapters": {"id": ("id", False), "title": ("標題", False), "summary": ("摘要", False),
                 "body": ("正文", False), "content": ("正文", False)},
    "beats": {"id": ("id", False), "type": ("類型", False), "characterId": ("角色", False),
              "line": ("台詞", False), "action": ("動作", False), "emotion": ("情緒", False),
              "notes": ("備註", False), "start": ("開始秒數", True), "end": ("結束秒數", True),
              "cut": ("剪點", True), "camera": ("運鏡", False), "locationId": ("場景", False)},
    "dialogue": {"characterId": ("角色", False), "line": ("台詞", False)},
    "outfits": {"id": ("id", False), "name": ("名稱", False), "desc": ("描述", False),
                "promptEn": ("英文 prompt", False)},
}
_ARRAY_PREFIX = {"chapters": "ch", "beats": "b", "dialogue": "d", "outfits": "o"}
_ARRAY_LABEL = {"chapters": "章回", "beats": "beat", "dialogue": "台詞", "outfits": "服裝"}


def _apply_allowed(kind: str, path: str) -> bool:
    base = APPLY_FIELD_WHITELIST.get(kind, set()) | (_APPLY_FIELD_PATTERNS.get(kind, set()))
    if path in base:
        return True
    # 數字索引換成 * 再比一次（outfits.0.promptEn → outfits.*.promptEn）
    normalized = ".".join("*" if part.isdigit() else part for part in (path or "").split("."))
    return normalized in base


def _apply_allowed_list(kind: str) -> list[str]:
    # _APPLY_FIELD_PATTERNS 是「扁平 set」（全部 kind 共用），不是 dict——
    # 舊寫法 .get(kind) 會在「不允許寫入的欄位」這條錯誤訊息裡炸 AttributeError，
    # 使用者看到的是 HTTP 500（而不是清楚的 400 與可用欄位清單）。
    pool = set(APPLY_FIELD_WHITELIST.get(kind, set())) | set(_APPLY_FIELD_PATTERNS)
    return sorted(pool)


def _array_key(path: str) -> str | None:
    """整份陣列路徑（chapters / beats / dialogue / outfits）回它的 key，其餘 None。"""
    p = (path or "").strip()
    return p if p in _ARRAY_ITEM_FIELDS else None


# 指向 id 的字串陣列（可直接整份覆寫，不像物件陣列要逐項驗欄位）
_SIMPLE_ID_ARRAYS = {"beatIds", "stageIds", "dialogBeatIds"}

_ELEM_ALIAS = {("chapters", "body"): "content"}  # 章回正文在資料模型叫 content


def _elem_field_path(path: str, key: str) -> str:
    """chapters.1.body → chapters.1.content；beats.0.x 原樣。"""
    parts = path.split(".")
    if len(parts) >= 3:
        parts[2] = _ELEM_ALIAS.get((key, parts[2]), parts[2])
    return ".".join(parts)


def character_name_map(data: dict) -> dict[str, str]:
    """角色名字／別名 → id（M13a：模型輸出常寫名字，「角色名：台詞」要能對到 id）。"""
    out: dict[str, str] = {}
    for c in data.get("characters") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        for label in [c.get("name")] + list(c.get("aliases") or []):
            name = str(label or "").strip()
            if name and name not in out:
                out[name] = str(c["id"])
    return out


def resolve_character_id(data: dict, value, *, warn_name: str = "") -> tuple[str | None, str | None]:
    """characterId 正規化：id 直接用；給名字就查 id；查不到回 (None, 警告文字)。"""
    raw = str(value or "").strip()
    if not raw:
        return None, None
    ids = {str(c.get("id")) for c in data.get("characters") or [] if isinstance(c, dict)}
    if raw in ids:
        return raw, None
    hit = character_name_map(data).get(raw)
    if hit:
        return hit, None
    who = f"{warn_name} " if warn_name else ""
    return None, f"{who}的角色「{raw}」對不到現有人物，characterId 已存 null（請自行指定）"


def _add_skipped(value: object) -> object:
    """beats／dialogue 整份覆寫後，補回被換掉的舊項（標記 _skipped，不進生成）。

    M12a 讓 mirror 是單向的：場次 beats 是真相，鏡頭 beats 從它生成。
    模型只改了台詞時把舊的動作／鏡位丟掉太可惜，所以保留在檔案裡（帶 _skipped），
    之後可使用者在劇本頁還原；驗證時帶 _skipped 的項把引用欄位先清空再檢查。
    """
    out = []
    for it in value if isinstance(value, list) else []:
        if isinstance(it, dict):
            it = {**it, "_skipped": True}
        out.append(it)
    return out


def _diff_arrays(old: list, new: list) -> list:
    """old 有、new 沒有的項（以關鍵字比對）→ 加上 _skipped。"""
    def key(x):
        return json.dumps({k: v for k, v in x.items() if not k.startswith("_") and k != "id"},
                          ensure_ascii=False, sort_keys=True)
    keys = {key(x) for x in new if isinstance(x, dict)}
    return [x for x in old if isinstance(x, dict) and key(x) not in keys]


def _reconcile_beat_refs(data: dict, es: dict, valid_ids: set[str], label: str,
                         warnings: list[str]) -> None:
    """場次 beats 換掉後，把引用它的鏡頭對齊（M12a 的約定：不讓引用指向不存在的句子）。

    - dialogBeatIds 只留這次仍存在的句子（被刪掉的句子引用一併移除）
    - dialogLineRefs 依「每個 dialogue beat 的舊→新」換算；
      舊句子不在新清單裡就看得出「你這鏡原本演的那句被換掉了」→ 該鏡不再引用（不硬塞別句台詞）
    """
    old_dlg = [b for b in (es.get("beats") or []) if isinstance(b, dict) and b.get("type") == "dialogue"]
    new_dlg = [b for b in (es.get("beats") or [])
               if isinstance(b, dict) and b.get("type") == "dialogue" and not b.get("_skipped")]
    removed = len({str(b.get("id")) for b in old_dlg} - {str(b.get("id")) for b in new_dlg})
    touched = 0
    for sh in data.get("shots") or []:
        if not isinstance(sh, dict) or sh.get("episodeSceneId") != es.get("id"):
            continue
        ids = [x for x in (sh.get("dialogBeatIds") or []) if isinstance(x, str)]
        kept = [x for x in ids if x in valid_ids]
        lost = len(ids) - len(kept)
        if lost or ids != sh.get("dialogBeatIds"):
            touched += 1
            sh["dialogBeatIds"] = kept
        sh["dialogLineRefs"] = dialog_beat_index(kept, new_dlg)
    if touched and removed:
        warnings.append(
            f"{label}原有的 {removed} 句台詞被換掉，引用它們的 {touched} 個鏡頭已不再引用該句"
            f"（要保留請在鏡頭卡重新勾選「引用劇本台詞」）")


def _norm_array_items(data: dict, key: str, items, *, mode: str, existing: list,
                      label: str) -> tuple[list[dict], list[str]]:
    """把「整份陣列」的輸入驗成合法元素清單。不合法 → StorageError（中文，指出第幾項）。

    - 只收已知欄位（防模型亂塞 promptPlaceholders 等程式產生欄位）
    - id：沒給就自動編（沿用 storage.next_id，append 時避開既有 id）；重複 → 報錯
    - 章回：body 與 content 同義，一律存 content（前端故事頁讀 content）
    - beats：type 只收 dialogue／action；characterId 給名字自動換 id（對不到存 null＋warning）
    """
    if not isinstance(items, list):
        raise StorageError(f"{label}必須是陣列（收到 {type(items).__name__}）")
    if mode == "append" and not items:
        raise StorageError(f"附加（append）{label}至少要有一項")
    fields = _ARRAY_ITEM_FIELDS[key]

    def _fields_for(alias: bool) -> dict:
        f = dict(fields)
        if alias and key == "beats":
            f["action"] = fields["line"]       # beats.*.action 是 line 的別名（不改資料模型）
        return f

    out: list[dict] = []
    warnings: list[str] = []
    hard = [dict(x) for x in (existing or []) if isinstance(x, dict)]
    for i, item in enumerate(items):
        where = f"{label} 第 {i + 1} 項"
        if not isinstance(item, dict):
            raise StorageError(f"{where}必須是物件（例如 {{\"title\": …, \"body\": …}}）")
        unknown = [k for k in item.keys() if k not in fields]
        if unknown:
            raise StorageError(
                f"{where}含不接受的欄位：{'、'.join(map(str, unknown))}（可用：{'、'.join(fields)}）")
        row: dict = {}
        for k, v in item.items():
            ok_text = _fields_for(True)[k][1]
            if v is None:
                continue
            if ok_text and not isinstance(v, (int, float, bool)):
                raise StorageError(f"{where}的{fields[k][0]}（{k}）必須是數字或布林")
            if not ok_text and not isinstance(v, str):
                raise StorageError(f"{where}的{fields[k][0]}（{k}）必須是文字（收到 {type(v).__name__}）")
            row[k] = v
        if isinstance(item.get("_skipped"), bool):
            row["_skipped"] = item["_skipped"]
        if key == "chapters":
            content = row.pop("body", "") or row.get("content") or ""
            row["content"] = str(content)
            title = str(row.get("title") or "").strip()
            if not (title or row["content"].strip()):
                raise StorageError(f"{where}的標題與正文都是空的")
            row["title"] = title
            row.setdefault("summary", str(row.get("summary") or ""))
        if key == "beats" and "type" in row and row["type"]:
            t = str(row["type"]).strip()
            if t not in _BEAT_TYPES:
                raise StorageError(
                    f"{where}的類型（type）只能是 dialogue 或 action（收到 {t!r}）")
            row["type"] = t
        if key in ("beats", "dialogue") and "characterId" in row:
            cid, warn = resolve_character_id(data, row.get("characterId"), warn_name=where)
            row["characterId"] = cid
            if warn:
                warnings.append(warn)
        if key == "beats" and any("action" in it for it in items if isinstance(it, dict)):
            warnings.append(
                "beats 的 action 欄位已寫進 line（資料模型沒有單獨的 action；台詞與動作要分開請拆成兩個 beat）")
        if key == "chapters" and any("body" in it for it in items if isinstance(it, dict)) \
                and not any("body 已寫進 content" in w for w in warnings):
            warnings.append("章回的 body 已寫進 content（故事頁的正文欄位）")
        if key == "beats" and row.get("type") in (None, "") and row.get("line"):
            row["type"] = "dialogue"
        old_id = row.get("id")
        if old_id:
            oid = str(old_id).strip()
            if oid in {str(x.get("id")) for x in out}:
                raise StorageError(f"{where}的 id 重複：{oid}")
            row["id"] = oid
        out.append(row)
    # 缺 id 的自動編號（append 時把既有的一起算進去，避免撞號）
    need = [row for row in out if not row.get("id")]
    if need:
        pool = hard + [r for r in out if r.get("id")]
        for row in need:
            row["id"] = next_id(pool, _ARRAY_PREFIX[key])
            pool = pool + [row]
    if key == "beats":
        seen_lines = "、".join(f"第 {i + 1} 項" for i, r in enumerate(out) if not str(r.get("line") or "").strip())
        if seen_lines:
            warnings.append(f"{seen_lines}沒有台詞／動作（line 是空的）")
    return out, warnings


def _merge_array_value(key: str, old, value, mode: str, *, label: str) -> list[dict]:
    """整份陣列的 replace／append 換算（append 只加在尾端，不覆蓋既有）。

    附加的新項一律拿掉 id：append 是「多一回／多一句」，沿用既有 id 會撞號，
    由 _norm_array_items 重新編。
    """
    if not isinstance(value, list):
        raise StorageError(f"{label}必須是陣列（收到 {type(value).__name__}）")
    if mode == "append":
        extra = [dict(x) for x in value if isinstance(x, dict)]
        for x in extra:
            x.pop("id", None)
            x.pop("_skipped", None)
        return [dict(x) for x in (old or []) if isinstance(x, dict)] + extra
    return [dict(x) for x in value]


_LLM_DEFAULTS = {
    "local": {"baseUrl": "http://127.0.0.1:8080/v1", "model": "auto"},
    "online": {"baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "custom": {"baseUrl": "", "model": ""},
}


def llm_settings(slug: str | None) -> dict:
    """專案的 settings.llm（沒有專案就用預設）。壞掉時退回預設值，不拋例外。

    M3b：provider 決定 baseUrl/model 的預設；apiKey 不會被讀進來（只由 llm 層解析來源）。
    """
    defaults = {**_LLM_DEFAULTS["local"], "temperature": 0.8, "provider": "local"}
    if not slug:
        return defaults
    try:
        data = load_project(slug)
    except StorageError:
        return defaults
    got = ((data.get("settings") or {}).get("llm") or {}) if isinstance(data, dict) else {}
    out = dict(defaults)
    if isinstance(got, dict):
        for k in ("baseUrl", "model", "temperature", "provider", "apiKey"):
            if got.get(k) not in (None, ""):
                out[k] = got[k]
    return out


def strip_llm_api_key(data: dict) -> bool:
    """把 settings.llm.apiKey 清空（key 永遠不進 project.json）。回 True 表示有清到。"""
    llm = (data.get("settings") or {}).get("llm") if isinstance(data.get("settings"), dict) else None
    if isinstance(llm, dict) and "apiKey" in llm:
        llm.pop("apiKey", None)
        return True
    return False


def _apply_allowed(kind: str, path: str) -> bool:
    if path in APPLY_FIELD_WHITELIST.get(kind, set()) or path in _APPLY_FIELD_PATTERNS:
        return True
    # 數字索引換成 * 再比一次（outfits.0.promptEn → outfits.*.promptEn）
    normalized = ".".join("*" if part.isdigit() else part for part in (path or "").split("."))
    return normalized in _APPLY_FIELD_PATTERNS


def find_target(data: dict, kind: str, target_id: str | None) -> dict:
    """依 kind+id 找物件：story（忽略 id）、character / scene（在陣列裡找 id）、
    episodeScene / beat / shot（M3b：在 script／shots 裡找）。"""
    if kind == "story":
        story = data.get("story")
        if not isinstance(story, dict):
            raise StorageError("專案缺少 story")
        return story
    if kind in ("episodeScene", "beat"):
        if not target_id:
            raise StorageError(f"{kind} 類別需要指定 id")
        for ep in ((data.get("script") or {}).get("episodes") or []):
            if not isinstance(ep, dict):
                continue
            for es in ep.get("scenes") or []:
                if not isinstance(es, dict):
                    continue
                if kind == "episodeScene" and es.get("id") == target_id:
                    return es
                if kind == "beat":
                    for beat in es.get("beats") or []:
                        if isinstance(beat, dict) and beat.get("id") == target_id:
                            return beat
        raise StorageError(f"找不到{'場次' if kind == 'episodeScene' else 'beat'}：{target_id}")
    if kind == "shot":
        if not target_id:
            raise StorageError("鏡頭類別需要指定 id")
        for sh in data.get("shots") or []:
            if isinstance(sh, dict) and sh.get("id") == target_id:
                return sh
        raise StorageError(f"找不到鏡頭：{target_id}")
    if kind in ("character", "characters"):
        pool, label = data.get("characters") or [], "人物"
    elif kind in ("scene", "scenes"):
        pool, label = data.get("scenes") or [], "場景"
    else:
        raise StorageError(
            f"不支援的對象類別：{kind!r}（可用：story / character / scene / episodeScene / beat / shot）")
    if not target_id:
        raise StorageError(f"{label}類別需要指定 id")
    for item in pool:
        if isinstance(item, dict) and item.get("id") == target_id:
            return item
    raise StorageError(f"找不到{label}：{target_id}")


def run_for_beat(data: dict, beat_id: str) -> dict | None:
    """給前端/API 標示用：beat id 在全專案可能重複（每個場次從 b01 起），
    回 {'episodeSceneId': …, 'episodeId': …}，找不到回 None。"""
    for ep in ((data.get("script") or {}).get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        for es in ep.get("scenes") or []:
            if not isinstance(es, dict):
                continue
            for beat in es.get("beats") or []:
                if isinstance(beat, dict) and beat.get("id") == beat_id:
                    return {"episodeSceneId": es.get("id"), "episodeId": ep.get("id")}
    return None


def get_path(obj: dict, path: str):
    """取值；不存在回 None。"""
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(obj: dict, path: str, value, mode: str = "replace") -> tuple[object, object]:
    """寫入 obj 的點分路徑（不存在的中間層自動建 {}）。

    回 (舊值, 新值)。防護：
    - 路徑段只能是英文字母數字底線（不可 __x__ / 空段 / 超過 4 層）
    - 最上層若是 id / path / refAudio 直接拒絕（避免改掉定位或檔案引用）
    - 目標節點是 dict / list 時拒絕（避免把整個外型物件洗成一串字）
    """
    if mode not in ("replace", "append"):
        raise StorageError(f"mode 只能是 replace 或 append（收到 {mode!r}）")
    parts = (path or "").strip().split(".")
    if not path or any((not p) for p in parts):
        raise StorageError(f"欄位路徑不合法：{path!r}")
    if len(parts) > _MAX_PATH_DEPTH:
        raise StorageError(f"欄位路徑太深（最多 {_MAX_PATH_DEPTH} 層）：{path}")
    for p in parts:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*|\d+", p):  # 欄位名或陣列索引
            raise StorageError(f"欄位路徑含不合法字元：{p!r}")
    if parts[0] in _PROTECTED_KEYS:
        raise StorageError(f"欄位 {parts[0]} 不開放模型寫入（定位或檔案路徑）")
    # M13a：beats.*.action 是 line 的別名（資料模型沒有單獨的 action 欄位）
    if len(parts) >= 3 and parts[0] == "beats" and parts[2] == "action":
        parts = parts[:2] + ["line"]
    # M13a：指到既有陣列的某一項時，索引必須存在（不讓路徑自己長出空殼章回／beat）
    for i in range(len(parts) - 1):
        if parts[i] in _ARRAY_ITEM_FIELDS and re.fullmatch(r"\d+", parts[i + 1]):
            base = obj
            for seg in parts[:i]:
                base = base.get(seg) if isinstance(base, dict) else None
                if base is None:
                    break
            if not isinstance(base, list) or int(parts[i + 1]) >= len(base):
                raise StorageError(
                    f"陣列索引超出範圍：{'.'.join(parts[:i + 2])}（目前只有 "
                    f"{len(base) if isinstance(base, list) else 0} 項；要新增請寫整份 {parts[i]}）")

    cur = obj  # 目前所在的容器（dict 或 list）
    created: list[tuple] = []  # 沿途新建的容器（失敗時拆掉，別在專案裡留下空殼）

    def drop(obj: dict) -> None:
        for parent, key in reversed(created):
            try:
                if isinstance(parent, list):
                    if isinstance(key, int) and key < len(parent) and parent[key] == {}:
                        parent.pop(key)
                elif isinstance(parent, dict) and key in parent and parent[key] in ({}, []):
                    parent.pop(key)
            except Exception:
                pass

    for i, part in enumerate(parts[:-1]):
        is_idx = isinstance(cur, list)
        if is_idx:
            if not re.fullmatch(r"\d+", part):
                drop(obj)
                raise StorageError(f"欄位路徑 {path} 的 {part!r} 應為數字索引")
            idx = int(part)
            while len(cur) <= idx:
                created.append((cur, len(cur)))
                cur.append({})
            nxt = cur[idx]
            if nxt is None:
                nxt = [] if re.fullmatch(r"\d+", parts[i + 1]) else {}
                cur[idx] = nxt
                created.append((cur, idx))
        else:
            nxt = cur.get(part)
            if nxt is None:
                # 下一段是純數字 → 建陣列（例如 outfits.0.promptEn 且尚無服裝）
                nxt = [] if re.fullmatch(r"\d+", parts[i + 1]) else {}
                if isinstance(nxt, list):
                    created.append((nxt, 0))
                    nxt.append({})
                cur[part] = nxt
                created.append((cur, part))
        if not isinstance(nxt, (dict, list)):
            drop(obj)
            raise StorageError(f"欄位 {part} 不是物件，無法往下寫 {path}")
        cur = nxt

    leaf = parts[-1]
    if isinstance(cur, list):
        if not re.fullmatch(r"\d+", leaf):
            drop(obj)
            raise StorageError(f"欄位 {path} 指向陣列，請用數字索引（例如 outfits.0.promptEn）")
        drop(obj)
        raise StorageError(f"欄位路徑 {path} 指到陣列元素，請再指定欄位（例如 {path}.promptEn）")
    old = cur.get(leaf)
    # M14a：字串陣列欄位（beatIds／stageIds）是「指向 id 的清單」，不是物件陣列，
    # 不能走下面的物件陣列錯誤，也不能當字串塞。結構在這裡驗，覆蓋率交給 validate_project 的 V1／V2。
    if leaf in _SIMPLE_ID_ARRAYS:
        if value is None:
            value = []
        if not isinstance(value, list):
            drop(obj)
            raise StorageError(f"欄位 {path} 必須是字串陣列（例如 [\"b02\", \"b04\"]）")
        items: list[str] = []
        for i, x in enumerate(value):
            if not isinstance(x, (str, int)):
                drop(obj)
                raise StorageError(f"{path} 第 {i + 1} 項不是字串（收到 {type(x).__name__}）")
            s = str(x).strip()
            if s and s not in items:
                items.append(s)
        if mode == "append":
            base = [str(x) for x in (old or []) if isinstance(x, (str, int))]
            items = base + [x for x in items if x not in base]
        cur[leaf] = items
        return old, items
    if isinstance(old, (dict, list)):
        drop(obj)
        raise StorageError(f"欄位 {path} 是物件／陣列，請指定到更裡面的單一欄位（例如 {path}.promptEn）")

    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if mode == "append":
        base = "" if old is None else str(old)
        if base.strip():
            joiner = "\n" if ("\n" in base or len(base) > 80) else " "
            new = base.rstrip() + joiner + text.strip()
        else:
            new = text
    else:
        new = text
    cur[leaf] = new
    return old, new


def locate_target(data: dict, kind: str, target_id: str | None,
                  parent_id: str | None = None) -> tuple[dict, str | None]:
    """kind+id(+parent) → 目標物件。beat 用 parentId（所屬場次）定位（beat id 只在場次內唯一）。
    回 (物件, 所屬場次 id 或 None)。"""
    if kind == "beat" and parent_id:
        found = None
        for ep in ((data.get("script") or {}).get("episodes") or []):
            if not isinstance(ep, dict):
                continue
            for es in ep.get("scenes") or []:
                if isinstance(es, dict) and es.get("id") == parent_id:
                    for beat in es.get("beats") or []:
                        if isinstance(beat, dict) and beat.get("id") == target_id:
                            found = beat
                            break
                if found:
                    break
            if found:
                break
        if found is None:
            raise StorageError(f"找不到 beat：{parent_id}/{target_id}")
        return found, parent_id
    target = find_target(data, kind, target_id)
    where = None
    if kind == "beat":
        hit = run_for_beat(data, target_id or "")
        where = (hit or {}).get("episodeSceneId")
    return target, where


def _merge_beats_allowed(kind: str, target: dict, path: str, warnings: list[str]) -> None:
    """M13a：鏡頭的 beats 是片長節點（start／end／cut／camera，由生成端產生），不是台詞清單。
    只寫 beats.0.line 這種「有歌詞感」的路徑會把結構洗掉 → 一律拒絕，並指出該改哪裡。"""
    if kind != "shot":
        return
    if path.startswith("beats") and not target.get("beats") and path != "beats":
        raise StorageError(
            f"鏡頭的 {path} 指向不存在的 beat；鏡頭的 beats 是片長節點，請寫整份 beats 或改回填台詞（dialogue）")
    if path == "beats" and target.get("beats"):
        warnings.append("鏡頭的 beats 是片長節點（start／end／cut），改台詞請用 dialogue 或「引用劇本台詞」")


def _hints_for(kind: str) -> str:
    """整份陣列被誤餵成文字時的提示（指出該用哪個欄位）。"""
    return {
        "story": "章回請用 chapters（整份陣列，每項 {title, body}），或 chapters.*.title／chapters.*.body",
        "episodeScene": "台詞請用 beats（整份陣列，每項 {type, characterId, line}），或 beats.*.line",
        "shot": "本鏡台詞請用 dialogue（整份陣列，每項 {characterId, line}）",
        "character": "服裝請用 outfits（整份陣列，每項 {name, desc, promptEn}）",
    }.get(kind, "")


def _check_apply_path(kind: str, path: str) -> str:
    """白名單檢查 + 路徑形狀檢查（點分、段名字元）。回正規化後的路徑。"""
    if kind not in APPLY_FIELD_WHITELIST:
        raise StorageError(
            f"不支援的套用對象：{kind!r}（可用：{' / '.join(APPLY_FIELD_WHITELIST)}）")
    p = (path or "").strip()
    parts = p.split(".")
    if not p or any(not x for x in parts) or len(parts) > _MAX_PATH_DEPTH:
        raise StorageError(f"欄位路徑不合法：{path!r}")
    for part in parts:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*|\d+", part):
            raise StorageError(f"欄位路徑含不合法字元：{part!r}")
    if not _apply_allowed(kind, p):
        hint = _hints_for(kind)
        raise StorageError(
            f"不允許寫入的欄位：{p!r}（{kind} 可用：{'、'.join(_apply_allowed_list(kind))}）"
            + (f"；{hint}" if hint else ""))
    return p


def apply_item_in_memory(data: dict, target: dict, kind: str, path: str, value, mode: str,
                         warnings: list[str] | None = None, *, is_single_target: bool = True) -> dict:
    """把一個欄位（純量或整份結構陣列）寫進記憶體中的 target。不驗證、不寫檔。

    規則只有這一份：apply_to_target（單欄）與 apply_to_target_batch（多欄）都走這裡。
    回 {"path","oldValue","value"}；不合法的陣列元素 → StorageError（中文）。
    """
    if mode not in ("replace", "append"):
        raise StorageError(f"mode 只能是 replace 或 append（收到 {mode!r}）")
    p = _check_apply_path(kind, path)
    out_warn = [] if warnings is None else warnings
    _merge_beats_allowed(kind, target, p, out_warn)
    key = _array_key(p)
    if key:
        label = _ARRAY_LABEL[key]
        old_arr = target.get(key) if isinstance(target.get(key), list) else []
        if mode == "append" and "." in p:
            # 指到某一項（beats.0.line、chapters.1.body）的附加：只在該項內附加文字，不加新項
            old_arr = [dict(x) for x in old_arr]
            target[key] = old_arr
            old, new = set_path(target, _elem_field_path(p, key), value, "append")
        else:
            merged = _merge_array_value(key, old_arr, value, mode, label=label)
            items, warns = _norm_array_items(data, key, merged, mode=mode, existing=old_arr, label=label)
            out_warn.extend(warns)
            if mode == "replace" and is_single_target and key in ("beats", "dialogue"):
                gone = _diff_arrays(old_arr, items)
                if gone:
                    items = items + _add_skipped(gone)
                    out_warn.append(f"{label}原有 {len(gone)} 項被新內容取代，舊項已保留（標記 _skipped，不進生成）")
            old, new = old_arr, items
            target[key] = items
        if key == "beats" and kind == "episodeScene":
            _reconcile_beat_refs(data, target, {str(x.get("id")) for x in items if not x.get("_skipped")},
                                 label, out_warn)
        if key == "beats" and kind in ("episodeScene", "shot") and isinstance(target.get("characterIds"), list):
            # 新增的角色順手補進場次／鏡頭的 characterIds（否則存了角色卻不在名單裡）
            ids = target["characterIds"]
            for it in items:
                cid = it.get("characterId")
                if cid and cid not in ids:
                    ids.append(cid)
    else:
        old, new = set_path(target, p, value, mode)
    return {"path": p, "oldValue": old, "value": new}


def _contract_error_signatures(data: dict) -> set[str]:
    """分鏡契約（h3compose V1–V5 等）的 error 簽章集合。

    storage.validate_project 只是淺層 schema；「漏一句台詞」「長度不合法」這些
    是 h3compose.validate_project 在管。寫入時要比對「新增的 error」——
    舊專案本來就有的 error 不該讓使用者連改一個字都被擋。
    """
    try:
        from . import h3compose
        v = h3compose.validate_project(data)
    except Exception:
        return set()
    return {f"{i.get('scope')}|{i.get('targetId')}|{i.get('message')}"
            for i in (v.get("issues") or []) if i.get("level") == "error"}


def _block_new_contract_errors(before: set[str], data: dict) -> None:
    new = _contract_error_signatures(data) - before
    if new:
        raise StorageError("寫入會造成分鏡契約錯誤（整批未寫入）：" + "；".join(sorted(new))[:600])


def apply_to_target_batch(slug: str, kind: str, target_id: str | None, items: list[dict],
                          parent_id: str | None = None) -> dict:
    """M13a：一次套用多個欄位（可能就是結構陣列＋幾個純量）。

    行為：一次 load → 逐項寫在記憶體 → 單次 validate_project → 原子寫檔一次。
    任何一項不合法就整批不寫（StorageError 帶「第 N 項」，檔案完全不動）。
    """
    if not isinstance(items, list) or not items:
        raise StorageError("items 至少要有一項要套用的欄位")
    with _lock:
        data = load_project(slug)
        baseline = _contract_error_signatures(data)
        # 先把所有路徑／白名單檢查跑完，再動資料（避免前面寫了才發現後面不合法）
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                raise StorageError(f"第 {i + 1} 項必須是物件（path／value／mode）")
            try:
                _check_apply_path(kind, str(it.get("path") or ""))
            except StorageError as e:
                raise StorageError(f"第 {i + 1} 項：{e}") from e
        target, where = locate_target(data, kind, target_id, parent_id)
        updated: list[dict] = []
        warnings: list[str] = []
        for i, it in enumerate(items):
            try:
                updated.append(apply_item_in_memory(
                    data, target, kind, str(it.get("path") or ""), it.get("value"),
                    str(it.get("mode") or "replace"), warnings))
            except StorageError as e:
                raise StorageError(f"第 {i + 1} 項（{it.get('path')}）：{e}") from e
        migrate_dialog_linkage(data)
        errors = validate_project(data)
        if errors:
            raise StorageError("寫入後結構不合法（整批未寫入）：" + "；".join(errors))
        _block_new_contract_errors(baseline, data)
        meta = data["meta"]
        meta["updatedAt"] = now_iso()
        ensure_media_dirs(slug)
        _write_json_atomic(project_file(slug), data)
    out_target = target
    if kind == "shot":
        out_target = next((s for s in data.get("shots") or []
                           if isinstance(s, dict) and s.get("id") == target_id), target)
    return {
        "updated": updated,
        "targetAfter": out_target,
        "target": {"kind": kind, "id": target.get("id") if kind != "story" else None,
                   **({"parentId": where} if where else {})},
        "warnings": warnings,
        "updatedAt": meta["updatedAt"],
    }


def apply_to_target(slug: str, kind: str, target_id: str | None, path: str, value, mode: str = "replace",
                    parent_id: str | None = None) -> dict:
    """讀-改-寫：套用單一欄位到專案的某張卡，原子寫回，回更新後片段。

    M3b：path 必須在白名單內；kind=beat 用 parent_id（所屬場次）定位。
    M13a：改走 apply_item_in_memory（與 apply_to_target_batch 同一套驗證），
          因此「整份陣列」（章回／beats／dialogue／outfits）也能用單欄介面寫。
    """
    if kind not in APPLY_FIELD_WHITELIST:
        raise StorageError(f"不支援的套用對象：{kind!r}（可用：{' / '.join(APPLY_FIELD_WHITELIST)}）")
    _check_apply_path(kind, path)
    with _lock:
        data = load_project(slug)
        baseline = _contract_error_signatures(data)
        target, where = locate_target(data, kind, target_id, parent_id)
        warnings: list[str] = []
        done = apply_item_in_memory(data, target, kind, path, value, mode, warnings)
        old, new = done["oldValue"], done["value"]
        errors = validate_project(data)
        if errors:
            raise StorageError("；".join(errors))
        _block_new_contract_errors(baseline, data)
        meta = data["meta"]
        meta["updatedAt"] = now_iso()
        ensure_media_dirs(slug)
        _write_json_atomic(project_file(slug), data)
    out_target = target
    if kind == "shot":
        out_target = next((s for s in data.get("shots") or [] if isinstance(s, dict) and s.get("id") == target_id), target)
    return {
        "target": {"kind": kind, "id": target.get("id") if kind != "story" else None,
                   **({"parentId": where} if where else {})},
        "path": path,
        "mode": mode,
        "oldValue": old,
        "value": new,
        "warnings": warnings,
        "updatedAt": meta["updatedAt"],
        "targetAfter": out_target,
    }


# ── M3c：bulk 寫入（預覽後按「套用」才會走這裡）─────────────────
def _next_seq(pool: list, prefix: str, width: int = 2) -> str:
    """pool 可以是 [{id:...}] 或 ["e02s01", ...]；取「prefix+號碼」的最大號 +1。

    prefix 可以是 e（集）或 e02s（場次，含數字）→ 不靠 _ID_RE，直接前置比對。
    """
    biggest = 0
    for item in pool or []:
        iid = str((item.get("id") if isinstance(item, dict) else item) or "")
        if iid.startswith(prefix) and iid[len(prefix):].isdigit():
            biggest = max(biggest, int(iid[len(prefix):]))
    return f"{prefix}{biggest + 1:0{width}d}"


def _norm_shot_spec(spec: dict) -> dict:
    spec = spec if isinstance(spec, dict) else {}
    angle = spec.get("angle")
    if not isinstance(angle, dict):
        angle = {"h": str(angle or ""), "v": "", "s": ""}
    return {
        "shotType": storage_shot_type(spec.get("shotType")),
        "angle": {k: str(angle.get(k) or "") for k in ("h", "v", "s")},
        "cameraMove": str(spec.get("cameraMove") or ""),
        "composition": str(spec.get("composition") or ""),
        "lighting": str(spec.get("lighting") or ""),
        "framingRule": str(spec.get("framingRule") or V02_FRAMING_RULE),
    }


def storage_shot_type(value) -> str:
    """shotType 接受中文顯示值或英文枚舉；不認識原樣保留（不讓舊資料爆掉）。"""
    s = str(value or "").strip()
    return shot_type_en(s) or s


def _bulk_episode(data: dict, ep: dict, mode: str) -> dict:
    if not isinstance(ep, dict):
        raise StorageError("kind=episode 的 data 必須是物件（{title, scenes:[…]}）")
    scenes = ep.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise StorageError("劇本缺少 scenes 或為空")
    script = data.setdefault("script", {"episodes": []})
    if not isinstance(script.get("episodes"), list):
        script["episodes"] = []
    eps = script["episodes"]
    char_ids = {c.get("id") for c in data.get("characters") or [] if isinstance(c, dict)}
    scene_ids = {s.get("id") for s in data.get("scenes") or [] if isinstance(s, dict)}

    if mode == "replace":
        # 覆蓋既有集：保留 id，內容整份換掉；場次 id 沿用（同序沿用，多出來的重新編）
        target = next((e for e in eps if isinstance(e, dict) and e.get("id") == ep.get("id")), None)
        if target is None:
            raise StorageError(f"replace 需要指定已存在的集 id（找不到 {ep.get('id')!r}）")
        ep_out = target
        ep_out["title"] = str(ep.get("title") or ep_out.get("title") or "")
        old = [s for s in ep_out.get("scenes") or [] if isinstance(s, dict)]
        base = {"id": ep_out["id"], "scenes": old}
        ep_out["scenes"] = []
    else:
        ep_id = _next_seq(eps, "e")
        ep_out = {"id": ep_id, "title": str(ep.get("title") or ""), "scenes": []}
        base = {"id": ep_id, "scenes": []}

    created = []
    existing_ids = {s.get("id") for s in base["scenes"]}
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            raise StorageError(f"第 {i + 1} 場不是物件")
        sid = sc.get("sceneId")
        if sid and sid not in scene_ids:
            raise StorageError(f"第 {i + 1} 場的 sceneId 引用不存在：{sid}")
        chars = [c for c in (sc.get("characterIds") or []) if c]
        for cid in chars:
            if cid not in char_ids:
                raise StorageError(f"第 {i + 1} 場的 characterId 引用不存在：{cid}")
        beats_in = sc.get("beats")
        if not isinstance(beats_in, list) or not beats_in:
            raise StorageError(f"第 {i + 1} 場沒有 beats")
        beats = []
        for j, b in enumerate(beats_in):
            if not isinstance(b, dict) or not str(b.get("line") or "").strip():
                raise StorageError(f"第 {i + 1} 場第 {j + 1} 個 beat 缺 line")
            cid = b.get("characterId") or None
            if cid and cid not in char_ids:
                raise StorageError(f"第 {i + 1} 場第 {j + 1} 個 beat 的 characterId 引用不存在：{cid}")
            beats.append({"id": next_id(beats, "b"), "type": "action" if b.get("type") == "action" else "dialogue",
                          "characterId": cid, "line": str(b["line"]).strip(),
                          "emotion": str(b.get("emotion") or ""), "notes": str(b.get("notes") or "")})
        # 場次 id：沿用同序舊 id（replace），否則新編
        reuse = base["scenes"][i].get("id") if i < len(base["scenes"]) else None
        es_id = reuse or _next_seq(sorted(existing_ids), f"{ep_out['id']}s")
        existing_ids.add(es_id)
        ep_out["scenes"].append({
            "id": es_id, "title": str(sc.get("title") or ""), "sceneId": sid or None,
            "characterIds": chars, "summary": str(sc.get("summary") or ""), "beats": beats,
        })
        created.append(es_id)

    if mode == "replace":
        # 被換掉的舊場次 id 若不再存在 → 引用它的鏡頭 episodeSceneId 清空（不刪鏡頭）
        alive = {s["id"] for s in ep_out["scenes"]}
        for sh in data.get("shots") or []:
            if isinstance(sh, dict) and str(sh.get("episodeSceneId") or "").startswith(f"{ep_out['id']}s") \
                    and sh.get("episodeSceneId") not in alive:
                sh["episodeSceneId"] = None
                sh["dialogLineRefs"] = []
    else:
        eps.append(ep_out)
    return {"episodeId": ep_out["id"], "title": ep_out["title"], "episodeSceneIds": created}


def _bulk_shots(data: dict, payload: dict, mode: str) -> dict:
    if isinstance(payload, dict):
        es_id = payload.get("episodeSceneId")
        shots_in = payload.get("shots")
    else:
        raise StorageError("kind=shots 的 data 必須是物件（{episodeSceneId, shots:[…]}）")
    es_map = {}
    for ep in ((data.get("script") or {}).get("episodes") or []):
        for s in ep.get("scenes") or []:
            if isinstance(s, dict) and s.get("id"):
                es_map[s["id"]] = s
    if not isinstance(shots_in, list) or not shots_in:
        raise StorageError("鏡頭清單 shots 不可為空")
    char_ids = {c.get("id") for c in data.get("characters") or [] if isinstance(c, dict)}
    scene_ids = {s.get("id") for s in data.get("scenes") or [] if isinstance(s, dict)}
    shots = data.setdefault("shots", [])
    if not isinstance(shots, list):
        shots = data["shots"] = []
    ts = now_iso()
    created = []
    for i, sh in enumerate(shots_in):
        if not isinstance(sh, dict):
            raise StorageError(f"第 {i + 1} 個鏡頭不是物件")
        eid = sh.get("episodeSceneId") or es_id
        if eid not in es_map:
            raise StorageError(f"第 {i + 1} 個鏡頭的 episodeSceneId 引用不存在：{eid!r}")
        es = es_map[eid]
        sid = sh.get("sceneId") or es.get("sceneId")
        if sid and sid not in scene_ids:
            raise StorageError(f"第 {i + 1} 個鏡頭的 sceneId 引用不存在：{sid}")
        chars = [c for c in (sh.get("characterIds") or es.get("characterIds") or []) if c]
        for cid in chars:
            if cid not in char_ids:
                raise StorageError(f"第 {i + 1} 個鏡頭的 characterId 引用不存在：{cid}")
        refs = sh.get("dialogLineRefs") if isinstance(sh.get("dialogLineRefs"), list) else []
        upper = len([b for b in (es.get("beats") or []) if isinstance(b, dict)])
        for r in refs:
            if not isinstance(r, int) or isinstance(r, bool) or not (1 <= r <= upper):
                raise StorageError(f"第 {i + 1} 個鏡頭的 dialogLineRefs {r!r} 超出場次 {eid} 的 beat 數（1–{upper}）")
        try:
            dur = float(sh.get("durationSec") or 5)
        except (TypeError, ValueError):
            dur = 5.0
        created.append({
            "id": next_id(shots, "sh"), "no": 0, "episodeSceneId": eid, "sceneId": sid or None,
            "characterIds": chars, "spec": _norm_shot_spec(sh.get("spec")), "durationSec": dur,
            "promptZh": str(sh.get("promptZh") or ""), "promptEn": str(sh.get("promptEn") or ""),
            "dialogue": [d for d in (sh.get("dialogue") or []) if isinstance(d, dict)],
            "beats": [b for b in (sh.get("beats") or []) if isinstance(b, dict)],
            "dialogLineRefs": refs,
            "stageIds": [s for s in (sh.get("stageIds") or []) if isinstance(s, str)],
            "sourceChapterId": sh.get("sourceChapterId") or None,
            "referencePlan": sh.get("referencePlan") if isinstance(sh.get("referencePlan"), dict)
            else {"slots": [], "weakReferences": [], "promptPlaceholders": {}},
            "images": {"first": "", "last": ""},
            "video": {"path": "", "status": "none", "taskType": "r2v", "seed": 0, "note": ""},
            "voice": {"path": "", "engine": ""}, "durationFrames": 0, "updatedAt": ts,
        })
        shots.append(created[-1])
    if mode == "replace":
        # 同場次舊鏡頭讓位（保留在檔案外＝直接移除；只影響該場次）
        keep = [s for s in shots if not (isinstance(s, dict) and s.get("episodeSceneId") == es_id
                                         and s not in created)]
        shots[:] = keep
    # 全部鏡頭 no 依陣列順序重排成 1..N（不影響 id）
    for j, s in enumerate(shots):
        if isinstance(s, dict):
            s["no"] = j + 1
    return {"episodeSceneId": es_id, "created": [s["id"] for s in created],
            "nos": [s["no"] for s in created], "shotCount": len(shots)}


def apply_bulk(slug: str, kind: str, data: object, mode: str = "append") -> dict:
    """bulk 寫入：kind=episode（一整集）或 shots（一場次的多鏡頭）。

    append：id/no 全部重新編號避免撞號；replace：episode 需帶 id、shots 需帶 episodeSceneId。
    前端送來的是「預覽編輯後的 JSON」，這裡再驗一次引用（不信前端）。
    """
    if kind not in ("episode", "shots"):
        raise StorageError(f"bulk 只支援 kind=episode / shots（收到 {kind!r}）")
    if mode not in ("append", "replace"):
        raise StorageError(f"mode 只能是 append 或 replace（收到 {mode!r}）")
    with _lock:
        project = load_project(slug)
        if kind == "episode":
            summary = _bulk_episode(project, data, mode)
        else:
            summary = _bulk_shots(project, data, mode)
        errors = validate_project(project)
        if errors:
            raise StorageError("寫入後結構不合法：" + "；".join(errors))
        project["meta"]["updatedAt"] = now_iso()
        ensure_media_dirs(slug)
        _write_json_atomic(project_file(slug), project)
    return {"kind": kind, "mode": mode, **summary, "updatedAt": project["meta"]["updatedAt"]}


def save_upload(slug: str, kind: str, filename: str, payload: bytes) -> str:
    """存上傳檔到 media/<kind>/<時間戳>_<安全檔名>，回相對專案根路徑。"""
    assert_slug(slug)
    if kind not in UPLOAD_KINDS:
        raise StorageError(f"上傳類別不合法：{kind!r}（合法值：{'/'.join(UPLOAD_KINDS)}）")
    if not payload:
        raise StorageError("上傳內容為空")
    if not (project_dir(slug) / "project.json").is_file():
        raise StorageError(f"找不到專案：{slug}")
    ensure_media_dirs(slug)
    stamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d-%H%M%S")
    fname = f"{stamp}_{safe_filename(filename)}"
    target = project_dir(slug) / "media" / kind / fname
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return f"media/{kind}/{fname}"
