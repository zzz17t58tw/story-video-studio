# Story Video Studio（1117）

> **版本：v0.9.0**（預發版）　·　版號來源：`VERSION`　·　**版本紀錄見 [`CHANGELOG.md`](CHANGELOG.md)**
>
> 三個位置的分工、一鍵發佈流程與回滾方式見 [`docs/VERSIONING.md`](docs/VERSIONING.md)：
> 根目錄＝源碼｜`dist/StoryVideoStudio/`＝**你日常在用的 exe（資料在此，絕不清空 projects）**｜`release/`＝乾淨發佈包。
> 發佈新版只要跑 `python tools/release.py`。

故事 → 分鏡 → 影片 一體化製作程式。主線與里程碑見 `PLAN.md`。
**本輪＝M3a：模型對話基礎層（OpenAI 相容 client、chat / apply API、模型狀態）＋ 故事／人物／場景三頁的「✦ 與模型對話」抽屜。**（M2a 人物卡／場景卡、M2b 劇本／鏡頭已完成）

## 怎麼啟動

1. 雙擊 `start.bat`（會自動檢查 python、需要的話裝 uvicorn、起伺服器、2 秒後開瀏覽器）。
   或手動：
   ```
   cd "C:\Users\user\Desktop\hermes work\1117"
   python -m uvicorn app.server:app --host 127.0.0.1 --port 8788
   ```
2. 瀏覽器開 <http://127.0.0.1:8788>。終端會印「Story Video Studio 已啟動 http://127.0.0.1:8788」。
3. 停止：在終端按 `Ctrl+C`。

需求：Python 3.11 + FastAPI（本機已裝 fastapi 0.133.1 / uvicorn 0.41.0）。前端純靜態，**無 build、無 npm、無 CDN**，內網可跑。

## 資料放哪

```
projects/<slug>/project.json     ← 單一真相（UTF-8、縮排 2、camelCase，規格見 docs/data-model.md）
projects/<slug>/media/
    characters/ scenes/ shots/ clips/ voices/     ← 上傳的參考圖、音檔等
projects/_trash/                                  ← 刪除的專案移到這裡（不直接刪檔）
```

- `slug` 一律小寫英數字（中文專案名只存在 `meta.name`）；時間戳格式 `project-YYYYmmdd-HHMMSS`。
- 寫入一律「寫 .tmp → `os.replace`」原子替換，並更新 `meta.updatedAt`。
- 前端不直接寫檔，一律走 API。

## API（M1）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/health` | 健康檢查 |
| GET | `/api/projects` | 列表（slug、name、updatedAt、shotCount、coverImage） |
| POST | `/api/projects` | 建立（body：`{name, aspectRatio}`）→ 回 slug |
| GET | `/api/projects/{slug}` | 讀整份 project.json |
| PUT | `/api/projects/{slug}` | 整份寫回（驗證失敗回 **400 + 中文錯誤**，不會 500） |
| DELETE | `/api/projects/{slug}` | 移到 `projects/_trash/` |
| POST | `/api/projects/{slug}/upload?kind=characters\|scenes\|shots\|voices` | multipart 上傳，存 `media/<kind>/<時間戳>_<安全檔名>`，回 `{path, url}` |
| GET | `/files/<slug>/media/...` | 直接抓媒體檔給前端顯示 |

## M2a 已做（人物卡 + 場景卡 + 上傳 UI + ID 產生器）

前端沿用 M1 的深色風格與 1.5 秒 debounce 自動儲存；**資料只走既有的 `GET/PUT /api/projects/{slug}` 與 `POST /upload`，沒有新增 API、沒有改 `project.json` 格式、沒有改資料庫。**

### 「2 人物」分頁
- 人物列表卡：顯示每個人物的姓名、頭像縮圖、角色定位與參考圖張數；「＋ 新增人物」自動給下一個號（c01、c02…，掃現有最大號 +1，刪掉中間的號不會重用）。列表上方有膠囊鈕可直接跳到該張人物卡。
- 每張人物卡可免寫程式編輯**全部**欄位：`name`、`aliases`（一行一個）、`role`（主角／配角／反派／群眾／旁白）、
  `appearance`（age / gender / skin / face / hair / body / features 一行一條 / promptEn）、
  `outfits`（多筆，每筆 id + name + desc + promptEn，可增刪）、
  `background`（birth / personality / goal / secret / relations）、
  `voice`（desc / refAudio / engine / note）、`refImages`、`lockedFace`。
- **參考圖上傳**：`input type=file` 可多選 → `POST /api/projects/{slug}/upload?kind=characters` → 回傳的 `path` 直接加進 `refImages`。上傳前可先在「上傳類型」選 `face／full／outfit`，並勾「鎖臉」→ 這批圖會寫成 `{path, kind, locked:true}` 並把 `lockedFace` 設 true。縮圖可**點擊放大**（燈箱，Esc 關閉）、可用下拉改單張 kind、可按 ✕ 移除。
- **聲音參照音檔**：上傳走 `kind=voices`，寫進 `voice.refAudio`，附 `<audio controls>` 可直接試聽、可移除、可換一個。
- **「✦ 用外型欄位自動組英文 prompt」**：純字串拼接（age/gender/skin/face/hair/body/features + outfits + 鎖臉提示），**不呼叫任何模型 API**；組好後照樣走 1.5 秒自動儲存。要模型改寫是 M3 的事。

### 「3 場景」分頁
- 場景列表卡（名稱、縮圖、自動編號 sc01、sc02…、可新增／刪除）＋膠囊跳轉。
- 每張場景卡可編輯 `name`、`desc`、`timeOfDay`（清晨／白天／黃昏／夜晚／深夜）、`lighting`、`mood`、`promptEn`；
  `ambience`（note + refAudio，可播放試聽）；`refImages` 多張上傳（kind=`wide／detail`，可改、可刪、可點擊放大）。

### ID 產生器與引用檢查
- 新增人物自動 `c01/c02…`，新增場景 `sc01/sc02…`：前端 `nextId()` 掃現有同前綴的最大號 +1（後端 `storage.next_id()` 同規則，`PUT` 回應附帶 `nextIds` 供多分頁時核對）。ID 建立後不可在 UI 修改。
- 後端 `validate_project()` 補上人物／場景 **id 格式與唯一性**檢查（`c01`、`sc01` 這種形式），重複或格式錯 → **400 + 中文錯誤**，不會寫壞檔。
- **刪人物／場景前先檢查引用**：被 `shots[].characterIds`／`shots[].dialogue[].characterId`／`script.episodes[].scenes[].characterIds`／`scenes[].beats[].characterId`／`shots[].sceneId`／`script.episodes[].scenes[].sceneId` 引用時，會彈出**頁內確認框並逐條列出引用處**（例如「鏡頭 sh01（第 1 鏡）出場人物」）。
  - 使用者按「仍要刪除」→ 這些引用會被清成 `null`（人物陣列中的 id 則從陣列移除），**鏡頭與劇本本身不會被刪**；按「取消」則完全不動。
  - 沒有引用時，確認框只顯示「目前沒有任何引用」。

### 其他
- 參考圖放大用自寫的小燈箱（`#lightbox`，無外部套件）；刪除確認用自寫的頁內對話框（`#modal-confirm`），因為原生 `confirm()` 無法列出引用明細。
- 上傳後只改記憶體資料再走自動儲存；**移除參考圖只從卡片移除，實體檔案仍留在 `media/`**（避免誤刪共用圖）。

### 驗收截圖
- `docs/shots/m2a-characters.png`：人物卡參考圖區（kind 下拉、鎖臉勾選、縮圖與 ✕）
- `docs/shots/m2a-scenes.png`：場景卡與參考圖
- `docs/shots/m2a-delete-warning.png`：刪人物時的引用警告框

## M2b 已做（劇本分頁 + 鏡頭分頁）

前端沿用 M2a 的深色風格與 1.5 秒 debounce 自動儲存；**沒有新增 API、沒有改 `project.json` 格式、沒有引入任何套件／CDN，完全沒有呼叫模型 API（M2b 不需要）。**
所有下拉（人物、場景、場次）都**即時掃同一份 project 生成**，沒有寫死選項；改人物／場景名之後兩頁立刻跟著變。

### 「4 劇本」分頁（`script.episodes[].scenes[].beats[]`）
- **集（episodes）**：可新增（自動編 `e01`、`e02`…）、刪除、改 `title`；集卡標題列顯示「N 個場次 · M 個 beats」。
- **場次（scenes）**：新增（自動編 `eXXsYY`，如 `e02s01`）、刪除、上下移；欄位 `title`、`sceneId`（**下拉選「3 場景」的真實場景卡**）、`characterIds`（**依「2 人物」的人物卡勾選**）、`summary`。
  場次下方有一行「場景：霧中森林（sc01）　人物：小紅帽」把代號翻成人名／場景名。
- **beats**：對白／動作分開加（`＋ 對白`／`＋ 動作`），可上下移、刪除；對白有角色下拉（來源人物卡）、`line`、`emotion`、`notes`；動作的 `line` 標成「動作描述」。beat id 在場次內自動編 `b01`、`b02`…
- 刪場次／及時，會提示有幾個 beat、幾個鏡頭的「所屬場次」會被清空（**鏡頭本身不會被刪**，只把 `episodeSceneId` 設 null）。
- 改角色下拉時只就地更新「顯示：某某」那一行，不會整頁重畫（避免打字時失焦）。

### 「5 鏡頭」分頁（`shots[]`）
- 鏡頭以卡片式分鏡表呈現，**依場次分組**（「e01／霧林相遇（e01s01） · 4 鏡」）。
- 可編輯：`no`、`episodeSceneId`（**所屬場次下拉**）、`characterIds`（**角色多選勾選**）、
  `spec`（`shotType` 特寫／中景／全景／過肩／POV／其他、`angle`、`cameraMove`、`composition`、`lighting`）、`durationSec`（預設 5）、
  `promptZh`、`promptEn`（多行，旁邊一行小字提示 H3 六段式寫法）、`dialogue`（可多筆，角色下拉＋台詞）、`video.note`。
- **首尾幀上傳**：`images.first` / `images.last`，走 `POST /api/projects/{slug}/upload?kind=shots`；顯示縮圖、可點放大（燈箱）、可 ✕ 移除（檔案仍留在 `media/`）。
- **影片欄位只顯示狀態徽章**（`none` 未生成／`queued` 排隊中／`running` 生成中／`done` 已完成／`failed` 失敗）＋ `video.note` 備註，附 `status=` / `taskType=` 原始值；**不生影片**（M4／M5 才做）。
- 新增（自動 `sh01`…）、刪除、上下移；**`no` 一律依陣列順序自動重排成 1..N（不動 `id`）**；可**依場次篩選**。
- **「⊞ 拆成鏡頭草稿」**：選一個場次，把它的 beats **按順序每 beat 一鏡** —— 對白帶入 `dialogue`，場景與人物繼承該場次，`promptZh` 用場次標題＋角色名＋台詞／動作組一句（純字串拼接）；**純規則、不呼叫模型**，新鏡頭插在「同場次最後一鏡之後」讓分組保持連續，**已存在的鏡頭不覆蓋、不刪除**。
- 「✦ 用欄位組英文草稿」：純字串拼接（主體／動作／運鏡／場景／光線），同樣不呼叫模型，事後可手改。

### 引用顯示與安全
- 代號一律翻成人名／場景名／場次名；引用失效時顯示「（已刪除的人物 c0X）」「（引用已失效：…）」，不會出現裸 id。
- 在「2 人物」刪掉被引用的角色，原本的引用檢查對話框照常列出明細，確認後清空引用；劇本頁與鏡頭頁重繪不報錯（顯示「（人物已刪除：c0X）」）。

### 驗收（本輪實際跑出）
- `curl PUT /api/projects/project-20261117-221600`（1 集、2 場次各引用真實 `sc01`/`sc02`/`c01`/`c02`、每場次 2 beat、3 個鏡頭其中 sh01 有 `images.first`）→ **200**；`GET` 讀回 `script` 與 `shots` 與送出內容 **deep-equal = True**。
- `curl POST .../upload?kind=shots`（PNG）→ **200**，回 `media/shots/20261117-221603_m2b_first.png`；`GET /files/...` → **200**。
- 瀏覽器：劇本頁新增 e02→場次 e02s01→對白 beat，選場景 sc02、勾人物 c01、打台詞 → 右上「已儲存」→ F5 後資料仍在（UI 與 API 一致）。
- 鏡頭頁「拆成鏡頭草稿」e01s01 → 新增 2 鏡，`no` 連號 1–5、id 不重複、分組連續；尾幀上傳 → 寫入 API；上移後 `no` 重排正確；篩選 e01s02 只顯示 sh03。
- 刪人物 c02 → 引用明細列出 8 處 → 確認後劇本／鏡頭頁顯示「（人物已刪除：c02）」引用清成 null，無錯誤。
- `node --check static/app.js` → exit 0；`python -m compileall -q app` → exit 0。
- 截圖：`docs/shots/m2b-script.png`（場次＋beats）、`docs/shots/m2b-shots.png`（工具列、分組、首幀縮圖、狀態徽章）。

### M2b 沒做（照計畫留給後續）
- 鏡頭的生圖／生影片／合併（M4／M5／M6）；與模型對話改寫（M3）。本輪未碰 ComfyUI、未改 `contracts.md`／`data-model.md`／`PLAN.md`。

## M3a 已做（模型對話基礎層 + 三頁「與模型對話」抽屜）

**原則：模型只回文字；寫進專案一定由使用者按「套用」→ `POST /chat/apply`，前端不會自動寫人資料。**
沒有引入新套件（用標準庫 `urllib.request`）、沒有前端框架、沒有串流。

### 後端
- `app/llm.py`：OpenAI 相容 `POST {baseUrl}/chat/completions`（非串流）
  - baseUrl 預設 `http://127.0.0.1:8080/v1`，可由 `settings.llm.baseUrl` 覆寫；只填 `http://127.0.0.1:8080` 會自動補 `/v1`。
  - `model` 留空或 `auto` → 先 `GET /v1/models` 取第一個 id（回傳實際使用的模型名）。
  - 連線/TCP 超時 5 秒、整體讀取超時 180 秒；任何失敗都轉成**中文**訊息（例：「連不上模型服務（127.0.0.1:8080）：請確認本機 LLM 已啟動…」），API 回 502 而不是 500 traceback。
  - 支援 `temperature` / `max_tokens`。
- `app/prompts.py`：組 system prompt＝【專案資料】（名稱、比例、fps、概念、風格、現有人物／場景）＋【本次對象】（該卡的完整 JSON）＋【本次任務】（9 個任務規則：story_outline / story_chapters / story_style / character_look / character_outfit / character_background / character_voice / scene_expand / scene_prompt_en）＋【通則】（繁體中文、只回本體、不要 markdown、不宣稱已寫檔）。
- `app/storage.py`：`llm_settings()`、`find_target(kind,id)`、`set_path()`、`apply_to_target()`。`set_path` 的防護：只接受英數欄位名／數字索引、最多 4 層、不接受覆寫 `id`／`path`／`refAudio`、目標是物件或陣列時拒絕、失敗會拆掉中途新建的空殼；寫入走「讀-改-寫 + `validate_project` + 原子替換」，並更新 `meta.updatedAt`。

### API
| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/llm/status[?slug=&baseUrl=&model=]` | 回 `{ok, baseUrl, model, models[], error?, provider}`；連不上也是 **200 + ok:false**（前端才能畫紅點與橫幅） |
| POST | `/api/projects/{slug}/chat` | body `{messages, task?, target?{kind,id}, temperature?, maxTokens?, model?}` → `{reply, model, usage, finishReason, systemChars}`；**只回文字，不寫檔** |
| POST | `/api/projects/{slug}/chat/apply` | body `{target{kind,id}, path:"appearance.promptEn", value, mode:"replace"\|"append"}` → 寫入 project.json，回 `{value, oldValue, updatedAt, targetAfter, label}`；路徑不合法 → 400 中文 |

`target.kind` 支援 `story`（不需 id）、`character`（`c01`）、`scene`（`sc01`）。

### 前端（故事／人物／場景三頁）
- 每張卡（故事章回卡、每張人物卡、每張場景卡）標題列右上有「**✦ 與模型對話**」→ 右側抽屜（≥1100px 時主畫面自動縮排、不遮擋；窄視窗時全寬覆蓋，Esc 關閉）。
- 抽屜：模型狀態（綠點＝已連線＋模型名／紅點＝未連線＋位址）、**紅色橫幅**列後端錯誤＋「如何啟動本機模型」4 步＋「重新檢查」；預設任務 chip（點了把中文提示詞填進輸入框，可先改再送）；對話氣泡；輸入框 **Ctrl+Enter 送出**；模型回覆下有「複製」與「套用到…」（欄位下拉＋取代／附加＋套用）。
- 套用後：後端回傳整張卡覆蓋回前端記憶體、畫面即時更新、狀態列顯示「已套用到 人物 c01 的 appearance.promptEn · 22:25:28」，且不會再觸發一次整份 PUT。
- 「設定 / 原始資料」加了「模型服務（settings.llm）」卡：baseUrl／model／temperature 可直接改（自動儲存）＋「檢查模型」按鈕。

### 附帶：靜態檔 no-cache
`app/server.py` 的 `NoCacheStaticFiles`（掛 `/` 那支）對所有靜態回應加 `Cache-Control: no-store, must-revalidate` 與 `Pragma: no-cache`；`/files` 的媒體檔仍可快取。**改 `static/*.js`／`*.css` 後按 F5 就生效，不必重啟 uvicorn**（實測：改字串 → F5 → 新字串出現，伺服器 PID 未變）。

### 驗收（本輪實際跑出）
- `GET /api/llm/status?slug=project-20261117-221919`（專案指向真實 8080、服務未開）→ **HTTP 200** `{"ok":false,…,"error":"連不上模型服務（127.0.0.1:8080）：請確認本機 LLM 已啟動…"}`（2.0 秒回）。
- `GET /api/llm/status?baseUrl=http://127.0.0.1:8999/v1`（假 OpenAI 相容伺服器 `tools/mock_llm.py`）→ **HTTP 200** `{"ok":true,"baseUrl":"http://127.0.0.1:8999/v1","model":"mock-strata-7b","models":["mock-strata-7b","mock-strata-70b"]}`。
- `POST /chat`（`task=story_outline`、`target={kind:story}`、`model:auto`）→ **HTTP 200**，`systemChars=828`、`usage.total_tokens=1171`；回覆前 200 字：「霧季第三天的清晨，小紅帽提著裝滿麵包的籃子，離開村口唯一一條通往森林的路。她從小就聽大人說，霧裡住著「替人保管秘密」的東西……」
- `POST /chat/apply`（`character c01` / `appearance.promptEn` / replace）→ **HTTP 200**，`GET /api/projects/...` 與磁碟 project.json 都確認寫入、`meta.updatedAt` 更新；錯誤路徑（寫 `id`、不存在的人物、`mode=merge`、空 value、`style..genre`、寫到物件 `ambience`）全部 **400 + 中文**。
- 瀏覽器：故事頁「① 擴寫成完整大綱」→ 送出 → 回覆出現 → 「套用到…故事大綱」→ 狀態列「已套用到 故事 的 synopsis」→ 左側大綱欄位即時更新；人物／場景抽屜的任務 chip 與套用欄位各自正確；把 baseUrl 改回 8080 後抽屜顯示紅點＋紅色橫幅，送訊息得到「⚠ 模型回覆失敗：連不上模型服務…」，畫面不白屏。
- `node --check static/app.js` → exit 0；`python -m compileall -q app` → exit 0。
- 截圖：`docs/shots/m3a-chat.png`（故事頁抽屜＋套用後）、`docs/shots/m3a-chat-offline.png`（模型未連線的紅色橫幅）。

### M3a 沒做（留給後續）
- 串流輸出、對話歷史持久化（重新整理就清空）、劇本／鏡頭頁的「與模型對話」（只做故事／人物／場景）、把回覆直接寫成章回陣列（目前章回是逐欄套用）。
- 假模型伺服器 `tools/mock_llm.py` 只是驗證成功路徑用的開發工具，正式執行不會用到。

## M1 已做
- `app/storage.py`：`list_projects / create_project / load_project / save_project / delete_project / save_upload`；slug 防路徑穿越（只允許 `[a-z0-9_-]`）；頂層 key 齊全 + id 引用（sceneId / characterId / episodeSceneId / no 唯一）驗證；原子寫檔。
- `app/server.py`：FastAPI，`static/` 掛 `/`、`projects/` 掛 `/files`；上述 API；啟動橫幅。
- `static/`：左側專案列表＋六大區塊導覽、右側內容區，深色繁體中文介面。
  - **真的能用**：「故事」（premise／synopsis／style 四欄／章回新增、刪除、改標題與正文、上下移）、「設定/原始資料」（project.json 唯讀預覽＋下載備份、name／比例／fps）。
  - 任何欄位變更 → 1.5 秒 debounce 自動 PUT；右上角顯示「儲存中…／已儲存 hh:mm:ss」，失敗轉紅顯示錯誤。
  - 新專案彈窗（名稱＋比例 9:16／16:9／1:1）。
- `start.bat`（CRLF，雙擊即用）。

## M1 未做（照 PLAN.md 留给后续里程碑）

- 「生成與合併」分頁：目前仍是 M4／M5／M6 佔位卡。（故事 M1、人物與場景 M2a、劇本與鏡頭 M2b 已實作）
- 與模型對話（M3）、生圖（M4）、生影片（M5）、ffmpeg 合併（M6）、打包與設定頁（M7）。
- M2a **未碰 ComfyUI**、未呼叫任何模型 API，也未改 `docs/contracts.md`、`docs/data-model.md`、`PLAN.md`。

## 現成範例專案

`projects/` 內留有實測用的兩個專案（可直接開來看介面與自動儲存）：

- **「M1 煙霧測試」** `project-20261117-070132`：story 欄位有實測文字、1 個章回、1 個人物（小雞）、1 個場景、1 個鏡頭（含合法引用）、1 張 64×64 測試圖在 `media/characters/`。
- **「第二個測試」** `project-20261117-070430`：空白專案，測列表用。

不要的話從左側 ✕ 刪除（會進 `projects/_trash/`，不直接刪檔）。

## 改程式後要注意

**M3a 起不需要這樣做了**：`server.py` 的 `NoCacheStaticFiles` 已對 `/` 靜態檔加 `Cache-Control: no-store`，改完 `static/*.js`／`*.css` 直接按 **F5** 就會生效（不必重啟 uvicorn、也不必 Ctrl+F5）。若哪天又看到舊檔，先確認瀏覽器 DevTools → Network 的 `Cache-Control` 是否為 `no-store`。

