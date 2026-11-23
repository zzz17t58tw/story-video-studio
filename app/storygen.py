"""M3c：結構化一次生成（故事→整集劇本、場次→多鏡頭）＋時長估算。

只組 prompt、呼叫模型、驗證結構；**不寫檔**（寫入一律走 storage.apply_bulk）。
時長算式沿用 huobao-drama 的四條規則（docs/research-existing-tools.md §3-A，只抄規則文字）：
  1. 目標總時長 = 劇本字數 ÷ 500 字/分鐘
  2. 段落數 ≈ 總時長 ÷ 12 秒（±20%）
  3. 段落時長 ≥ 段內台詞字數 ÷ 4.5 字/秒 + 2 秒表演餘量
  4. 段落長度參考：過渡 8–10s、敘事 10–15s、爆點 12–15s
"""

from __future__ import annotations

import json
import re
from typing import Callable

from . import llm, prompts, storage

_SEC_LIMITS = {"min": 2.0, "max": 15.0}
_SHOT_FRAMING = {"close_up": "cu", "medium": "mcu", "over_shoulder": "mcu", "pov": "mcu", "wide": "wide"}
# 結構化輸出很長（3 鏡六段式英文 ≈ 3k tokens）；deepseek 這類模型的思考內容也算進 max_tokens，
# 留太低會 finish_reason=length 拿到空回覆（M3c 實測 4096 不夠）。
MAX_TOKENS = 8192


class GenError(Exception):
    """生成失敗（含兩次嘗試後的結構錯誤）→ API 轉 400/502 中文。"""

    def __init__(self, message: str, status: int = 400, raw: str = "", warnings: list | None = None):
        super().__init__(message)
        self.status = status
        self.raw = raw or ""
        self.warnings = warnings or []


# ── 時長估算（huobao 四條算式）───────────────────────────────────
def _nonspace_len(text) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def estimate_durations(beats: list, target_seconds: float | None = None) -> dict:
    """依 §3-A 算式估一組 beats 的時長。

    回 {paragraphCount, paragraphSec, dialogueSec, targetSeconds, perBeat:[{index,chars,sec}], warnings[]}
    - 目標總時長：有給 targetSeconds 就用，否則「字數 ÷ 500 字/分」
    - 段落數 ≈ 總時長 ÷ 12 秒，抽查 ±20%
    - 段內時長 ≥ 台詞字數 ÷ 4.5 字/秒 + 2 秒；夾在 2–15 秒
    """
    beats = [b for b in (beats or []) if isinstance(b, dict)]
    total_chars = sum(_nonspace_len(b.get("line")) for b in beats)
    by_text = total_chars / (500.0 / 60.0)  # 字 ÷ 8.33 字/秒
    target = float(target_seconds) if target_seconds else max(by_text, 1.0)
    warnings: list[str] = []
    para = target / 12.0
    per = []
    for i, b in enumerate(beats):
        chars = _nonspace_len(b.get("line"))
        is_dialogue = (b.get("type") or "dialogue") != "action"
        sec = chars / 4.5 + 2.0 if is_dialogue else max(3.0, chars / 6.0 + 2.0)
        per.append({"index": i, "type": b.get("type") or "dialogue", "chars": chars,
                    "sec": round(min(max(sec, _SEC_LIMITS["min"]), _SEC_LIMITS["max"]), 1)})
    sum_sec = round(sum(p["sec"] for p in per), 1)
    low, high = target * 0.8, target * 1.2
    if beats and not (low <= sum_sec <= high):
        warnings.append(
            f"估算總長 {sum_sec}s 不在目標 {target:.0f}s 的 ±20%（{low:.0f}–{high:.0f}s）："
            f"{'偏短，可加動作／補台詞' if sum_sec < low else '偏長，可刪句或拆成多段'}")
    if beats and not (max(1, para * 0.8) <= len(beats) <= max(1, para * 1.2)):
        warnings.append(f"段落數 {len(beats)} 與概算 {para:.1f} 段（總時長÷12s）差距較大")
    return {
        "targetSeconds": round(target, 1),
        "textSeconds": round(by_text, 1),
        "paragraphCount": len(beats),
        "paragraphGuess": round(para, 1),
        "estimatedSeconds": sum_sec,
        "perBeat": per,
        "warnings": warnings,
    }


# ── JSON 提取（容忍 ```json 包裹與前後雜訊）─────────────────────
def extract_json(text: str):
    raw = str(text or "")
    fence = re.search(r"```(?:json|JSON)?\s*(.+?)```", raw, re.S)
    candidates = [fence.group(1) if fence else raw]
    # 從第一個 { 到最後一個 } 再試一次（常見：模型先講一句話再吐 JSON）
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.index("{"): raw.rindex("}") + 1])
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            pass
        # 本機小模型的常見瑕疵：尾逗號、字串內的真換行、單引號 key —— 修一次再試
        fixed = re.sub(r",\s*([}\]])", r"\1", cand)
        fixed = re.sub(r'"\s*\n\s*"', '" "', fixed)  # 字串被斷行（"..."\n"..."）
        fixed = fixed.replace("\r", " ")
        for attempt in (fixed, fixed.replace("\n", "\\n")):
            try:
                return json.loads(attempt)
            except Exception:
                continue
    raise GenError("模型回覆不是合法 JSON", raw=raw[:800])


# ── 共用：呼叫模型 + 驗證 + 失敗回饋重試一次 ──────────────────────
def _run(llm_cfg: dict, system: str, user: str, validate: Callable[[object], tuple[dict, list]],
         attempts: int = 2) -> dict:
    """呼叫模型 → 驗結構 → 不合格就把錯誤回饋給模型再試（預設共 2 次；本機小模型可給 3 次）。

    本機模型偶爾會在字串中間被截斷（finish_reason=length 或連線斷），這是取樣問題不是提示問題，
    所以重試時「降溫＋只留錯誤訊息」（不把半截輸出塞回去，免得越改越亂）。
    """
    model = (llm_cfg or {}).get("model")
    base_temp = float((llm_cfg or {}).get("temperature") or 0.7)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_raw, last_err = "", ""
    for attempt in range(1, attempts + 1):
        try:
            result = llm.chat(messages, llm_cfg=llm_cfg, model=model,
                              temperature=max(0.35, base_temp - 0.1 * (attempt - 1)),
                              max_tokens=MAX_TOKENS)
        except llm.LLMError:
            if attempt == attempts:
                raise
            last_err = "模型服務中斷或回應異常"
            messages = messages[:2] + [{"role": "user", "content": "請重新輸出完整且合法的 JSON 物件本體。"}]
            continue
        last_raw = result["reply"]
        if result.get("finish_reason") == "length":
            last_err = f"輸出被長度上限截斷（finish_reason=length，{result.get('usage', {}).get('completion_tokens')} tokens）"
            if attempt == attempts:
                raise GenError(f"{last_err}（已自動重試 {attempts - 1} 次仍失敗）", status=400, raw=last_raw[:800])
            messages = messages[:2] + [{"role": "user", "content":
                "上一次輸出太長被截斷了。請輸出**更精簡**的同一份 JSON：句子縮短、不要重複描述、"
                "但欄位一個都不能少，只回 JSON 本體。"}]
            continue
        try:
            data = extract_json(last_raw)
            out, warnings = validate(data)
            if attempt > 1:
                warnings = [*(warnings or []), f"第 {attempt} 次輸出才合格（前幾次結構不符，已自動修正）"]
            return {"preview": out, "warnings": warnings, "raw": last_raw, "model": result["model"],
                    "usage": result["usage"], "attempts": attempt}
        except GenError as e:
            last_err = str(e)
            if attempt == attempts:
                raise GenError(f"{last_err}（已自動重試 {attempts - 1} 次仍失敗；"
                               f"finish_reason={result.get('finish_reason')}／輸出 {len(last_raw)} 字元）",
                               status=400, raw=last_raw[:800]) from e
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                {"role": "user", "content":
                    f"上一次的輸出不合格：{last_err}。請重新輸出**完整且合法**的 JSON 物件本體："
                    "不要 markdown 圍欄、不要任何說明文字、不要省略欄位、字串內不要換行。"},
            ]
    raise GenError(last_err or "生成失敗", status=400, raw=last_raw[:500])


def _card_context(project: dict) -> str:
    """真實角色卡／場景卡／故事／風格（逐字給模型，不准發明新 id）。"""
    chars = []
    for c in project.get("characters") or []:
        if not isinstance(c, dict):
            continue
        app = c.get("appearance") or {}
        looks = "、".join(str(app.get(k)) for k in ("age", "gender", "face", "hair", "body", "features") if app.get(k))
        chars.append({"id": c.get("id"), "name": c.get("name"), "role": c.get("role"),
                      "外型": (app.get("promptEn") or looks or "")[:200],
                      "服裝": ((c.get("outfits") or [{}])[0].get("desc") if c.get("outfits") else "") or ""})
    scenes = [{"id": s.get("id"), "name": s.get("name"), "時間": s.get("timeOfDay"), "光線": s.get("lighting"),
               "氛圍": s.get("mood"), "描述": (s.get("desc") or "")[:200]}
              for s in project.get("scenes") or [] if isinstance(s, dict)]
    story = project.get("story") or {}
    return json.dumps({
        "故事概念": story.get("premise") or "",
        "故事大綱": (story.get("synopsis") or "")[:1500],
        "章回": [{"id": ch.get("id"), "title": ch.get("title"), "summary": (ch.get("summary") or "")[:200]}
                 for ch in story.get("chapters") or [] if isinstance(ch, dict)],
        "風格": story.get("style") or {},
        "人物卡（只能用這些 id）": chars,
        "場景卡（只能用這些 id）": scenes,
        "畫面比例": (project.get("meta") or {}).get("aspectRatio"),
    }, ensure_ascii=False, indent=2)


# ── 整集劇本 ───────────────────────────────────────────────────
_SCRIPT_SCHEMA = """\
{
  "title": "集名（繁體中文，10–20 字）",
  "logline": "一句話說這一集發生什麼",
  "scenes": [
    {
      "title": "場次標題（繁體中文）",
      "sceneId": "現有場景卡 id（只能從上面清單選）",
      "characterIds": ["出場人物 id"],
      "summary": "這一場發生什麼（60–150 字）",
      "targetSeconds": 40,
      "beats": [
        {"type": "dialogue", "characterId": "c01", "line": "台詞原文", "emotion": "情緒", "notes": ""},
        {"type": "action", "characterId": null, "line": "動作描述（鏡頭拍得到的具體行為）", "emotion": "", "notes": ""}
      ]
    }
  ]
}"""


def gen_script(project: dict, llm_cfg: dict, episode_title: str = "", scene_count: int = 3,
               target_minutes: float = 1.5) -> dict:
    """回一集 script（不含 id，id 由 apply_bulk 編；dryRun 不寫檔）。"""
    scene_count = max(1, min(int(scene_count or 3), 12))
    target_minutes = max(0.2, min(float(target_minutes or 1.5), 60.0))
    target_sec = int(target_minutes * 60)
    style = (project.get("story") or {}).get("style") or {}
    system = f"""你是短劇編劇。要為專案寫「一集」可直接分鏡的劇本（繁體中文）。
【專案資料】
{_card_context(project)}

【這一集的規則】
- 場景數：恰好 {scene_count} 場。
- 目標總時長：約 {target_sec} 秒（{target_minutes} 分鐘）。依字數÷500字/分、段落≈總時長÷12秒估。
- 集名：{episode_title or "由你依故事命名"}。
- 只能使用上面「人物卡」「場景卡」的 id，**不准發明新角色或新場景 id**；每個 sceneId / characterId 必須存在。
- beats 依序涵蓋全場戲：對白（type=dialogue，必填 characterId 與 line）與動作（type=action，characterId 可 null）。
- 台詞要短、能自然說出口（每句 5–40 字），避免旁白式解釋；場景順序要服務衝突推進。
- 風格：類型 {style.get('genre') or '—'}／基調 {style.get('tone') or '—'}／視覺 {style.get('visualStyle') or '—'}。
- 回「純 JSON 物件」（不要 markdown 圍欄、不要說明），結構如下（id 欄位不要填）：
{_SCRIPT_SCHEMA}"""
    user = f"請寫這一集劇本：集名「{episode_title or '（由你命名）'}」，{scene_count} 場，約 {target_sec} 秒。"

    allowed_scenes = {s.get("id") for s in project.get("scenes") or [] if isinstance(s, dict)}
    allowed_chars = {c.get("id") for c in project.get("characters") or [] if isinstance(c, dict)}

    def validate(data) -> tuple[dict, list]:
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        if not isinstance(data, dict):
            raise GenError("最外層必須是 JSON 物件")
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise GenError("缺少 scenes 陣列或為空")
        warnings: list[str] = []
        out_scenes = []
        for i, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                raise GenError(f"第 {i + 1} 場不是物件")
            sid = sc.get("sceneId")
            if sid not in allowed_scenes:
                raise GenError(f"第 {i + 1} 場的 sceneId {sid!r} 不存在（可用：{'、'.join(sorted(allowed_scenes)) or '無'}）")
            be = sc.get("beats")
            if not isinstance(be, list) or not be:
                raise GenError(f"第 {i + 1} 場沒有 beats")
            for j, b in enumerate(be):
                if not isinstance(b, dict):
                    raise GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 不是物件")
                if not str(b.get("line") or "").strip():
                    raise GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 的 line 是空的")
                b["type"] = "action" if str(b.get("type")) == "action" else "dialogue"
                cid = b.get("characterId")
                if cid and cid not in allowed_chars:
                    raise GenError(f"第 {i + 1} 場第 {j + 1} 個 beat 的 characterId {cid!r} 不存在")
                if b["type"] == "dialogue" and not cid:
                    raise GenError(f"第 {i + 1} 場第 {j + 1} 個對話 beat 缺少 characterId")
            chars = [c for c in (sc.get("characterIds") or []) if isinstance(c, str)]
            for cid in chars:
                if cid not in allowed_chars:
                    raise GenError(f"第 {i + 1} 場的 characterId {cid!r} 不存在")
            # 出場人物 = 宣告 ∪ beats 用的；保持順序
            for b in be:
                if b.get("characterId") and b["characterId"] not in chars:
                    chars.append(b["characterId"])
            est = estimate_durations(be, sc.get("targetSeconds"))
            warnings += [f"第 {i + 1} 場：{w}" for w in est["warnings"]]
            out_scenes.append({
                "title": str(sc.get("title") or "").strip() or f"場次 {i + 1}",
                "sceneId": sid, "characterIds": chars,
                "summary": str(sc.get("summary") or "").strip(),
                "beats": [{"type": b["type"], "characterId": b.get("characterId") or None,
                           "line": str(b["line"]).strip(), "emotion": str(b.get("emotion") or ""),
                           "notes": str(b.get("notes") or "")} for b in be],
            })
        if len(out_scenes) != scene_count:
            warnings.append(f"要求 {scene_count} 場，模型給了 {len(out_scenes)} 場")
        total_est = sum(estimate_durations(s["beats"])["estimatedSeconds"] for s in out_scenes)
        if not (target_sec * 0.6 <= total_est <= target_sec * 1.4):
            warnings.append(f"全集合計估算 {total_est:.0f}s，與目標 {target_sec}s 差距較大")
        title = str(data.get("title") or "").strip() or episode_title or "未命名集"
        return ({"title": title, "logline": str(data.get("logline") or "").strip(),
                 "scenes": out_scenes, "estimatedSeconds": round(total_est, 1),
                 "targetSeconds": target_sec}, warnings)

    return _run(llm_cfg, system, user, validate)


# ── 單場次 → 多鏡頭 ─────────────────────────────────────────────
_SHOT_SCHEMA = """\
{
  "shots": [
    {
      "durationSec": 6,
      "spec": {"shotType": "close_up|medium|wide|over_shoulder|pov",
               "angle": {"h": "front|front_left|left|back_left|back|back_right|right|front_right",
                         "v": "worm|low|eye_level|high",
                         "s": "close_up|medium|wide"},
               "cameraMove": "static|push_in|pull_out|pan_left|pan_right|tracking|handheld",
               "composition": "構圖說明（中文）", "lighting": "光線（中文）"},
      "beats": [{"start": 0.0, "end": 3.0, "cut": false, "action": "子鏡頭動作（中文）", "camera": "運鏡（中文）", "locationId": ""}],
      "dialogLineRefs": [1],
      "promptZh": "繁體中文畫面描述（給人看）",
      "promptEn": "1. Subject: …\\n2. Action: …\\n3. Camera: …\\n4. Lighting: …\\n5. Atmosphere: …\\n6. Audio: …"
    }
  ]
}"""


def gen_shots(project: dict, llm_cfg: dict, episode_scene_id: str, count: int = 3) -> dict:
    """回某場次的多個鏡頭（不含 id/no；含 spec/beats/dialogLineRefs/promptZh/promptEn）。"""
    count = max(1, min(int(count or 3), 12))
    es = ep = None
    for e in (project.get("script") or {}).get("episodes") or []:
        for s in e.get("scenes") or []:
            if isinstance(s, dict) and s.get("id") == episode_scene_id:
                es, ep = s, e
    if es is None:
        raise GenError(f"找不到劇本場次：{episode_scene_id}", status=400)
    beats = [b for b in (es.get("beats") or []) if isinstance(b, dict)]
    if not beats:
        raise GenError(f"場次 {episode_scene_id} 沒有 beats，先在劇本頁加台詞或動作再生成鏡頭", status=400)
    scene = next((s for s in project.get("scenes") or [] if isinstance(s, dict) and s.get("id") == es.get("sceneId")), None)
    if scene is None and not es.get("sceneId"):
        raise GenError(f"場次 {episode_scene_id} 沒有指定場景卡", status=400)
    est = estimate_durations(beats)
    beat_list = "\n".join(
        f"  第 {i + 1} 句｜{b.get('type') or 'dialogue'}｜{prompts._char_brief(project, b.get('characterId')) if b.get('characterId') else '（無角色）'}"
        f"｜{b.get('line')}｜{b.get('emotion') or ''}｜估 {est['perBeat'][i]['sec']}s"
        for i, b in enumerate(beats))
    def stage_bit(cid: str) -> str:
        ch = next((c for c in project.get("characters") or [] if isinstance(c, dict) and c.get("id") == cid), {})
        ids = [f"{cid}:{st.get('id')}" for st in (ch.get("stages") or []) if isinstance(st, dict) and st.get("id")]
        return "、".join(ids) or "（無，留空）"

    char_lines = "\n".join(f"  {prompts._char_brief(project, cid)}｜stageIds 可用：{stage_bit(cid)}"
                           for cid in (es.get("characterIds") or []))
    system = f"""你是分鏡師。要把劇本的「一場」拆成 {count} 個可拍的鏡頭（繁體中文描述＋英文 prompt）。
【專案資料】
{_card_context(project)}

【這一場】
集：{ep.get('id')} · {ep.get('title') or ''}　場次：{es.get('id')} · {es.get('title') or ''}
場景卡：{scene.get('name') if scene else '（未指定）'}（{es.get('sceneId')}）｜場次摘要：{es.get('summary') or '（無）'}
出場人物：
{char_lines or '  （未指定）'}
台詞與動作（1-based 編號就是 dialogLineRefs 的寫法）：
{beat_list}

【規則】
- 恰好 {count} 鏡，順序就是剪接順序；合起來要涵蓋全部 {len(beats)} 句台詞，不重複演同一句。
- dialogLineRefs 填這鏡要演的「句號」（1–{len(beats)}），沒有台詞的純動作鏡填 []。
- spec.angle / shotType / cameraMove 只能用英文枚舉（見結構）。有人的鏡頭禁 wide（framing rule）。
- durationSec 依台詞字數估（字數÷4.5 + 2 秒餘量），2–15 秒；每鏡 1–3 個 beats（start/end 秒數落在 durationSec 內）。
- promptEn 用 H3 六段式（1 Subject / 2 Action / 3 Camera / 4 Lighting / 5 Atmosphere / 6 Audio），英文、不用角色中文名；
  不要寫 <Picture N> 或 {{c01}} 這類佔位符（參考圖槽位由程式產生）。
- 只能使用上面列出的人物／場景 id。
- 回「純 JSON 物件」（不要 markdown 圍欄、不要說明）：
{_SHOT_SCHEMA}"""
    user = f"請把場次 {episode_scene_id} 拆成 {count} 個鏡頭。"

    allowed_chars = {c for c in (es.get("characterIds") or []) if isinstance(c, str)}

    def validate(data) -> tuple[dict, list]:
        if isinstance(data, dict):
            shots = data.get("shots")
        elif isinstance(data, list):
            shots = data
        else:
            shots = None
        if not isinstance(shots, list) or not shots:
            raise GenError("缺少 shots 陣列或為空")
        warnings: list[str] = []
        seen_refs: dict[int, int] = {}
        out = []
        for i, sh in enumerate(shots):
            if not isinstance(sh, dict):
                raise GenError(f"第 {i + 1} 鏡不是物件")
            spec = sh.get("spec") if isinstance(sh.get("spec"), dict) else {}
            st = storage.shot_type_en(spec.get("shotType"))
            if not st:
                raise GenError(f"第 {i + 1} 鏡的 shotType {spec.get('shotType')!r} 不合法"
                               f"（可用：{'/'.join(storage.V02_SHOT_TYPES)}）")
            angle = spec.get("angle") if isinstance(spec.get("angle"), dict) else {}
            for key, allowed in (("h", storage.V02_ANGLES_H), ("v", storage.V02_ANGLES_V), ("s", storage.V02_ANGLES_S)):
                val = angle.get(key) or ""
                if val and val not in allowed:
                    raise GenError(f"第 {i + 1} 鏡的 angle.{key} {val!r} 不合法（可用：{'/'.join(allowed)}）")
            move = spec.get("cameraMove") or ""
            if move and move not in storage.V02_CAMERA_MOVES:
                raise GenError(f"第 {i + 1} 鏡的 cameraMove {move!r} 不合法（可用：{'/'.join(storage.V02_CAMERA_MOVES)}）")
            refs = sh.get("dialogLineRefs") or []
            if not isinstance(refs, list):
                raise GenError(f"第 {i + 1} 鏡的 dialogLineRefs 必須是陣列")
            for r in refs:
                if not isinstance(r, int) or isinstance(r, bool) or not (1 <= r <= len(beats)):
                    raise GenError(f"第 {i + 1} 鏡的 dialogLineRefs 含不合法句號 {r!r}（1–{len(beats)}）")
                if r in seen_refs:
                    raise GenError(f"第 {r} 句台詞在第 {seen_refs[r] + 1} 鏡與第 {i + 1} 鏡重複演出")
                seen_refs[r] = i
            dur = sh.get("durationSec")
            try:
                dur = float(dur)
            except (TypeError, ValueError):
                raise GenError(f"第 {i + 1} 鏡的 durationSec 不是數字")
            dur = min(max(dur, 2.0), 15.0)
            # 台詞時長下限（huobao §3-A：字數÷4.5+2 秒）；但不超過單鏡上限 → 拉不到就只警告
            need_raw = sum(_nonspace_len(beats[r - 1].get("line")) / 4.5 + 2.0 for r in refs) if refs else 0
            need = min(need_raw, _SEC_LIMITS["max"])
            if need and dur + 0.01 < need:
                warnings.append(f"第 {i + 1} 鏡 {dur:g}s 少於台詞所需 {need:g}s，已調為 {need:g}s")
                dur = need
            if need_raw > _SEC_LIMITS["max"]:
                warnings.append(f"第 {i + 1} 鏡台詞需 {need_raw:.1f}s 超過單鏡上限 15s：建議把台詞拆到相鄰鏡頭")
            sb = []
            for b in sh.get("beats") or []:
                if not isinstance(b, dict):
                    continue
                try:
                    s0, e0 = float(b.get("start") or 0), float(b.get("end") or dur)
                except (TypeError, ValueError):
                    s0, e0 = 0.0, dur
                loc = b.get("locationId") or ""
                if loc and loc != es.get("sceneId"):
                    raise GenError(f"第 {i + 1} 鏡的 beat locationId {loc!r} 與場次場景不符")
                sb.append({"start": round(max(0.0, min(s0, dur)), 2), "end": round(max(s0, min(e0, dur)), 2),
                           "cut": bool(b.get("cut")), "action": str(b.get("action") or ""),
                           "camera": str(b.get("camera") or ""), "locationId": loc})
            if not sb:
                sb = [{"start": 0.0, "end": round(dur, 2), "cut": False,
                       "action": "（模型未給子鏡頭，單一連續動作）", "camera": "", "locationId": es.get("sceneId") or ""}]
            if st == "wide" and allowed_chars:
                warnings.append(f"第 {i + 1} 鏡含人物卻用 wide（MSB 規則：禁 wide）")
            out.append({
                "durationSec": round(dur, 1),
                "characterIds": [c for c in (sh.get("characterIds") or list(allowed_chars)) if c in allowed_chars],
                "spec": {"shotType": st, "angle": {k: (angle.get(k) or "") for k in ("h", "v", "s")},
                         "cameraMove": move, "composition": str(spec.get("composition") or ""),
                         "lighting": str(spec.get("lighting") or ""),
                         "framingRule": storage.V02_FRAMING_RULE},
                "beats": sb,
                "dialogLineRefs": refs,
                "stageIds": [s for s in (sh.get("stageIds") or []) if isinstance(s, str) and s.split(":", 1)[0] in allowed_chars],
                "promptZh": str(sh.get("promptZh") or "").strip(),
                "promptEn": str(sh.get("promptEn") or "").strip(),
            })
        if len(out) != count:
            warnings.append(f"要求 {count} 鏡，模型給了 {len(out)} 鏡")
        missing = [r for r in range(1, len(beats) + 1) if r not in seen_refs]
        if missing:
            warnings.append(f"台詞未覆蓋：第 {'、'.join(map(str, missing))} 句（dialogLineRefs 有漏）")
        return ({"episodeSceneId": episode_scene_id, "sceneId": es.get("sceneId"), "shots": out,
                 "coverage": {"total": len(beats), "covered": len(seen_refs)}}, warnings)

    return _run(llm_cfg, system, user, validate)
