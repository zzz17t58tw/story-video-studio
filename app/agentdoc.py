"""Agent 對接層：讓「外部 AI 的代理人」一次掌握這支程式的每一個細節。

為什麼要有這支檔
----------------
使用者（或他接上的 AI）不該靠猜測來操作這個程式。這裡把三件事集中成單一真相：

1. **機器可讀的操作清單**：`manifest(app)` 直接從 FastAPI 的 OpenAPI 取得每個端點的
   參數與 schema（不會因為程式改了而文件漂移），再補上「這一支是做什麼、什麼時候用、
   有什麼副作用」的人工語意。
2. **給 LLM 讀的操作手冊**：`guide()` 產生一份 Markdown（結構化、可整份塞進 context），
   內容包含資料模型樹、硬限制、正確的操作順序、常見配方與禁止事項。
3. **完整度自檢**：`coverage(app)` 回「哪些路由沒有語意說明」，`tools/check_agent_doc.py`
   會在缺少時讓檢查失敗——所以新增端點卻忘了寫說明，會被擋下來。

對外入口（由 app/server.py 掛上）：
- `GET /api/agent/manifest`  → JSON（含 openapi schema）
- `GET /api/agent/guide`     → text/markdown
- `GET /api/agent/coverage`  → 文件完整度
"""

from __future__ import annotations

from typing import Any

AGENT_DOC_VERSION = "1.0"

# ── 硬限制（違反就會生不出東西或壞檔，外部 AI 最常踩）────────────────
HARD_LIMITS: list[dict[str, str]] = [
    {"id": "h3_frames", "rule": "H3 單鏡影格必須落在 17k+5 且 124–362 幀（fps 24）",
     "meaning": "單鏡片段合法長度 = 5.17–15.08 秒；想更短只能生成後剪，想更長必須拆鏡",
     "where": "shots[].durationSec；POST /storyboard/build 會自動吸附"},
    {"id": "h3_total_frames", "rule": "送件的 total_frames 需對齊 17k+1",
     "meaning": "程式內部 h3gen 會自己換算，外部只要給 durationSec", "where": "app/h3gen.py"},
    {"id": "aspect_ratio", "rule": "aspectRatio 只收 9:16 / 3:4 / 4:3 / 1:1 / 16:9 / 21:9 / 自定义",
     "meaning": "舊值「16:9(横屏)」會被正規化成預設值，等於沒指定", "where": "settings.video"},
    {"id": "shot_type", "rule": "spec.shotType ∈ close_up / medium / wide / over_shoulder / pov",
     "meaning": "含人物的鏡頭不得用 wide（取景規則），程式會報 error", "where": "shots[].spec.shotType"},
    {"id": "camera_move", "rule": "spec.cameraMove ∈ static / push_in / pull_out / pan_left / pan_right / tracking / handheld",
     "meaning": "不在清單內的值會讓驗證報 error", "where": "shots[].spec.cameraMove"},
    {"id": "prompt_rules", "rule": "H3 用六段式（1. Subject / 2. Action / 3. Camera / 4. Lighting / 5. Atmosphere / 6. Audio）；MOSS 禁 quality tag 與編號式開頭",
     "meaning": "送件口會硬驗；不合格會自動改用模板編譯稿或直接拒送", "where": "app/promptrules.py、GET /api/prompt-rules"},
    {"id": "beats_truth", "rule": "劇本 beats 是唯一真相；分鏡只能挑選/分組，不得改寫台詞",
     "meaning": "要改台詞回劇本層改，改分鏡的台詞會被驗證擋下", "where": "docs/spec/storyboard-contract.md"},
    {"id": "gpu_serial", "rule": "本機只有一張 GPU：生圖/生影片/本地 LLM 不能同時跑",
     "meaning": "要跑生成前先卸載本地 LLM；批次任務是「依序」不是並行", "where": "POST /api/gpu/release-comfy、/api/gpu/stop-llama"},
]

# ── 資料模型（給 agent 的骨架說明；完整欄位見 docs/spec/data-model.md）──────
DATA_MODEL: list[dict[str, Any]] = [
    {"path": "meta", "fields": "id, title, createdAt, updatedAt, fps(預設 24), version", "note": "所有寫入都要更新 updatedAt"},
    {"path": "story", "fields": "synopsis, premise, style{genre,tone,visualStyle,negative}, chapters[]",
     "note": "chapters[]: {id, title, content}"},
    {"path": "characters[]", "fields": "id, name, aliases[], appearance{age,gender,face,hair,body,skin,promptEn}, voice{desc}, outfits[], identityAnchors{}, stages[]",
     "note": "identityAnchors 是「逐字貼進 prompt」的鎖臉錨點，不准改寫"},
    {"path": "scenes[]", "fields": "id, name, timeOfDay, lighting, mood, promptEn, props[]", "note": "場景卡（地點），不含對白"},
    {"path": "script.episodes[].scenes[]", "fields": "id(e01s01), title, sceneId, characterIds[], summary, beats[]",
     "note": "beats[] = {id(b01), type(dialogue|action), characterId, line, emotion, notes}；這是戲的唯一真相"},
    {"path": "shots[]", "fields": "id, no, episodeSceneId, beatIds[], role, derivedFrom, padSec, durationSec, characterIds[], spec{}, promptZh, promptEn, referencePlan{}, images{first,last}, video{}, beats(停用), dialogue(停用), legacyBeats",
     "note": "beatIds 是主鍵；role ∈ establish/dialogue/reaction/action/insert/closing"},
    {"path": "settings", "fields": "llm{}, imageEngine, video{}, comfy{}, tts{}", "note": "apiKey 不寫進 project.json"},
    {"path": "pipeline", "fields": "stage, logs[]", "note": "流程紀錄"},
]

# ── 驗證規則（POST /validate 會回這些）────────────────────────────
VALIDATION_RULES: list[dict[str, str]] = [
    {"id": "V1", "rule": "場次內 shots（依 no）的 beatIds 串接 == 該場次 beats（排除 _skipped）",
     "level": "error", "fix": "POST /storyboard/build 重建，或修 shots[].beatIds"},
    {"id": "V2", "rule": "每個 dialogue beat 剛好被某一鏡引用一次", "level": "error",
     "fix": "同上；漏句時錯誤訊息會指出「第幾句」"},
    {"id": "V3", "rule": "durationSec 吸附後必須落在 124–362 幀；只是沒對齊則為 warn（送件時自動吸附）",
     "level": "error|warn", "fix": "調整 durationSec 到 5.17–15.08 秒之間"},
    {"id": "V4", "rule": "durationSec − padSec ≥ 台詞字數 / 4.5（台詞要唸得完）", "level": "error",
     "fix": "加長鏡頭或縮短台詞"},
    {"id": "V5", "rule": "role 與 beat 類型一致（dialogue 必含台詞；establish 只能是首鏡且無台詞；reaction 不含台詞）",
     "level": "error", "fix": "改 role 或改 beatIds"},
    {"id": "write_guard", "rule": "任何寫入只要「新增」上述 error 就整批不寫（400）",
     "level": "error", "fix": "先修內容再寫；舊專案既有的 error 不會擋你改字"},
]

# ── 常見工作流（照著做就不會亂序）─────────────────────────────────
RECIPES: list[dict[str, Any]] = [
    {
        "id": "zero_to_film",
        "title": "從零到一支成片",
        "steps": [
            "POST /api/projects 建專案（或 GET /api/projects 挑一個）",
            "故事層：POST /api/projects/{slug}/chat（target.kind=story）取得文字 → POST /chat/apply-batch 寫入 synopsis/chapters/style",
            "人物層：POST /chat（kind=character）→ /chat/apply-batch 寫入 appearance/outfits/voice",
            "場景層：POST /chat（kind=scene）→ /chat/apply-batch 寫 desc/promptEn",
            "劇本層：POST /generate/script（dryRun=true 先看）→ POST /apply/bulk 寫入 episodes/scenes/beats",
            "分鏡層：POST /storyboard/build（scope=all）→ GET /storyboard 檢查台詞是否逐字、每鏡 5.17–15.08 秒",
            "鎖臉圖：POST /generate/characters（或 /characters/{cid}/generate/sheet）",
            "首幀：POST /preflight/image 檢查 → POST /generate/images（批次）",
            "影格提示詞：POST /shots/{shot_id}/compile-prompt → GET /shots/{shot_id}/prompt-preview 確認真的要送出的字",
            "影片：POST /generate/videos（批次，背景）→ 輪詢 GET /generate/videos/{job_id}",
            "檢查 POST /validate（0 error）→ 合併 GET /assemble/plan 看缺什麼 → POST /assemble",
        ],
    },
    {
        "id": "edit_one_line",
        "title": "只改一句台詞，並讓引用它的鏡頭跟上",
        "steps": [
            "POST /chat/apply-batch，target.kind=beat（id=b02, parentId=e01s01），items=[{path:'line', value:'新台詞'}]",
            "引用它的鏡頭不用改：台詞是從 beat 即時讀取的（單一真相）",
            "POST /validate 確認 0 error（V4 可能因新台詞變長而報「唸不完」，那就把 durationSec 調長）",
        ],
    },
    {
        "id": "rebuild_storyboard",
        "title": "劇本大改後重建分鏡",
        "steps": [
            "POST /storyboard/build，body {scope:'all', force:false, keepManual:true}",
            "keepManual=true 會保留你手動調過的鏡頭（derivedFrom=manual），其餘依 beats 重建",
            "GET /storyboard 檢查鏡號／台詞／秒數；要覆蓋手動鏡頭再 force=true",
        ],
    },
    {
        "id": "regen_one_shot",
        "title": "只重生一顆鏡頭（影像或影片）",
        "steps": [
            "GET /shots/{shot_id}/prompt-preview 確認要送出的字（engine=h3_video|moss_t2i|qwen_image）",
            "影像：POST /shots/{shot_id}/generate/image（同步，最多等 180 秒）",
            "影片：POST /shots/{shot_id}/generate/video（背景）→ 輪詢 GET /generate/video/{job_id}",
        ],
    },
    {
        "id": "gpu_memory",
        "title": "顯示卡記憶體不夠時",
        "steps": [
            "GET /api/gpu/status 看誰佔用（llama / comfy / 其他）",
            "POST /api/gpu/stop-llama（停本地 LLM）→ 需要時再 POST /api/gpu/start-llama",
            "POST /api/gpu/release-comfy（叫 ComfyUI 卸載模型）",
            "生成完再啟動 LLM：順序反了會卡在排隊",
        ],
    },
    {
        "id": "assemble_final",
        "title": "把鏡頭合成成片",
        "steps": [
            "POST /validate（0 error 才往下）",
            "GET /assemble/plan → 看缺哪幾鏡、參數是否一致、建議模式與總時長",
            "POST /assemble → 回 final 路徑／時長／大小；成品清單用 GET /outputs",
        ],
    },
]

# ── 禁止事項（外部 AI 最容易「好心做壞事」）───────────────────────
FORBIDDEN: list[str] = [
    "不要直接改寫 projects/<slug>/project.json：一律走 API，後端才有驗證與原子寫檔",
    "不要改寫 beats 的台詞來「讓分鏡更順」——台詞是使用者的創作，改它等於改劇本",
    "不要為了湊到 H3 最短 5.17 秒而在 prompt 裡加台詞或新增動作；只能留白（padSec）或合併相鄰 beat",
    "不要在生成任務執行中同時跑本地 LLM 或另一個生成批次（單張 GPU，會互搶）",
    "不要把 apiKey 寫進 project.json 或回給使用者",
    "不要批次平行送生成任務：本程式是刻意「依序」執行以避免 OOM",
    "不要刪除 shots 而不重建：V1/V2 會立刻報「台詞沒被覆蓋」",
    "不要把 promptEn 的 <Picture N> 自己編：槽位由 referencePlan 產生",
]

# ── 端點語意補充（機器可讀清單由 OpenAPI 自動產生，這裡只補「用途／時機／副作用」）──
# 未列出的端點會由 docstring 第一行自動當 summary；仍缺者會出現在 coverage() 的 missing。
ROUTE_NOTES: dict[tuple[str, str], dict[str, str]] = {
    ("GET", "/api/health"): {"purpose": "存活與版本檢查", "when": "接手／自動化開始前先打一次", "side_effect": "無"},
    ("GET", "/api/projects"): {"purpose": "列出所有專案（含 slug、標題、進度摘要）", "when": "想知道有哪些專案可操作", "side_effect": "無"},
    ("POST", "/api/projects"): {"purpose": "新建專案（可用 fromTemplate 複製結構）", "when": "從零開始時的第一步", "side_effect": "建立 projects/<slug>/ 目錄"},
    ("GET", "/api/projects/{slug}"): {"purpose": "讀整份 project.json", "when": "需要看目前資料全貌", "side_effect": "無"},
    ("PUT", "/api/projects/{slug}"): {"purpose": "整份覆寫專案（少用；優先走 apply-batch）", "when": "只有匯入／還原時才用", "side_effect": "整檔覆寫，風險高"},
    ("DELETE", "/api/projects/{slug}"): {"purpose": "刪除專案（含媒體）", "when": "確定不要了才用", "side_effect": "不可回復，需使用者明確同意"},
    ("POST", "/api/projects/{slug}/upload"): {"purpose": "上傳參考圖／素材到專案 media/", "when": "要提供鎖臉圖、場景圖、音檔", "side_effect": "寫入 media/"},
    ("POST", "/api/projects/{slug}/chat"): {"purpose": "與模型對話（回文字，不改資料）", "when": "要草稿：大綱／章回／外型／台詞變體", "side_effect": "無寫檔（只回 reply）"},
    ("POST", "/api/projects/{slug}/chat/apply"): {"purpose": "把一段文字寫進單一欄位", "when": "只有一個欄位要改", "side_effect": "寫檔（受契約守門）"},
    ("POST", "/api/projects/{slug}/chat/apply-batch"): {"purpose": "一次寫多個欄位（含整份 chapters/beats/dialogue/outfits）", "when": "回填模型輸出、批次改資料", "side_effect": "原子寫檔；新增契約 error → 整批不寫"},
    ("POST", "/api/projects/{slug}/generate/script"): {"purpose": "故事 → 整集劇本（episode/scenes/beats）", "when": "劇本層還是空的", "side_effect": "dryRun=true 只預覽、不寫檔"},
    ("POST", "/api/projects/{slug}/generate/shots"): {"purpose": "某場次 → 多個鏡頭（LLM 分鏡，derivedFrom=deploy）", "when": "想要模型給創意分鏡（規則式請用 storyboard/build）", "side_effect": "dryRun=true 只預覽"},
    ("POST", "/api/projects/{slug}/apply/bulk"): {"purpose": "把確認過的預覽一次寫入（append 會重編 id/no）", "when": "generate/script|shots 的 dryRun 預覽確認後", "side_effect": "寫檔"},
    ("GET", "/api/projects/{slug}/storyboard"): {"purpose": "分鏡表（唯讀，含台詞逐字與累計秒數）", "when": "檢查「這部片在演什麼」", "side_effect": "無"},
    ("POST", "/api/projects/{slug}/storyboard/build"): {"purpose": "依劇本 beats 規則式重建分鏡（一 beat 一鏡，長度吸附 H3 合法格）", "when": "劇本改完／第一次分鏡", "side_effect": "重建 shots；keepManual=true 保留手動鏡頭"},
    ("POST", "/api/projects/{slug}/validate"): {"purpose": "跑 V1–V5 與格式檢查，回 {issues, counts}", "when": "每次寫入後、送生成前", "side_effect": "無"},
    ("POST", "/api/llm/test"): {"purpose": "測 LLM 連線並列出可用模型", "when": "設定頁按「重新檢查」", "side_effect": "無"},
    ("PUT", "/api/projects/{slug}/llm/settings"): {"purpose": "存 LLM 供應商設定（apiKey 不寫進 project.json）", "when": "換模型／換供應商", "side_effect": "寫 settings"},
    ("PUT", "/api/projects/{slug}/video/settings"): {"purpose": "存影片參數（解析度／fps／steps／LoRA）", "when": "調整 H3 生成參數", "side_effect": "寫 settings.video"},
    ("POST", "/api/projects/{slug}/preflight/image"): {"purpose": "生圖前檢查（缺鎖臉圖／槽位不符／沒提示詞）", "when": "批次生圖前", "side_effect": "無（不碰 GPU）"},
    ("POST", "/api/projects/{slug}/generate/images"): {"purpose": "批次生首幀（依序、每鏡完成即寫回）", "when": "要一次把整場首幀生完", "side_effect": "背景 job，寫 shots[].images"},
    ("POST", "/api/projects/{slug}/generate/videos"): {"purpose": "批次生影片（依序，失敗不中斷）", "when": "首幀都好了、要出片", "side_effect": "背景 job，寫 shots[].video"},
    ("POST", "/api/projects/{slug}/shots/{shot_id}/compile-prompt"): {"purpose": "用模板編譯這一鏡的六段式 promptEn（不呼叫模型）", "when": "promptEn 是空的或是舊稿", "side_effect": "寫 shots[].promptEn"},
    ("POST", "/api/projects/{slug}/shots/{shot_id}/build-reference-plan"): {"purpose": "產生 referencePlan 槽位（誰是 <Picture 1>）", "when": "參考圖有變動、或 promptEn 的 <Picture N> 對不上", "side_effect": "寫 shots[].referencePlan"},
    ("GET", "/api/projects/{slug}/deploy/info"): {"purpose": "一鍵佈署前的情境資訊（已有多少內容、append 會怎麼編號）", "when": "按一鍵佈署前", "side_effect": "無"},
    ("POST", "/api/projects/{slug}/deploy"): {"purpose": "一鍵佈署（**分範圍**）：body 的 scope=cast_locations（角色＋場景）／script（用現有卡片寫一集）／shots（為最新一集拆鏡頭）／all（四層全跑）；每次只呼叫該範圍需要的模型次數", "when": "內容還很空、想快速成型；建議照 角色與場景 → 劇本 → 分鏡 的順序分別按", "side_effect": "背景 job；scope=script/shots 只清自己那層（script 會連鏡頭一起清），cast_locations 覆蓋時四層全清；先看 deploy/info"},
    ("GET", "/api/projects/{slug}/assemble/plan"): {"purpose": "合併前檢查（缺片／參數不一致／建議模式／總時長）", "when": "合併前必看", "side_effect": "無"},
    ("POST", "/api/projects/{slug}/assemble"): {"purpose": "用 ffmpeg 合併成片", "when": "plan 說可以合了", "side_effect": "產出 output/*.mp4"},
    ("GET", "/api/projects/{slug}/outputs"): {"purpose": "成品清單（新到舊）", "when": "找上次合併的檔", "side_effect": "無"},
    ("POST", "/api/gpu/release-comfy"): {"purpose": "請 ComfyUI 卸載模型、釋放 VRAM", "when": "要跑別的吃 VRAM 的東西前", "side_effect": "無資料變更"},
    ("POST", "/api/gpu/stop-llama"): {"purpose": "停掉本地 LLM 以釋放 VRAM", "when": "要生圖／生影片", "side_effect": "服務中斷（可再 start）"},
    ("POST", "/api/gpu/start-llama"): {"purpose": "重新啟動本地 LLM", "when": "生成跑完、要回對話功能", "side_effect": "佔用 VRAM"},
    ("GET", "/api/comfy/status"): {"purpose": "ComfyUI 連線與可用裝置狀態", "when": "生成前確認後端在跑", "side_effect": "無"},
    ("GET", "/api/prompt-rules"): {"purpose": "各引擎的官方提示詞規則卡（h3_video／moss_t2i／qwen_image）", "when": "要自己寫 prompt 前先讀", "side_effect": "無"},
    ("GET", "/api/llm/status"): {"purpose": "LLM 供應商與模型清單", "when": "確認要用哪個模型", "side_effect": "無"},
}

# 這些路由不需要語意說明（框架內建或內部用）
IGNORED_ROUTES: set[tuple[str, str]] = {
    ("GET", "/openapi.json"), ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"), ("GET", "/api/agent/manifest"), ("GET", "/api/agent/guide"),
    ("GET", "/api/agent/coverage"),
}

OPERATING_ORDER = [
    "meta / story（大綱、章回、風格）",
    "characters（外型、身分、服裝、音色）",
    "scenes（地點、光線、氛圍）",
    "script.episodes[].scenes[].beats（誰、說什麼、做什麼）← 唯一真相",
    "shots（分鏡：引用 beats、決定鏡位與長度）",
    "shots[].images（首幀）→ shots[].video（片段）→ assemble（成片）",
]


def _iter_api_routes(app: Any):
    """走 FastAPI 的 openapi paths，回 (method, path) 清單（排除 docs 類）。"""
    spec = app.openapi()
    out: list[tuple[str, str]] = []
    for path, ops in (spec.get("paths") or {}).items():
        for method in ops:
            m = method.upper()
            if m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((m, path))
    return sorted(out, key=lambda x: (x[1], x[0]))


def _summary_of(app: Any, method: str, path: str) -> str:
    spec = app.openapi()
    op = ((spec.get("paths") or {}).get(path) or {}).get(method.lower()) or {}
    return (op.get("summary") or "").strip()


def _endpoint_doc_of(app: Any, method: str, path: str) -> str:
    """抓 handler docstring 第一行（很多人寫了但 summary 是空的）。"""
    for r in getattr(app, "routes", []):
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set()):
            doc = (getattr(r.endpoint, "__doc__", "") or "").strip()
            return doc.split("\n")[0].strip() if doc else ""
    return ""


def routes_doc(app: Any) -> list[dict[str, Any]]:
    """每個 API 的語意說明（schema 由 OpenAPI 帶，這裡不重抄）。"""
    spec = app.openapi()
    out: list[dict[str, Any]] = []
    for method, path in _iter_api_routes(app):
        note = ROUTE_NOTES.get((method, path), {})
        op = ((spec.get("paths") or {}).get(path) or {}).get(method.lower()) or {}
        out.append({
            "method": method,
            "path": path,
            "purpose": note.get("purpose") or _summary_of(app, method, path) or _endpoint_doc_of(app, method, path),
            "when": note.get("when", ""),
            "sideEffect": note.get("side_effect", ""),
            "requestSchemaRef": _schema_ref(op),
            "queryParams": [p.get("name") for p in (op.get("parameters") or []) if p.get("in") == "query"],
        })
    return out


def _schema_ref(op: dict) -> str:
    body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {})
    sch = body.get("schema") or {}
    ref = sch.get("$ref") or ""
    if ref:
        return ref.rsplit("/", 1)[-1]
    return "inline" if sch else ""


def coverage(app: Any) -> dict[str, Any]:
    """文件完整度：哪些 API 還沒有語意說明。"""
    documented, missing = [], []
    docs = {(d["method"], d["path"]): d for d in routes_doc(app)}
    for method, path in _iter_api_routes(app):
        if (method, path) in IGNORED_ROUTES:
            continue
        purpose = (docs.get((method, path)) or {}).get("purpose") or ""
        (documented if purpose else missing).append({"method": method, "path": path})
    return {"total": len(documented) + len(missing), "documented": len(documented),
            "missing": len(missing), "missingList": missing}


def manifest(app: Any, *, version: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """機器可讀的完整說明。schema 直接引用 OpenAPI（不會與程式碼漂移）。"""
    spec = app.openapi()
    schemes = list((spec.get("components") or {}).get("schemas") or {})
    return {
        "agentDocVersion": AGENT_DOC_VERSION,
        "app": {"name": "Story Video Studio", "version": version or "",
                "purpose": "把故事演成影片：劇本(beats) → 分鏡(shots) → 首幀 → 片段 → 成片"},
        "entrypoints": {
            "guide": "/api/agent/guide", "manifest": "/api/agent/manifest",
            "openapi": "/openapi.json", "docs": "/docs", "health": "/api/health",
        },
        "operatingOrder": OPERATING_ORDER,
        "hardLimits": HARD_LIMITS,
        "dataModel": DATA_MODEL,
        "validation": VALIDATION_RULES,
        "recipes": RECIPES,
        "forbidden": FORBIDDEN,
        "routes": routes_doc(app),
        "schemas": schemes,
        "contracts": {"storyboard": "docs/spec/storyboard-contract.md", "dataModel": "docs/spec/data-model.md"},
        **(extra or {}),
    }


def guide(app: Any, *, version: str = "") -> str:
    """給 LLM 讀的操作手冊（Markdown，一份就能上手）。"""
    cov = coverage(app)
    lines: list[str] = []
    A = lines.append
    A("# Story Video Studio — Agent 操作手冊")
    A("")
    A(f"版本 `{version or '?'}`｜文件版本 `{AGENT_DOC_VERSION}`｜端點 {cov['total']} 支（已說明 {cov['documented']}）")
    A("")
    A("## 連線與版本")
    A("")
    A("- 使用端 exe：`http://127.0.0.1:8788`；開發模式：`http://127.0.0.1:8790`")
    A("- 開工前先 `GET /api/health`，確認 `{ok:true}` 且 `version` 與你要操作的功能相符")
    A("- 精確欄位型別去 `GET /openapi.json` 的 `components.schemas`；本手冊只講語意與順序")
    A("")
    A("## 這是什麼")
    A("")
    A("一本機工具，把「故事」演成影片。資料是單一事實來源的樹狀結構，**順序不能亂**：")
    A("")
    for i, step in enumerate(OPERATING_ORDER, 1):
        A(f"{i}. {step}")
    A("")
    A("程式只做三件事：**寫資料 → 驗證 → 送生成**。所有寫入都要走 API（後端才有驗證與原子寫檔）。")
    A("")
    A("## 硬限制（違反必壞，先讀這段）")
    A("")
    A("| 限制 | 規則 | 意義 |")
    A("|---|---|---|")
    for h in HARD_LIMITS:
        A(f"| {h['id']} | {h['rule']} | {h['meaning']} |")
    A("")
    A("## 資料模型（骨架）")
    A("")
    A("| 路徑 | 主要欄位 | 備註 |")
    A("|---|---|---|")
    for d in DATA_MODEL:
        A(f"| `{d['path']}` | {d['fields']} | {d['note']} |")
    A("")
    A("## 驗證規則（`POST /api/projects/{slug}/validate`）")
    A("")
    A("| 規則 | 內容 | 等級 | 怎麼修 |")
    A("|---|---|---|---|")
    for v in VALIDATION_RULES:
        A(f"| {v['id']} | {v['rule']} | {v['level']} | {v['fix']} |")
    A("")
    A("## 常見工作流")
    A("")
    for r in RECIPES:
        A(f"### {r['title']}（`{r['id']}`）")
        A("")
        for i, s in enumerate(r["steps"], 1):
            A(f"{i}. {s}")
        A("")
    A("## 禁止事項")
    A("")
    for f in FORBIDDEN:
        A(f"- {f}")
    A("")
    A("## API 索引")
    A("")
    A("| 方法 | 路徑 | 用途 | 何時用 | 副作用 |")
    A("|---|---|---|---|---|")
    for d in routes_doc(app):
        A(f"| {d['method']} | `{d['path']}` | {d['purpose'] or '（未說明）'} | {d['when']} | {d['sideEffect']} |")
    A("")
    A("參數與 JSON 結構不要用猜的：`GET /api/agent/manifest` 內含每支端點的 schema 名稱，"
      "完整定義在 `GET /openapi.json`（`components.schemas`）。")
    A("")
    A("## 背景任務的輪詢方式")
    A("")
    A("批次生圖／生影片／一鍵佈署都是「先回 jobId，再輪詢」：")
    A("")
    A("```")
    A("POST /api/projects/{slug}/generate/videos   → {jobId, pollUrl}")
    A("GET  /api/projects/{slug}/generate/videos/{jobId}  → 每 2–5 秒一次，直到 status=done|failed|cancelled")
    A("```")
    A("")
    A("## 回報與錯誤")
    A("")
    A("- 單欄／批次寫入成功回 `{ok, updated[], targetAfter}`；不合法回 **400** 並指出「第 N 項（path）」與原因。")
    A("- 找不到專案／卡片回 **404**。")
    A("- 生成類失敗一律回中文 `error` 字串（已截斷），並把 `video.status` 改 `failed`。")
    return "\n".join(lines) + "\n"
