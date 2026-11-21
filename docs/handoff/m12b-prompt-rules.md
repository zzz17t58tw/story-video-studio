---
branch: m12b-prompt-rules
milestone: M12b
status: ready
opened: 2026-11-21 12:21（台北）
files: ["app/promptrules.py", "app/h3gen.py", "app/imagegen.py", "app/server.py", "static/app.js", "static/style.css", "docs/handoff/m12b-prompt-rules.md"]
---

# 交接單：m12b-prompt-rules

## 做了什麼

新增 `app/promptrules.py` 當三個生成引擎（H3 影片／MOSS t2i／Qwen Image）官方提示詞規則的唯一真相（規則卡＋build 包裝＋中文 check），並把送件口改成硬驗：H3 影片 `promptEn` 不合格自動改用 `h3compose.compile_prompt` 的即時結果（不寫回 project.json，warnings 逐條列出缺什麼），MOSS／Qwen 不合格直接擋下不送 GPU。另加 `GET /api/prompt-rules` 與 `GET .../prompt-preview` 兩支純讀端點，鏡頭卡的生圖／生影片區塊顯示「規則：引擎名稱」並可一鍵看這次真正要送出的提示詞、規則條列、warnings 與 problems。

## 動到的檔案

- `app/promptrules.py`（新增）：`RULES`（h3_video／moss_t2i／qwen_image，各含 id／label／source／rulesZh／build／check）、`prepare()`、`check()`、`describe()`、`rule_of()`、`ctx_for()`。
- `app/h3gen.py`：新增 `_sendable_ordinals`／`_pick_prompt`／`_used_pictures`；`build_timeline` 先算出「這次送得出哪些 `<Picture N>` 序號」，再用 `promptrules.check("h3_video", ...)` 驗 `promptEn`，不合格就換模板編譯；timeline `_meta` 增加 `promptRule`／`promptSource`／`promptProblems`；影片 job 同步帶出這三欄。
- `app/imagegen.py`：`generate_moss_image` 送件前跑 `promptrules.check("moss_t2i", ...)`（不合格拋 `ImageGenError`，中文列出缺什麼）；`build_prompt` 送件前跑 `promptrules.check("qwen_image", ...)`，不合格先拋出。
- `app/server.py`：新增 `/api/prompt-rules`、`/api/projects/{slug}/shots/{shot_id}/prompt-preview`。
- `static/app.js`：新增 `loadPromptRules`／`promptRuleLabel`／`promptPreviewPanel`／`promptPreviewButton`；生圖區塊加規則徽章＋預覽按鈕＋panel；生影片區塊加規則徽章、預覽按鈕、`promptEn` 不符規則的事前提醒、按下確認時列出違規項目、完成後顯示 `promptSource`（compiled／promptEn）與「上次提示詞」。
- `static/style.css`：`.rule-badge`、`.pp-box`、`.pp-rules`、`.pp-prompt`、`.pp-pre-warn` 樣式（插在既有 subcard 內，未改版面結構）。

## 介面變更

- `GET /api/prompt-rules` → `{ok, engines[], rules:[{id, engine, label, source, rulesZh[]}]}`。
- `GET /api/projects/{slug}/shots/{shot_id}/prompt-preview?engine=h3_video|moss_t2i|qwen_image` → `{ok, engine, ruleId, label, source, rulesZh[], prompt, promptSource, warnings[], problems[], chars}`；組稿失敗時回 HTTP 200＋`ok:false`＋`error`（前端顯示原因，不吐 traceback）。
- `h3gen.build_timeline()` 回傳的 timeline `_meta` 新增 `promptRule: "h3_video"`、`promptSource: "compiled"|"promptEn"`、`promptProblems: string[]`；`h3gen` 影片 job dict 新增同名三欄（`GET /generate/video/{jobId}` 的 `job` 內）。
- `app/promptrules.py`：`prepare(engine, project, obj, **kw)` → `(prompt, warnings, ruleId)`；`check(engine, prompt, ctx)` → `(ok, problems)`；`describe()` → `[規則卡]`；`ctx_for(engine, project, shot, **kw)` → `dict`。

## 未完成／已知限制

- 本輪未呼叫任何模型（LLM／ComfyUI），所有驗證都是純邏輯＋HTTP 端點；**沒有實際送 H3／MOSS／Qwen 跑出一張圖／一支片**，「模型的實際反應」仍待主對話端到端實測。
- MOSS 的 `identityAnchors` 逐字比對接受了 `，`→`, `、`。`→`. `、`、`→`, ` 三種接法（因為 `build_moss_caption` 自己會轉換），不是字面上的 100% 原字串比對。
- 驗收副本的 6 鏡原本 `referencePlan.slots` 全是空的；為了讓 H3／Qwen 走到「有槽位」的路徑，我在**暫存副本**（`%LOCALAPPDATA%\Temp\m12b-rules-test`，已刪）手工補了角色／場景參考圖並用 `h3compose.build_reference_plan` 重算 slots，沒有動使用者的 `dist/`。
- 實際跑出的規則判定：sh03（特寫＋`characterIds` 兩人）的 MOSS caption 會被新規則擋下（`build_moss_caption` 對 close_up 一律當「只有物件」而不寫 `In frame` 的臉部描述＝identityAnchors 沒貼上）；sh03 的 `promptEn` 依 H3 規則也會被判不合格（與其他 5 鏡一樣，原本就是未編譯草稿）。這是規則如實反映資料，不是回歸；要不要改組稿邏輯是另一個範圍。
- 前端「按下生影片的確認框」用 `prompt-preview` 判斷是否會改用模板編譯；預覽端點失敗時不阻擋（送件口本身仍硬驗）。
- 批次生影片／批次生圖沿用同一個 `submit()`／`generate_shot_image()`，因此自動繼承新規則；但批次的 UI 沒有另外加規則徽章。
- 角色立繪的 MOSS caption（`build_character_sheet_caption`）本輪未納入 `promptrules`（規格只列 t2i／qwen／h3 三個 engine）。

## 驗證方式

```bash
# 準備驗收副本（暫存，不寫入使用者的 dist/）
SC="$LOCALAPPDATA/Temp/m12b-rules-test"; mkdir -p "$SC/projects/project-x"
cp -r "C:/Users/user/Desktop/hermes work/1117/dist/StoryVideoStudio/projects/project-20261120-172537/." "$SC/projects/project-x/"
SVS_DATA_DIR="$(cygpath -w "$SC/projects")" "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8790

# a) 三張規則卡
curl -s 127.0.0.1:8790/api/prompt-rules | "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -c "import sys,json;d=json.load(sys.stdin);print(d['engines']);[print(r['id'],r['label'],len(r['rulesZh']),r['source']) for r in d['rules']]"
# b) MOSS caption 預覽（不得有 quality tag／1. Subject／<Picture）
curl -s "127.0.0.1:8790/api/projects/project-x/shots/sh01/prompt-preview?engine=moss_t2i" | "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -c "import sys,json;d=json.load(sys.stdin);print(d['ruleId'],d['ok'],d['chars'],d['prompt'][:80])"
# c) H3 六段式預覽（六段＋<Picture N>；原 promptEn 不合格時 warnings 列缺什麼）
curl -s "127.0.0.1:8790/api/projects/project-x/shots/sh01/prompt-preview?engine=h3_video" | "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -c "import sys,json;d=json.load(sys.stdin);print(d['promptSource'],d['problems']);print(d['prompt'][:300])"
# d) 直接呼叫 build_timeline（不送 ComfyUI）
cd "C:/Users/user/Desktop/hermes work/1117-wt-rules" && SVS_DATA_DIR="$(cygpath -w "$SC/projects")" "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -c "import os,sys;sys.path.insert(0,r'C:\\Users\\user\\Desktop\\hermes work\\1117-wt-rules');from app import storage,h3gen;s=os.listdir(storage.PROJECTS_DIR)[0];d=storage.load_project(s);sh=next(x for x in d['shots'] if x['id']=='sh01');t=h3gen.build_timeline(d,sh,{'seed':1});m=t['_meta'];print(m['promptRule'],m['promptSource'],'<Picture' in t['segments'][0]['prompt']);print(t['_warnings'][0][:120])"
# e) MOSS 負向
"C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -c "import sys;sys.path.insert(0,r'C:\\Users\\user\\Desktop\\hermes work\\1117-wt-rules');from app import promptrules;print(promptrules.check('moss_t2i','A man, masterpiece, best quality, 8k'))"
# f) 前端有接
curl -s 127.0.0.1:8790/app.js | grep -c prompt-preview
# 收尾：關伺服器、刪 "$SC"
```

## 是否動到契約（data-model.md / contracts.md）

沒有。timeline `_meta` 與 job dict 是執行期回傳欄位（不落地、不進 project.json）；`project.json` 的結構完全未改。
