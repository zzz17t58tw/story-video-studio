---
branch: feat/m13b-chat-task-targets
milestone: M13b
status: ready            # wip | ready | merged
opened: 2026-11-21 13:21（台北）
files: ["static/app.js", "static/style.css", "PLAN.md"]
---

# 交接單：m13b-chat-task-targets

## 做了什麼

「與模型對話」的每個預設任務現在都宣告自己的回填目標（`CHAT_TASKS[*].applyHint`）：任務 chip 上直接標「→ 回填：章回／外型四欄＋promptEn／台詞…」，回覆氣泡的按鈕變成「⇣ 回填到 目標名稱」（例如「⇣ 回填到 外型四欄＋promptEn」），點下去開的面板**只展開並預勾這個任務指定的格子**，其餘欄位收進「其他欄位」。補了外型解析器（`parseAppearance`，中英文標籤都認）與多版本一鍵選用（三版台詞／三個音色建議 → 「用第 N 版」直接寫檔）。7 種對象 23 個任務（含 6 個 `custom`）逐一排查：22 個有實際寫入路徑，1 個（鏡頭③檢查矛盾）明確標「僅建議，不回填」，沒有回填按鈕。

## 動到的檔案

- `static/app.js`
  - `CHAT_TASKS`：每個任務加 `applyHint`（`{fields, multi?, target, variants?}` 或 `null`）；鏡頭③加 `adviceOnly: "僅建議，不回填"`。
  - 新增 `chatTaskById()`／`splitHintFields()`（把 applyHint 拆成「多欄格子／單欄」）／`applyHintLabel()`／`HINT_FIELD_LABELS`＋`hintFieldLabel()`（單欄中文標籤，不依賴卡片是否載入）。
  - 新增 `parseAppearance()`＋`APPEARANCE_LABELS/KEYS`：認「臉型五官／髮型／體型／膚色／英文 prompt」與 `Face:/Hair:/Body:/Skin:/English prompt:`；「英文 prompt：」單獨一行、內容在下一行也補抓；整段純英文 → 視為 promptEn。
  - `renderChatTasks()`：chip 文字加「→ 回填：…」、鏡頭③加「→ 僅建議，不回填」；任務列的「⇣ 回填到…」入口會跟著最近回覆的任務改字。
  - `renderChatLog()`：氣泡按鈕依 `meta.task` 顯示「⇣ 回填到 目標名稱」；`applyHint === null` 顯示「僅建議，不回填（照建議自己改欄位）」且不給回填按鈕；錯誤氣泡不給任何套用控制（修掉 M13a 遺留的錯誤氣泡也顯示「⇣ 解析成多欄」）。
  - `openReplyBackfill()`、`replyVariants()`／`voiceCandidates()`／`variantValueFor()`／`variantButtons()`／`applyVariant()`：多版本候選解析與一鍵寫入（走 apply-batch 單欄 replace）。
  - `renderMultiPanel(host, idx, selArg, taskIdArg)`：依 applyHint 只渲染目標格子並預填／預勾；有 applyHint 時按鈕是「↻ 重新解析」而不是「⇣ 解析成多欄」；其他欄位收在摺疊區（details）。
  - `buildPlainRow()`、`plainHintFor()`、`simplifyReplyText()`、`firstEnglishBlock()`：單欄回填（預填、勾選、台詞去編號引號、音色截前三行）。
  - `collectMultiItems()`：「其他欄位」的 textarea 沒勾就完全不參與（避免偷偷寫入），預填格空字串則直接略過不洗版。
  - `sendChat()`：助理訊息 `meta.task` 記下這次是哪個任務（回填面板靠它決定目標）。
- `PLAN.md`：M13 段補上 M13b 里程碑一行。
- `static/style.css`：`.chip-hasapply`／`.chip-to`／`.chip-active .chip-to`／`.chip-advice`／`.ma-variants`／`.btn-variant`／`.ma-variant-tag`（9 行）。

## 介面變更

- 前端新增（皆在 `static/app.js`，全域）：`parseAppearance(text) -> {face,hair,body,skin,promptEn}`、`chatTaskById(kind,id)`、`splitHintFields(target,hint)`、`replyVariants(text,task)`、`applyVariant(btn,idx,task,no,value)`；`renderMultiPanel(host, idx, selArg, taskIdArg?)` 多一個選擇性參數。
- `chatState.messages[*].meta` 多一個 `task`（任務 id，可為 null）。
- 沒有任何 API／後端變更：全部走 M13a 已有的 `POST /api/projects/{slug}/chat/apply-batch`（單欄 replace 也走它）。沒有改後端白名單。

### 任務 → 回填對照（23 個任務全數排查）

| 對象 | 任務 id | 回填目標（applyHint.fields） | 形式 | 實測 |
|---|---|---|---|---|
| 故事 | `story_outline` | `synopsis` | 單欄 | 覆蓋率檢查通過 |
| 故事 | `story_chapters` | `chapters` | 多欄（整份，可追加） | d) 實跑寫入 3 回 |
| 故事 | `story_style` | `style` → `style.genre/tone/visualStyle/negative` | 多欄四項 | 實跑寫入 4 欄 |
| 故事 | `custom` | `synopsis` | 單欄 | 覆蓋率檢查通過 |
| 人物 | `character_look` | `appearance.promptEn`＋`appearance.face/hair/body/skin` | 單欄×5，一鍵全展開 | b) 實跑寫入 5 欄 |
| 人物 | `character_outfit` | `outfits` | 多欄 | 實跑寫入 1 欄 |
| 人物 | `character_background` | `background` → `background.birth/personality/goal/secret` | 多欄四項 | 實跑寫入 4 欄 |
| 人物 | `character_voice` | `voice.desc` | 單欄＋三版按鈕 | 實跑「用第 3 版」寫入 |
| 人物 | `custom` | 同 `character_look` | 單欄×5 | 覆蓋率檢查通過 |
| 場景 | `scene_expand` | `desc` | 單欄 | 實跑寫入 |
| 場景 | `scene_prompt_en` | `promptEn` | 單欄 | 覆蓋率檢查通過 |
| 場景 | `custom` | `desc` | 單欄 | 覆蓋率檢查通過 |
| 場次 | `episode_scene_dialogue` | `beats` | 多欄 | 覆蓋率檢查通過 |
| 場次 | `episode_scene_rewrite` | `beats` | 多欄 | 覆蓋率檢查通過 |
| 場次 | `episode_scene_action` | `beats` | 多欄 | 實跑寫入（含警告與 `_skipped` 行為） |
| 場次 | `custom` | `beats` | 多欄 | 覆蓋率檢查通過 |
| beat | `beat_rewrite` | `line` | 單欄（自動去「1.」與引號） | 邊界實測值＝「就算你是親衛隊長，也攔不住我。」 |
| beat | `beat_variants` | `line` | 單欄＋三版按鈕 | c) 實跑「用第 2 版」寫入 |
| beat | `custom` | `line` | 單欄 | 覆蓋率檢查通過 |
| 鏡頭 | `shot_prompt_h3` | `promptEn` | 單欄 | 實跑寫入 sh02 |
| 鏡頭 | `shot_prompt_en_from_zh` | `promptEn` | 單欄 | 覆蓋率檢查通過 |
| 鏡頭 | `shot_check_consistency` | **null（僅建議，不回填）** | 無 | e) chip 與氣泡均標示，無回填按鈕 |
| 鏡頭 | `custom` | `promptEn` | 單欄 | 覆蓋率檢查通過 |

## 未完成／已知限制

- 解析仍是規則式、只在前端：模型輸出格式太自由（例如把四欄寫成一大段散文）時，格子可能預填不到東西，面板會顯示「沒有辨識到…」提醒，使用者可在 textarea 手改或先選取那段再按；沒有做「一次把同一段回覆分給多個單欄」的智慧切分。
- 多版本按鈕只在回覆「像 1.／2.／3.」或（音色任務）前幾行時出現；若模型把三版寫成同一段，就不會出現按鈕，此時只能手動編修。
- 「其他欄位」的多欄格子（人物的身世／服裝、故事另一格）在有 applyHint 時一律預設不勾、要靠使用者自己勾；單欄「其他欄位」必須勾了且打了字才會寫 — 這是刻意的（避免誤寫），但代表「其他欄位」不會自動帶入回覆全文。
- `character_look` 的辨識特徵（`appearance.features`）不在白名單／UI 內，辨識特徵只能塞進 face／body 文字，沒有獨立格子。
- 選取文字仍是「先選再按」才有精準範圍；「其他欄位」若使用者自己勾了仍會一併寫入（沒有「只寫目標格子」的硬鎖）。
- 沒有寫自動化測試檔：驗證是起伺服器＋瀏覽器注入假回覆實跑；23 個任務的覆蓋率是由瀏覽器 console 逐一呼叫 `CHAT_TASKS`／`multiApplyFieldsFor`／`applyFieldsFor` 產表（23/23 有 hint，0 個 MISSING 路徑）。

## 驗證方式

```bash
# 0) 準備（絕不寫入使用者 dist：複本放 scratch）
mkdir -p "C:/Users/user/AppData/Local/hermes/cache/scratch/m13b/projects/project-20261120-172537"
cp -r "C:/Users/user/Desktop/hermes work/1117/dist/StoryVideoStudio/projects/project-20261120-172537/." "C:/Users/user/AppData/Local/hermes/cache/scratch/m13b/projects/project-20261120-172537/"
cd "C:/Users/user/Desktop/hermes work/1117-wt-chattasks"
SVS_DATA_DIR=C:/Users/user/AppData/Local/hermes/cache/scratch/m13b/projects \
  "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8791
# ⚠ 若 8790 已被別的 uvicorn 佔用（netstat -ano | grep 8790），換一個埠（本輪用 8791），
#   否則 curl 會連到別的工作樹版本、看不到新的 applyHint。

# 1) 靜態檢查：每個任務都帶回填宣告
curl -s 127.0.0.1:8791/app.js | grep -c applyHint        # 預期 46（0 = 載到舊檔）

# 2) 單欄寫入（不經 UI，直接打 API 驗白名單）
curl -s -X POST 127.0.0.1:8791/api/projects/project-20261120-172537/chat/apply-batch \
  -H "Content-Type: application/json" \
  -d '{"target":{"kind":"beat","id":"b02","parentId":"e01s01"},"items":[{"path":"line","value":"就憑你這種貨色，也想攔我？","mode":"replace"}]}'
curl -s 127.0.0.1:8791/api/projects/project-20261120-172537 | python -c "import json,sys; p=json.load(sys.stdin); p=p.get('project',p); print([b['line'] for ep in p['script']['episodes'] for es in ep['scenes'] for b in es['beats'] if b['id']=='b02'])"

# 3) 瀏覽器（http://127.0.0.1:8791/，選專案後在 devtools console）
#    a) openChat({kind:'shot', id:'sh01'}) → 任務列應見「③ 檢查與人物卡是否矛盾→ 僅建議，不回填」
#       再注入 chatState.messages=[{role:'assistant',content:'1. 沒問題',meta:{model:'fake',task:'shot_check_consistency'}}]; renderChatLog();
#       → 氣泡應只有「僅建議，不回填（照建議自己改欄位）」，沒有「⇣ 回填」按鈕
#    b) openChat({kind:'character', id:'c01'})
#       chatState.messages=[{role:'assistant',content:'臉型五官：長橢圓臉\n髮型：及肩亂髮\n體型：瘦高\n膚色：暖黃\n英文 prompt：a tall lean man, 9:16',meta:{model:'fake',task:'character_look'}}]; renderChatLog();
#       點「⇣ 回填到 外型四欄＋promptEn」→ summary 應為「將套用 5 欄：appearance.*」→ 按套用
#    c) openChat({kind:'beat', id:'b02', parentId:'e01s01'})，注入三行「1. …／2. …／3. …」（meta.task='beat_variants'）
#       → 出現「用第 1/2/3 版」→ 點第 2 版 → 狀態列「已寫入第 2 版到 … 的 line」
#    d) openChat({kind:'story'})，注入「第一回…第二回…第三回…」（meta.task='story_chapters'）
#       → 面板只展開「整份章回」，其他欄位收在 details

# 4) 收尾
#    關掉自己起的 uvicorn，刪掉 scratch/m13b/projects（dist 目錄不可寫）
```

## 是否動到契約（data-model.md / contracts.md）

沒有。沒有動 `docs/data-model.md`／`docs/contracts.md`，也沒有動後端 API 或白名單；只是前端把 M13a 已有的 `chat/apply-batch` 欄位路徑與任務對應起來。
