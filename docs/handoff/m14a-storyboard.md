---
branch: m14a-storyboard
milestone: M14a
status: ready
opened: 2026-11-21 17:13（台北）
files: ["app/storyboard.py", "app/h3compose.py", "app/server.py", "app/storage.py", "app/deploy.py", "static/app.js", "static/style.css"]
---

# 交接單：m14a-storyboard

## 做了什麼

依 `docs/storyboard-contract.md` v1 建出規則式分鏡層 `app/storyboard.py`（劇本 beats → 一鏡一事件、長度吸附 H3 合法格 17k+5、不足用 `padSec` 補、超長拆 `beatPart`），新增 `POST /storyboard/build` 與 `GET /storyboard`，並把 `validate_project()` 補上契約 V1／V4／V5、V3 由 warn 改 error。實測 `project-20261120-172537` 的 18 個 beats 派生成 14 鏡（103.51 秒），V1–V5 全 0 error；鏡頭頁新增「🎬 由劇本自動分鏡」面板與分鏡表。

## 動到的檔案

- `app/storyboard.py`（新增，約 600 行）：`build_scene_shots()`／`build_project_storyboard()`／`storyboard_rows()`／`migrate_shot_fields()`／`group_beats()`／`legal_frames()`
- `app/h3compose.py`：新增 `_validate_storyboard_contract()`（V1／V4／V5），V3 由 warn 改 error
- `app/server.py`：新增 `GET /api/projects/{slug}/storyboard`、`POST /api/projects/{slug}/storyboard/build`
- `app/storage.py`：`save_project()` 與 `fill_project_defaults_notes()` 併入 `migrate_shot_fields()`（beats → legacyBeats）
- `app/deploy.py`：`_land_shots()` 加成時間長吸附（`_fit_frames`）、輸出 `beatIds`／`role`／`derivedFrom="deploy"`／`padSec`，不再寫 `shot.beats`
- `static/app.js`：`storyboardTable()`、`sbBuildPanel()`、`buildStoryboard()`、`loadStoryboardRows()`、`shotBeatsOf()`；鏡頭卡新增職責／beatIds／留白區塊與「吸附合法格」按鈕
- `static/style.css`：`.sb-*` 樣式（分鏡表、職責標籤、面板）
- `docs/shots/m14a-storyboard.png`：鏡頭頁實測截圖

## 介面變更

**新增 API**

- `GET /api/projects/{slug}/storyboard` → `{ok, slug, rows[], shotCount, totalSeconds, beatCount}`
  每列：`no / shotId / episodeSceneId / sceneTitle / role / roleLabel / derivedFrom / speaker / chars / lines[](beatId,speaker,text,type,emotion) / beatIds / hasLine / text / durationSec / padSec / frames / framesInRange / startSec / endSec / mergedCount / beatPart`
- `POST /api/projects/{slug}/storyboard/build`
  body：`{scope: "all"|"scene", sceneId?, force?: false, keepManual?: true}`
  回：`{ok, slug, created, replaced, kept, shots[], warnings[], skippedScenes[], totalSeconds, issues[], counts}`
  契約驗證出現**新增** error 時回 400 且**不寫檔**（原本就有的錯誤不擋）。

**新增／啟用欄位（shots[]）**：`beatIds`、`beatPart`、`role`、`derivedFrom`、`padSec`、`legacyBeats`（`shot.beats` 遷移目的地）。`dialogBeatIds` 由 `beatIds` 中的 dialogue beat 推導；`shot.dialogue` 保留不刪、不再當台詞來源。

**函式簽章**

- `storyboard.build_scene_shots(project, es_id, opts=None) -> dict`
- `storyboard.build_project_storyboard(project, scene_ids=None, force=False, keep_manual=True) -> dict`
- `storyboard.storyboard_rows(project) -> dict`
- `storyboard.migrate_shot_fields(project) -> list[str]`

## 未完成／已知限制

- **`role=dialogue` 可能一鏡兩 beat**：契約 L3 允許短句合併，本版合併時優先「併前一個」，所以第一場會出現「b01 動作＋b02 台詞」同鏡、`speaker` 只顯示有台詞者。要更細的一鏡一事件請回劇本層把句子寫長或自行拆鏡。
- **reaction 幾乎不會自動產生**：規則式分鏡只在場景有第三個角色時才可能判反應鏡；實測六種 role 只出現 establish／dialogue／action／insert／closing 五種。
- **合併鏡不支援 `beatPart`**：拆鏡只在「單一 beat 估時 > 15.08 秒」時發生，且靠標點切；沒有標點的長句只會壓到 15.08 秒並給 warning（不自動改台詞）。
- **V5 的 reaction 條件只驗「不含台詞」**：契約另寫「必須有 speakerId 的對手在場」，本版未強制檢查（避免誤判舊資料）。
- **`shot.dialogue` 只是停用、沒有清空**（契約要求舊資料不刪）；前端不再讀它。
- **前端分鏡表每次 `renderShots()` 都會打一次 `GET /storyboard`**（含輸入變更觸發的重繪），大專案請求數偏多；沒有節流。
- **只改 `static/app.js`**；`templates/`、`dist/`、`release/`、`launcher.py` 一律未動。

## 驗證方式

```bash
# 1) 複本＋起伺服器（不要用 8790）
mkdir -p C:/Users/user/AppData/Local/hermes/cache/scratch/m14/projects && cp -r "C:/Users/user/Desktop/hermes work/1117/dist/StoryVideoStudio/projects/project-20261120-172537" C:/Users/user/AppData/Local/hermes/cache/scratch/m14/projects/
cd "C:/Users/user/Desktop/hermes work/1117-wt-storyboard" && SVS_DATA_DIR=C:/Users/user/AppData/Local/hermes/cache/scratch/m14/projects "C:/Users/user/Desktop/hermes work/1117/.venv/Scripts/python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8792

# 2) 建分鏡（force=true 清掉舊產物；預設 keepManual=true 會保留 derivedFrom=manual 的鏡頭）
curl -s -X POST http://127.0.0.1:8792/api/projects/project-20261120-172537/storyboard/build -H "Content-Type: application/json" --data-binary '{"scope":"all","force":true}'

# 3) 分鏡表與校驗（期望 counts.error=0、14 鏡、總長 103.51s）
curl -s http://127.0.0.1:8792/api/projects/project-20261120-172537/storyboard
curl -s -X POST http://127.0.0.1:8792/api/projects/project-20261120-172537/validate

# 4) 故意漏 beat 必須擋：把某鏡 beatIds 刪一項寫回 project.json，再 validate → V1（＋可能 V2）error
# 5) 舊專案相容：GET /api/projects/project-20261118-022129、POST .../validate、GET .../storyboard 都不得 500
```

實測結果（2026-11-21 本機 8792）：build 回 `{"ok":true,"created":14,"replaced":20,"kept":0,"totalSeconds":103.51,"counts":{"error":0,"warn":44}}`；每鏡幀數 124–277 全為 17k+5 且在 124–362；beatIds 串接＝18 beats；`validate` 0 error；刪掉第一場首鏡的 `b01` 後出現 V1 error「第 1 個位置：鏡頭串接是 b02，劇本是 b01」。舊專案 172537 複本 GET／validate／storyboard 皆 200、0 error、6 鏡的 `beats` 全數遷移成 `legacyBeats`。截圖：`docs/shots/m14a-storyboard.png`。

## 是否動到契約（data-model.md / contracts.md）

沒有。本版只是在程式裡實作 `docs/storyboard-contract.md` v1；`docs/data-model.md` 與 `docs/contracts.md` 未修改。
