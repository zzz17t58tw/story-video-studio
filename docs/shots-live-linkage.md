# 鏡頭頁即時連動契約（v1，2026-11-24）

> 這份文件定義「鏡頭頁不得顯示過期資料」的**機械判準**。實作照這份做，驗收看最後一節。
> 起因：使用者在人物／場景／劇本頁改了東西，回到鏡頭頁準備生成時看到的仍是舊資料
> （實測他的 `project-20261122-121207`：28 鏡裡 25 鏡 `promptEn` 是 0 字、26 鏡參考圖槽位為 0、
> e01s03 第 2 句台詞 b05 沒有任何鏡頭引用、e01s01 有 4 支重複的手動鏡）。

---

## 1. 真相 vs 衍生（唯一分界）

**真相（A 類）＝ 只由使用者或模型寫入決定，衍生流程不得改寫**

| 欄位 | 說明 |
|---|---|
| `script.episodes[].scenes[].beats[]` | 台詞與動作的唯一真相 |
| `characters[]`（含 `appearance`／`identityAnchors`／`stages[].refImages`／`refImages`） | 人物唯一真相 |
| `scenes[]`（含 `refImages`／`promptEn`） | 場景唯一真相 |
| `shots[].beatIds` / `characterIds` / `sceneId` / `stageIds` / `spec` / `durationSec` / `padSec` / `role` | 分鏡決策（人寫的） |
| `shots[].promptEnManual === true` 的 `promptEn` | 手寫稿，不得自動覆蓋 |

**衍生（B 類）＝ 任何時候讀到它，必須等於「用當下真相重算一次」的結果**

| 代號 | 衍生值 | 由什麼算出 | 落盤？ |
|---|---|---|---|
| D1 | `shots[].referencePlan`（`slots[]`／`promptPlaceholders`／`weakReferences`） | `characterIds`＋`stageIds`＋`sceneId`＋角色/場景 `refImages` | 可以落盤，但**讀寫兩端都必須重算** |
| D2 | `shots[].promptEn` | `h3compose.compile_prompt(project, shot)` | 同上（手動稿除外） |
| D3 | `shots[].dialogLineRefs` | 由 `dialogBeatIds` 對照該場次台詞順序 | 落盤但每次重算 |
| D4 | 場次覆蓋狀態（哪些 beat 沒被任何鏡頭拍到） | 掃 `shots[].beatIds` ∪ `dialogBeatIds` | **不落盤**，每次算 |
| D5 | 孤兒鏡頭（`beatIds` 空或全部指向不存在的 beat） | 同上 | **不落盤**，每次算 |

**核心規則**：畫面上的字、API 回的字、實際送出的字，三者必須相同。
現況的破口是「送件口會現場重編譯、畫面顯示的是舊快照」——這一條要在本輪消滅。

---

## 2. 後端接線（兩處，缺一不可）

1. `storage.save_project(project)`：填完預設值後、驗證前，呼叫
   `h3compose.refresh_derived(project)`。
2. `GET /api/projects/{slug}`（以及任何回傳整份專案的端點）：在 **deepcopy** 上呼叫同一支，
   **不寫檔**。讓「讀」也永遠一致。

`refresh_derived(project)` 的行為：

- 逐鏡重算 D1：`build_reference_plan(project, shot)`（沿用既有函式，不重寫邏輯）。
- 逐鏡重算 D3。
- 逐鏡重算 D2 **僅當** `shot.promptEnManual` 不為 true **且** `derivedFrom` ∈ {`storyboard`, `deploy`}
  或 `promptEn` 為空。使用者自己打的字（`derivedFrom=manual` 且非空）不覆蓋，
  但要在 `shot.derived` 標記 `promptEnStale: true/false`（比對「現算」與「存檔值」是否相同）。
- 嚴格不得 raise：任何一鏡算失敗只記錄在 `shot.derived.error`，其他鏡照算。

每鏡加一個衍生摘要欄位（供前端顯示，不參與生成）：

```json
"derived": {
  "at": "2026-11-24T19:00:00+08:00",
  "promptChars": 3124,
  "promptSource": "compiled|manual|empty",
  "promptEnStale": false,
  "slots": 2,
  "pictures": [1, 2],
  "orphan": false,
  "uncoveredBeats": []
}
```

## 3. 分鏡增量補齊（D4 的處方）

`storyboard` 新增 `backfill_shots(project, scene_ids=None)`：

- 對每個場次，找出**沒有任何鏡頭引用**的 beat，用既有 `build_scene_shots()` 的同一套分組規則
  只為這些 beat 產生新鏡（`derivedFrom="storyboard"`、`fillReason="backfill"`）。
- **只增不刪、不改既有鏡頭的 `beatIds`**。
- 回傳 `{created: [shotId...], skipped: [beatId...]}`。
- 接在 `POST /api/projects/{slug}/storyboard/build`：預設先 backfill；`force=true` 才走舊的全重建
  （保留 `keep_manual` 語義）。

## 4. 孤兒鏡頭（D5 的處方，不自動刪）

- `derived.orphan = true` 的條件：`beatIds` 為空或全部指向不存在的 beat，且同場次有規則式鏡頭。
- 前端在該卡顯示徽章「⚠ 未連動劇本（與同場規則式鏡頭重複）」，並在場次標題列提供一顆
  「移除本場未連動的鏡頭（N）」按鈕：走既有刪除端點、`SVSConfirm` 二次確認、列出將刪的 shot id。
- **不得自動刪**（破壞性操作一律人手確認）。

## 5. 前端接線

1. `setView("shots" | "generate")`：若 `state.dirty` 先 `await saveNow()`，再 `GET /api/projects/{slug}`
   整份重抓（`applyLoadedProject` 之類的既有入口）→ `renderViews()`。
2. 生圖／生影片 job 完成：從「只補 `video` 欄位」改成**整份重抓**（避免同一個 job 寫回的
   referencePlan／promptEn 沒進畫面）。
3. 鏡頭卡的提示詞區塊：標頭顯示 `derived` 摘要——
   `已同步（3124 字／2 張圖）` 或 `⚠ 手動稿已落後`.
4. 場景／人物／劇本頁的輸入事件**不需要**改；它們已經寫在 `state.project` 上。

## 6. 驗收（機械斷言，缺一不算完成）

在**複本**資料上跑（`cp -r dist/StoryVideoStudio/projects/<slug> $TMPDIR/x`，
`SVS_DATA_DIR=<複本>/projects`，埠 8790；不得對 dist 正本寫）：

| # | 斷言 |
|---|---|
| T1 | 改 `characters[c01].appearance.face` → `GET /api/projects/{slug}`：所有引用 c01 且非手動稿的鏡頭，`promptEn` 內容改變且含新字串；`derived.promptChars` 同步變 |
| T2 | 從 `characters[c01].refImages` 移除一張 → 該角色相關鏡頭的 `referencePlan.slots` 不再含該 path（`pictures` 少 1） |
| T3 | 在 e01s03 第 2 句位置插入一個新 beat → `POST /storyboard/build`：`created` 非空、該場次 `uncoveredBeats` 變空、既有鏡頭 `beatIds` 一字不動、無重複引用 |
| T4 | 某鏡手動改 `promptEn` 並 `promptEnManual=true` → 改 beats 後 `save_project`，該鏡 `promptEn` 不變、`promptEnStale=true`；同場非手動鏡有更新 |
| T5 | 真實 28 鏡複本：`save_project` 一次後 `validate` error 數不增加（基準 0）、sh25–sh28 的 `derived.orphan` 為 true、其餘 24 鏡 false |
| T6 | 前端：8790＋複本，切到鏡頭頁，每張卡顯示的 prompt 長度 == `GET` 回的值；console 錯誤 0 |
| T7 | `python -m compileall app tools`、`node --check static/app.js` 全過 |
| T8 | 不退化：批次生影片 `_vb_precheck()` 對 28 鏡複本仍全部放行；`prompt-preview` 的字 == `h3gen.build_timeline()` 的 `segments[0].prompt`（逐字相等） |

## 7. 明確不做

- 不改 ComfyUI 工作流、不呼叫任何模型／ComfyUI（本輪零 GPU）。
- 不改既有欄位名、不刪既有 API；新增欄位一律「選用、缺了自動補」。
- 不自動刪任何鏡頭或媒體檔。
- 不 push、不發 Release、不動 `dist/StoryVideoStudio/projects`。
