---
branch: feat/m13a-chat-apply-structured
milestone: M13a
status: ready            # wip | ready | merged
opened: 2026-11-21 12:52（台北）
files: ["app/storage.py", "app/server.py", "app/h3compose.py", "static/app.js", "static/style.css"]
---

# 交接單：m13a-chat-apply-structured

## 做了什麼

「與模型對話」的回填從「一次只能寫一個純文字欄位」升級成「一次套用多個欄位，而且能寫結構化資料」（章回、場次 beats、鏡頭 dialogue、服裝）。回覆氣泡新增「⇣ 解析成多欄」：一段回覆自動拆成可預覽、可編輯的條目，確認後才寫檔；只選取回覆裡的一段時，陣列類欄位預設「追加」而不是整份蓋掉。

## 動到的檔案

- `app/storage.py`：`apply_to_target_batch()`、`_norm_array_items()`（整份陣列白名單與結構驗證）、`_add_skipped()`／`_diff_arrays()`／`_reconcile_beat_refs()`、`apply_item_in_memory()`（單欄與 batch 共用同一套規則）；`APPLY_FIELD_WHITELIST`／`_APPLY_FIELD_PATTERNS` 擴充。
- `app/server.py`：新增 `POST /api/projects/{slug}/chat/apply-batch`。
- `app/h3compose.py`：組稿與驗證時過濾 `_skipped` 的 beat（整份覆寫留下的舊項不得進生成）。
- `static/app.js`：`MULTI_APPLY_FIELDS`＋規則式解析器（`parseChapters`／`parseLabeled`／`parseStoryStyle`／`parseCharacterTraits`／`parseBeats`／`parseDialogue`／`tidyParseResult`）；氣泡「⇣ 解析成多欄」與任務列入口；寫入方式（追加／整份取代）選單；選取文字快取 `bubbleSelCache`；劇本頁只渲染有效 beat、作廢項收在「已作廢」區塊可還原。
- `static/style.css`：多欄面板、作廢 beat 樣式。

## 介面變更

- 新 API：`POST /api/projects/{slug}/chat/apply-batch`
  body：`{"target":{"kind":"story|character|scene|episodeScene|beat|shot","id":"...","parentId":"..."},"items":[{"path":"chapters","value":[...],"mode":"replace|append"}]}`
  回：`{"ok":true,"updated":[{"path","oldValue","value"}],"targetAfter":{...},"warnings":[...]}`；任一項不合法 → 400 並指出「第 N 項（path）」，整批不寫檔。
- 新可寫路徑（白名單）：`chapters`／`chapters.*.title|body`、`beats`／`beats.*.line|action|emotion|notes|type|characterId`、`dialogue`／`dialogue.*.line|characterId`、`outfits`／`outfits.*.name|desc|promptEn`、`spec.angle.h|v|s`、`shot.beats`。
- `_skipped` 約定：整份覆寫 beats／dialogue 時舊項保留並標 `_skipped`（不進生成、不列入引用清單），驗證與 `h3compose` 都過濾。
- `beats` 寫入時：`characterId` 給角色名字會自動換成 id（含別名、打錯一字），對不到存 null 並回 warning；`action` 視為 `line` 別名；被換掉的 beat 會同步清理引用它的鏡頭 `dialogBeatIds`／`dialogLineRefs` 並回 warning。

## 未完成／已知限制

- 解析器仍寫在 `static/app.js`（未抽成獨立檔案），沒有單元測試檔，驗證靠瀏覽器與 `chat/apply-batch` 實跑。
- 解析是規則式（「第 N 回」「角色名：台詞」「標籤：內容」）；模型輸出格式太自由時可能只認到一部分，未認到的會列在預覽提醒，使用者仍可手動補。
- 角色 `stages`、`identityAnchors` 尚未列入可回填路徑。
- 舊的單欄套用 UI 保留（相容），新舊兩條路都走後端同一套驗證。
- 批次寫入不做 `referencePlan` 重算（那是另一支 API）。
- 每個對話任務的「預設回填目標」與「多版本二選一」留給 M13b。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117-wt-chatapply"
SVS_DATA_DIR=C:/路徑/到/複本專案的projects "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8790
# 1) 多欄＋結構：chapters(3 回)＋style.tone 一次寫入 → GET 確認、POST /api/projects/{slug}/validate 回 error 0
# 2) 場次 beats 用「角色名：台詞」→ characterId 自動對到；錯名字 → 存 null 並回 warning
# 3) 故意送不合法（beats 的 type 亂填）→ 400 且 project.json 位元組不變
# 4) 瀏覽器：對話回覆選一段 → 按「⇣ 解析成多欄」→ 寫入方式預設「追加」→ 套用後章回只多一回
```

## 是否動到契約（data-model.md / contracts.md）

沒有改欄位語意：`chapters`／`beats`／`dialogue`／`outfits` 本來就在資料模型裡，本輪只是把「可寫入」開出來。`_skipped` 是陣列內的內部標記（讀取端一律過濾），建議補進 `docs/data-model.md` 的欄位說明。
