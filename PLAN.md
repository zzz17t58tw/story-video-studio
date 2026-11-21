# 1117 專案：故事 → 分鏡 → 影片 一體化製作程式

> 建立時間：2026-11-17（台北）
> 主代理只負責：盯主線、驗收、接需求。實作一律派子代理。

## ★ 北極星（唯一主線）

在自有獨立程式內，從**一句故事概念**走到**一條可播放的長影片（多鏡頭合併、含聲音）**，
中間每一層（故事 / 人物 / 場景 / 對話腳本 / 鏡頭文字 / 鏡頭圖 / 鏡頭影片）都能
「與模型對話修整」或「手動編輯」。

下游接口對齊 AIMixer 導演台（ComfyUI_MiniMaxH3_Director）：
- 最終可直接匯出 `.mmxpack.zip` 給導演台匯入
- 或由程式直接送 ComfyUI API 全自動

## 目前狀態總覽（2026-11-20 10:30 更新）

**版本**：`VERSION 0.9.1`（原始碼在 main；`dist/` 的 exe 已是 v0.9.2（含 M11 修正，2026-11-20 10:35 重打包）。GitHub `zzz17t58tw/story-video-studio`，Release v0.9.1）

**北極星已達成**：整條鏈路真的跑過一次完整成片——故事 → 一鍵佈署（角色/場景/劇本/分鏡）→ MOSS 生圖 → H3 生影片 → ffmpeg 合併，產出《雨夜的便利商店》31.08 秒成片（768×1344、h264+aac、音視差 20.7ms）。

| 層 | 現況 | 入口 |
|---|---|---|
| 故事 | 概念/大綱/章回，可擴寫、可對話改寫 | 故事頁「✦ 擴寫大綱」「🚀 一鍵佈署」 |
| 一鍵佈署 | **逐步落地（M11）**：角色／場景／劇本各自寫檔、每場鏡頭各自寫檔；失敗或取消保留已完成部分；支援本機與線上模型 | 故事頁「🚀 一鍵佈署到各頁面」 |
| 人物 | 外型六欄＋辨識特徵＋身分錨點（Hex）＋服裝＋身世＋聲音參照＋參考圖鎖臉＋分階段造型 | 人物頁 |
| 場景 | 描述/光線/氣氛/英文 prompt（自帶「畫面內不得有人」，中英兩種寫法都認） | 場景頁 |
| 劇本 | 集 → 場次 → 對白/動作，人物與場景下拉連動 | 劇本頁 |
| 分鏡 | 鏡頭類型（英文枚舉＋中文標籤）/角度三元組/運鏡/時長/六段式 prompt/台詞覆蓋/首尾幀 | 鏡頭頁「檢查全專案」 |
| 生圖 | Qwen 2.1（i2i 鎖臉，參考圖可到 4 張）＋ MOSS（t2i，靠文字錨點） | 鏡頭頁「⚡ 生圖」、人物頁「生成立繪」 |
| 生影片 | H3 導演台 r2v，原生音軌，可掛 LoRA（Pika） | 鏡頭頁「🎬 生影片」、批次 |
| 合併 | ffmpeg，自動判斷需不需要重編碼（H3 音訊是 aac 32k） | 「生成與合併」頁 |
| 桌面 | pywebview 原生視窗 exe，關窗即完全退出 | `dist/StoryVideoStudio/StoryVideoStudio.exe` |
| 發佈 | `python tools/release.py` 一鍵（固定 `--distpath build/release-work`，不碰使用端 projects） | `docs/VERSIONING.md` |
| 協作 | 分支規矩＋交接單工具（P1）；M11 交接單已建 | `docs/BRANCH-WORKFLOW.md`、`tools/handoff.py`、`docs/handoff/main-deploy-incremental.md` |

**資料現況**：`projects/` 12 個專案（另 `projects/_trash` 有 5 個回收專案）（含成果專案 `project-20261118-022129` 雨夜便利商店、本輪驗收用的 `project-20261120-091108` 線上版與 `project-20261120-093033` 本地版）。

**已知未完成（依優先序）**
1. `.mmxpack.zip` 匯出給 AIMixer 導演台手動匯入（目前走直接 API，還沒做匯出包）
2. 角色聲音參照尚未接進 H3 的 `<Audio J>` 槽位（現在只有 H3 原生音訊）
3. `app/server.py`（1394 行）、`static/app.js`（約 5400 行）未拆檔 → 多分支並行必撞（backlog 已記）
4. 本地模型的品質落差：場景數偶爾少給（只警告）、角色偶有幻覺（backlog 有記錄，暫不強制）
5. 「雨夜便利商店」大綱結尾斷句、「一鍵佈署」的 5 個殘留畫面缺陷（sh02 櫃台位置等）

## MVP 驗收標準（v0.1，不准擴張）

一個專案：3 個人物、2 個場景、6 個鏡頭；
每鏡頭有「文字 prompt + 圖片 + 影片」；
合併輸出 `final.mp4`（含音軌）在程式中一鍵完成。
其餘功能全部延後。

## 里程碑（一輪一個，做完驗收再下一輪）

- M0 技術契約勘察：確認 ComfyUI 導演台 timeline.json / .mmxpack 結構、Qwen 生圖 payload、ffmpeg 合併參數 → `docs/contracts.md`
- M1 骨架：FastAPI 後端 + 專案資料模型 + 前端外殼 + 專案 CRUD（能開、能存、能重啟不丟資料）
- M2 內容層 UI：故事、人物卡、場景卡、劇本場次、鏡頭卡 的編輯與列表
- M3 模型對話層：任一欄位/卡片可「與模型對話」產生或改寫（預設本地 Strata :8080，可切線上）
- M4 生圖：鏡頭／人物／場景 一鍵送 ComfyUI（Qwen Image 2.1）並回填圖片
- M5 生影片：鏡頭 → H3 導演台（送 API 或匯出 mmxpack），回填影片
- M6 合併：ffmpeg concat → final.mp4
- M7 打包與打磨：啟動器、設定頁、匯出/匯入專案
- M10 修正批次（每個 bug 一個分支切片，編號 M10a、M10b…）：目前追蹤中的 bug 見 `docs/TODO-backlog.md`
  - M10a 燈箱關閉鈕失效（`bindLightboxEvents` 在 `#lightbox` 存在前就執行，事件從未綁上）
- M11 佈署品質與健壯性（2026-11-20，直接 commit 在 main）：版本戳單一真相、狀態探測快取、一鍵佈署實走除錯（線上＋本地）、**一鍵佈署改成逐步落地**（每完成一層就寫檔）。交接單：`docs/handoff/main-deploy-incremental.md`
- M12 內容層一致性（2026-11-21，使用者回報兩件，拆兩輪各一條分支）
  - M12a 鏡頭↔劇本連動：鏡頭卡顯示可勾選的「引用劇本台詞」（dialogBeatIds，舊 dialogLineRefs 自動遷移）、劇本頁每句標註被哪些鏡頭引用、兩頁同一份真相不再各存副本
  - M12b 提示詞規則寫死：`app/promptrules.py` 集中每個引擎的官方規則（H3 六段式＋`<Picture N>`／MOSS caption／Qwen 官方槽位），送件前硬驗並在 UI 顯示本次送出的提示詞
- M13 模型對話回填升級（2026-11-21，使用者：「回填應該要有相對應的格子」）
  - M13a 多欄套用＋結構化回填：`POST /chat/apply-batch`（一次多欄、單次驗證、原子寫檔）、章回／場次 beats／鏡頭 dialogue／服裝整份可寫並驗元素形狀、前端「解析成多欄」把一段回覆自動分到多個欄位
  - M13b 任務↔回填目標宣告：`CHAT_TASKS[*].applyHint`（任務 chip 標「→ 回填：…」）、氣泡「⇣ 回填到 目標」只展開該任務的格子、外型解析 `parseAppearance`、三版台詞／音色「用第 N 版」一鍵寫入、檢查矛盾標「僅建議，不回填」

## 技術選型（固定，子代理不得自行更換）

- 後端：Python 3.11 + FastAPI + uvicorn，埠 **8788**
- 前端：純靜態 HTML/CSS/JS（無 build 步驟），由 FastAPI 掛 `/static`
- 資料：`projects/<slug>/project.json` 為單一真相（schema 見 docs/data-model.md）
      媒體檔在 `projects/<slug>/media/**`，檔案路徑相對專案根
- 模型對話：OpenAI 相容 `/v1/chat/completions`（預設 `http://127.0.0.1:8080/v1`）
- 生圖／生影片：ComfyUI `http://127.0.0.1:8000`
- 合併：系統 ffmpeg（8.1.1）
- 啟動：`start.bat`（雙擊即用，非技術使用者）

## 防偏線規則（每一輪子代理都要遵守）

1. 只做本輪里程碑寫的事，發現別的問題→記到 `docs/TODO-backlog.md`，不要順手做。
2. 不引入新框架、新資料庫、外部雲端服務。資料一律 JSON 檔。
3. 不修改 ComfyUI 本體、不改作者工作流原檔；要改一律複製到 `templates/` 再改。
4. 每次交付必須「實際跑起來」並貼出可驗證證據（指令輸出／截圖路徑／API 回應）。
5. 使用者是台灣人，UI 文字一律繁體中文。
6. 碰不動的地方停下來回報，不要用假資料充當成果。
7. **分支紀律**：開新的分支對話前先讀 `docs/BRANCH-WORKFLOW.md`。一個分支只做一個里程碑切片、只碰自己那組檔案；收工必交 `docs/handoff/<分支名>.md`（用 `python tools/handoff.py new <分支名>` 產生）。**沒有交接單的分支視為未完成，不合併。** 契約檔（`docs/data-model.md`、`docs/contracts.md`）與本檔只有主對話能改。
8. **主代理派子代理一律走 worktree 隔離**：`git worktree add ../1117-wt-<名> -b feat/<名>`，子代理只在那個目錄施工，做完由主代理驗收後 `git merge --no-ff`。理由：`app/server.py` 與 `static/app.js` 是單體大檔，子代理與使用者的分支對話同時改會直接撞；同一工作目錄施工是這類衝突的根源。
9. **每輪開工前三個檢查**（10 秒）：`git status --short`、`git branch -a`、`python tools/handoff.py list`。有未合併分支或 ready 交接單時，先合併／驗收再開新工。

## 分支協作（多對話並行時）

- 規矩：`docs/BRANCH-WORKFLOW.md`（含檔案歸屬表、高衝突檔規則、合併檢查清單）
- 交接單：`docs/handoff/`（模板 `_TEMPLATE.md`；五段必填，缺一不可）
- 工具：`python tools/handoff.py new|list|check|brief`
- 合併前一定先跑 `python tools/handoff.py check`：除了驗格式，還會用 `git diff main...<分支>` 對帳，抓出「交接單說沒改、實際卻改了」的檔案。

## 目錄結構

```
1117/
  PLAN.md                  <- 本檔（主線／任務板）
  CHANGELOG.md             <- 版本變更紀錄
  docs/                    <- 文件：contracts.md（外部契約）、data-model.md（資料契約）、
                              TODO-backlog.md（雜事停車場）、BRANCH-WORKFLOW.md（分支規矩）、
                              VERSIONING.md（發佈流程）、handoff/（分支交接單）
  app/                     <- 後端（FastAPI）
    server.py              <- 全部路由（🔴 高衝突檔）
    storage.py             <- 專案資料讀寫
    llm.py / prompts.py    <- 模型對話層
    storygen.py / deploy.py<- 故事→分鏡生成
    imagegen.py / comfy.py <- 生圖（Qwen / MOSS）
    h3gen.py / h3compose.py<- 生影片（H3 導演台）
    assemble.py            <- ffmpeg 合併
    gpu.py / paths.py      <- 環境
  static/                  <- 前端 index.html / app.js（🔴 5383 行，高衝突） / style.css
  tools/                   <- release.py、verify_release.py、handoff.py、make_icon.py、mock_llm.py
  templates/               <- 從作者工作流複製來的模板（Qwen 生圖、H3 導演台）
  projects/                <- 使用者專案資料（不進 git）
  start.bat / 啟動 Story Video Studio.bat
```

> 🔴 高衝突檔（`app/server.py`、`static/app.js`）：同一時間只允許一個分支動同一個檔的同一區段。
> 開工前先宣告「我要用的是哪個區段」，見 `docs/BRANCH-WORKFLOW.md` §3。

## 進度紀錄

| 時間 | 里程碑 | 狀態 | 證據 |
|---|---|---|---|
| 2026-11-17 01:00 | M0 契約勘察 | ✅ 完成 | `docs/contracts.md`（187 行）＋`templates/` 三份模板。實測：H3 單段 r2v 39 影格送 API 成功產 mp4（h264+aac）、Qwen 2.1 i2i 送件成功、ffmpeg 兩方案實測。風險：多段 batch 未實跑、ComfyUI 已被子代理關閉（需重啟） |
| 2026-11-17 06:20 | M1 骨架＋專案 CRUD | ✅ 通過驗收 | `app/{server,storage}.py`、`static/`、`start.bat`。實測：health 200、建/改/重啟不丟、上傳可讀、錯誤全 400/404 中文、UI 截圖確認繁中深色可用（主代理親驗 10:45） |
| 2026-11-17 22:01 | M2a 人物卡＋場景卡 | ✅ 通過驗收 | 子代理 8 分鐘完成（線上模型）。實測：PUT/GET deep-equal、上傳可讀、瀏覽器 F5 後資料在、刪人物跳引用警告、截圖 `docs/shots/m2a-*.png`（主代理親驗 22:11：外型六欄/服裝/身世/聲音試聽/參考圖鎖臉齊全） |
| 2026-11-17 22:20 | M2b 劇本＋鏡頭分頁 | ✅ 通過驗收 | 子代理 8 分鐘。實測：script/shots GET deep-equal True、首幀上傳 200、拆鏡頭草稿 no 連號、刪人物 8 處引用正確清 null。主代理親驗截圖 `m2b-shots.png`：分鏡表完整、下拉顯示人名/場次名、無跑版 |
| 2026-11-17 22:40 | M3a 對話層＋三頁對話抽屜 | ✅ 通過驗收 | 新增 `app/llm.py`、`app/prompts.py`、`/api/llm/status`、`/chat`、`/chat/apply`、no-cache。主代理實測：8080 無模型時回中文錯誤（ok:false、provider local）；UI 截圖抽屜清楚（任務鈕／氣泡／套用下拉）。⚠缺口：成功路徑只對 mock 伺服器驗過、不支援線上 key → M3b 補 |
| 2026-11-17 22:55 | M3b 線上供應商＋劇本/鏡頭抽屜 | ✅ 通過驗收 | 真實 DeepSeek 驗證（`deepseek-flash`，models 清單真實、key 只回末四碼 `75eb`）；鏡頭卡寫入六段式 promptEn 1575 字；beat 改寫可指定 parentId。主代理實測 status ok:true、截圖確認抽屜與六段式 prompt 正常 |
| 2026-11-17 23:10 | R1 現成開源專案調查 | ✅ 完成 | `docs/research-existing-tools.md`（313 行，14 個專案）。結論：**不 fork 任何一家**（沒人把 H3 導演台當一等公民），移植 3 小模組＋補 5 項 schema。授權紅燈：huobao(NC-SA)、ai-shotlive(NC)、AI-Comic-Director、toonflow(AGPL)、storycine(無授權) |
| 2026-11-17 23:25 | M3c 結構化生成（故事→整集劇本、場次→多鏡頭） | ✅ 通過驗收 | 真模型 13 分鐘（含 2 次真生成）。實測：`generate/script` 200/13.4s、`apply/bulk` 生 e02、`generate/shots` 200/36.5s coverage 10/10、shot nos 4-6 連號；dryRun 前後 md5 相同；失敗路徑全 400 中文。主代理親驗：221919 現有 3 集 9 鏡、anchors/refPlan 存在、預覽視窗可編輯 JSON |
| 2026-11-17 23:35 | M3d anchors/stages UI＋refPlan＋模板編譯＋校驗面板 | ✅ 通過驗收 | 新增 `app/h3compose.py`（~470 行，純邏輯）。實測 sh04：refPlan 3 槽位（c01 stage=s1 / c02 face / sc01 wide）、compile-prompt 1785 字 6 段含 `<Picture 1>`、validate 回 9 error / 27 warn（台詞未覆蓋、wide 違規、影格不在 17k+5）。主代理親驗截圖：問題面板分色可點跳；⚠發現 UI bug：角度顯示 `[object Object]` → 排入 M4a 修 |
| 2026-11-17 23:50 | M4a 單鏡生圖全鏈路（Qwen 2.1） | ✅ 通過驗收 | 實生 sh04：200/22.7s、1024×1024 PNG。主代理親驗圖：紅斗篷少女+編織籃+霧林逆光+樹後灰狼，無變形。⚠限制：只送 2 張參考圖、場景圖被丟 → M4b |
| 2026-11-18 00:05 | M4b 參考圖 4 張＋前置檢查＋批次生圖 | ✅ 通過驗收 | 4 張參考圖直送實測 200 成功；sh04 重跑 28.7s/3 張參考圖、warnings 解除；批次 sh05 因缺參考圖被跳過（非失敗）。主代理親驗新圖：灰狼臉清楚（3 張參考圖生效）、無瑕疵 |
| 2026-11-18 00:15 | M5a 單鏡生影片（H3 導演台 r2v） | ✅ 通過驗收 | 實生 sh04：480×864/39 幀/1.625s/18.4s 完成，h264+aac。主代理抽 0/13/26 幀確認有運動、無崩壞；⚠品質偏差（蠟像/偏青）因 4 步＋參考圖為 96×96 佔位 |
| 2026-11-18 02:30 | S1 完整故事專案（真模型） | ✅ 完成 | `project-20261118-022129`「雨夜便利商店」：3 人物／2 場景／1 集 2 場 8 beats／6 鏡（六段式 promptEn 齊全）、大綱 709 字、章回 1084 字、validate error 0。⚠大綱結尾斷句待修 |
| 2026-11-18 00:30 | M5b+M6 品質對照、批次生影片、合併 | ✅ 通過驗收（MVP 里程碑） | A: 704×1280/124 幀/5.167s/steps12=294.6s；B: steps20=467.3s。合併成品 `final_20261118-000159.mp4`：117 幀 4.896s、aac 48k、影音差 21ms。主代理親驗 A/B 圖：畫質明顯優於 4 步版，但 seed 不同→非乾淨對照。⚠發現 H3 音訊是 aac 32k → copy 不安全，自動轉 reencode |
| 2026-11-18 02:40 | S2 MOSS 生圖整合＋9 張圖 | ✅ 通過驗收 | engine=moss（768×1344/30步/cfg3，13.6-15.1s/張）×3 立繪（寫入 refImages lockedFace）+6 鏡圖。CLIPLoader 讀回官方 int8 未被回退。主代理親驗分鏡縮圖：**店員六張同一張臉**、電影感寫實。⚠sh02 櫃台位置、sh04 前景肩、c03 下顎外露為殘留缺陷 |
| 2026-11-18 16:10 | M7a 桌面化（pywebview + PyInstaller） | ✅ 通過驗收 | 專屬 `.venv`（3.14，未動 Hermes）、`launcher.py`、`app/paths.py`（dev/frozen 雙路徑）、`啟動 Story Video Studio.bat`、`dist/StoryVideoStudio/StoryVideoStudio.exe`。實測：原生視窗 1280×880 無網址列、關窗 154ms 內埠釋放且進程消失、exe 自帶 8 專案可讀媒體。主代理親驗截圖（含 GPU/ComfyUI 狀態列） |
| 2026-11-18 03:00 | S3 六鏡生影片＋合併（最終成片） | ✅ 完成（全線打通） | 6 鏡 0 失敗 0 重試，每鏡 355-366s（總 35.8 分）。`final_20261118-033215.mp4`：768×1344 h264、746 幀 31.08s、aac 48k、音視差 20.7ms、5.6MB。主代理親驗 contact sheet：故事連貫、人物一致、無崩壞；sh05 為監視器視角靜止人影（幀差小屬合理） |
| 2026-11-18 16:20 | M7b 乾淨發佈包（zip） | ✅ 完成 | `release/StoryVideoStudio/`（112M：exe + _internal 32M + 只有雨夜便利商店 72M + 使用說明）→ `release/StoryVideoStudio-乾淨版-20261118.zip`（90M）。解壓到全新目錄實測：health 200、只有 1 專案、final.mp4 200/5,626,636B、關窗進程消失。順手修掉「exe 會把開發機 8 個測試專案 seed 到別人機器」的真 bug |
| 2026-11-18 18:30 | M8 一鍵佈署（故事→角色/場景/劇本/分鏡） | ✅ 通過驗收 | `app/deploy.py`（四步 cast/locations/script/shots）。線上模型實跑 108 秒（角色17.1/場景14.6/劇本9.7/分鏡66.6）、產出 3 角色+2 場景+1 集+**6 鏡**（shotPlan [3,3]）、validate error 0、圖片欄位全空。修正：shotCount 改總數、覆蓋預設勾選+警示、完成摘要四顆跳頁鈕、provider 提示。主代理親驗截圖（總鏡數 6／模型來源／四顆按鈕／無重疊） |
| 2026-11-18 18:40 | M7c 重打包 exe＋乾淨版 zip（含一鍵佈署） | ✅ 完成 | exe 8,952,439B（18:31）、release 111M、新 zip `StoryVideoStudio-乾淨版-20261118b.zip`（90MB）。解壓全新目錄實測：health 200、1 專案、final.mp4 200/5,626,636B、`_internal/static/app.js` 含「一鍵佈署」×6、「總鏡數」×2、`/api/deploy/info` 200。⚠舊 zip 保留作對照 |
| 2026-11-19 11:10 | M9 影片加 Pika LoRA | ✅ 完成 | 走導演台原生 `loras` 欄位（**editMode=global 只讀 global.loras，只寫 segment 會被靜默忽略**→兩處都寫）。預設：pika_h3_flex_ref2va bf16/1.0、steps 6、cfg 1.0、shift 12/3、euler+simple。同 seed A/B：幀差 0.267→0.722（2.7×）、亮度 56→68、音訊 max -35.9→-15.3dB、PSNR 16.3dB。主代理看圖：同場景同人，A 較暗高對比有電影感、B 較亮細節清楚 |
| 2026-11-19 10:30 | M9 版本管理＋GitHub 發佈 | ✅ 完成 | git repo（源碼 77 檔、排除 projects/dist/release）、`VERSION 0.9.0`、`CHANGELOG.md`、`tools/release.py`（固定 `--distpath build/release-work`，只覆寫 exe/_internal，同步前後比對專案數）、`docs/RELEASE-NOTES-v0.9.0.md`（含 sha256）。GitHub：`zzz17t58tw/story-video-studio` 已 push、Release v0.9.0 附 `StoryVideoStudio-v0.9.0-clean.zip`（94,387,320B，下載 HEAD 200 相符）、repo 內確認無 projects/dist/release |
| 2026-11-19 10:20 | 事故：dist/projects 第二次被 PyInstaller `--noconfirm` 清空 | ⚠已補救 | 根 `projects/` 全程未動（10 專案 130MB），已全部複製回 `dist/StoryVideoStudio/projects/`；腳本已根治（見上列 M9） |
| 2026-11-19 11:25 | P1 分支協作規矩＋交接單工具 | ✅ 完成 | 新增 `docs/BRANCH-WORKFLOW.md`（檔案歸屬表、🔴高衝突檔規則、開分支五句模板、合併檢查清單）、`docs/handoff/_TEMPLATE.md`、`tools/handoff.py`（new/list/check/brief）。實測：new 正確帶入分支名/里程碑/時間；未填模板 check 抓出 3 個問題並 exit 1；填滿後全綠 exit 0；git 對帳正確抓出「交接單列了但實際沒改」；測試檔已清理，list 歸零 |
| 2026-11-19 12:05 | M10a 燈箱關閉鈕失效 | ✅ 已合併（分支示範） | worktree `1117-wt-m10a` / `feat/m10a-lightbox-close`。根因：`bindLightboxEvents` 是 IIFE，執行時 `#lightbox`（index.html 第 114 行）尚未解析（script 在第 99 行）→ 事件從未綁上。修法：改 document 層 click/keydown 委派＋`lightboxEl` 加 `isConnected` 快取。主代理在 8788 親驗：點縮圖開、X／Esc／點背景三種都關得掉。合併閘：`handoff.py check` 格式✅＋git 對帳相符 2 檔；順手修掉工具兩個誤報（HTML 標籤被當佔位符、交接單本身被算成沒列的變更） |
| 2026-11-19 11:40 | P2 狀態總覽全面更新 | ✅ 完成 | PLAN 新增「目前狀態總覽」（版本 v0.9.0／北極星達成／11 層現況表／5 項已知未完成）；防偏線加第 8、9 條（子代理一律 worktree 隔離；每輪開工前三檢查）。TODO-backlog 標記已修項。⚠仍未解：main 有 6 個未提交變更，`handoff.py` git 對帳會失準，待使用者在基線 commit 上裁示 |
| 2026-11-20 10:30 | M11 整理除錯＋一鍵佈署實走＋逐步落地 | ✅ 完成（直接 commit main，四筆） | ① health 版本戳改讀 VERSION（原本硬編碼 M5b+M6）；gpu/llm 狀態加唯讀探測快取 2100ms→2–9ms。② 模擬使用者走線上佈署，修 5 個 bug（覆蓋確認框用刪除文案、台詞量含 action、摘要顯示設定模型、禁人語兩套判定、鏡頭類型下拉中文 vs 資料英文）。③ 本地 27B 三輪實測：思考關 76.8s／100.9s 完成、思考開 492.5s（cast 單步 216.6s），推翻舊「6 次 4 次失敗」。④ **逐步落地**：`run_steps_landing()` 每層（含每場 shots）完成即寫檔、job 加 `written`、取消改 `cancelled` 並列出保留內容、覆蓋清空延後到 cast 成功後。實測本地 91.1s／UI 110.4s／線上 129.5s 全成功；取消情境保留 3 角色 2 場景 2 場 3 鏡；三專案 validate 0 error。交接單 `docs/handoff/main-deploy-incremental.md` |
| 2026-11-21 12:07 | v0.9.3 佈署確認框被參數窗蓋住 | ✅ 已發佈 | 根因：`#modal-deploy`／`#modal-gen` 是執行時 append 到 body、DOM 在 `#modal-confirm` 之後，三者 z-index 同為 50 → 確認框被蓋住、按鈕點不到。修法：`SVSConfirm()` 取可見彈窗最大 z-index +10、關閉還原；CSS `#modal-confirm` 預設 70；確認框開著時其他彈窗點背景不關閉；`dpState.confirming` 防連點。實測 `elementFromPoint` 由 `.dp-params` 變 `.cf-ok`，真滑鼠點下去只送 1 次 `POST /deploy`。exe 已重建（`version=0.9.3`＋`frozen:true`） |
| 2026-11-21 12:45 | M12a 鏡頭↔劇本連動＋M12b 提示詞規則寫死 | ✅ 已合併（v0.9.4） | M12a：`shot.dialogBeatIds`（beat id 為主鍵、`dialogLineRefs` 變衍生值、舊資料讀取時遷移）、鏡頭卡可勾選引用劇本台詞並列「本鏡台詞」、劇本頁標「被 sh0X 引用」。主代理親驗：真資料 6 鏡遷移正確、`validate` error 0、UI 勾選落盤同步、中間插一句後引用不錯位。M12b：新增 `app/promptrules.py`（h3_video／moss_t2i／qwen_image 三張規則卡：build＋check）＋`GET /api/prompt-rules`、`/shots/{id}/prompt-preview`；H3 送件口改 `_pick_prompt`：promptEn 未過規則就自動用模板編譯（不寫回 project.json，`_meta.promptSource` 記錄）。主代理修正：規則分硬性/品質兩級（否則 MOSS 幾乎每鏡都被擋）並讓預覽一律回傳真的要送出的字。實測 baseline 專案 promptEn 2241 字無 `<Picture>` → 送件組出 3196 字含 `<Picture` 的編譯稿 |
| 2026-11-21 13:35 | M13a＋M13b 對話回填（多欄／結構化／任務對應） | ✅ 已合併（v0.9.5） | 新增 `POST /chat/apply-batch`（一次多項、單次原子寫、任一項不合法整批不寫）；白名單開到 chapters／beats／dialogue／outfits／spec.angle.*，含結構驗證與角色名→id；前端「⇣ 解析成多欄／回填到 X」＋23 個任務的 applyHint＋外型五欄解析＋三版一鍵選用＋僅建議任務標示。主代理親驗：chapters＋tone 一次寫入 validate 0 error；外型 5 欄寫入後 API 重讀正確；三版點第 2 版後 b02 變更；不合法輸入整批不寫；作廢舊 beat（_skipped）不出現在劇本頁與 h3compose。 |
| 2026-11-21 14:20 | M14a 分鏡層（劇本→分鏡表，契約化） | ✅ 已合併（v0.9.6） | 新增 `docs/storyboard-contract.md`：三層單一真相（beats 為真相、shots 衍生）、`beatIds` 取代 `dialogue`／`beats` 自由欄位、長度吸附 H3 124–362 幀（5.17–15.08s，短句以 padSec 補足或合併相鄰 beat，長 beat 拆鏡）、V1–V5 驗證（順序覆蓋／台詞覆蓋／長度合法／台詞唸得完／職責一致）改為 error。 |
