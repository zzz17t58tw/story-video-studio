---
name: story-video-studio-public
description: Story Video Studio 公開版代理人技能卡：接手本程式前先讀，含入口 API、硬限制、寫入方式與常用配方。
metadata:
  scope: public
  audience: external-ai-agent
  source-of-truth: app/agentdoc.py（匯出於 docs/agent/AGENT-GUIDE.md、docs/agent/agent-manifest.json）
---

# Story Video Studio — 公開版代理人技能卡

> **這是公開版**：只寫「任何人拿到這個 repo／exe 都用得到」的內容，**不含**本機路徑、個人環境設定、開發史與發佈流程。
> 要更細的規格見 `docs/spec/`；開發工程與歷程見 `docs/dev/`；文件總索引見 `docs/00-index.md`。
> 本卡是 `docs/agent/AGENT-GUIDE.md`（`GET /api/agent/guide` 的匯出）的濃縮；兩者衝突時**以程式行為與 `AGENT-GUIDE.md` 為準**。

---

## ① 你是什麼

你是 **Story Video Studio 的代理人**。這是一個跑在使用者本機的桌面工具，把「故事」演成影片：

```
故事 → 人物卡 → 場景卡 → 劇本（集／場／beats）→ 分鏡（shots）→ 首幀圖 → 片段影片 → 合併成片
```

程式本身只做三件事：**寫資料 → 驗證 → 送生成**。
你的角色是：幫使用者讀懂現況、提出下一步、透過 API 寫資料與送生成；**不是**直接改檔案。

資料是單一事實來源的樹狀結構，順序不能亂：

1. `meta` / `story`（大綱、章回、風格）
2. `characters`（外型、身分、服裝、音色）
3. `scenes`（地點、光線、氛圍）
4. `script.episodes[].scenes[].beats`（誰、說什麼、做什麼）← **唯一真相**
5. `shots`（分鏡：引用 beats、決定鏡位與長度）
6. `shots[].images`（首幀）→ `shots[].video`（片段）→ `assemble`（成片）

---

## ② 啟動檢查（每次接手先做）

| 動作 | 端點 | 你要看什麼 |
|---|---|---|
| 存活與版本 | `GET /api/health` | `{ok:true, version:"x.y.z"}`；`version` 要與你要用的功能相符 |
| 可運作目錄 | `GET /api/health` 的 `paths` | 回 `{frozen, bundle, data, static, templates, projects}`：`frozen=true` 代表跑在 PyInstaller 打包後的 exe（`bundle`／`static`／`templates` 是唯讀資源，`projects` 才是可寫），`false` 代表原始碼目錄。實際寫入位置看 `paths.projects` |
| 有哪些專案 | `GET /api/projects` | 回各專案 `slug`／標題／進度摘要；後續路徑都用 `{slug}` |
| 後端是否就緒 | `GET /api/comfy/status` | ComfyUI 連線與可用裝置（生成前才需要） |

位址：打包版 exe 是 `http://127.0.0.1:8788`；開發模式（`start.bat`／`python -m uvicorn app.server:app`）同一埠，若被佔用則改 `8790`。**先打 `/api/health` 再決定你要連哪個埠**，不要假設。

`GET /api/health` 以外的欄位請以實際回應為準——不要憑記憶猜欄位，拿不到就用 `/openapi.json` 查。

---

## ③ 三個入口（讀完再動手）

| 入口 | 內容 | 什麼時候用 |
|---|---|---|
| `GET /api/agent/guide` | Markdown 操作手冊：資料模型、硬限制、驗證規則、配方、禁止事項、**全部 API 索引** | **每次接手先整份讀完**（約 1 萬字，可整份塞 context） |
| `GET /api/agent/manifest` | 機器可讀 JSON：每支端點的 purpose／when／副作用／schema 名稱、`hardLimits`、`validation`、`recipes`、`forbidden` | 要程式化決策、需要欄位清單時 |
| `GET /openapi.json` | FastAPI 完整 schema；`components.schemas` 是所有請求／回應結構 | 需要精確型別時 |
| `GET /api/agent/coverage` | 已說明／未說明的 API 覆蓋率 | 想知道文件有沒有落後程式 |

**不要用猜的欄位名稱。** 先用 manifest / openapi 查，再送請求。

---

## ④ 三條硬限制（違反必壞）

1. **H3 單鏡合法長度 = 124–362 幀**（fps 24 → **5.17–15.08 秒**，影格網格為 17k+5）。
   - 想要更短：生成後剪，不要在 prompt 裡硬塞台詞或動作。
   - 想要更長：**拆鏡**，或合併相鄰 beat。
   - 短句不要硬塞事件，用留白（`padSec`）補。
2. **劇本 `beats` 是唯一真相**：分鏡只能「挑選／分組」beats，**不得改寫台詞**。要改台詞回劇本層（`target.kind=beat`），分鏡會即時跟上。
3. **一張 GPU**：生圖／生影片／本地 LLM 不能同時跑。批次任務是**依序**執行，不要平行送；生成前先停本地 LLM。

其他會踩到的（完整清單見 manifest 的 `hardLimits`）：

- `aspectRatio` 只收 `9:16 / 3:4 / 4:3 / 1:1 / 16:9 / 21:9 / 自定义`
- `spec.shotType ∈ close_up / medium / wide / over_shoulder / pov`；**含人物的鏡頭不得用 `wide`**
- `spec.cameraMove ∈ static / push_in / pull_out / pan_left / pan_right / tracking / handheld`
- prompt 規則：H3 用六段式（1. Subject / 2. Action / 3. Camera / 4. Lighting / 5. Atmosphere / 6. Audio）；MOSS 禁 quality tag 與編號式開頭。規則卡：`GET /api/prompt-rules`

---

## ⑤ 寫資料的唯一正確方式

| 情境 | 端點 |
|---|---|
| 改**單一欄位** | `POST /api/projects/{slug}/chat/apply` |
| 改**多欄位／整份陣列**（章回、beats、服裝） | `POST /api/projects/{slug}/chat/apply-batch` |
| 把已預覽確認的整批結果寫入 | `POST /api/projects/{slug}/apply/bulk` |
| 寫完後確認 | `POST /api/projects/{slug}/validate` → 必須 **0 error** |
| 只想產生草稿、不改資料 | `POST /api/projects/{slug}/chat` |

鐵律：

- **不要直接改 `projects/<slug>/project.json`**：會繞過驗證與原子寫檔。
- 任何寫入只要**新增**驗證 error（V1–V5 等）就**整批不寫並回 400**——這是設計，不是 bug。
- 成功回 `{ok, updated[], targetAfter}`；不合法回 **400** 並指出「第 N 項（path）」與原因；找不到專案／卡片回 **404**。
- 生成類失敗回中文 `error` 字串，並把 `video.status` 改為 `failed`。

驗證規則速記（細節見 `/api/agent/guide`）：

| 規則 | 內容 |
|---|---|
| V1 | 場次內 shots（依 `no`）的 `beatIds` 串接 == 該場次 beats（排除 `_skipped`） |
| V2 | 每個 dialogue beat 剛好被某一鏡引用一次 |
| V3 | `durationSec` 吸附後必須落在 124–362 幀（沒對齊為 warn，送件自動吸附） |
| V4 | `durationSec − padSec ≥ 台詞字數 / 4.5`（台詞要唸得完） |
| V5 | `role` 與 beat 類型一致（dialogue 必含台詞；establish 只能首鏡且無台詞；reaction 不含台詞） |

---

## ⑥ 五個常用配方

> 完整版（含每一步的 body 範例）在 `GET /api/agent/manifest` 的 `recipes`；以下為摘要。
> 後三支任務（rebuild／regen／gpu／assemble）也是常見需求，一併列出。

1. **從零到一支成片**（`zero_to_film`）
   建專案 → 故事層（`/chat` + `/chat/apply-batch`）→ 人物層 → 場景層 → 劇本層（`/generate/script` 先 `dryRun=true`，再用 `/apply/bulk`）→ 分鏡（`/storyboard/build`）→ 鎖臉圖 → 首幀（`/preflight/image` → `/generate/images`）→ 影片（`/generate/videos` + 輪詢）→ `/validate` 0 error → `/assemble`
2. **只改一句台詞，讓引用它的鏡頭跟上**（`edit_one_line`）
   `/chat/apply-batch`（`target.kind=beat`，`items=[{path:'line', value:'新台詞'}]`）→ 鏡頭**不用改**（台詞從 beat 即時讀取）→ `/validate` 確認 0 error（V4 可能因台詞變長報「唸不完」，把 `durationSec` 調長）
3. **劇本大改後重建分鏡**（`rebuild_storyboard`）
   `POST /storyboard/build`，body `{scope:'all', force:false, keepManual:true}` → `GET /storyboard` 檢查鏡號／台詞／秒數；要覆蓋手動鏡頭才 `force=true`
4. **只重生一顆鏡頭**（`regen_one_shot`）
   `GET /shots/{shot_id}/prompt-preview` 確認真的要送出的字 → 影像：`POST /shots/{shot_id}/generate/image`（同步，最多等 180 秒）；影片：`POST /shots/{shot_id}/generate/video`（背景）→ 輪詢 `GET /generate/video/{job_id}`
5. **合併成片**（`assemble_final`）
   `/validate` 0 error → `GET /assemble/plan`（缺哪幾鏡、參數是否一致、建議模式、總時長）→ `POST /assemble` → 成品清單 `GET /outputs`

補充：**GPU 記憶體不夠**（`gpu_memory`）
`GET /api/gpu/status` 看誰佔用 → `POST /api/gpu/stop-llama`（停本地 LLM）／`POST /api/gpu/release-comfy`（叫 ComfyUI 卸載模型）→ 生成完再 `POST /api/gpu/start-llama`。順序反了會卡在排隊。

**背景任務一律「先回 jobId，再輪詢」**：每 2–5 秒一次 `GET …/{jobId}`，直到 `status=done|failed|cancelled`。

---

## ⑦ 禁止事項

- 不要直接改寫 `projects/<slug>/project.json`。
- 不要改寫 beats 的台詞「讓分鏡更順」——台詞是使用者的創作。
- 不要為了湊 H3 最短 5.17 秒而在 prompt 裡加台詞或新增動作（只能留白或合併相鄰 beat）。
- 不要在生成任務執行中同時跑本地 LLM 或另一個生成批次（單張 GPU 會互搶）。
- 不要批次平行送生成任務（程式刻意依序執行以避免 OOM）。
- 不要刪除 shots 而不重建：V1/V2 會立刻報「台詞沒被覆蓋」。
- 不要把 promptEn 的 `<Picture N>` 自己編：槽位由 `referencePlan` 產生（`POST /shots/{shot_id}/build-reference-plan`）。
- 不要把 `apiKey` 寫進 `project.json`，也不要在回覆中顯示。

**行為準則**：破壞性操作（刪專案、整份覆寫、`force` 重建、取消任務）先向使用者確認；生成很貴，先 `dryRun`／`preflight`／`prompt-preview` 驗；回報用使用者看得懂的話（哪一鏡、幾秒、缺什麼、下一步）。

---

## ⑧ 資料模型速查

完整 schema 與不變式：**`docs/spec/data-model.md`**（`project.json` 的權威來源）。

| 路徑 | 主要欄位／重點 |
|---|---|
| `meta` | `id, title, createdAt, updatedAt, fps`（預設 24）, `version` |
| `story` | `synopsis, premise, style{genre,tone,visualStyle,negative}, chapters[]` |
| `characters[]` | `id, name, aliases[], appearance{…, promptEn}, background{}, voice{}, outfits[], identityAnchors{}, stages[]`；`identityAnchors` 是逐字貼進 prompt 的鎖臉錨點，**不准改寫** |
| `scenes[]` | `id, name, timeOfDay, lighting, mood, promptEn, props[]`（地點卡，不含對白） |
| `script.episodes[].scenes[]` | `id(e01s01), title, sceneId, characterIds[], summary, beats[]`；`beats[] = {id(b01), type(dialogue|action), characterId, line, emotion, notes}` ← 戲的唯一真相 |
| `shots[]` | `id, no, episodeSceneId, beatIds[], role, derivedFrom, padSec, durationSec, characterIds[], spec{}, promptZh, promptEn, referencePlan{}, images{first,last}, video{}`；`beatIds` 是主鍵；`role ∈ establish/dialogue/reaction/action/insert/closing`；`beats`／`dialogue` 已停用 |
| `settings` | `llm{}, imageEngine, video{}, comfy{}, tts{}`；`apiKey` 不寫進 `project.json` |
| `pipeline` | `stage, logs[]` |

相關 spec：
- 分鏡契約（三層真相、長度規則、V1–V5）：`docs/spec/storyboard-contract.md`｜**動分鏡前必讀**
- H3 送件契約（影格網格、參數）：`docs/spec/contracts.md`
- 鏡頭頁即時連動（真相／衍生分界）：`docs/spec/shots-live-linkage.md`

---

## ⑨ 遇到錯誤怎麼查

| 症狀 | 先查什麼 |
|---|---|
| `400` 且訊息有「第 N 項（path）」 | 先用 `/validate` 看 issues；多半是 V1–V5 或欄位型別 |
| 寫入後資料沒變 | 是不是「新增了 error」被整批擋下（設計如此）；先修內容再寫 |
| `404` | `slug`／`shot_id`／`beat id` 是否打錯；先 `GET /api/projects` |
| 分鏡秒數對不上 | `durationSec` 是否落在 5.17–15.08 秒；`padSec` 是否吃掉台詞時間（V4） |
| 台詞沒被覆蓋 | 增刪 beat 後要重建分鏡（`/storyboard/build`）；不要只刪 shots |
| 生成卡住／很慢 | `GET /api/gpu/status` 看 VRAM 被誰佔用；是否還有 LLM 或別的批次在跑 |
| 送件被拒、prompt 不合格 | `GET /api/prompt-rules` + `GET /shots/{shot_id}/prompt-preview` 看實際送出的字 |
| 欄位／型別不確定 | `GET /openapi.json` 的 `components.schemas`；或 `GET /api/agent/manifest` 的 `routes[].schema` |
| 程式行為與文件不一致 | **以程式為準**；文件落後的線索看 `GET /api/agent/coverage` |

---

## 本卡與其他文件的關係

- 對人的說明：`README.md`（含模型／節點／授權出處）
- 三分鐘入口：`AGENTS.md`
- 完整操作手冊：`docs/agent/AGENT-GUIDE.md`（＝`GET /api/agent/guide`）
- 機器可讀：`docs/agent/agent-manifest.json`（＝`GET /api/agent/manifest`）
- 文件總索引：`docs/00-index.md`
- 開發史、版本規則、分支協作：`docs/dev/`（**非使用本程式所需**）
