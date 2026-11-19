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

## 目錄結構

```
1117/
  PLAN.md                  <- 本檔（主線）
  docs/                    <- contracts.md, data-model.md, TODO-backlog.md
  app/                     <- 後端
    server.py
    storage.py
    llm.py
    comfy.py
    assemble.py
    routes/
  static/                  <- 前端 index.html / app.js / style.css
  templates/               <- 從作者工作流複製來的模板（Qwen 生圖、H3 導演台）
  projects/                <- 使用者專案資料（每個專案一資料夾）
  start.bat
```

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
| 2026-11-19 10:30 | M9 版本管理＋GitHub 發佈 | ✅ 完成 | git repo（源碼 77 檔、排除 projects/dist/release）、`VERSION 0.9.0`、`CHANGELOG.md`、`tools/release.py`（固定 `--distpath build/release-work`，只覆寫 exe/_internal，同步前後比對專案數）、`docs/RELEASE-NOTES-v0.9.0.md`（含 sha256）。GitHub：`zzz17t58tw/story-video-studio` 已 push、Release v0.9.0 附 `StoryVideoStudio-v0.9.0-clean.zip`（94,387,320B，下載 HEAD 200 相符）、repo 內確認無 projects/dist/release |
| 2026-11-19 10:20 | 事故：dist/projects 第二次被 PyInstaller `--noconfirm` 清空 | ⚠已補救 | 根 `projects/` 全程未動（10 專案 130MB），已全部複製回 `dist/StoryVideoStudio/projects/`；腳本已根治（見上列 M9） |
