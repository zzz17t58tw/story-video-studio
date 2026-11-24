"""M3d：純邏輯層 — referencePlan 產生、H3 六段式確定性 prompt 編譯、全專案機械校驗。

**本模組不呼叫任何模型 API**（零 token、離線可跑）。規格見：

- docs/data-model.md v0.2「shots[] 追加」的 referencePlan 結構與「機械校驗」五條。
- docs/research-existing-tools.md §3-B（MSB 六段式與 `<Picture N>` 硬規則）：
  1. 每個 subject 都要在 detailed_description 被點名；
  2. `<Picture i>` 序號由程式決定（這裏就是 slots 的 ordinal），模型不得自寫；
  3. `<Picture N>` 只看這次真的送進去的圖（slots），不超範圍。
- docs/contracts.md §3：`total_frames` 需 17k+1 對齊（H3 合法格 17k+5）。

對外函式：
  build_reference_plan(data, shot) -> (referencePlan, warnings)
  compile_prompt(data, shot)        -> (promptEn, warnings, meta)
  validate_project(data)            -> {"issues": [...], "counts": {...}}
  frames_for(durationSec, fps)      -> dict（換算與對齊）
"""

from __future__ import annotations

import re
from typing import Any

MAX_SLOTS = 9

# ── 顯示 → 英文（編譯模板用；資料本身一律存英文枚舉）─────────────
SHOT_TYPE_EN = {
    "close_up": "close-up", "medium": "medium", "wide": "wide",
    "over_shoulder": "over-the-shoulder", "pov": "first-person POV",
    "特寫": "close-up", "中景": "medium", "全景": "wide", "過肩": "over-the-shoulder",
}
SHOT_TYPE_CODE = {
    "close_up": "close_up", "medium": "medium", "wide": "wide",
    "over_shoulder": "over_shoulder", "pov": "pov",
    "特寫": "close_up", "中景": "medium", "全景": "wide", "過肩": "over_shoulder",
}
CAMERA_MOVE_EN = {
    "static": "locked-off static camera",
    "push_in": "slow push-in",
    "pull_out": "slow pull-out",
    "pan_left": "pan to the left",
    "pan_right": "pan to the right",
    "tracking": "steadicam tracking shot",
    "handheld": "handheld camera with slight shake",
    "": "locked-off static camera",
}
ANGLE_H_EN = {
    "front": "from straight ahead", "front_left": "from the front-left",
    "left": "from the left side", "back_left": "from the back-left",
    "back": "from behind", "back_right": "from the back-right",
    "right": "from the right side", "front_right": "from the front-right",
}
ANGLE_V_EN = {"worm": "worm's-eye", "low": "low angle", "eye_level": "eye level", "high": "high angle"}

REF_ROLE_ORDER = {"face": 0, "full": 1, "outfit": 2}


# ── 小工具 ─────────────────────────────────────────────────────
def _s(v: Any) -> str:
    return str(v or "").strip()


def _char_names(data: dict, ids: list[str]) -> str:
    names = []
    for cid in ids:
        ch = next((c for c in (data.get("characters") or []) if c.get("id") == cid), None)
        names.append((ch or {}).get("name") or cid)
    return "、".join(names)


def find_scene_ctx(data: dict, shot: dict) -> dict:
    """回 {ep, es}：鏡頭所屬的集與場次物件（找不到給 None）。"""
    es_id = shot.get("episodeSceneId")
    for ep in ((data.get("script") or {}).get("episodes") or []):
        for es in (ep.get("scenes") or []):
            if es.get("id") == es_id:
                return {"ep": ep, "es": es}
    return {"ep": None, "es": None}


def scene_dialogue(ctx: dict) -> list[dict]:
    return [b for b in ((ctx.get("es") or {}).get("beats") or [])
            if b.get("type") == "dialogue" and not b.get("_skipped")]


def shot_dialog_lines(shot: dict, ctx: dict, warnings: list | None = None) -> tuple[list[tuple[int, dict]], str, list[str]]:
    """鏡頭台詞 → ([(場次第幾句 1-based, beat)], 來源欄位, 錯誤訊息)。

    M12a：優先讀 dialogBeatIds（beat id 不怕插／刪／搬句錯位），沒有新欄位才退回
    dialogLineRefs（場次內第 N 句）。回傳依劇本順序（＝演出順序），並先做去重與超範圍
    檢查：錯誤訊息給 validate 轉成 error（行為與 M12a 前一致：重複、超範圍都要報），
    warnings（非阻斷的提示）另寫進呼叫端清單。
    """
    warnings = warnings if warnings is not None else []
    errors: list[str] = []
    dlg = scene_dialogue(ctx)
    pos = {str(b.get("id")): i + 1 for i, b in enumerate(dlg) if b.get("id")}
    refs: set[int] = set()
    source = "dialogLineRefs"
    ids = [b for b in (shot.get("dialogBeatIds") or []) if isinstance(b, str)]
    if ids:
        source = "dialogBeatIds"
        for b in [x for x in ids if x not in pos]:
            errors.append(f"dialogBeatIds 引用了所屬場次沒有的台詞 {b}")
        if len(ids) != len(set(ids)):
            errors.append("dialogBeatIds 重複引用同一句")
        refs |= {pos[b] for b in ids if b in pos}
    else:
        for r in (shot.get("dialogLineRefs") or []):
            try:
                n = int(r)
            except (TypeError, ValueError):
                errors.append(f"dialogLineRefs 含非數字值「{r}」")
                continue
            if not (1 <= n <= len(dlg)):
                errors.append(f"dialogLineRefs 超範圍（場次只有 {len(dlg)} 句）：{n}")
                continue
            refs.add(n)
        ints = [r for r in (shot.get("dialogLineRefs") or []) if isinstance(r, int) and not isinstance(r, bool)]
        dup = sorted({n for n in ints if ints.count(n) > 1})
        if dup:
            errors.append("dialogLineRefs 重複：第 " + "、".join(map(str, dup)) + " 句")
    return [(n, dlg[n - 1]) for n in sorted(refs)], source, errors


# ── 1. referencePlan ───────────────────────────────────────────
def _pick_char_ref(ch: dict) -> dict | None:
    """單一角色的參考圖：鎖臉 face > face > full > 其他；同級取第一張。"""
    refs = [r for r in (ch.get("refImages") or []) if isinstance(r, dict) and _s(r.get("path"))]
    if not refs:
        return None
    refs = sorted(refs, key=lambda r: (REF_ROLE_ORDER.get(_s(r.get("kind")), 3),
                                      0 if r.get("locked") else 1))
    locked_face = [r for r in refs if r.get("locked") and _s(r.get("kind")) == "face"]
    return (locked_face or refs)[0]


def _stage_ref(ch: dict, stage_id: str) -> dict | None:
    st = next((s for s in (ch.get("stages") or []) if s.get("id") == stage_id), None)
    if not st or not _s(st.get("characterSheet")):
        return None
    return {"path": st["characterSheet"], "kind": "sheet", "locked": True, "stage": stage_id}


def build_reference_plan(data: dict, shot: dict) -> tuple[dict, list[str]]:
    """依 stageIds（若有）優先，否則 characterIds；再接 sceneId，最多 9 張。

    回 (referencePlan, warnings)。缺圖只回 warning，不中斷。
    """
    warnings: list[str] = []
    slots: list[dict] = []
    placeholders: dict[str, str] = {}
    weak: list[str] = []

    stage_ids = [x for x in (shot.get("stageIds") or []) if _s(x)]
    stage_by_char: dict[str, str] = {}
    for sid in stage_ids:
        cid, _, stid = _s(sid).partition(":")
        if cid and stid:
            stage_by_char[cid] = stid

    char_ids = [c for c in (shot.get("characterIds") or []) if _s(c)] if not stage_ids else \
        [k for k in stage_by_char] + [c for c in (shot.get("characterIds") or []) if _s(c) and c not in stage_by_char]

    for cid in char_ids:
        ch = next((c for c in (data.get("characters") or []) if c.get("id") == cid), None)
        if not ch:
            warnings.append(f"角色 {cid} 不存在（鏡頭 {shot.get('id')} 有引用）")
            continue
        stid = stage_by_char.get(cid)
        ref = _stage_ref(ch, stid) if stid else None
        if stid and ref is None:
            warnings.append(f"角色 {cid} 沒有 stage「{stid}」的 characterSheet，改退回角色參考圖")
        if ref is None:
            ref = _pick_char_ref(ch)
        if ref is None:
            warnings.append(f"角色 {cid}（{ch.get('name') or cid}）沒有參考圖")
            weak.append(cid)
            continue
        slots.append({
            "ordinal": len(slots) + 1, "kind": "character", "id": cid,
            "stage": stid if (stid and ref.get("stage")) else "",
            "path": ref["path"],
            "role": "stage" if ref.get("stage") else _s(ref.get("kind")) or "face",
            "locked": bool(ref.get("locked")),
        })

    scid = _s(shot.get("sceneId"))
    if scid:
        sc = next((s for s in (data.get("scenes") or []) if s.get("id") == scid), None)
        if not sc:
            warnings.append(f"場景 {scid} 不存在（鏡頭 {shot.get('id')} 有引用）")
        else:
            sref = next((r for r in (sc.get("refImages") or []) if isinstance(r, dict) and _s(r.get("path"))), None)
            if sref is None:
                warnings.append(f"場景 {scid}（{sc.get('name') or scid}）沒有參考圖")
                weak.append(scid)
            else:
                slots.append({
                    "ordinal": len(slots) + 1, "kind": "scene", "id": scid,
                    "stage": "", "path": sref["path"],
                    "role": _s(sref.get("kind")) or "wide", "locked": False,
                })

    dropped = []
    if len(slots) > MAX_SLOTS:
        dropped = [s for s in slots[MAX_SLOTS:]]
        slots = slots[:MAX_SLOTS]
        warnings.append(
            f"參考圖超過上限 9 張，已截掉 {len(dropped)} 張（"
            + "、".join(f"{d['kind']}:{d['id']}" for d in dropped) + "）")

    for s in slots:
        placeholders[f"{{{{{s['id']}}}}}"] = f"<Picture {s['ordinal']}>"

    return ({"slots": slots, "weakReferences": weak, "promptPlaceholders": placeholders}, warnings)


# ── 2. H3 六段式確定性編譯 ─────────────────────────────────────
def _anchor_line(ch: dict) -> str:
    """identityAnchors 逐字貼上（不改寫）成一行英文錨點描述。空欄位整段略過。"""
    a = ch.get("identityAnchors") or {}
    parts = []
    if _s(a.get("hairStyle")):
        parts.append(f"hair {_s(a.get('hairStyle'))}")
    if _s(a.get("faceShape")):
        parts.append(f"{_s(a.get('faceShape'))} face")
    if _s(a.get("facialFeatures")):
        parts.append(f"features: {_s(a.get('facialFeatures'))}")
    if _s(a.get("uniqueMarks")):
        parts.append(f"distinct marks: {_s(a.get('uniqueMarks'))}")
    if _s(a.get("skinTexture")):
        parts.append(f"skin texture {_s(a.get('skinTexture'))}")
    ca = a.get("colorAnchors") or {}
    cols = [f"{k if k != 'primaryOutfit' else 'primary outfit'} {_s(ca.get(k))}"
            for k in ("hair", "eyes", "skin", "primaryOutfit") if _s(ca.get(k))]
    if cols:
        parts.append("colors (hex): " + ", ".join(cols))
    return "; ".join(parts)


def _stage_line(data: dict, ch: dict, stid: str) -> str:
    st = next((s for s in (ch.get("stages") or []) if s.get("id") == stid), None)
    if not st:
        return ""
    bits = []
    if _s(st.get("label")):
        bits.append(f"look stage \"{_s(st.get('label'))}\"")
    oid = _s(st.get("outfitId"))
    if oid:
        outfit = next((o for o in (ch.get("outfits") or []) if o.get("id") == oid), None)
        if outfit:
            bits.append(_s(outfit.get("promptEn")) or _s(outfit.get("name")) or oid)
    if _s(st.get("note")):
        bits.append(_s(st.get("note")))
    return ", ".join(bits)


def compile_prompt(data: dict, shot: dict,
                   first_frame_as_picture: bool = False) -> tuple[str, list[str], dict]:
    """確定性模板編譯（不呼模型）。回 (promptEn, warnings, meta)。

    六段式：1 subject_definitions / 2 summary(動作) / 3 retention_analysis(鏡頭語言)
            4 detailed_description(光影/構圖) / 5 overall_soundscape(氛圍與現場聲) / 6 non_diegetic_music(音訊/台詞)
    圖片引用一律 `<Picture N>`，N 取自 referencePlan.slots 的 ordinal。

    first_frame_as_picture：這鏡沒有任何角色參考圖、但送件時把首幀當 <Picture 1>（見
    h3gen.collect_refs 的 firstFrameAsPicture）。以前這裡不寫 <Picture 1>，導致編譯稿
    與實際送出的圖對不上（H3 只把首幀當氛圍、不鎖主體），現在由呼叫端明確告知。
    """
    warnings: list[str] = []
    ctx = find_scene_ctx(data, shot)
    es = ctx.get("es") or {}
    sc = next((s for s in (data.get("scenes") or []) if s.get("id") == shot.get("sceneId")), None)
    spec = shot.get("spec") or {}
    refs = (shot.get("referencePlan") or {}).get("slots") or []
    ref_by_id = {s.get("id"): s for s in refs}

    # ① 主體定義
    subject_bits = []
    if first_frame_as_picture and not refs:
        # 首幀就是這鏡唯一的圖 → 明講「臉與身體以 <Picture 1> 為準」，
        # 否則模型只會把它當參考氛圍，換臉換衣服。
        subject_bits.append(
            "<Picture 1> is the main subject, taken from this shot's first frame: "
            "keep the same facial identity, hairstyle, body build and clothing as <Picture 1>.")
    char_ids = [c for c in (shot.get("characterIds") or []) if _s(c)]
    for cid in char_ids:
        ch = next((c for c in (data.get("characters") or []) if c.get("id") == cid), None)
        if not ch:
            warnings.append(f"角色 {cid} 不存在，主體定義略過")
            continue
        slot = ref_by_id.get(cid)
        tag = f"<Picture {slot['ordinal']}>" if slot else f"{ch.get('name') or cid}"
        app = _s((ch.get("appearance") or {}).get("promptEn"))
        line = f"{tag} is the character \"{ch.get('name') or cid}\""
        if slot:
            line += " (identity locked to the referenced picture)"
        if app:
            line += f": {app}"
        stid = next((x.split(":", 1)[1] for x in (shot.get("stageIds") or [])
                     if _s(x).startswith(cid + ":")), "")
        stline = _stage_line(data, ch, stid) if stid else ""
        if stline:
            line += f". Current look: {stline}"
        anc = _anchor_line(ch)
        if anc:
            line += f". Fixed anchors: {anc}"
        subject_bits.append(line)
    if not char_ids:
        subject_bits.append("No human subject in frame (empty plate / environment-only shot).")
    scid = _s(shot.get("sceneId"))
    if scid:
        sslot = ref_by_id.get(scid)
        stag = f"<Picture {sslot['ordinal']}>" if sslot else (sc.get("name") or scid)
        sc_desc = _s((sc or {}).get("promptEn")) or _s((sc or {}).get("desc")) or _s((sc or {}).get("name")) or scid
        subject_bits.append(f"{stag} is the location: {sc_desc}"
                            + (f" ({_s(sc.get('timeOfDay'))})" if _s((sc or {}).get("timeOfDay")) else ""))
    if not refs:
        if first_frame_as_picture:
            warnings.append("這鏡沒有角色／場景參考圖槽位；本次用本鏡首幀當 <Picture 1>"
                            "（要鎖同一個人的臉，請先在人物頁生成立繪再按「建參考圖槽位」）")
        else:
            warnings.append("這鏡沒有參考圖槽位（<Picture N> 無從對應）；建議先按「建參考圖槽位」或補上傳參考圖")
    else:
        warnings.append(f"已編譯者請確認 slots 順序：{'、'.join(f'#%s %s' % (s['ordinal'], s['id']) for s in refs)}")

    # ② 動作（beats.action 優先，沒有就用 promptZh／場次摘要）
    actions = [_s(b.get("action")) for b in (shot.get("beats") or [])
               if _s(b.get("action")) and not b.get("_skipped")]
    if actions:
        joined = ""
        for i, a in enumerate(actions):
            a = a if a.endswith((".", "!", "？", "。")) else a + "."
            joined += ((" Then: " if i else "") if joined else "") + a
        action_text = joined
    elif _s(shot.get("promptZh")):
        action_text = _s(shot.get("promptZh"))
    elif _s(es.get("summary")):
        action_text = _s(es.get("summary"))
        warnings.append("鏡頭沒有 beats.action，動作段暫用場次摘要")
    else:
        action_text = ""
        warnings.append("鏡頭沒有 beats.action 也沒有 promptZh，動作段為空")

    # ③ 鏡頭語言
    st_code = SHOT_TYPE_CODE.get(_s(spec.get("shotType")), "")
    st_en = SHOT_TYPE_EN.get(_s(spec.get("shotType")), _s(spec.get("shotType")) or "medium")
    angle = spec.get("angle") if isinstance(spec.get("angle"), dict) else {}
    cam_bits = [f"{st_en} shot"]
    if _s(angle.get("v")):
        cam_bits.append(ANGLE_V_EN.get(_s(angle.get("v")), _s(angle.get("v"))))
    if _s(angle.get("h")):
        cam_bits.append(ANGLE_H_EN.get(_s(angle.get("h")), _s(angle.get("h"))))
    cam_bits.append(CAMERA_MOVE_EN.get(_s(spec.get("cameraMove")), _s(spec.get("cameraMove")) or "locked-off static camera"))
    retention = ", ".join(cam_bits)
    if st_code == "wide" and char_ids:
        warnings.append("含人物的鏡頭卻設 wide：H3 ref2v 的身份住在臉的像素量上，建議改 medium／close_up")

    # ④ 詳細描述（光影／構圖）
    detail_bits = []
    if _s(spec.get("lighting")):
        detail_bits.append("Lighting: " + _s(spec.get("lighting")))
    elif sc and _s(sc.get("lighting")):
        detail_bits.append("Lighting: " + _s(sc.get("lighting")))
    if _s(spec.get("composition")):
        detail_bits.append("Composition: " + _s(spec.get("composition")))
    if _s(es.get("title")):
        detail_bits.append("Story beat context: " + _s(es.get("title")))
    if char_ids:
        detail_bits.append("Identity must match the referenced pictures exactly; do not restyle faces or clothes.")

    # ⑤ 現場氛圍／聲音
    amb = (sc or {}).get("ambience") or {}
    sound_bits = []
    amb_text = _s(amb.get("desc")) or _s(amb.get("promptEn")) or _s(amb.get("note"))
    if amb_text:
        sound_bits.append(amb_text)
    mood = _s((sc or {}).get("mood")) or ""
    atmos = ", ".join(x for x in [
        (_s(sc.get("timeOfDay")) if sc else ""), mood,
        ("dense atmosphere, cinematic fairy-tale realism" if char_ids else "quiet empty environment"),
    ] if x)
    if not sound_bits:
        sound_bits.append("natural location ambience, soft distant forest air" if char_ids else "subtle room tone")

    # ⑥ 音訊：台詞（M12a：dialogBeatIds 優先，退回舊 dialogLineRefs → 所屬場次第 N 句）
    dlg_lines = scene_dialogue(ctx)
    spoken = []
    picked, _, dlg_errors = shot_dialog_lines(shot, ctx)
    for msg in dlg_errors:
        warnings.append(msg)
    for n, b in picked:
        spk = _char_names(data, [b.get("characterId")]) if b.get("characterId") else "off-screen voice"
        emo = f" ({_s(b.get('emotion'))})" if _s(b.get("emotion")) else ""
        spoken.append(f"{spk}{emo} says \"{_s(b.get('line'))}\"")
    seen_refs = {n for n, _ in picked}
    missing = [i for i in range(1, len(dlg_lines) + 1) if i not in seen_refs]
    if dlg_lines and missing:
        warnings.append("未覆蓋所屬場次的台詞 " + "、".join(str(i) for i in missing) + "（送生成前校驗會報 error）")
    audio_bits = []
    if spoken:
        audio_bits.append("Dialogue in Mandarin Chinese: " + "; ".join(spoken) + ".")
    else:
        audio_bits.append("No dialogue; performance carried by breathing and movement.")
    audio_bits.append("Audio bed: " + "; ".join(sound_bits) + ".")
    audio_bits.append("Music: restrained non-diegetic score, low sustained strings, no lyrics.")

    prompt = "\n".join([
        "1. Subject definitions: " + " ".join(subject_bits),
        "2. Summary: " + (action_text or "(no action specified)"),
        "3. Retention analysis / camera: " + retention + ".",
        "4. Detailed description: " + " ".join(detail_bits) + (" Atmosphere: " + atmos + "." if atmos else ""),
        "5. Overall soundscape: " + "; ".join(x for x in [amb_text] if x) + ("." if amb_text else "diegetic ambience only."),
        "6. Non-diegetic audio: " + " ".join(audio_bits),
    ])
    meta = {
        "shotId": shot.get("id"),
        "segments": 6,
        "pictures": [{"ordinal": s["ordinal"], "id": s["id"], "kind": s["kind"]} for s in refs],
        "dialogLines": len(spoken),
        "charCount": len(prompt),
        "engine": "deterministic-template (no model call)",
    }
    return prompt, warnings, meta


# ── 3. 機械校驗 ────────────────────────────────────────────────
PICTURE_RE = re.compile(r"<Picture\s+(\d+)>")
SCENE_NO_HUMAN = "畫面內不得有人（含剪影、倒影、背影）"


def frames_for(duration_sec: Any, fps: int = 24) -> dict:
    """durationSec → 影格。H3 合法格 17k+5（訓練域 124–362），total_frames 需 17k+1 對齊。"""
    try:
        sec = float(duration_sec or 0)
    except (TypeError, ValueError):
        sec = 0.0
    raw = int(round(sec * fps))
    # 吸附到最近的 17k+5
    k = max(0, round((raw - 5) / 17)) if raw >= 5 else 0
    legal = 17 * k + 5
    aligned = 17 * k + 1
    return {"fps": fps, "raw": raw, "legal": legal, "aligned": aligned,
            "k": k, "inRange": 124 <= legal <= 362,
            "deltaFrames": raw - legal, "deltaMs": round((raw - legal) / fps * 1000)}


def _issue(level: str, scope: str, target_id: str, message: str, name: str = "") -> dict:
    return {"level": level, "scope": scope, "targetId": target_id,
            "name": name, "message": message}


def _nonspace_len(text: Any) -> int:
    import re as _re
    return len(_re.sub(r"\s+", "", str(text or "")))


def _validate_storyboard_contract(data: dict, fps: int) -> list[dict]:
    """契約 §4 的 V1／V4／V5（V2 在①、V3 在③）。純邏輯，不動資料。

    V1 順序覆蓋：場次內 shots（依 no）的 beatIds 串接＝該場次 beats（排除 _skipped），不符一律 error。
      舊專案（沒有 beatIds 也沒有 dialogBeatIds）不報 error，只給 warn「此場次尚未分鏡」。
    V4 台詞唸得完：durationSec - padSec ≥ 台詞字數 / 4.5。
    V5 職責一致：dialogue 必含 ≥1 dialogue beat；establish 只能場次第一鏡且不含台詞；
       reaction 不含台詞；closing／action／insert 不得違反各自的定義。
    """
    issues: list[dict] = []
    shots = [s for s in (data.get("shots") or []) if isinstance(s, dict)]
    valid_roles = ("establish", "dialogue", "reaction", "action", "insert", "closing")

    for ep in ((data.get("script") or {}).get("episodes") or []):
        for es in (ep.get("scenes") or []):
            es_id = es.get("id")
            es_shots = sorted([s for s in shots if s.get("episodeSceneId") == es_id],
                              key=lambda s: (int(s.get("no") or 0)))
            beats = [b for b in (es.get("beats") or []) if isinstance(b, dict) and not b.get("_skipped")]
            want = [str(b.get("id")) for b in beats if b.get("id")]
            beat_by_id = {str(b.get("id")): b for b in beats if b.get("id")}
            label = es.get("title") or es_id

            has_new = any(s.get("beatIds") for s in es_shots)
            has_any_ref = any(s.get("beatIds") or s.get("dialogBeatIds") for s in es_shots)
            # 這一場到底有幾個 beat 被鏡頭演到（beatIds 優先，舊欄位 fallback）。
            # 部署產生的鏡頭只挑台詞、從不引用動作 beat → 這類「有引用但沒演完」的場次
            # 以前完全不報，使用者只會看到「影片看不出在演什麼」卻找不到原因。
            covered_all: set[str] = set()
            for s in es_shots:
                ids_s = [x for x in (s.get("beatIds") or []) if isinstance(x, str)]
                if not ids_s:
                    ids_s = [x for x in (s.get("dialogBeatIds") or []) if isinstance(x, str)]
                covered_all |= set(ids_s)
            unshot = [b for b in beats if str(b.get("id")) and str(b.get("id")) not in covered_all]
            unshot_lines = [b for b in unshot if _s(b.get("type") or "dialogue") != "action"]
            if es_shots and not has_any_ref:
                # 舊專案：整場都沒引用任何 beat → 不當錯誤擋人（V1 不誤傷舊資料）
                issues.append(_issue("warn", "episodeScene", es_id,
                                     f"此場次尚未分鏡：{len(beats)} 個 beats 沒有任何鏡頭以 beatIds 引用"
                                     f"（可用「🎬 由劇本自動分鏡」依劇本重生分鏡）", label))
                continue
            _covered_has_line = any(
                _s(b.get("type") or "dialogue") != "action"
                for b in (beat_by_id.get(str(x)) for x in covered_all) if isinstance(b, dict))
            if unshot_lines and not unshot and _covered_has_line:
                # 整場的 beat 都被演到，但台詞一句都沒引用 → 分鏡只演了畫面（V2 的主體會接手報）
                active_lines = [b for b in beats if _s(b.get("type") or "dialogue") != "action"]
                issues.append(_issue(
                    "error", "episodeScene", es_id,
                    f"這一場有 {len(active_lines)} 句台詞沒有任何鏡頭引用"
                    f"（{'、'.join(str(b.get('id')) for b in active_lines)}）", label))
            elif unshot:
                # 有動作／台詞 beat 沒有鏡頭 → 品質提醒（不擋生成），要講清楚是哪幾個
                issues.append(_issue("warn", "episodeScene", es_id,
                                     f"劇本有 {len(beats)} 個演出點，鏡頭只演了 {len(beats) - len(unshot)} 個："
                                     f"{'、'.join(str(b.get('id')) for b in unshot[:8])}"
                                     f"{'…' if len(unshot) > 8 else ''} 沒有對應鏡頭"
                                     f"（用「🎬 由劇本自動分鏡」可一次補齊，動作 beat 目前不會自動生成鏡頭）",
                                     label))
            if not has_new:
                # 有 dialogBeatIds 但沒有 beatIds（M12a 時期的資料）→ 要求升級格式
                issues.append(_issue("warn", "episodeScene", es_id,
                                     "此場次是舊格式（只有 dialogBeatIds、沒有 beatIds）；"
                                     "V1 順序覆蓋只檢查新格式，建議用「🎬 由劇本自動分鏡」重建", label))
                continue

            # ── V1 順序覆蓋 ──
            # chain（場次內依鏡號串接的 beatIds）必須完全等於劇本 beats 的順序，不符一律 error。
            # 這條不放寬：成品播放順序就靠鏡號，順序排錯卻是黃字等於讓使用者帶著錯片出貨。
            # （storyboard.backfill_shots 會把補洞鏡插在「第一個 beat 在劇本裡的位置」，
            #  補完後 chain 自然回到劇本順序——實測 e01s03 → b01/b02/b05/b03/b04，與 want 相同。）
            chain: list[str] = []
            other_scene_beats: set[str] = set()
            for s in shots:
                if s.get("episodeSceneId") != es_id:
                    other_scene_beats |= {str(x) for x in (s.get("beatIds") or [])}
            for s in es_shots:
                for bid in (s.get("beatIds") or []):
                    if not isinstance(bid, str) or not bid:
                        continue
                    if bid not in beat_by_id:
                        issues.append(_issue(
                            "error", "shot", s.get("id"),
                            f"beatIds 引用「{bid}」，但場次 {es_id} 沒有這個 beat"
                            + ("（它屬於別的場次；分鏡不得跨場次）" if bid in other_scene_beats else "")
                            + "（合約 V1）", f"第 {s.get('no')} 鏡"))
                        continue
                    chain.append(bid)
            if chain != want:
                # 找出第一處不一致，訊息要能直接指路
                first = next((i for i in range(min(len(chain), len(want))) if chain[i] != want[i]), None)
                if first is not None:
                    detail = f"第 {first + 1} 個位置：鏡頭串接是 {chain[first]}，劇本是 {want[first]}"
                elif len(chain) < len(want):
                    missing = [b for b in want if b not in chain]
                    detail = f"漏了 {len(missing)} 個 beat：{('、'.join(missing))[:80]}"
                else:
                    extra = [b for b in chain if b not in want]
                    dup = sorted({b for b in chain if chain.count(b) > 1})
                    detail = (("重複引用：" + "、".join(dup)) if dup
                              else ("多出不在劇本的 beat：" + "、".join(extra)))
                issues.append(_issue(
                    "error", "episodeScene", es_id,
                    f"鏡頭的 beatIds 串接（依鏡號）與劇本 beats 不一致（合約 V1）：{detail}",
                    label))

            # ── V5 職責一致 ──
            first_shot_id = es_shots[0].get("id") if es_shots else None
            for s in es_shots:
                role = _s(s.get("role"))
                ids = [x for x in (s.get("beatIds") or []) if isinstance(x, str)]
                ref_beats = [beat_by_id[b] for b in ids if b in beat_by_id]
                dlg_refs = [b for b in ref_beats if _s(b.get("type") or "dialogue") != "action"]
                nm = f"第 {s.get('no')} 鏡"
                if role and role not in valid_roles:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         f"role「{role}」不在六種之內（{'/'.join(valid_roles)}）（合約 V5）", nm))
                    continue
                if role == "dialogue" and not dlg_refs:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         "role=dialogue 但這一鏡沒有引用任何 dialogue beat（合約 V5）", nm))
                if role == "establish" and s.get("id") != first_shot_id:
                    # 不放寬：establish 只能場次第一鏡。補洞時 backfill_shots 自己會把補洞鏡的
                    # establish 降級成 dialogue/action，校驗端不需要為補洞開後門。
                    issues.append(_issue("error", "shot", s.get("id"),
                                         "role=establish 只能用在場次第一鏡（合約 V5）", nm))
                if role == "establish" and dlg_refs:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         f"role=establish 不得含台詞，但引用了 {len(dlg_refs)} 句（合約 V5）", nm))
                if role == "reaction" and dlg_refs:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         f"role=reaction 不得含台詞，但引用了 {len(dlg_refs)} 句（合約 V5）；"
                                         "要演台詞請改 role=dialogue", nm))
                if role == "closing" and dlg_refs:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         "role=closing 是純視覺收尾，不得含台詞（合約 V5）", nm))
                if role == "insert" and (s.get("characterIds") or []) and dlg_refs:
                    issues.append(_issue("error", "shot", s.get("id"),
                                         "role=insert 是細節／物件鏡，不得同時帶人物與台詞（合約 V5）", nm))

    # ── V4 台詞唸得完（逐鏡）＋ V3 的 padSec 合理性 ──
    for s in shots:
        ctx = find_scene_ctx(data, s)
        picked, _src, _errs = shot_dialog_lines(s, ctx)
        # V4 不看 validate 分支的錯誤訊息（那屬於 ① 台詞覆蓋），只算「這一鏡實際會唸的字」。
        # 舊資料的 dialogLineRefs 指到不存在的句子時 picked 會是空的，這裡就沒有字可算。
        chars = sum(_nonspace_len(b.get("line")) for _n, b in picked)
        # 本鏡臨時台詞（shot.dialogue）沒有 beat、不在 V2 覆蓋範圍內 → 這裡要自己算進來，
        # 否則「台詞唸不完」只在有 beat 引用時才檢查，手動寫的台詞會被截斷。
        free_lines = [_s(x.get("line") if isinstance(x, dict) else x) for x in (s.get("dialogue") or [])]
        chars += sum(_nonspace_len(x) for x in free_lines if x)
        if not chars:
            continue
        need = chars / 4.5
        try:
            dur = float(s.get("durationSec") or 0)
            pad = float(s.get("padSec") or 0)
        except (TypeError, ValueError):
            continue
        speakable = dur - pad
        if speakable + 0.01 < need:
            # 合法影格（124–362 幀＝5.17–15.08 秒）的硬上限是 15.08 秒，換算成字數上限；
            # 台詞超過它時「請加長」是空話（加長也生不出來），必須講拆鏡。使用者看到的
            # 5.17 秒其實是「最短 124 幀」——原文只寫「只剩 5.17 秒」看不出這層。
            max_frames = 362
            need_frames = int(-(-need * fps // 1))
            over_limit = need > max_frames / fps - 0.05
            if over_limit:
                tail = (f"{max_frames} 幀（{max_frames / fps:.2f} 秒）是單鏡上限 → 一定要拆鏡，"
                        f"建議拆成 {max(2, -(-need_frames // max_frames))} 鏡")
            elif need_frames <= 124:
                tail = (f"{need:.2f} 秒在最短 124 幀（5.17 秒）以內，只要把 durationSec 設到 "
                        f"{need + pad:.2f} 秒即可（不用拆鏡）")
            else:
                tail = (f"請把 durationSec 設到至少 {need + pad:.2f} 秒"
                        f"（{frames_for(need + pad, fps)['legal']} 幀）或拆鏡")
            issues.append(_issue(
                "error", "shot", s.get("id"),
                f"台詞唸不完（合約 V4）：{chars} 字需 {need:.2f} 秒，扣掉留白 {pad:g}s 只剩 {speakable:.2f} 秒"
                f"（durationSec={dur:g}）→ 配音會被截斷；{tail}",
                f"第 {s.get('no')} 鏡"))
        if pad < -0.001:
            issues.append(_issue("error", "shot", s.get("id"),
                                 f"padSec={pad} 不可為負（合約 §3）", f"第 {s.get('no')} 鏡"))
    return issues


def validate_project(data: dict) -> dict:
    issues: list[dict] = []
    fps = int(((data.get("meta") or {}).get("fps")) or 24)
    chars = data.get("characters") or []
    scenes = data.get("scenes") or []

    # ① 台詞覆蓋（M12a：dialogBeatIds 優先、舊 dialogLineRefs 退回）：完整覆蓋所屬場次 dialogue
    fixes: list[str] = []      # 「已自動處理」的說明，不當 issue（前端顯示在獨立區塊）
    for ep in ((data.get("script") or {}).get("episodes") or []):
        for es in (ep.get("scenes") or []):
            dlg = [b for b in (es.get("beats") or [])
                   if b.get("type") == "dialogue" and not b.get("_skipped")]
            covered: set[int] = set()
            for sh in (data.get("shots") or []):
                if sh.get("episodeSceneId") != es.get("id"):
                    continue
                picked, source, errs = shot_dialog_lines(sh, {"es": es, "ep": ep})
                # 舊資料的衍生欄位 dialogLineRefs 指到不存在的句子／重複時：
                # 1) 不再報「超範圍」——那只是同一句台詞的另一種說法，會與場次的
                #    「第 1、2 句沒覆蓋」成對出現，使用者看到兩條只會更混亂；
                # 2) 保住畫面：改引用「同一鏡 beatIds 的第一句台詞」或「場次第 1 句」，
                #    讓正在出片的專案不會因為一個舊欄位而整個被擋。
                bad_refs = [e for e in errs if str(e).startswith("dialogLineRefs")]
                if sh.get("dialogBeatIds") or (bad_refs and dlg):
                    errs = [e for e in errs if not str(e).startswith("dialogLineRefs")]
                    if bad_refs and not picked:
                        picked = [(1, dlg[0])]
                        fixes.append(f"{es.get('id')}／第 {sh.get('no')} 鏡：舊台詞編號 "
                                     f"{(sh.get('dialogLineRefs') or [])} 對不上這一場的 {len(dlg)} 句，"
                                     f"已暫用第 1 句（請到鏡頭頁重新勾選台詞）")
                for msg in errs:
                    issues.append(_issue("error", "shot", sh.get("id"), msg, f"第 {sh.get('no')} 鏡"))
                covered |= {n for n, _ in picked}
            missing = [i for i in range(1, len(dlg) + 1) if i not in covered]
            if dlg and missing:
                issues.append(_issue("error", "episodeScene", es.get("id"),
                                     f"場次共 {len(dlg)} 句台詞，鏡頭沒有覆蓋第 " + "、".join(map(str, missing)) + " 句",
                                     es.get("title") or es.get("id")))

    # ②〜④ 逐鏡
    for sh in (data.get("shots") or []):
        sid = sh.get("id")
        name = f"第 {sh.get('no')} 鏡"
        prompt_en = _s(sh.get("promptEn"))
        slots = ((sh.get("referencePlan") or {}).get("slots")) or []
        ordinals = [s.get("ordinal") for s in slots if isinstance(s.get("ordinal"), int)]
        used = [int(m) for m in PICTURE_RE.findall(prompt_en)]
        if prompt_en and slots:
            if not used:
                issues.append(_issue("warn", "shot", sid, "promptEn 沒有任何 <Picture N> 引用（參考圖沒被點名，模型可能不認臉）", name))
            for n in sorted(set(used)):
                if n not in ordinals:
                    issues.append(_issue("error", "shot", sid,
                                         f"promptEn 引用 <Picture {n}>，但 referencePlan 沒有這個序號（現有：{ordinals or '無'}）", name))
            for n in ordinals:
                if n not in used:
                    issues.append(_issue("warn", "shot", sid, f"referencePlan 有 slot {n} 但 promptEn 沒引用 <Picture {n}>", name))
            first_positions = [used.index(n) for n in dict.fromkeys(used)]
            if first_positions != sorted(first_positions):
                issues.append(_issue("warn", "shot", sid, "promptEn 中 <Picture N> 首次出現順序與 slots 序號不一致", name))
            if len(ordinals) != len(set(ordinals)):
                issues.append(_issue("error", "shot", sid, "referencePlan slots 的 ordinal 有重複", name))
            if ordinals and ordinals != sorted(ordinals):
                issues.append(_issue("error", "shot", sid, "referencePlan slots 的 ordinal 不是從 1 遞增", name))
        elif not prompt_en:
            issues.append(_issue("warn", "shot", sid, "promptEn 是空的（可先按「模板編譯 prompt」產生草稿）", name))

        # ③ 影格（契約 V3）：
        #    - 吸附後落在合法域 124–362 → 只是「沒對齊」，送件時 h3gen 會自動吸附，故為 warn
        #    - 吸附後超出合法域（太短／太長，真的生不出來）→ error
        fr = frames_for(sh.get("durationSec"), fps)
        if not fr["inRange"]:
            issues.append(_issue("error", "shot", sid,
                                 f"durationSec={sh.get('durationSec')}s → {fr['raw']} 幀，吸附後 {fr['legal']} 幀（17k+5）"
                                 f"不在 H3 合法域 124–362（合約 V3）；請調到 5.17–15.08 秒或按「由劇本自動分鏡」重修",
                                 name))
        elif abs(fr["deltaFrames"]) > 0:
            issues.append(_issue("warn", "shot", sid,
                                 f"durationSec={sh.get('durationSec')}s 換算 {fr['raw']} 幀，不是 H3 合法格 17k+5"
                                 f"（合約 V3）；送件時會吸附到 {fr['legal']} 幀＝{fr['legal'] / fps:.2f}s"
                                 f"（total_frames 對齊 {fr['aligned']}=17k+1）",
                                 name))

        # ④ 含人物不得 wide
        st = SHOT_TYPE_CODE.get(_s((sh.get("spec") or {}).get("shotType")), "")
        if (sh.get("characterIds") or []) and st == "wide":
            issues.append(_issue("error", "shot", sid,
                                 f"含人物（{_char_names(data, sh.get('characterIds') or [])}）卻設為 wide；MSB 取景規則：有人的鏡頭一律 medium／MCU／CU",
                                 name))

        # 參考圖缺件
        for cid in (sh.get("characterIds") or []):
            ch = next((c for c in chars if c.get("id") == cid), None)
            if ch is None:
                issues.append(_issue("error", "shot", sid, f"引用不存在的角色 {cid}", name))
            elif not any(isinstance(r, dict) and _s(r.get("path")) for r in (ch.get("refImages") or [])):
                issues.append(_issue("warn", "shot", sid,
                                     f"角色 {cid}（{ch.get('name') or cid}）沒有參考圖 → 送 H3 時是 weak reference",
                                     name))
        if _s(sh.get("sceneId")):
            sc = next((s for s in scenes if s.get("id") == sh.get("sceneId")), None)
            if sc is None:
                issues.append(_issue("error", "shot", sid, f"引用不存在的場景 {sh.get('sceneId')}", name))
            elif not any(isinstance(r, dict) and _s(r.get("path")) for r in (sc.get("refImages") or [])):
                issues.append(_issue("warn", "shot", sid,
                                     f"場景 {sc.get('id')}（{sc.get('name') or sc.get('id')}）沒有參考圖 → 場景一致性只能靠文字",
                                     name))

    # ── M14a：契約 §4 的分鏡覆蓋／順序／職責（V1／V4／V5；V2 在上方①、V3 在上方③）──
    issues += _validate_storyboard_contract(data, fps)

    # ⑤ 場景 prompt 缺「不得有人」句
    for sc in scenes:
        blob = " ".join(_s(sc.get(k)) for k in ("promptEn", "desc", "mood"))
        for r in (sc.get("refImages") or []):
            if isinstance(r, dict):
                blob += " " + _s(r.get("promptEn"))
        # 兩種寫法都算數：中文句（人工／舊資料）或 deploy 產的英文句
        # 「no people in silhouette / in reflection」。只認中文會讓一鍵佈署的場景永久掛 warn。
        low = blob.lower()
        has_clause = SCENE_NO_HUMAN in blob or (
            ("no people" in low or "no person" in low or "no humans" in low or "empty" in low)
            and "silhouette" in low and "reflection" in low)
        if not has_clause:
            issues.append(_issue("warn", "scene", sc.get("id"),
                                 f"場景 prompt 缺「{SCENE_NO_HUMAN}」（或英文 no people in silhouette / in reflection）"
                                 "這句空景禁人語，免得生出路人",
                                 sc.get("name") or sc.get("id")))

    # 人物錨點／角色存在性
    for ch in chars:
        a = ch.get("identityAnchors") or {}
        ca = a.get("colorAnchors") or {}
        if not any(_s(a.get(k)) for k in ("faceShape", "facialFeatures", "uniqueMarks", "skinTexture", "hairStyle")):
            issues.append(_issue("warn", "character", ch.get("id"), "identityAnchors 全是空的（跨鏡鎖臉會很不穩）",
                                 ch.get("name") or ch.get("id")))
        for k, v in ca.items():
            if _s(v) and not re.fullmatch(r"#[0-9A-Fa-f]{6}", _s(v)):
                issues.append(_issue("error", "character", ch.get("id"),
                                     f"colorAnchors.{k}「{v}」不是 #RRGGBB", ch.get("name") or ch.get("id")))

    # 同一顆角色／場景在每一鏡各報一次，6 鏡專案就會出現 5 條同因 warn；
    # 使用者看到一堆重複警告反而找不到「到底缺幾張圖」。合併成「一顆角色一筆、
    # 附被哪些鏡頭用到」，其餘 issue 原樣保留、順序不變。
    issues = _merge_missing_ref_issues(issues)

    counts = {"error": sum(1 for i in issues if i["level"] == "error"),
              "warn": sum(1 for i in issues if i["level"] == "warn")}
    return {"issues": issues, "counts": counts, "fixes": fixes,
            "checked": {"shots": len(data.get("shots") or []), "scenes": len(scenes),
                        "characters": len(chars), "fps": fps},
            "at": _now()}


def _merge_missing_ref_issues(issues: list[dict]) -> list[dict]:
    """把「角色／場景沒有參考圖」的逐鏡重複 warn 併成一筆。

    不直接用 targetId 當鍵：targetId 是「哪一鏡」，真正的重複來源是同一顆角色／場景。
    """
    merged: dict[str, dict] = {}
    out: list[dict] = []
    for it in issues:
        msg = _s(it.get("message"))
        is_missing = (it.get("level") == "warn" and it.get("scope") == "shot"
                      and (msg.endswith("→ 送 H3 時是 weak reference")
                           or msg.endswith("→ 場景一致性只能靠文字")
                           # 已經併過一次的訊息尾巴是「（被 第 1 鏡、第 2 鏡 用到）」，
                           # 逐鏡掃描時會讓同一筆又被當成新的一筆加回來（實測 7 鏡會長出 6 條）。
                           or "→ 送 H3 時是 weak reference（被 " in msg
                           or "→ 場景一致性只能靠文字（被 " in msg))
        if is_missing:
            key = _missing_ref_key(msg)
            if key not in merged:
                copy = dict(it)
                copy["_msgs"] = [msg]
                copy["_shots"] = [it.get("name") or ""]
                merged[key] = copy
                out.append(copy)
            else:
                merged[key]["_msgs"].append(msg)
                merged[key]["_shots"].append(it.get("name") or "")
        else:
            out.append(it)
    for copy in merged.values():
        names = [n for n in dict.fromkeys(copy.pop("_shots", [])) if n]
        uniq = list(dict.fromkeys(copy.pop("_msgs", [])))
        base = uniq[0] if uniq else _s(copy.get("message"))
        if len(names) > 1:
            base += f"（被 {'、'.join(names)} 用到）"
        copy["message"] = base
    return out


def _missing_ref_key(msg: str) -> str:
    """從「角色 c01（阿哲）沒有參考圖 → …」抽出 `角色:c01` 當合併鍵。"""
    m = re.search(r"(角色|場景)\s+([A-Za-z0-9_:-]+)", msg)
    return f"{m.group(1)}:{m.group(2)}" if m else msg


def _now() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


# ── 4. M16a：衍生值重算（docs/shots-live-linkage.md §1–§2）──────────
# 真相（A 類）＝劇本 beats／人物／場景／分鏡決策；衍生（B 類）＝referencePlan／promptEn／
# dialogLineRefs／覆蓋狀態／孤兒判定。核心規則：畫面上的字＝API 回的字＝實際送出的字。
# 所以「讀」與「寫」兩端都要呼叫 refresh_derived（讀端在 deepcopy 上算、不寫檔）。
PROMPT_AUTO_SOURCES = ("storyboard", "deploy")   # 這些來源的 promptEn 可以被自動覆蓋
DERIVED_FIELDS = ("referencePlan", "dialogLineRefs", "derived")


def scene_coverage(data: dict) -> dict[str, dict]:
    """每場次：{covered: set(beatId), missing: [beatId 依劇本順序], uncoveredDialog: [beatId]}。

    D4：掃 shots[].beatIds ∪ dialogBeatIds（沒有 beatIds 的舊資料才退回 dialogBeatIds）。
    純讀，不改資料；refresh_derived 算 uncoveredBeats 與 storyboard.backfill_shots 共用這支。
    """
    shots = [s for s in (data.get("shots") or []) if isinstance(s, dict)]
    out: dict[str, dict] = {}
    for ep in ((data.get("script") or {}).get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        for es in (ep.get("scenes") or []):
            if not isinstance(es, dict) or not _s(es.get("id")):
                continue
            es_id = _s(es.get("id"))
            beats = [b for b in (es.get("beats") or [])
                     if isinstance(b, dict) and _s(b.get("id")) and not b.get("_skipped")]
            covered: set[str] = set()
            for sh in shots:
                if _s(sh.get("episodeSceneId")) != es_id:
                    continue
                ids = [x for x in (sh.get("beatIds") or []) if isinstance(x, str)]
                if not ids:
                    ids = [x for x in (sh.get("dialogBeatIds") or []) if isinstance(x, str)]
                covered |= set(ids)
            missing = [str(b.get("id")) for b in beats if str(b.get("id")) not in covered]
            out[es_id] = {
                "covered": covered,
                "missing": missing,
                "uncoveredDialog": [str(b.get("id")) for b in beats
                                    if str(b.get("id")) in missing and b.get("type") != "action"],
                "beatIds": [str(b.get("id")) for b in beats],
            }
    return out


def _prompt_source_of(shot: dict) -> str:
    """manual＝人寫的稿（不得自動覆蓋）／compiled＝衍生流程算的／empty＝空的。"""
    if not _s(shot.get("promptEn")):
        return "empty"
    return "manual" if shot.get("promptEnManual") is True else "compiled"


def refresh_derived(project: dict) -> dict:
    """就地重算每鏡的衍生值（D1／D2／D3）＋衍生摘要 `shot.derived`；永不 raise。

    - D1 referencePlan：沿用 build_reference_plan（不重寫邏輯）。
    - D3 dialogLineRefs：以 dialogBeatIds 對照該場次台詞順序重算（無 dialogBeatIds 的舊資料
      保留原值，不然會把使用者填的舊編號清成空）。
    - D2 promptEn：三段判定（保護使用者手工成果，見 docs/shots-live-linkage.md §1）
        1. promptEnManual=true → 保留（永不自動覆蓋）。
        2. promptEn 非空、與這次算出的 compiled 不同、且沒有旗標 → **採認為手寫稿**：
           設 promptEnManual=true、保留原值、promptSource=manual、promptEnStale=true、adoptedManual=true。
           （舊版前端改字不打旗標，第一次存檔就會被覆蓋——這條是補那個破口。）
        3. promptEn 為空 或 與 compiled 相同 → 寫入 compiled（一般重算）。
      唯一例外：derivedFrom=deploy 且存檔值是部署短草稿（<800 字且不含 <Picture）→ 照舊覆蓋
      （那是必須被編譯的草稿，凍結它等於讓使用者永遠拿到廢稿）。
    - derived：{at, promptChars, promptSource(compiled|manual|empty), promptEnStale, adoptedManual,
      slots, pictures, orphan, uncoveredBeats}；單鏡失敗記在該鏡 derived.error，其他鏡照算。

    只在記憶體上改，寫檔由呼叫端決定（storage.save_project 會存；讀端在 deepcopy 上算）。
    回 {shots: 鏡數, errors: {shotId: 訊息}}。
    """
    from . import storage  # 延遲匯入：storage 會反過來呼叫本函式（避免循環匯入）

    stats: dict = {"shots": 0, "errors": {}}
    if not isinstance(project, dict):
        return stats
    shots = [s for s in (project.get("shots") or []) if isinstance(s, dict)]
    cov = scene_coverage(project)
    at = _now()
    scene_shots: dict[str, list[dict]] = {}
    for sh in shots:
        scene_shots.setdefault(_s(sh.get("episodeSceneId")), []).append(sh)
    es_lookup: dict[str, dict] = {}
    for ep in ((project.get("script") or {}).get("episodes") or []):
        for es in ((ep or {}).get("scenes") or []):
            if isinstance(es, dict) and _s(es.get("id")):
                es_lookup[_s(es.get("id"))] = es

    for sh in shots:
        stats["shots"] += 1
        d: dict = {"at": at}
        try:
            # D1 referencePlan
            plan, _warns = build_reference_plan(project, sh)
            sh["referencePlan"] = plan
            # D3 dialogLineRefs（有 dialogBeatIds 才重算；舊資料保留原值）
            dialog_ids = [x for x in (sh.get("dialogBeatIds") or []) if isinstance(x, str)]
            if dialog_ids:
                es = es_lookup.get(_s(sh.get("episodeSceneId"))) or {}
                dlg = scene_dialogue({"es": es})
                sh["dialogLineRefs"] = storage.dialog_beat_index(dialog_ids, dlg)
            elif not isinstance(sh.get("dialogLineRefs"), list):
                sh["dialogLineRefs"] = []
            # D2 promptEn（三段判定：手改稿保護）
            stored = _s(sh.get("promptEn"))
            manual = sh.get("promptEnManual") is True
            auto = (not manual) and (
                _s(sh.get("derivedFrom")) in PROMPT_AUTO_SOURCES or not stored)
            # 判定要「跟這次算出的 compiled 比」，所以規則式鏡一律現算一份（編譯是確定性的）
            compiled = ""
            if auto or not manual or stored:
                compiled, _cw, _meta = compile_prompt(project, sh)
                compiled = _s(compiled)
            deploy_draft = (_s(sh.get("derivedFrom")) == "deploy" and len(stored) < 800
                            and "<Picture" not in stored)
            adopted = False
            if manual:
                fresh = stored                                  # ① 手寫稿（有旗標）
            elif not stored or stored == compiled or deploy_draft:
                fresh = compiled                                # ③ 空／同值／部署短草稿 → 重算
                sh["promptEn"] = compiled
            else:
                # ② 非空、與編譯稿不同、且沒有旗標 → 舊版前端改的字：採認為手寫稿並保全
                sh["promptEnManual"] = True
                fresh = stored
                adopted = True
            new_source = _prompt_source_of(sh)
            d.update({
                "promptChars": len(fresh),
                "promptSource": new_source,
                # 手寫稿才算「落後」：衍生流程自己算的那份一定是即時的
                "promptEnStale": bool(new_source == "manual" and stored != compiled),
                "adoptedManual": adopted,
                "slots": len(plan.get("slots") or []),
                "pictures": [int(s["ordinal"]) for s in (plan.get("slots") or [])
                             if isinstance(s, dict) and isinstance(s.get("ordinal"), int)],
            })
            if new_source != "manual":
                d["promptEnStale"] = False
            if not adopted:
                d.pop("adoptedManual", None)
            else:
                d["promptEnStale"] = True
            # D5 孤兒：beatIds 空或全部指向不存在的 beat，且同場次有規則式鏡頭
            es_id = _s(sh.get("episodeSceneId"))
            scene_cov = cov.get(es_id) or {}
            valid = set(scene_cov.get("beatIds") or [])
            ids = [x for x in (sh.get("beatIds") or []) if isinstance(x, str)]
            has_rule_shots = any(_s(o.get("derivedFrom")) in PROMPT_AUTO_SOURCES
                                 for o in (scene_shots.get(es_id) or []) if o is not sh)
            orphan = bool(es_id in cov and has_rule_shots and not any(i in valid for i in ids))
            d["orphan"] = orphan
            d["uncoveredBeats"] = [] if orphan else list(scene_cov.get("missing") or [])
        except Exception as e:  # noqa: BLE001 — 單鏡失敗不能拖垮整份專案
            d["error"] = f"{type(e).__name__}: {e}"
            d.setdefault("promptChars", len(_s(sh.get("promptEn"))))
            d.setdefault("promptSource", _prompt_source_of(sh))
            d.setdefault("promptEnStale", False)
            d.setdefault("slots", len(((sh.get("referencePlan") or {}).get("slots")) or []))
            d.setdefault("pictures", [])
            d.setdefault("orphan", False)
            d.setdefault("uncoveredBeats", [])
            stats["errors"][_s(sh.get("id"))] = d["error"]
        sh["derived"] = {k: v for k, v in d.items() if v is not None}
    return stats
