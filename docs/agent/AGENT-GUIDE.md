# Story Video Studio — Agent 操作手冊

版本 `0.9.22`｜文件版本 `1.0`｜端點 59 支（已說明 59）

## 連線與版本

- 使用端 exe：`http://127.0.0.1:8788`；開發模式：`http://127.0.0.1:8790`
- 開工前先 `GET /api/health`，確認 `{ok:true}` 且 `version` 與你要操作的功能相符
- 精確欄位型別去 `GET /openapi.json` 的 `components.schemas`；本手冊只講語意與順序

## 這是什麼

一本機工具，把「故事」演成影片。資料是單一事實來源的樹狀結構，**順序不能亂**：

1. meta / story（大綱、章回、風格）
2. characters（外型、身分、服裝、音色）
3. scenes（地點、光線、氛圍）
4. script.episodes[].scenes[].beats（誰、說什麼、做什麼）← 唯一真相
5. shots（分鏡：引用 beats、決定鏡位與長度）
6. shots[].images（首幀）→ shots[].video（片段）→ assemble（成片）

程式只做三件事：**寫資料 → 驗證 → 送生成**。所有寫入都要走 API（後端才有驗證與原子寫檔）。

## 硬限制（違反必壞，先讀這段）

| 限制 | 規則 | 意義 |
|---|---|---|
| h3_frames | H3 單鏡影格必須落在 17k+5 且 124–362 幀（fps 24） | 單鏡片段合法長度 = 5.17–15.08 秒；想更短只能生成後剪，想更長必須拆鏡 |
| h3_total_frames | 送件的 total_frames 需對齊 17k+1 | 程式內部 h3gen 會自己換算，外部只要給 durationSec |
| aspect_ratio | aspectRatio 只收 9:16 / 3:4 / 4:3 / 1:1 / 16:9 / 21:9 / 自定义 | 舊值「16:9(横屏)」會被正規化成預設值，等於沒指定 |
| shot_type | spec.shotType ∈ close_up / medium / wide / over_shoulder / pov | 含人物的鏡頭不得用 wide（取景規則），程式會報 error |
| camera_move | spec.cameraMove ∈ static / push_in / pull_out / pan_left / pan_right / tracking / handheld | 不在清單內的值會讓驗證報 error |
| prompt_rules | H3 用六段式（1. Subject / 2. Action / 3. Camera / 4. Lighting / 5. Atmosphere / 6. Audio）；MOSS 禁 quality tag 與編號式開頭 | 送件口會硬驗；不合格會自動改用模板編譯稿或直接拒送 |
| beats_truth | 劇本 beats 是唯一真相；分鏡只能挑選/分組，不得改寫台詞 | 要改台詞回劇本層改，改分鏡的台詞會被驗證擋下 |
| gpu_serial | 本機只有一張 GPU：生圖/生影片/本地 LLM 不能同時跑 | 要跑生成前先卸載本地 LLM；批次任務是「依序」不是並行 |

## 資料模型（骨架）

| 路徑 | 主要欄位 | 備註 |
|---|---|---|
| `meta` | id, title, createdAt, updatedAt, fps(預設 24), version | 所有寫入都要更新 updatedAt |
| `story` | synopsis, premise, style{genre,tone,visualStyle,negative}, chapters[] | chapters[]: {id, title, content} |
| `characters[]` | id, name, aliases[], appearance{age,gender,face,hair,body,skin,promptEn}, voice{desc}, outfits[], identityAnchors{}, stages[] | identityAnchors 是「逐字貼進 prompt」的鎖臉錨點，不准改寫 |
| `scenes[]` | id, name, timeOfDay, lighting, mood, promptEn, props[] | 場景卡（地點），不含對白 |
| `script.episodes[].scenes[]` | id(e01s01), title, sceneId, characterIds[], summary, beats[] | beats[] = {id(b01), type(dialogue|action), characterId, line, emotion, notes}；這是戲的唯一真相 |
| `shots[]` | id, no, episodeSceneId, beatIds[], role, derivedFrom, padSec, durationSec, characterIds[], spec{}, promptZh, promptEn, referencePlan{}, images{first,last}, video{}, beats(停用), dialogue(停用), legacyBeats | beatIds 是主鍵；role ∈ establish/dialogue/reaction/action/insert/closing |
| `settings` | llm{}, imageEngine, video{}, comfy{}, tts{} | apiKey 不寫進 project.json |
| `pipeline` | stage, logs[] | 流程紀錄 |

## 驗證規則（`POST /api/projects/{slug}/validate`）

| 規則 | 內容 | 等級 | 怎麼修 |
|---|---|---|---|
| V1 | 場次內 shots（依 no）的 beatIds 串接 == 該場次 beats（排除 _skipped） | error | POST /storyboard/build 重建，或修 shots[].beatIds |
| V2 | 每個 dialogue beat 剛好被某一鏡引用一次 | error | 同上；漏句時錯誤訊息會指出「第幾句」 |
| V3 | durationSec 吸附後必須落在 124–362 幀；只是沒對齊則為 warn（送件時自動吸附） | error|warn | 調整 durationSec 到 5.17–15.08 秒之間 |
| V4 | durationSec − padSec ≥ 台詞字數 / 4.5（台詞要唸得完） | error | 加長鏡頭或縮短台詞 |
| V5 | role 與 beat 類型一致（dialogue 必含台詞；establish 只能是首鏡且無台詞；reaction 不含台詞） | error | 改 role 或改 beatIds |
| write_guard | 任何寫入只要「新增」上述 error 就整批不寫（400） | error | 先修內容再寫；舊專案既有的 error 不會擋你改字 |

## 常見工作流

### 從零到一支成片（`zero_to_film`）

1. POST /api/projects 建專案（或 GET /api/projects 挑一個）
2. 故事層：POST /api/projects/{slug}/chat（target.kind=story）取得文字 → POST /chat/apply-batch 寫入 synopsis/chapters/style
3. 人物層：POST /chat（kind=character）→ /chat/apply-batch 寫入 appearance/outfits/voice
4. 場景層：POST /chat（kind=scene）→ /chat/apply-batch 寫 desc/promptEn
5. 劇本層：POST /generate/script（dryRun=true 先看）→ POST /apply/bulk 寫入 episodes/scenes/beats
6. 分鏡層：POST /storyboard/build（scope=all）→ GET /storyboard 檢查台詞是否逐字、每鏡 5.17–15.08 秒
7. 鎖臉圖：POST /generate/characters（或 /characters/{cid}/generate/sheet）
8. 首幀：POST /preflight/image 檢查 → POST /generate/images（批次）
9. 影格提示詞：POST /shots/{shot_id}/compile-prompt → GET /shots/{shot_id}/prompt-preview 確認真的要送出的字
10. 影片：POST /generate/videos（批次，背景）→ 輪詢 GET /generate/videos/{job_id}
11. 檢查 POST /validate（0 error）→ 合併 GET /assemble/plan 看缺什麼 → POST /assemble

### 只改一句台詞，並讓引用它的鏡頭跟上（`edit_one_line`）

1. POST /chat/apply-batch，target.kind=beat（id=b02, parentId=e01s01），items=[{path:'line', value:'新台詞'}]
2. 引用它的鏡頭不用改：台詞是從 beat 即時讀取的（單一真相）
3. POST /validate 確認 0 error（V4 可能因新台詞變長而報「唸不完」，那就把 durationSec 調長）

### 劇本大改後重建分鏡（`rebuild_storyboard`）

1. POST /storyboard/build，body {scope:'all', force:false, keepManual:true}
2. keepManual=true 會保留你手動調過的鏡頭（derivedFrom=manual），其餘依 beats 重建
3. GET /storyboard 檢查鏡號／台詞／秒數；要覆蓋手動鏡頭再 force=true

### 只重生一顆鏡頭（影像或影片）（`regen_one_shot`）

1. GET /shots/{shot_id}/prompt-preview 確認要送出的字（engine=h3_video|moss_t2i|qwen_image）
2. 影像：POST /shots/{shot_id}/generate/image（同步，最多等 180 秒）
3. 影片：POST /shots/{shot_id}/generate/video（背景）→ 輪詢 GET /generate/video/{job_id}

### 顯示卡記憶體不夠時（`gpu_memory`）

1. GET /api/gpu/status 看誰佔用（llama / comfy / 其他）
2. POST /api/gpu/stop-llama（停本地 LLM）→ 需要時再 POST /api/gpu/start-llama
3. POST /api/gpu/release-comfy（叫 ComfyUI 卸載模型）
4. 生成完再啟動 LLM：順序反了會卡在排隊

### 把鏡頭合成成片（`assemble_final`）

1. POST /validate（0 error 才往下）
2. GET /assemble/plan → 看缺哪幾鏡、參數是否一致、建議模式與總時長
3. POST /assemble → 回 final 路徑／時長／大小；成品清單用 GET /outputs

## 禁止事項

- 不要直接改寫 projects/<slug>/project.json：一律走 API，後端才有驗證與原子寫檔
- 不要改寫 beats 的台詞來「讓分鏡更順」——台詞是使用者的創作，改它等於改劇本
- 不要為了湊到 H3 最短 5.17 秒而在 prompt 裡加台詞或新增動作；只能留白（padSec）或合併相鄰 beat
- 不要在生成任務執行中同時跑本地 LLM 或另一個生成批次（單張 GPU，會互搶）
- 不要把 apiKey 寫進 project.json 或回給使用者
- 不要批次平行送生成任務：本程式是刻意「依序」執行以避免 OOM
- 不要刪除 shots 而不重建：V1/V2 會立刻報「台詞沒被覆蓋」
- 不要把 promptEn 的 <Picture N> 自己編：槽位由 referencePlan 產生

## API 索引

| 方法 | 路徑 | 用途 | 何時用 | 副作用 |
|---|---|---|---|---|
| GET | `/api/agent/coverage` | Api Agent Coverage |  |  |
| GET | `/api/agent/guide` | Api Agent Guide |  |  |
| GET | `/api/agent/manifest` | Api Agent Manifest |  |  |
| GET | `/api/comfy/status` | ComfyUI 連線與可用裝置狀態 | 生成前確認後端在跑 | 無 |
| POST | `/api/gpu/release-comfy` | 請 ComfyUI 卸載模型、釋放 VRAM | 要跑別的吃 VRAM 的東西前 | 無資料變更 |
| GET | `/api/gpu/settings` | Api Gpu Settings |  |  |
| PUT | `/api/gpu/settings` | Api Put Gpu Settings |  |  |
| POST | `/api/gpu/start-llama` | 重新啟動本地 LLM | 生成跑完、要回對話功能 | 佔用 VRAM |
| GET | `/api/gpu/status` | Api Gpu Status |  |  |
| POST | `/api/gpu/stop-llama` | 停掉本地 LLM 以釋放 VRAM | 要生圖／生影片 | 服務中斷（可再 start） |
| GET | `/api/health` | 存活與版本檢查 | 接手／自動化開始前先打一次 | 無 |
| GET | `/api/llm/status` | LLM 供應商與模型清單 | 確認要用哪個模型 | 無 |
| POST | `/api/llm/test` | 測 LLM 連線並列出可用模型 | 設定頁按「重新檢查」 | 無 |
| GET | `/api/projects` | 列出所有專案（含 slug、標題、進度摘要） | 想知道有哪些專案可操作 | 無 |
| POST | `/api/projects` | 新建專案（可用 fromTemplate 複製結構） | 從零開始時的第一步 | 建立 projects/<slug>/ 目錄 |
| DELETE | `/api/projects/{slug}` | 刪除專案（含媒體） | 確定不要了才用 | 不可回復，需使用者明確同意 |
| GET | `/api/projects/{slug}` | 讀整份 project.json | 需要看目前資料全貌 | 無 |
| PUT | `/api/projects/{slug}` | 整份覆寫專案（少用；優先走 apply-batch） | 只有匯入／還原時才用 | 整檔覆寫，風險高 |
| POST | `/api/projects/{slug}/apply/bulk` | 把確認過的預覽一次寫入（append 會重編 id/no） | generate/script|shots 的 dryRun 預覽確認後 | 寫檔 |
| POST | `/api/projects/{slug}/assemble` | 用 ffmpeg 合併成片 | plan 說可以合了 | 產出 output/*.mp4 |
| GET | `/api/projects/{slug}/assemble/plan` | 合併前檢查（缺片／參數不一致／建議模式／總時長） | 合併前必看 | 無 |
| GET | `/api/projects/{slug}/characters/{cid}/caption/moss` | Api Preview Moss Character Caption |  |  |
| POST | `/api/projects/{slug}/characters/{cid}/generate/sheet` | Api Generate Character Sheet Angles |  |  |
| GET | `/api/projects/{slug}/characters/{cid}/generate/sheet/{job_id}` | Api Generate Character Sheet Angles Status |  |  |
| POST | `/api/projects/{slug}/chat` | 與模型對話（回文字，不改資料） | 要草稿：大綱／章回／外型／台詞變體 | 無寫檔（只回 reply） |
| POST | `/api/projects/{slug}/chat/apply` | 把一段文字寫進單一欄位 | 只有一個欄位要改 | 寫檔（受契約守門） |
| POST | `/api/projects/{slug}/chat/apply-batch` | 一次寫多個欄位（含整份 chapters/beats/dialogue/outfits） | 回填模型輸出、批次改資料 | 原子寫檔；新增契約 error → 整批不寫 |
| POST | `/api/projects/{slug}/deploy` | 一鍵佈署（**分範圍**）：body 的 scope=cast_locations（角色＋場景）／script（用現有卡片寫一集）／shots（為最新一集拆鏡頭）／all（四層全跑）；每次只呼叫該範圍需要的模型次數 | 內容還很空、想快速成型；建議照 角色與場景 → 劇本 → 分鏡 的順序分別按 | 背景 job；scope=script/shots 只清自己那層（script 會連鏡頭一起清），cast_locations 覆蓋時四層全清；先看 deploy/info |
| GET | `/api/projects/{slug}/deploy/info` | 一鍵佈署前的情境資訊（已有多少內容、append 會怎麼編號） | 按一鍵佈署前 | 無 |
| GET | `/api/projects/{slug}/deploy/latest` | Api Deploy Latest |  |  |
| GET | `/api/projects/{slug}/deploy/{job_id}` | Api Deploy Status |  |  |
| POST | `/api/projects/{slug}/deploy/{job_id}/cancel` | Api Deploy Cancel |  |  |
| POST | `/api/projects/{slug}/generate/characters` | Api Generate Characters |  |  |
| POST | `/api/projects/{slug}/generate/images` | 批次生首幀（依序、每鏡完成即寫回） | 要一次把整場首幀生完 | 背景 job，寫 shots[].images |
| GET | `/api/projects/{slug}/generate/images/latest` | Api Generate Images Latest |  |  |
| GET | `/api/projects/{slug}/generate/images/{job_id}` | Api Generate Images Status |  |  |
| POST | `/api/projects/{slug}/generate/script` | 故事 → 整集劇本（episode/scenes/beats） | 劇本層還是空的 | dryRun=true 只預覽、不寫檔 |
| POST | `/api/projects/{slug}/generate/shots` | 某場次 → 多個鏡頭（LLM 分鏡，derivedFrom=deploy） | 想要模型給創意分鏡（規則式請用 storyboard/build） | dryRun=true 只預覽 |
| GET | `/api/projects/{slug}/generate/video/latest` | Api Generate Video Latest |  |  |
| GET | `/api/projects/{slug}/generate/video/{job_id}` | Api Generate Video Status |  |  |
| POST | `/api/projects/{slug}/generate/videos` | 批次生影片（依序，失敗不中斷） | 首幀都好了、要出片 | 背景 job，寫 shots[].video |
| GET | `/api/projects/{slug}/generate/videos/latest` | Api Generate Videos Latest |  |  |
| GET | `/api/projects/{slug}/generate/videos/{job_id}` | Api Generate Videos Status |  |  |
| POST | `/api/projects/{slug}/generate/videos/{job_id}/cancel` | Api Cancel Videos Batch |  |  |
| GET | `/api/projects/{slug}/image/engine` | Api Get Image Engine |  |  |
| PUT | `/api/projects/{slug}/image/engine` | Api Put Image Engine |  |  |
| PUT | `/api/projects/{slug}/llm/settings` | 存 LLM 供應商設定（apiKey 不寫進 project.json） | 換模型／換供應商 | 寫 settings |
| GET | `/api/projects/{slug}/outputs` | 成品清單（新到舊） | 找上次合併的檔 | 無 |
| POST | `/api/projects/{slug}/preflight/image` | 生圖前檢查（缺鎖臉圖／槽位不符／沒提示詞） | 批次生圖前 | 無（不碰 GPU） |
| POST | `/api/projects/{slug}/shots/{shot_id}/build-reference-plan` | 產生 referencePlan 槽位（誰是 <Picture 1>） | 參考圖有變動、或 promptEn 的 <Picture N> 對不上 | 寫 shots[].referencePlan |
| GET | `/api/projects/{slug}/shots/{shot_id}/caption/moss` | Api Preview Moss Caption |  |  |
| POST | `/api/projects/{slug}/shots/{shot_id}/compile-prompt` | 用模板編譯這一鏡的六段式 promptEn（不呼叫模型） | promptEn 是空的或是舊稿 | 寫 shots[].promptEn |
| POST | `/api/projects/{slug}/shots/{shot_id}/generate/image` | Api Generate Shot Image |  |  |
| POST | `/api/projects/{slug}/shots/{shot_id}/generate/video` | Api Generate Shot Video |  |  |
| GET | `/api/projects/{slug}/shots/{shot_id}/prompt-preview` | Api Prompt Preview |  |  |
| GET | `/api/projects/{slug}/storyboard` | 分鏡表（唯讀，含台詞逐字與累計秒數） | 檢查「這部片在演什麼」 | 無 |
| POST | `/api/projects/{slug}/storyboard/build` | 依劇本 beats 規則式重建分鏡（一 beat 一鏡，長度吸附 H3 合法格） | 劇本改完／第一次分鏡 | 重建 shots；keepManual=true 保留手動鏡頭 |
| POST | `/api/projects/{slug}/upload` | 上傳參考圖／素材到專案 media/ | 要提供鎖臉圖、場景圖、音檔 | 寫入 media/ |
| POST | `/api/projects/{slug}/validate` | 跑 V1–V5 與格式檢查，回 {issues, counts} | 每次寫入後、送生成前 | 無 |
| GET | `/api/projects/{slug}/video/settings` | Api Get Video Settings |  |  |
| PUT | `/api/projects/{slug}/video/settings` | 存影片參數（解析度／fps／steps／LoRA） | 調整 H3 生成參數 | 寫 settings.video |
| GET | `/api/prompt-rules` | 各引擎的官方提示詞規則卡（h3_video／moss_t2i／qwen_image） | 要自己寫 prompt 前先讀 | 無 |

參數與 JSON 結構不要用猜的：`GET /api/agent/manifest` 內含每支端點的 schema 名稱，完整定義在 `GET /openapi.json`（`components.schemas`）。

## 背景任務的輪詢方式

批次生圖／生影片／一鍵佈署都是「先回 jobId，再輪詢」：

```
POST /api/projects/{slug}/generate/videos   → {jobId, pollUrl}
GET  /api/projects/{slug}/generate/videos/{jobId}  → 每 2–5 秒一次，直到 status=done|failed|cancelled
```

## 回報與錯誤

- 單欄／批次寫入成功回 `{ok, updated[], targetAfter}`；不合法回 **400** 並指出「第 N 項（path）」與原因。
- 找不到專案／卡片回 **404**。
- 生成類失敗一律回中文 `error` 字串（已截斷），並把 `video.status` 改 `failed`。
