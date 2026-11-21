"""M12b：每個生成引擎的官方提示詞規則 —— 單一真相（規則只在這裡寫一份）。

為什麼要這一層：使用者的要求是「每個生成圖／影的提示詞都有對應的官方提示詞規則，程式內要
把規則寫死才送出」。先前三個送件口各寫各的（h3compose 會編譯、h3gen 卻只要 promptEn 有字就
原樣送出、imagegen 的 MOSS 有清洗但沒驗），所以「不合格的提示詞」會被默默送進模型而不報錯
（H3 實際踩過：6 個鏡頭的 promptEn 都是 839–993 字的未編譯草稿、`<Picture` 出現 0 次 →
模型沒有 conditioning，亂生且不報錯）。

本模組不重寫任何組稿邏輯，只做三件事：
  1. RULES：engine → 官方規則卡（id／label／source 一手來源／rulesZh 給 UI／build／check）；
  2. prepare(engine, project, obj, **kw) → (prompt, warnings, ruleId)：呼叫該引擎既有的組稿
     函式（h3compose.compile_prompt／imagegen.build_moss_caption／imagegen.build_prompt），
     再跑 check，把不合格條目寫成中文 warnings；
  3. check(engine, prompt, ctx) → (ok, problems)：純驗證，不做任何修改（口語：只看不動）。

一手來源（原始檔在專案內，改規則前先回去對）：templates/h3_director_r2v.ui.json、
templates/moss_image_sft.ui.json、templates/qwen21_i2i_edit.api.json／qwen21_t2i.api.json，
以及 docs/contracts.md §3（H3 導演台 timeline／`<Picture N>`／影格對齊）。

對外函式：
  describe()                       -> [規則卡]（不含函式本體；給 GET /api/prompt-rules）
  prepare(engine, project, obj, **kw) -> (prompt, warnings, ruleId)
  check(engine, prompt, ctx)       -> (ok, problems)
  rule_of(engine)                  -> 單張規則卡 / None
  ctx_for(engine, project, shot, **kw) -> dict（給 check 的 ctx：槽位、身份錨點、尺寸…）
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import h3compose

# ── 小工具 ─────────────────────────────────────────────────────
def _s(v: Any) -> str:
    return str(v or "").strip()


_PIC_RE = re.compile(r"<Picture\s+(\d+)>")
# H3 六段式：1. Subject … 6.（SVS 模板與 MSB 官方寫法都用「數字. 標題」開頭）
_H3_SEG_RE = re.compile(
    r"(?im)^\s*([1-6])[.、]\s*"
    r"(subject\s+definitions?|subject|summary|retention\s+analysis(?:\s*/\s*camera)?|"
    r"camera|detailed\s+description|overall\s+soundscape|soundscape|"
    r"non-diegetic\s+audio|audio|主體定義|摘要|鏡頭語言|詳細描述|氛圍與現場聲|音訊)"
)
_H3_SEG_NAMES = ("1 主體定義", "2 動作摘要", "3 鏡頭語言", "4 詳細描述", "5 氛圍與現場聲", "6 音訊")
# MOSS：caption 風格，禁「1. Subject:」這種影片模型格式與 quality tag
_MOSS_NUMBERED_RE = re.compile(r"(?im)^\s*([1-9])[.、]\s*[A-Z]")
_MOSS_QUALITY = ("masterpiece", "best quality", "high quality", "top quality", "8k", "4k", "uhd",
                 "ultra detailed", "ultra-detailed", "highly detailed", "high detail",
                 "award winning", "award-winning", "trending on artstation", "hdr", "best detail")
_MOSS_SIZE_RE = re.compile(r"\b\d{3,4}\s*[x×]\s*\d{3,4}\b|\b(?:9:16|16:9|1:1|3:4|4:3|21:9)\b")


# ── ctx：check 需要的上下文（各引擎不同）─────────────────────────
def _shot_ref_slots(shot: dict) -> list[dict]:
    """referencePlan.slots（只留 ordinal 是整數的）。"""
    return [s for s in ((shot.get("referencePlan") or {}).get("slots") or [])
            if isinstance(s, dict) and isinstance(s.get("ordinal"), int)]


def _anchor_values(ch: dict) -> list[str]:
    """角色卡上所有 identityAnchors 值（含 colorAnchors 的十六進位），逐字比對用的接法清單。

    build_moss_caption 會把值裡的「，」換成「, 」、「。」換成「. 」再接句
    （例：「短黑髮微亂，碎瀏海」→「短黑髮微亂, 碎瀏海」），所以每個值都要接受原字串與這幾種接法，
    否則正常產出的 caption 會被誤判缺錨點。
    """
    a = ch.get("identityAnchors") or {}
    vals = [_s(a.get(k)) for k in ("faceShape", "facialFeatures", "uniqueMarks", "hairStyle", "skinTexture")]
    ca = a.get("colorAnchors") or {}
    vals += [_s(ca.get(k)) for k in ("hair", "eyes", "skin", "primaryOutfit")]
    out: list[str] = []
    for v in vals:
        if not v:
            continue
        for form in (v, v.replace("，", ", ").replace("。", ". ").replace("、", ", "), " ".join(v.split())):
            if form not in out:
                out.append(form)
    return out


def ctx_for(engine: str, project: dict, shot: dict, **kw: Any) -> dict:
    """組 check 用的 ctx。純讀，不改 project／shot。

    各引擎需要的欄位：
      h3_video   : slots（本次送得出的 <Picture 序號>，見 h3gen.collect_refs 的 ordinal）、refsCount
      moss_t2i   : anchors（本次入鏡角色的 identityAnchors 值，逐字必須出現）、maxChars
      qwen_image : slotOrdinals（實際送出的參考圖所對應的 slot ordinal）、refsCount、mode、size
    """
    ctx: dict[str, Any] = {"shotId": _s(shot.get("id"))}
    if engine == "h3_video":
        slots = kw.get("slots")
        if slots is None:
            slots = [s["ordinal"] for s in _shot_ref_slots(shot)]
        ctx["slots"] = sorted({int(x) for x in slots if isinstance(x, (int, float)) or _s(x).isdigit()})
        ctx["refsCount"] = int(kw.get("refsCount") or len(ctx["slots"]))
    elif engine == "moss_t2i":
        # 每個入鏡角色一組：variants 是「同一個錨點值的幾種接法」（原字串／逗號版／句號版），
        # 只要任何一種在 caption 裡就代表逐字有貼上；報錯時整組回報一次，不重複洗版。
        groups: list[dict] = []
        for cid in [c for c in (shot.get("characterIds") or []) if _s(c)]:
            ch = next((c for c in (project.get("characters") or []) if isinstance(c, dict) and _s(c.get("id")) == cid), None)
            if ch is None:
                continue
            groups.append({"id": cid, "name": _s(ch.get("name")) or cid, "variants": _anchor_values(ch)})
        ctx["anchorGroups"] = groups
        ctx["anchors"] = [v for g in groups for v in g["variants"]]
        ctx["maxChars"] = int(kw.get("maxChars") or 2000)
    elif engine == "qwen_image":
        ords = kw.get("slotOrdinals")
        if ords is None:
            ords = []
            for r in (kw.get("refs") or []):
                if isinstance(r.get("ordinal"), int):
                    ords.append(r["ordinal"])
        ctx["slotOrdinals"] = sorted({int(x) for x in ords if isinstance(x, int) or _s(x).isdigit()})
        ctx["refsCount"] = int(kw.get("refsCount") or len(ctx["slotOrdinals"]))
        ctx["mode"] = _s(kw.get("mode")) or "ref"
    return ctx


# ── check：三個引擎各一支（純驗證）──────────────────────────────
def check_h3_video(prompt: str, ctx: dict) -> tuple[bool, list[str], list[str]]:
    """H3 影片（導演台 r2v）：六段式齊全＋`<Picture N>` 都要在本次送得出的槽位內＋非空。

    回 (ok, problems, notes)。H3 的 check 只用來決定「要不要改用模板編譯」，
    所以這裡全部算硬性問題，notes 恆為空。
    """
    text = _s(prompt)
    problems: list[str] = []
    if not text:
        return False, ["提示詞是空的（電影模型收不到任何指令）"], []
    found = {int(m.group(1)) for m in _H3_SEG_RE.finditer(text)}
    missing = [name for i, name in enumerate(_H3_SEG_NAMES, start=1) if i not in found]
    if missing:
        problems.append("缺六段式段落：" + "、".join(missing)
                        + "（格式：1. Subject definitions／2. Summary／3. Retention analysis / camera／"
                          "4. Detailed description／5. Overall soundscape／6. Non-diegetic audio）")
    used = sorted({int(n) for n in _PIC_RE.findall(text)})
    slots = [int(x) for x in (ctx.get("slots") or [])]
    if not used:
        problems.append("沒有任何 <Picture N> 引用（模型不知道哪張圖是誰，等於只拿文字瞎猜）")
    else:
        bad = [n for n in used if n not in slots]
        if bad:
            problems.append("引用了本次送不出的 <Picture " + ">、<Picture ".join(str(n) for n in bad)
                            + f">（這次只有 {slots or '沒有槽位'} 號槽位）")
        if slots:
            unused = [n for n in slots if n not in used]
            if unused:
                problems.append("這些送得出的槽位提示詞沒引用：<Picture "
                                + ">、<Picture ".join(str(n) for n in unused) + ">（模型可能不認那張圖的身份）")
    return (not problems), problems, []


def check_moss_t2i(prompt: str, ctx: dict) -> tuple[bool, list[str], list[str]]:
    """MOSS Image（t2i，純文字 caption）：一段連續英文、不編號、禁 quality tag、
    不含 <Picture、identityAnchors 逐字、尺寸不進 caption。

    回 (ok, problems, notes)：
      problems＝**硬性格式違規**（編號分段、quality tag、`<Picture`、尺寸、空白）→ 送件口擋下。
      notes＝**會影響品質但生得出來**的缺件（錨點沒逐字貼、caption 偏長）→ 只警告不擋。
      為什麼要分開：實測 caption 幾乎都會超過 260 字（identityAnchors 逐字貼就將近 1000 字，
      skill 也寫「保身份優先、不要為字數砍錨點」），close_up 特寫鏡頭組稿時刻意不寫入鏡者的
      錨點——這兩種都不該讓使用者完全生不出圖。
    """
    text = _s(prompt)
    problems: list[str] = []
    notes: list[str] = []
    if not text:
        return False, ["caption 是空的（MOSS 只吃文字，空字串等於亂生）"], []
    numbered = _MOSS_NUMBERED_RE.search(text)
    if numbered:
        problems.append(f"出現編號分段「{numbered.group(0).strip()[:30]}」：那是影片模型（H3）的六段式格式，"
                        "MOSS 要的是一段連續英文 caption，不能編號")
    low = text.lower()
    hits = [q for q in _MOSS_QUALITY if re.search(rf"(?<![a-z0-9]){re.escape(q)}(?![a-z0-9])", low)]
    if hits:
        problems.append("含 quality tag：" + "、".join(hits) + "（MOSS 官方 caption 風格禁用畫質咒語）")
    if "<picture" in low:
        problems.append("含 <Picture N>：MOSS 是純文字模型、不吃參考圖槽位（身份要靠 caption 描述）")
    size_hit = _MOSS_SIZE_RE.search(text)
    if size_hit:
        problems.append(f"caption 裡有尺寸／比例「{size_hit.group(0)}」：尺寸由程式指定，寫進 caption 只會讓模型亂畫畫布")
    for g in (ctx.get("anchorGroups") or []):
        variants = [v for v in (g.get("variants") or []) if v]
        if not variants:
            continue
        if not any(v in text for v in variants):
            head = variants[0]
            notes.append(f"{g.get('id')}（{g.get('name')}）的 identityAnchors 沒有逐字出現"
                         f"（例：「{head}」）：MOSS 靠這句鎖長相，改寫或漏貼就會換人")
    mx = int(ctx.get("maxChars") or 2000)
    if len(text) > mx:
        notes.append(f"caption 長度 {len(text)} 字元超過上限 {mx}（MOSS 越長越分心）")
    elif len(text) > 260:
        notes.append(f"caption 長度 {len(text)} 字元超過建議的 260（通常來自 identityAnchors 逐字貼，"
                     "保身份優先；想更準可精簡場景與動作描述）")
    return (not problems), problems, notes


def check_qwen_image(prompt: str, ctx: dict) -> tuple[bool, list[str], list[str]]:
    """Qwen Image 2.1（i2i／t2i）：`<imageK>` 都要有實際送出的 images.image_K；
    i2i 沒送任何參考圖只警告（preflight 已另擋）；t2i 不該再引用參考圖槽位。"""
    text = _s(prompt)
    problems: list[str] = []
    notes: list[str] = []
    if not text:
        return False, ["提示詞是空的"], []
    refs_count = int(ctx.get("refsCount") or 0)
    mode = _s(ctx.get("mode")) or "ref"
    used = sorted({int(n) for n in re.findall(r"<image\s*(\d+)>", text, flags=re.I)})
    if mode == "ref" and refs_count <= 0:
        notes.append("i2i 模式卻沒有送出任何參考圖（沒有 images.image_1 可對應）")
    if re.match(r"\s*1\.\s*Subject\s*:", text, flags=re.I):
        notes.append("提示詞是 H3 的六段式寫法（給影片模型用的）：Qwen 讀得懂，"
                     "但官方建議用「場景＋主體＋動作」的連續描述；有 <imageK> 對應就不影響鎖臉")
    bad = [n for n in used if not (1 <= n <= refs_count)]
    if bad:
        problems.append("引用了不存在的 <image" + ">、<image".join(str(n) for n in bad)
                        + f">（這次只送 {refs_count} 張圖）")
    if re.search(r"<picture\s*\d*>", text, flags=re.I):
        problems.append("還留著 <Picture N>：Qwen 模板的槽位寫法是 <image1>…<image4>，送件前要換掉")
    return (not problems), problems, notes


# ── build：包裝既有組稿函式（不在這裡重寫任何組稿邏輯）───────────
def _build_h3(project: dict, shot: dict, **kw: Any) -> tuple[str, list[str], dict]:
    prompt, warnings, meta = h3compose.compile_prompt(project, shot)
    return prompt, warnings, meta


def _build_moss(project: dict, shot: dict, **kw: Any) -> tuple[str, list[str], dict]:
    from . import imagegen  # 延遲匯入：imagegen 會 import 本模組（避免循環）
    caption, warnings = imagegen.build_moss_caption(project, shot)
    return caption, warnings, {"engine": "moss"}


def _build_qwen(project: dict, shot: dict, **kw: Any) -> tuple[str, list[str], dict]:
    from . import imagegen
    refs = kw.get("refs")
    if refs is None:
        refs, cw = imagegen.collect_refs(project, shot)
    else:
        cw = []
    prompt, warnings = imagegen.build_prompt(project, shot, refs)
    return prompt, list(cw) + list(warnings), {"refsCount": len(refs)}


# ── 規則卡：唯一真相 ────────────────────────────────────────────
RULES: dict[str, dict[str, Any]] = {
    "h3_video": {
        "id": "h3_video",
        "label": "MiniMax H3 影片（導演台 r2v，六段式）",
        "engine": "h3_video",
        "source": "templates/h3_director_r2v.ui.json（MiniMaxH3Director）＋ docs/contracts.md §3",
        "rulesZh": [
            "六段式、每段以「數字. 標題」開頭：1. Subject definitions／2. Summary（動作）／"
            "3. Retention analysis / camera／4. Detailed description（光影、構圖）／"
            "5. Overall soundscape／6. Non-diegetic audio（台詞、音樂）。缺一段就擋。",
            "參考圖一律用 <Picture N> 引用，N = referencePlan.slots 的 ordinal（從 1 起連續）。",
            "<Picture N> 只能引用「這次真的送得出」的槽位（占位圖會被跳過、首幀當 <Picture 1> 時其餘槽位不存在）。",
            "每個有圖的槽位都要被點名，否則模型不認那張圖的身份；不得自創超範圍的 <Picture N>。",
            "含人物的鏡頭不得設 wide（身份住在臉的像素量上）；影格須為 17k+5（程式自動吸附）。",
            "不合格時送件口不會擋，而是自動改用 app/h3compose.py 的模板編譯結果（不寫回 project.json）。",
        ],
        "build": _build_h3,
        "check": check_h3_video,
    },
    "moss_t2i": {
        "id": "moss_t2i",
        "label": "MOSS Image SFT（t2i，連續英文 caption）",
        "engine": "moss_t2i",
        "source": "templates/moss_image_sft.ui.json（CLIPTextEncode caption）",
        "rulesZh": [
            "一段連續英文 caption（逗號／句號串起來），不可分段、不編號（禁「1. Subject:」這種影片模型格式）。",
            "禁 quality tag：masterpiece／best quality／8k／4k／ultra detailed／highly detailed／award winning 等。",
            "不含 <Picture N>：MOSS 是純文字模型，沒有 i2i、不吃參考圖槽位。",
            "人物身份靠 identityAnchors 逐字貼上（臉形、五官、辨識特徵、髮型、色碼）——漏貼或改寫就換人。",
            "尺寸／比例不寫進 caption（由程式依 meta.aspectRatio 指定畫布）。",
            "越短越準：目標 120–260 字元（identityAnchors 較長時可超，但不得超過 2000）。",
        ],
        "build": _build_moss,
        "check": check_moss_t2i,
    },
    "qwen_image": {
        "id": "qwen_image",
        "label": "Qwen Image 2.1（i2i 編輯／t2i，官方槽位）",
        "engine": "qwen_image",
        "source": "templates/qwen21_i2i_edit.api.json／qwen21_t2i.api.json（images.image_N 槽位）",
        "rulesZh": [
            "參考圖槽位寫法 <image1>…<image4>：K 是「這次第幾張送進去的圖」，不是 referencePlan 的 ordinal。",
            "每個 <imageK> 都要有實際送出的圖（i2i 上限 4 張；被跳過的槽位不得引用）。",
            "i2i 模式必須至少送 1 張參考圖；t2i 模式不得引用參考圖槽位。",
            "不得留下 <Picture N>（那是 H3 導演台的寫法，送件前由程式換成 <imageK>）。",
            "尺寸貼齊源圖／比例（resolution 指短邊，程式依 aspectRatio 換算畫布）。",
        ],
        "build": _build_qwen,
        "check": check_qwen_image,
    },
}

ALIASES = {"h3": "h3_video", "video": "h3_video", "h3_r2v": "h3_video",
           "moss": "moss_t2i", "qwen": "qwen_image", "qwen21": "qwen_image"}


def _norm(engine: str) -> str:
    e = _s(engine)
    return ALIASES.get(e, e)


def rule_of(engine: str) -> dict | None:
    return RULES.get(_norm(engine))


def describe() -> list[dict]:
    """給前端用的規則卡（不含函式本體）。"""
    return [{"id": r["id"], "engine": r["engine"], "label": r["label"],
             "source": r["source"], "rulesZh": list(r["rulesZh"])}
            for r in RULES.values()]


# ── 對外：prepare / check ───────────────────────────────────────
def check(engine: str, prompt: str, ctx: dict | None = None) -> tuple[bool, list[str], list[str]]:
    """驗證提示詞是否符合該引擎的官方規則。回 (ok, problems, notes)（皆中文）。

    problems＝硬性違規（送件口要擋／要改用模板編譯）；notes＝會影響品質的警告（照送，只提示）。
    """
    rule = rule_of(engine)
    if rule is None:
        return False, [f"不認得的引擎 {engine}（合法值：{'／'.join(RULES)}）"], []
    return rule["check"](prompt, ctx or {})


def prepare(engine: str, project: dict, obj: dict, **kw: Any) -> tuple[str, list[str], str]:
    """用該引擎的組稿函式組出提示詞並驗證。回 (prompt, warnings, ruleId)。

    warnings 一律中文：組稿本身的警告在前，接著「規則未過」的逐條說明。
    不寫回 project.json、不呼叫任何模型。
    """
    rule = rule_of(engine)
    if rule is None:
        raise ValueError(f"不認得的引擎 {engine}（合法值：{'／'.join(RULES)}）")
    prompt, warnings, _meta = rule["build"](project, obj, **kw)
    prompt = _s(prompt)
    warnings = list(warnings or [])
    ctx = kw.get("ctx") or ctx_for(rule["id"], project, obj, **kw)
    ok, problems, notes = rule["check"](prompt, ctx)
    warnings.extend(notes)
    if not ok:
        warnings.append(f"提示詞未符合「{rule['label']}」官方規則，缺：" + "；".join(problems))
    return prompt, warnings, rule["id"]
