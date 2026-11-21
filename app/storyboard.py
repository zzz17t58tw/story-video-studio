"""M14a：規則式分鏡層（劇本 beats → shots）。零模型呼叫、零 ComfyUI。

規格：`docs/storyboard-contract.md` v1（權威）。這裡只做契約允許的三件事——
**挑選、分組、決定鏡位與長度**；台詞一字不改、不新增動作（鐵則 S1／L4）。

三層關係：
    script.episodes[].scenes[].beats[]   ← 唯一真相
    shots[]                              ← 本模組產出（beatIds 引用 beats）
    durationSec / promptZh / referencePlan ← 由分鏡層派生

核心函式：
    build_scene_shots(project, es_id)       一場合法的鏡頭清單（未寫檔）
    build_project_storyboard(project, ...)  重建指定場次＋全專案 no 重排
    storyboard_rows(project)                前端分鏡表資料（唯讀）
    migrate_shot_fields(project)            舊欄位遷移（beats → legacyBeats）

長度規則（契約 §3）：
    L1 估時 = storygen.estimate_durations()（對白 字數/4.5+2.0、動作 max(3,字數/6+2)）
    L2 吸附 = h3compose.frames_for() 合法格 17k+5，合法域 124–362 幀（24fps → 5.17–15.08s）
        < 124 幀 → padSec 補足；> 362 幀 → 拆鏡（beatPart）
    L3 合併 = 單句不足 5.17 秒時與相鄰 beat 併成一鏡（標「合併 N 句」）
    L4 不得為湊長度灌水劇情
"""

from __future__ import annotations

import re
from typing import Any

from . import h3compose, storage, storygen

FPS_DEFAULT = 24
FRAME_MIN, FRAME_MAX = 124, 362
# 短 beat 不足最短長度時，最多願意用「留白」補到幾秒；超過就與相鄰 beat 合併。
# 0.5 秒的停留是戲（呼吸、眼神、沉默），2 秒的停留就是空等 → 寧可合併。
PAD_MAX_SEC = 1.5

ROLES = ("establish", "dialogue", "reaction", "action", "insert", "closing")
ROLE_LABEL = {
    "establish": "建立鏡", "dialogue": "對白鏡", "reaction": "反應鏡",
    "action": "動作鏡", "insert": "插入鏡", "closing": "收尾鏡",
}
# role → 鏡位預設（契約 §4 V5 的取值約束一起考慮：reaction 不含台詞、establish 不 wide 帶人）
ROLE_SPEC = {
    "establish": {"shotType": "wide", "angleH": "front", "angleV": "high", "cameraMove": "static"},
    "dialogue": {"shotType": "over_shoulder", "angleH": "front_right", "angleV": "eye_level", "cameraMove": "push_in"},
    "reaction": {"shotType": "close_up", "angleH": "front", "angleV": "eye_level", "cameraMove": "static"},
    "action": {"shotType": "medium", "angleH": "front_left", "angleV": "eye_level", "cameraMove": "tracking"},
    "insert": {"shotType": "close_up", "angleH": "front", "angleV": "eye_level", "cameraMove": "static"},
    "closing": {"shotType": "wide", "angleH": "front", "angleV": "high", "cameraMove": "pull_out"},
}
# 有人的鏡頭不得 wide（H3 ref2v 的身份住在臉的像素量上）→ 退回 medium
_SAFE_HUMAN_TYPE = "medium"

_MERGE_TOLERANCE = 0.3   # 合併後超過上限的容許誤差（秒）；超過就換另一邊合併


# ── 小工具 ─────────────────────────────────────────────────────
def _s(v: Any) -> str:
    return str(v or "").strip()


def _chars(text: Any) -> int:
    """字數（不含空白）——與 storygen._nonspace_len / estimate_durations 同一算法。"""
    return storygen._nonspace_len(text)


def _is_dialogue(b: dict) -> bool:
    return _s(b.get("type") or "dialogue") != "action"


def scene_beats(es: dict) -> list[dict]:
    """場次內參與演出的 beats（排除 _skipped），保持劇本順序。"""
    return [b for b in (es.get("beats") or []) if isinstance(b, dict) and not b.get("_skipped")]


def _find_es(project: dict, es_id: str) -> dict | None:
    for ep in ((project.get("script") or {}).get("episodes") or []):
        for es in (ep.get("scenes") or []):
            if isinstance(es, dict) and es.get("id") == es_id:
                return es
    return None


def build_reference_plan_cb(project: dict, es: dict, shots: list[dict]) -> None:
    """就地補 referencePlan（借用既有 h3compose.build_reference_plan，缺圖只回 warning）。"""
    for sh in shots:
        plan, _w = h3compose.build_reference_plan(project, sh)
        sh["referencePlan"] = plan


# ── 長度：估時 → 吸附 → padSec／拆鏡 ─────────────────────────────
def beat_seconds(beats: list[dict]) -> float:
    """一組 beats 的估時（秒）。直接重用 storygen 的算式，不另立一套。"""
    return _estimate_group(beats)


def legal_frames(sec: float, fps: int = FPS_DEFAULT) -> dict:
    """估時 → 合法格；k 夾在 124–362 對應的範圍（7..21）。"""
    fr = h3compose.frames_for(sec, fps)
    k_min = -(-(FRAME_MIN - 5) // 17)
    k_max = (FRAME_MAX - 5) // 17
    k = min(max(int(fr["k"]), k_min), k_max)
    legal = 17 * k + 5
    return {"sec": round(legal / fps, 2), "legal": legal, "k": k}


def _estimate_group(beats: list[dict]) -> float:
    """一組 beats 的**必要秒數**（不夾上限）。

    storygen.estimate_durations() 的 perBeat.sec 夾在 2–15 秒（那是給「一段」用的護欄），
    但分鏡這裡要的是「唸完這些字最少要幾秒」，上限由 H3 合法格（15.08s）決定，
    所以拿到夾過的 sec 後再把被夾掉的部分加回來——算式仍是同一套（字數/4.5+2、max(3,字數/6+2)）。
    """
    per = storygen.estimate_durations(beats)["perBeat"]
    total = 0.0
    for p in per:
        sec = float(p["sec"])
        chars = int(p["chars"] or 0)
        if sec >= 14.99:   # 被 _SEC_LIMITS["max"] 夾住 → 用原式重算
            sec = chars / 4.5 + 2.0 if (p.get("type") or "dialogue") != "action" else max(3.0, chars / 6.0 + 2.0)
        total += sec
    return total


def _fit_group(beats: list[dict], fps: int) -> tuple[float, float]:
    """回 (durationSec, padSec)：吸附合法格，不足最短就用 padSec 補。"""
    need = _estimate_group(beats)
    lf = legal_frames(need, fps)
    dur = lf["sec"]
    pad = round(max(0.0, dur - need), 2)
    return dur, pad


# ── 分組（一鏡一事件；長 beat 拆鏡）─────────────────────────────
def _split_long_beat(b: dict, fps: int) -> list[dict] | None:
    """單一 beat 估時 > 15.08 秒 → 依句讀／逗號切段，包成 beatPart。

    切段只**取用原有文字**（不新增、不改寫），每段各自吸附合法格。
    切不出來（一句話沒有可切的停頓）→ 回 None，由呼叫端報 warning。
    """
    text = _s(b.get("line"))
    pieces = [p for p in re.split(r"(?<=[，,。、；;！!？?\s])", text) if _s(p)]
    if len(pieces) < 2:
        return None
    # 貪心裝箱：每段不超過上限
    boxes: list[str] = []
    cur = ""
    for p in pieces:
        cand = cur + p
        if cur and _estimate_group([{**b, "line": cand}]) > FRAME_MAX / fps:
            boxes.append(cur)
            cur = p
        else:
            cur = cand
    if _s(cur):
        boxes.append(cur)
    # 段數太多（每段又短到得塞大量留白）→ 合併相鄰段，讓段數最少
    while len(boxes) > 1:
        merged: list[str] = []
        i = 0
        while i < len(boxes):
            if i + 1 < len(boxes) and _estimate_group([{**b, "line": boxes[i] + boxes[i + 1]}]) <= FRAME_MAX / fps:
                merged.append(boxes[i] + boxes[i + 1])
                i += 2
            else:
                merged.append(boxes[i])
                i += 1
        if len(merged) == len(boxes):
            break
        boxes = merged
    if len(boxes) < 2:
        return None
    total = len(boxes)
    return [{**b, "line": boxes[i], "_part": {"beatId": str(b.get("id")), "part": i + 1, "total": total}}
            for i in range(total)]


def group_beats(beats: list[dict], fps: int = FPS_DEFAULT) -> tuple[list[dict], list[str], list[str]]:
    """把一場的 beats 分組成鏡頭。回 (groups, warnings, lines)。

    groups 每筆：{beats:[beat...], beatIds:[...], part:{...}|None, merged:bool, split:bool}
    - 一句台詞一鏡為預設；單句（或單動作）不足 5.17 秒 → 與**相鄰** beat 合併（L3）
    - 單一 beat 超過 15.08 秒 → 拆成 beatPart（L2）
    """
    warnings: list[str] = []
    min_sec = FRAME_MIN / fps
    max_sec = FRAME_MAX / fps

    # ① 先把過長的 beat 拆開（拆點只取原文，不改字）
    atoms: list[dict] = []
    for b in beats:
        est = _estimate_group([b])
        if est > max_sec:
            parts = _split_long_beat(b, fps)
            if parts:
                warnings.append(
                    f"beat {b.get('id')} 估 {est:.1f} 秒超過單鏡上限 {max_sec:.2f} 秒，已拆成 "
                    f"{len(parts)} 個 beatPart（切在原有標點，台詞未改字）")
                atoms.extend(parts)
            else:
                warnings.append(
                    f"beat {b.get('id')} 估 {est:.1f} 秒超過單鏡上限 {max_sec:.2f} 秒，且找不到可切的停頓；"
                    f"先壓成 {max_sec:.2f} 秒（建議回劇本層拆句）")
                atoms.append(b)
        else:
            atoms.append(b)

    # ② 先定 role：逐 beat 只靠「類型＋是否場次首末」判，不依賴分組結果
    #    （所以「短句反應」可以單獨一鏡＋padSec，不必為了湊長度把三句塞進一鏡）
    roles: list[str] = []
    for idx, b in enumerate(atoms):
        dlg = _is_dialogue(b)
        if idx == 0 and not dlg:
            roles.append("establish")      # 場次第一鏡是動作／空景 → 建立鏡
        elif dlg:
            roles.append("dialogue")
        elif idx == len(atoms) - 1:
            roles.append("closing")
        elif _s(b.get("characterId")):
            roles.append("action")
        else:
            roles.append("insert")

    # ③ 裝箱：預設一 beat 一鏡＝一鏡一事件（這是使用者要的「看得出在演什麼」）。
    #    不足 5.17s 時**先補 padSec**（停留／呼吸／眼神，不准加台詞或動作），
    #    只有「補超過 PAD_MAX_SEC 秒會變成空等」時才與相鄰 beat 合併（合併是物理妥協，不是預設）。
    groups: list[list[dict]] = []
    n = len(atoms)
    i = 0
    while i < n:
        cur = [atoms[i]]
        need = _estimate_group(cur)
        i += 1
        if need < min_sec and (min_sec - need) > PAD_MAX_SEC:
            nxt = _estimate_group([atoms[i]]) if i < n else None
            cand_next = (need + nxt) if nxt is not None else None
            cand_prev = (_estimate_group([atoms[i - 2]]) + need) if (groups and i >= 2) else None
            # 只有合併後「吸附到合法格仍 ≤ 15.08 秒」才算併得進去。
            # 兩邊都併得進去時，優先跟在上一鏡後面（順場次流），除非併前一個會爆長度。
            fits_next = cand_next is not None and legal_frames(cand_next, fps)["legal"] <= FRAME_MAX
            fits_prev = cand_prev is not None and legal_frames(cand_prev, fps)["legal"] <= FRAME_MAX
            pick_prev = fits_prev
            pick_next = (not fits_prev) and fits_next
            if pick_prev:
                cur = groups.pop() + cur
            elif pick_next:
                cur = cur + [atoms[i]]
                i += 1
            # 兩邊都併不進去 → 單獨一鏡，靠 padSec 補足（L4）
        groups.append(cur)

    out: list[dict] = []
    role_at = 0
    for g in groups:
        parts = [x.get("_part") for x in g if x.get("_part")]
        base_role = roles[min(role_at, len(roles) - 1)] if roles else "dialogue"
        role_at += len(g)
        if len(g) > 1:
            # 合併鏡：以台詞鏡為主；合併到場次最後一 beat 且沒台詞 → 收尾
            if any(_is_dialogue(x) for x in g):
                role = "dialogue"
            elif roles and role_at >= len(roles):
                role = "closing"
            else:
                role = "action"
        else:
            role = base_role
        out.append({
            "beats": g,
            "beatIds": [str(x.get("id")) for x in g if x.get("id")],
            "part": parts[0] if parts else None,
            "merged": len(g) > 1,
            "split": bool(parts),
            "role": role,
        })
    return out, warnings, [_s(b.get("line")) for b in beats]


# ── role 說明／鏡位 ────────────────────────────────────────────
def pick_role(project: dict, es: dict, group: dict, idx: int, total: int) -> str:
    """分組階段已判好 role（group_beats 回傳）；這裡保留給外部呼叫（測試／手動）。"""
    return _s(group.get("role")) or "dialogue"


def _role_spec(role: str, has_person: bool) -> dict:
    base = dict(ROLE_SPEC.get(role) or ROLE_SPEC["dialogue"])
    if has_person and base["shotType"] == "wide":
        base["shotType"] = _SAFE_HUMAN_TYPE
    return {
        "shotType": base["shotType"],
        "angle": {"h": base["angleH"], "v": base["angleV"], "s": base["shotType"]},
        "cameraMove": base["cameraMove"],
        "composition": "",
        "lighting": "",
        "framingRule": storage.V02_FRAMING_RULE,
    }


def _group_character_ids(project: dict, es: dict, group: dict, role: str) -> list[str]:
    """本鏡出場人物：優先取 beats 指名的角色；旁白／空景退回場次名單。

    reaction 必須有對手在場（V5）→ 至少要兩個人才算「有人在聽」。
    """
    es_chars = [c for c in (es.get("characterIds") or []) if _s(c)]
    beat_chars: list[str] = []
    for b in group["beats"]:
        cid = _s(b.get("characterId"))
        if cid and cid not in beat_chars:
            beat_chars.append(cid)
    if role == "reaction":
        others = [c for c in es_chars if c not in beat_chars]
        return (beat_chars or es_chars[:1]) + (others[:1] if others else [])
    if beat_chars:
        return beat_chars
    if role == "insert":
        return []                    # 插入鏡＝細節／物件，不帶人
    return list(es_chars)


def _who_names(project: dict, ids: list[str]) -> str:
    names = []
    for cid in ids:
        ch = next((c for c in (project.get("characters") or []) if c.get("id") == cid), None)
        names.append((ch or {}).get("name") or cid)
    return "、".join(names)


def _prompt_draft(project: dict, es: dict, group: dict, role: str, chars: list[str], dur: float) -> str:
    """中文草稿：這一鏡唯一事件（誰／對誰說哪句／做什麼）＋鏡位。台詞逐字照抄。"""
    scene = next((s for s in (project.get("scenes") or []) if s.get("id") == es.get("sceneId")), None)
    bits = [f"《{es.get('title') or es.get('id')}》【{ROLE_LABEL.get(role, role)}】"]
    for b in group["beats"]:
        who = _who_names(project, [b.get("characterId")]) if _s(b.get("characterId")) else ""
        if _is_dialogue(b):
            emo = f"（{_s(b.get('emotion'))}）" if _s(b.get("emotion")) else ""
            bits.append(f"{who or '畫外音'}{emo}說：「{_s(b.get('line'))}」")
        else:
            bits.append(f"動作：{_s(b.get('line'))}")
    spec = _role_spec(role, bool(chars))
    bits.append(f"鏡位 {spec['shotType']}／{spec['cameraMove']}，{dur:g} 秒。")
    if scene:
        bits.append(f"場景：{scene.get('name') or scene.get('id')}"
                    + (f"·{scene.get('timeOfDay')}" if _s(scene.get("timeOfDay")) else "") + "。")
    if chars:
        bits.append(f"人物：{_who_names(project, chars)}。")
    return "".join(bits)


# ── 對外：一場合法的鏡頭 ────────────────────────────────────────
def build_scene_shots(project: dict, es_id: str, opts: dict | None = None) -> dict:
    """依契約把一場的 beats 派生成 shots（未寫檔、未編 id／no）。

    回 {shots, warnings, groups, esId, beatCount}
    """
    opts = opts or {}
    fps = int((project.get("meta") or {}).get("fps") or FPS_DEFAULT)
    es = _find_es(project, es_id)
    if es is None:
        return {"shots": [], "warnings": [f"找不到場次 {es_id}"], "groups": [], "esId": es_id, "beatCount": 0}
    beats = scene_beats(es)
    if not beats:
        return {"shots": [], "warnings": [f"場次 {es.get('id')} 沒有 beats（_skipped 不算），先到劇本頁補內容"],
                "groups": [], "esId": es_id, "beatCount": 0}

    groups, warns, _lines = group_beats(beats, fps)
    out: list[dict] = []
    for idx, g in enumerate(groups):
        role = pick_role(project, es, g, idx, len(groups))
        chars = _group_character_ids(project, es, g, role)
        # 收尾／建立鏡在無人可拍時帶人不好看，但 V5 只約束 establish 不含台詞；
        # reaction 一定需要對手在場（_group_character_ids 已補）。
        if role == "closing" and any(_is_dialogue(b) for b in g["beats"]):
            role = "dialogue"          # 收在台詞上：closing 不得含台詞（V5 一致性）
        dur, pad = _fit_group(g["beats"], fps)
        spec = _role_spec(role, bool(chars))
        dialogue_ids = [str(b.get("id")) for b in g["beats"] if _is_dialogue(b) and b.get("id")]

        prompt_zh = _prompt_draft(project, es, g, role, chars, dur)
        if g["merged"]:
            prompt_zh += f"（合併 {len(g['beats'])} 句beat：H3 最短 {FRAME_MIN / fps:.2f} 秒）"
        if pad > 0:
            prompt_zh += f"（其中 {pad:g} 秒為留白：停留／呼吸／環境）"
        if g["part"]:
            prompt_zh += f"（beat {g['part']['beatId']} 第 {g['part']['part']}/{g['part']['total']} 段）"

        out.append({
            "episodeSceneId": es.get("id"),
            "sceneId": es.get("sceneId") or None,
            "characterIds": chars,
            "stageIds": [],
            "beatIds": list(g["beatIds"]),
            "beatPart": g["part"],
            "role": role,
            "derivedFrom": "storyboard",
            "padSec": pad,
            "durationSec": dur,
            "dialogBeatIds": dialogue_ids,
            "dialogLineRefs": [i + 1 for i, b in enumerate([x for x in beats if _is_dialogue(x)])
                               if str(b.get("id")) in dialogue_ids],
            "spec": spec,
            "promptZh": prompt_zh,
            "promptEn": "",
            "referencePlan": {"slots": [], "weakReferences": [], "promptPlaceholders": {}},
        })
    build_reference_plan_cb(project, es, out)

    # 逐鏡再確認一次長度合法（規格自檢；不合法只在 warning 裡說，不硬改）
    for n, sh in enumerate(out, start=1):
        fr = h3compose.frames_for(sh["durationSec"], fps)
        if not (FRAME_MIN <= fr["legal"] <= FRAME_MAX) or fr["deltaFrames"] != 0:
            warns.append(f"場次 {es.get('id')} 第 {n} 鏡 durationSec={sh['durationSec']} 不在合法格"
                         f"（吸附後 {fr['legal']} 幀），請檢查分組邏輯")
    n_merge = sum(1 for g in groups if g["merged"])
    if n_merge:
        warns.append(f"場次 {es.get('id')} 有 {n_merge} 鏡是合併相鄰 beat（單句不足 H3 最短 {FRAME_MIN / fps:.2f} 秒）")
    total = round(sum(sh["durationSec"] for sh in out), 2)
    return {"shots": out, "warnings": warns, "groups": groups,
            "esId": es.get("id"), "beatCount": len(beats), "totalSeconds": total}


def _next_shot_seq(shots: list[dict]) -> int:
    biggest = 0
    for sh in shots or []:
        m = re.fullmatch(r"sh(\d{2,})", str((sh or {}).get("id") or ""))
        if m:
            biggest = max(biggest, int(m.group(1)))
    return biggest + 1


def build_project_storyboard(project: dict, scene_ids: list[str] | None = None,
                             force: bool = False, keep_manual: bool = True) -> dict:
    """重建指定場次（或全專案）的分鏡，並把全專案 shot.no 依「場次順序＋鏡內順序」重排。

    - `derivedFrom="manual"` 的鏡頭在 keep_manual=True 時原樣保留（不覆蓋、不刪）
    - force=True ⇒ 連手動鏡頭一起覆蓋
    - 純就地修改傳入的 project；寫檔由呼叫端走 storage.save_project（原子寫）
    回 {created, replaced, kept, shots, warnings, skippedScenes}
    """
    episodes = ((project.get("script") or {}).get("episodes") or [])
    all_es_ids = [es.get("id") for ep in episodes for es in (ep.get("scenes") or []) if es.get("id")]
    want = [s for s in (scene_ids or all_es_ids) if s in all_es_ids]
    if not want:
        return {"created": 0, "replaced": 0, "kept": 0, "shots": [], "warnings": ["沒有可重建的場次（專案沒有劇本場次）"],
                "skippedScenes": []}

    shots: list[dict] = [s for s in (project.get("shots") or []) if isinstance(s, dict)]
    warnings: list[str] = []
    skipped_scenes: list[str] = []
    ts = storage.now_iso()
    n_replaced = 0
    n_kept = 0

    for es_id in want:
        built = build_scene_shots(project, es_id, {"force": force})
        warnings += built["warnings"]
        if not built["shots"]:
            skipped_scenes.append(es_id)
            continue

        # 場次內分兩堆：要保留的（手動）與要換掉的（舊的規則／佈署產物）
        same = [s for s in shots if s.get("episodeSceneId") == es_id]
        keep: list[dict] = []
        for s in same:
            manual = _s(s.get("derivedFrom")) == "manual"
            if manual and keep_manual and not force:
                keep.append(s)
                n_kept += 1
            else:
                n_replaced += 1
                # 舊鏡頭若在別的場次有同 id 是不可能的（id 全專案唯一）；這裡不需清 map
        # 保留的手動鏡頭若也有 beatIds，重建出的鏡頭要避開同一批 beats（否則 V1 重複）
        covered = {bid for s in keep for bid in (s.get("beatIds") or [])}
        fresh: list[dict] = []
        for new in built["shots"]:
            if covered and all(bid in covered for bid in new["beatIds"]) and new["beatIds"]:
                warnings.append(f"場次 {es_id}：beatIds {new['beatIds']} 已被手動鏡頭涵蓋，重建時略過")
                continue
            seq_fmt = f"sh{_next_shot_seq(shots + fresh):02d}"
            if seq_fmt in {x.get("id") for x in shots + fresh}:
                # 極端情況（id 被手改成 sh01b 之類）→ 依 next_id 規則再保險
                seq_fmt = storage.next_id(shots + fresh, "sh")
            new = dict(new)
            new.update({
                "id": seq_fmt, "no": 0,
                "images": {"first": "", "last": ""},
                "video": {"path": "", "status": "none", "taskType": "r2v", "seed": 0, "note": ""},
                "voice": {"path": "", "engine": ""},
                "durationFrames": h3compose.frames_for(new["durationSec"])["legal"],
                "updatedAt": ts,
            })
            fresh.append(new)
        # 同場次：依「第一個 beat 在劇本裡的位置」排（手動鏡頭也歸位），
        # 沒有 beatIds 的手動鏡頭排最前面（不知道演哪段，就照它原本的位置擺）
        es = _find_es(project, es_id) or {}
        order_in_scene = {str(b.get("id")): i for i, b in enumerate(scene_beats(es))}

        def _sort_key(s: dict) -> tuple:
            ids = [x for x in (s.get("beatIds") or []) if isinstance(x, str)]
            pos = min((order_in_scene.get(x, 10 ** 6) for x in ids), default=-1)
            return (pos if pos >= 0 else -1, 0 if _s(s.get("derivedFrom")) == "manual" else 1)

        block = sorted(keep + fresh, key=_sort_key)
        # 建立鏡（establish）必須是「場次裡真的第一鏡」（契約 V5）：保留的舊手動鏡頭
        # 站在前面時，把 establish 降級成該鏡內容對應的角色（有台詞→dialogue，否則 action）
        if block and _s(block[0].get("role")) != "establish":
            for s in block[1:]:
                if _s(s.get("role")) == "establish":
                    by_id = {str(bb.get("id")): bb for bb in scene_beats(es)}
                    has_dlg = any(_is_dialogue(by_id[x]) for x in (s.get("beatIds") or []) if x in by_id)
                    s["role"] = "dialogue" if has_dlg else "action"
                    s["spec"] = _role_spec(s["role"], bool(s.get("characterIds")))
                    warnings.append(f"場次 {es_id}：本場第一鏡是保留的手動鏡頭，"
                                    f"原建立的鏡頭（{s.get('id')}）已由 establish 改為 {s['role']}")
                    break
        idxs = [i for i, s in enumerate(shots) if s.get("episodeSceneId") == es_id]
        if idxs:
            at = idxs[0]
            shots = [s for s in shots if s.get("episodeSceneId") != es_id]
            shots[at:at] = block
        else:
            shots = shots + block

    # 全專案 no：依場次順序、再依該場次內的排列順序
    order = {eid: i for i, eid in enumerate(all_es_ids)}
    # 先把「別的場次／舊殘留」鏡頭也按場次分群，組內維持原相對順序（sort 是穩定的）
    shots.sort(key=lambda s: order.get(s.get("episodeSceneId"), len(order)))
    for i, s in enumerate(shots, start=1):
        s["no"] = i
    project["shots"] = shots

    new_shots = [s for s in shots if _s(s.get("derivedFrom")) == "storyboard" and s.get("updatedAt") == ts]
    return {"created": len(new_shots), "replaced": n_replaced, "kept": n_kept,
            "shots": new_shots, "warnings": warnings, "skippedScenes": skipped_scenes}


# ── 舊資料遷移 ─────────────────────────────────────────────────
def migrate_shot_fields(project: dict) -> list[str]:
    """舊欄位收斂（就地修改）。永不拋例外：

    - `shot.beats`（自由敘述陣列）→ 有 notes 就併進 notes，否則存 `shot.legacyBeats`；
      清空 `shot.beats`（停用；它不參與生成，留著只會讓讀取端誤用）
    - `shot.dialogue` → 保留（契約：舊資料不刪、讀取端不用）。這裡只在它是非空陣列時
      回一則提示，不動內容
    - 有 `beatIds` 的鏡頭不碰（已是新格式）
    """
    notes: list[str] = []
    legacy = 0
    for sh in (project.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        raw = sh.get("beats")
        if isinstance(raw, list) and raw:
            if sh.get("legacyBeats") is None:
                sh["legacyBeats"] = raw
                legacy += 1
            sh["beats"] = []
        if not isinstance(sh.get("beats"), list):
            sh["beats"] = []
        if sh.get("beatIds") is not None and not isinstance(sh.get("beatIds"), list):
            sh["beatIds"] = [x for x in sh.get("beatIds") if isinstance(x, str)]
        if not isinstance(sh.get("padSec"), (int, float)) or isinstance(sh.get("padSec"), bool):
            sh["padSec"] = float(sh["padSec"]) if isinstance(sh.get("padSec"), str) and sh["padSec"].strip() else 0.0
        if _s(sh.get("role")) and _s(sh.get("role")) not in ROLES:
            notes.append(f"鏡頭 {sh.get('id')} 的 role「{sh.get('role')}」不在六種之內（已保留原值）")
        if not _s(sh.get("derivedFrom")):
            # 有 beatIds 的視為新格式；否則標成 manual（不動它、也不被自動重建覆蓋）
            sh["derivedFrom"] = "manual"
    if legacy:
        notes.append(f"{legacy} 個舊鏡頭的 beats（自由敘述）已移到 legacyBeats，之後不再參與生成")
    return notes


# ── 前端分鏡表 ─────────────────────────────────────────────────
def storyboard_rows(project: dict) -> dict:
    """回給前端的唯讀分鏡表：{rows, totalSeconds, shotCount, scenes}。

    每列：no／shotId／role／roleLabel／誰／台詞逐字（動作鏡給動作）／durationSec／padSec／
    startSec（累計）／endSec／hasLine／beatIds／mergedCount／issue（V 規則的簡寫）
    """
    shots = sorted([s for s in (project.get("shots") or []) if isinstance(s, dict)],
                   key=lambda s: (int(s.get("no") or 0)))
    rows: list[dict] = []
    t = 0.0
    for sh in shots:
        es = _find_es(project, sh.get("episodeSceneId") or "") or {}
        beats_all = {str(b.get("id")): b for b in scene_beats(es)}
        ids = [x for x in (sh.get("beatIds") or []) if isinstance(x, str)]
        if not ids:
            ids = [x for x in (sh.get("dialogBeatIds") or []) if isinstance(x, str)]
        picked = [beats_all[b] for b in ids if b in beats_all]
        lines = [{"beatId": str(b.get("id")), "speakerId": b.get("characterId") or "",
                  "speaker": _who_names(project, [b.get("characterId")]) if _s(b.get("characterId")) else "（畫外／旁白）",
                  "text": _s(b.get("line")), "emotion": _s(b.get("emotion")), "type": _s(b.get("type") or "dialogue")}
                 for b in picked]
        has_line = any(x["type"] != "action" for x in lines)
        dur = float(sh.get("durationSec") or 0)
        pad = float(sh.get("padSec") or 0)
        role = _s(sh.get("role"))
        start = round(t, 2)
        t += dur
        rows.append({
            "no": int(sh.get("no") or 0), "shotId": sh.get("id"),
            "episodeSceneId": sh.get("episodeSceneId") or "",
            "sceneTitle": es.get("title") or "",
            "role": role, "roleLabel": ROLE_LABEL.get(role, role or "（未設定）"),
            "derivedFrom": _s(sh.get("derivedFrom")) or "（舊資料）",
            "speaker": "、".join(dict.fromkeys(x["speaker"] for x in lines if x["type"] != "action")) or "",
            "characterIds": list(sh.get("characterIds") or []),
            "chars": _who_names(project, [c for c in (sh.get("characterIds") or []) if _s(c)]),
            "lines": lines, "beatIds": ids,
            "hasLine": has_line,
            "text": "／".join(x["text"] for x in lines if x["type"] != "action") if has_line
                    else "／".join(x["text"] for x in lines) or _s(sh.get("promptZh")),
            "durationSec": dur, "padSec": pad, "frames": h3compose.frames_for(dur)["legal"],
            "framesInRange": h3compose.frames_for(dur)["inRange"],
            "startSec": start, "endSec": round(t, 2),
            "mergedCount": len(picked),
            "beatPart": sh.get("beatPart") or None,
        })
    return {"rows": rows, "shotCount": len(rows), "totalSeconds": round(t, 2),
            "beatCount": sum(len(es.get("beats") or []) for ep in ((project.get("script") or {}).get("episodes") or [])
                             for es in (ep.get("scenes") or []))}
