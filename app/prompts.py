"""M3a：system prompt 組裝（專案 meta ＋ 目標卡片現況 ＋ 任務規則）。

只組文字，不呼叫模型、不寫檔。前端送來的 messages 一律當「使用者對話」，
這裡只負責給模型「你是誰、這個專案是什麼、這張卡現在長什麼樣、這次任務要回什麼」。
"""

from __future__ import annotations

import json

_KIND_LABEL = {
    "story": "故事",
    "character": "人物卡",
    "scene": "場景卡",
    "episodeScene": "劇本場次",
    "beat": "台詞／動作 beat",
    "shot": "鏡頭",
}

_COMMON_RULES = """\
你是「Story Video Studio」的劇本與美術顧問，協助使用者把一個故事做成可拍的短影片。
原則：
1. 一律用繁體中文回覆（除非被要求寫英文 prompt）。
2. 只輸出「可直接使用」的內容本體：不要開場寒暄、不要「好的，以下是…」、不要解釋你做了什麼、
   不要用 markdown 標題與粗體，不要加引號包住整段。
3. 不要編造跟現有資料矛盾的人物關係、場景、時間；若資料不足，用合理的細節補齊並保持前後一致。
4. 你現在只能「回文字」。不要說你已寫入檔案；要不要採用由使用者按「套用」決定。
5. 若使用者要求調整，直接給出修改後的完整版本（不要只給 diff 或「第 3 段改成…」）。
"""

_TASK_RULES = {
    "story_style": """\
任務：調整/建議故事風格設定。
- 針對類型（genre）、基調（tone）、視覺風格（visualStyle）、負向提示（negative）給出精簡建議。
- 每項一行，格式「類型：…」「基調：…」「視覺風格：…」「負向提示：…」，不要多餘說明。""",
    "character_look": """\
任務：依姓名與定位生成人物外型，並寫成英文生圖 prompt。
- 第一段（中文）：五官、髮型、體型、辨識特徵，面向可拍成短影片的具體細節。
- 第二段開頭固定寫「promptEn:」，後面接一整段英文 comma-separated 描述，
  順序：age/gender → face → hair → body → distinctive feature → style/lighting。
- 英文段不要寫中文、不要換行、不要 markdown。""",
    "character_outfit": """\
任務：為這個人物設計服裝。
- 先一行中文說明「服裝名稱：說明」。
- 再一行「promptEn:」接英文服裝描述（服裝、材質、顏色、配件），comma-separated 一段。""",
    "character_voice": """\
任務：依人物描述建議配音音色。
- 給 3 個候選音色，每個一行，格式「1. 音色名 — 為什麼適合（氣質/語速/音域）＋ 一句試音台詞」。
- 最後一行給「建議引擎：fish_s2 或 indextts2」與一句理由。""",
    "scene_expand": """\
任務：擴寫場景描述。
- 150–300 字，涵蓋：時間、光線、氣氛、可拍到的具體物件與材質、聲音線索（環境音）。
- 直接寫敘述段落，不要條列。""",
    "scene_prompt_en": """\
任務：把場景寫成英文生圖 prompt。
- 只回一整段英文 comma-separated 描述，不要中文、不要標題。
- 包含：location → time of day → lighting → atmosphere → key props/materials → camera framing。""",
    # ── M3b：劇本場次／beat ────────────────────────────────────
    "episode_scene_dialogue": """\
任務：把這一場寫成完整對白。
- 只保留這一場現有的角色（出場人物清單裡的人），不要新增角色。
- 依現有 beats 的順序與類型改寫成可直接拍攝的對白＋動作提示；每句一行，格式「角色名：台詞」。
- 有情緒或動作的地方用全形括號附在台詞後，例如「小紅帽（壓低聲音）：誰在那裡？」。
- 只寫這一場的內容，不要寫其他場次，不要加場次標題或編號。""",
    "episode_scene_rewrite": """\
任務：把這一場改寫得更口語、更有張力。
- 保留原本的角色與劇情走向，不新增角色、不改變事件結果。
- 口語：句子要短、能自然說出口，避免書面語與解釋性旁白。
- 張力：把關鍵資訊延後揭曉、讓角色的欲望互相衝突；動作提示要具體可拍。
- 輸出整場（含所有 beats）的完整版本，格式同原文。""",
    "episode_scene_action": """\
任務：為這一場加動作描述。
- 在現有對白的每個「說話點」前後，補上角色身體動作、手勢、視線、與環境的互動。
- 每句一行，格式「角色名（動作描述）：台詞」；沒有台詞的節奏用「（動作：…）」單獨一行。
- 動作必須是鏡頭拍得到的具體行為，不要寫內心戲。""",
    "beat_rewrite": """\
任務：改寫這一句台詞。
- 保留角色的說話目的與資訊量，只換說法；長度與原句相近（±30%）。
- 照指定情緒演出：用詞、斷句、語尾都要服務這個情緒。
- 只回那一句台詞本身：不加角色名、不加引號、不加說明或備選。""",
    "beat_variants": """\
任務：給這一句台詞三個版本。
- 三個版本分別偏「更口語」「更有張力」「更含蓄」，資訊量與原句相同。
- 每版一行，格式「1. 台詞」到「3. 台詞」；行末用全形括號註明風格（例：（更口語））。
- 不要在台詞前加角色名，不要加開場白或總結。""",
    # ── M3b：鏡頭（H3 六段式）────────────────────────────────
    "shot_prompt_h3": """\
任務：為這個鏡頭寫出 H3 影片生成用的英文 promptEn（六段式）。
- 只回英文 prompt 本體，分成 6 段，每段一行，段首固定寫「1. Subject:」「2. Action:」「3. Camera:」
  「4. Lighting:」「5. Atmosphere:」「6. Audio:」，不要中文、不要標題、不要 markdown 粗體。
- 1 Subject：人物以資料庫裡的外型描述定義（年齡/性別/髮型/服裝/辨識特徵），加上場景主體與關鍵物件；
  動物或非人物角色也要描述輪廓與質感。
- 2 Action：這一個鏡頭時間內發生的具體動作與表情變化（單一連續動作，不要塞多個事件）。
- 3 Camera：機位與運鏡（鏡頭類型、角度、構圖、推拉搖移），與 spec 欄位一致。
- 4 Lighting：光源方向、色溫、明暗對比。
- 5 Atmosphere：霧氣、粒子、色調、風格質感（與專案 visualStyle 一致）。
- 6 Audio：環境音與人聲線索（誰說話、音量、距離感）；沒有對話就寫環境音。
- 不要在英文 prompt 裡出現角色中文姓名（改用 “the girl in the crimson hood” 這類描述）。""",
    "shot_prompt_en_from_zh": """\
任務：把鏡頭的中文提示（promptZh）翻成英文 promptEn。
- 只回英文，六段式（1 Subject / 2 Action / 3 Camera / 4 Lighting / 5 Atmosphere / 6 Audio），每段一行。
- 中文沒寫到的段落，用現有 spec、場景卡與人物卡資料合理補齊，不要留空。
- 不要中文、不要解釋、不要 markdown。""",
    "shot_check_consistency": """\
任務：檢查這個鏡頭的 promptEn 與人物卡／場景卡設定是否矛盾。
- 逐項比對：人物年齡性別、髮型、服裝、辨識特徵、場景時間與光線、運鏡合理性。
- 有矛盾：每條一行，格式「✗ 矛盾：<promptEn 寫的> ↔ <卡片設定的>；建議改為：<英文片段>」。
- 沒有矛盾：只回一行「✓ 未發現矛盾」，再用一行點出可加強的 1–2 個具體細節（英文片段）。
- 用繁體中文寫說明，引用的 prompt 片段保留英文。""",
}


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n] + "…（略）"


def _project_context(project: dict) -> str:
    meta = project.get("meta") or {}
    story = project.get("story") or {}
    style = story.get("style") or {} if isinstance(story, dict) else {}
    chars = project.get("characters") or []
    scenes = project.get("scenes") or []
    lines = [
        f"專案名稱：{meta.get('name') or '（未命名）'}",
        f"畫面比例：{meta.get('aspectRatio') or '9:16'}（fps {meta.get('fps') or 24}）",
        f"故事概念：{_clip(story.get('premise'), 300) or '（尚未填寫）'}",
        f"風格：類型 {style.get('genre') or '—'}／基調 {style.get('tone') or '—'}／視覺 {style.get('visualStyle') or '—'}"
        f"／負向提示 {style.get('negative') or '—'}",
    ]
    if isinstance(story.get("synopsis"), str) and story["synopsis"].strip():
        lines.append(f"現有大綱（前 400 字）：{_clip(story['synopsis'], 400)}")
    if chars:
        lines.append("現有的人物：" + "、".join(
            f"{c.get('name') or c.get('id')}（{c.get('id')}，{c.get('role') or '未定'}）"
            for c in chars if isinstance(c, dict)))
    if scenes:
        lines.append("現有的場景：" + "、".join(
            f"{s.get('name') or s.get('id')}（{s.get('id')}）" for s in scenes if isinstance(s, dict)))
    return "\n".join(lines)


def _style_bits(project: dict) -> dict:
    story = project.get("story") or {}
    style = story.get("style") if isinstance(story, dict) else {}
    return style if isinstance(style, dict) else {}


def _char_brief(project: dict, cid: str | None) -> str:
    """人物 → 「名字（id，定位；外型：…）」一行（M3b：鏡頭／beat 對白要知道誰長什麼樣）。"""
    if not cid:
        return "（未指定角色）"
    for c in project.get("characters") or []:
        if isinstance(c, dict) and c.get("id") == cid:
            app = c.get("appearance") or {}
            look = app.get("promptEn") or "、".join(
                str(app.get(k)) for k in ("age", "gender", "face", "hair", "body") if app.get(k))
            outfits = c.get("outfits") or []
            fit = outfits[0].get("promptEn") or outfits[0].get("desc") if outfits and isinstance(outfits[0], dict) else ""
            bits = [f"{c.get('name') or cid}（{cid}，{c.get('role') or '定位未定'}）"]
            if look:
                bits.append(f"外型：{_clip(look, 220)}")
            if fit:
                bits.append(f"服裝：{_clip(fit, 120)}")
            return "；".join(bits)
    return f"（找不到人物卡 {cid}）"


def _scene_brief(project: dict, sid: str | None) -> str:
    if not sid:
        return "（未指定場景）"
    for s in project.get("scenes") or []:
        if isinstance(s, dict) and s.get("id") == sid:
            bits = [f"{s.get('name') or sid}（{sid}）"]
            for key, label in (("timeOfDay", "時間"), ("lighting", "光線"), ("mood", "氛圍"), ("desc", "描述")):
                if s.get(key):
                    bits.append(f"{label}：{_clip(s.get(key), 180)}")
            if s.get("promptEn"):
                bits.append(f"英文場景 prompt：{_clip(s['promptEn'], 180)}")
            return "；".join(bits)
    return f"（找不到場景卡 {sid}）"


def build_target(project: dict, kind: str, target_id: str | None) -> dict:
    """M3b：把 script/shot 的引用「展開成名字」，讓模型不必猜 id。

    - episodeScene：帶所屬集 + 場景卡 + 出場人物簡介 + 該場次 beats
    - beat：帶所屬場次 + 該 beat + 同場其他 beats（上下文）
    - shot：帶人物卡外型、場景卡、spec、promptZh/promptEn、dialogue
    其餘 kind（story/character/scene）由 storage.find_target 處理。
    """
    if kind == "episodeScene":
        for ep in (project.get("script") or {}).get("episodes") or []:
            for es in ep.get("scenes") or []:
                if es.get("id") == target_id:
                    return {
                        "類型": "劇本場次",
                        "所屬集": f"{ep.get('id')} · {ep.get('title') or '（未命名）'}",
                        "場次": _clip(es.get("summary"), 300),
                        "場景": _scene_brief(project, es.get("sceneId")),
                        "出場人物": [_char_brief(project, cid) for cid in (es.get("characterIds") or [])],
                        "beats": [
                            {
                                "id": b.get("id"),
                                "type": b.get("type"),
                                "角色": _char_brief(project, b.get("characterId")).split("（")[0]
                                if b.get("characterId") else "（未指定）",
                                "line": b.get("line"),
                                "emotion": b.get("emotion"),
                                "notes": b.get("notes"),
                            }
                            for b in (es.get("beats") or []) if isinstance(b, dict)
                        ],
                        "原始資料": es,
                    }
        raise KeyError(f"找不到場次：{target_id}")

    if kind == "beat":
        for ep in (project.get("script") or {}).get("episodes") or []:
            for es in ep.get("scenes") or []:
                for i, b in enumerate(es.get("beats") or []):
                    if isinstance(b, dict) and b.get("id") == target_id:
                        others = [f"{_char_brief(project, o.get('characterId')).split('（')[0]}：{o.get('line')}"
                                  for j, o in enumerate(es.get("beats") or [])
                                  if isinstance(o, dict) and j != i]
                        return {
                            "類型": "台詞／動作 beat",
                            "所屬場次": f"{es.get('id')} · {es.get('title') or '（未命名場次）'}"
                                        f"（{ep.get('id')} · {ep.get('title') or '（未命名集）'}）",
                            "場景": _scene_brief(project, es.get("sceneId")),
                            "這一個 beat": {
                                "id": b.get("id"),
                                "type": b.get("type"),
                                "角色": _char_brief(project, b.get("characterId")),
                                "line": b.get("line"),
                                "emotion": b.get("emotion"),
                                "notes": b.get("notes"),
                            },
                            "同場其他節拍": others,
                        }
        raise KeyError(f"找不到 beat：{target_id}")

    if kind == "shot":
        for sh in project.get("shots") or []:
            if isinstance(sh, dict) and sh.get("id") == target_id:
                spec = sh.get("spec") or {}
                return {
                    "類型": "鏡頭",
                    "鏡號": sh.get("no"),
                    "所屬場次": sh.get("episodeSceneId"),
                    "場景": _scene_brief(project, sh.get("sceneId")),
                    "人物": [_char_brief(project, cid) for cid in (sh.get("characterIds") or [])],
                    "畫面設定 spec": {k: spec.get(k) or "" for k in
                                      ("shotType", "angle", "cameraMove", "composition", "lighting")},
                    "長度秒數": sh.get("durationSec"),
                    "promptZh（中文提示）": sh.get("promptZh") or "",
                    "promptEn（現有英文提示，六段式）": sh.get("promptEn") or "",
                    "對白": [
                        f"{_char_brief(project, d.get('characterId')).split('（')[0]}：{d.get('line')}"
                        for d in (sh.get("dialogue") or []) if isinstance(d, dict)
                    ],
                    "畫面比例": (project.get("meta") or {}).get("aspectRatio"),
                    "風格": _style_bits(project),
                }
        raise KeyError(f"找不到鏡頭：{target_id}")

    return None


def build_system(project: dict, kind: str | None, target: dict | None, task: str | None) -> str:
    """組完整 system prompt（專案 meta → 目標卡片 JSON → 任務規則 → 通則）。"""
    parts = ["【專案資料】", _project_context(project or {})]
    if target is not None and kind:
        parts += [
            f"\n【本次對象：{_KIND_LABEL.get(kind, kind)}】",
            "目前這張卡的完整資料（JSON；請以此為基礎修改，未提到的欄位請保持原意）：",
            json.dumps(target, ensure_ascii=False, indent=2),
        ]
    rule = _TASK_RULES.get(task or "")
    if rule:
        parts += ["\n【本次任務】", rule]
    parts += ["\n【通則】", _COMMON_RULES]
    return "\n".join(parts)


def task_rules() -> dict:
    """給前端/文件參考：任務代號 → 規則首行。"""
    return {k: v.strip().splitlines()[0] for k, v in _TASK_RULES.items()}
