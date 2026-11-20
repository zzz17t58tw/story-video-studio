# Changelog

本檔記錄 Story Video Studio 的所有重要變更。
格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號規則見 `docs/VERSIONING.md`（0.x 為預發版）。

## [Unreleased]

### 修正（模擬使用者走一遍「一鍵佈署」發現）

- **覆蓋確認框用了刪除的文案**：一鍵佈署勾「覆蓋現有內容」送單時，`SVSConfirm` 把標題寫成
  「刪除「覆蓋現有內容？…」」、內文冠上「目前被 3 處引用：」、按鈕是「仍要刪除」，還補一句
  「這些引用會清成 null（鏡頭與劇本不會被刪）」——使用者會以為自己在刪資料。`SVSConfirm` 加
  `plain`／`okText` 選項，佈署改走 plain：標題原樣、條目直接列、按鈕「清空並重建」。
- **台詞量警告把動作句也算成台詞**：`deploy.shot_budget` 用全部 beats 估算，場次 8 beats（只 3 句台詞）
  卻警告「8 句共需約 51s 台詞塞不下」，還把鏡頭時長拉長。改為只算 dialogue，
  措辭改成「N 個演出點（X 句台詞＋Y 個動作）」。實測同題材總時長由 124–158 幀回到 124 幀為主。
- **完成摘要顯示設定值而非實際模型**：設定寫 `deepseek-chat`（帳號沒有）時後端自動改用
  `deepseek-flash`，摘要卻仍印 deepseek-chat。改為佈署前用 `llm.status()` 取實際模型寫進 job.params。
- **場景「禁人語」兩套判定標準**：`deploy` 要求英文 `no people in silhouette / in reflection`，
  `h3compose.validate_project` 只認中文句 → 一鍵佈署的場景永遠掛 2 筆 warn。校驗改為中英文任一皆可。
- **鏡頭類型下拉只有中文，資料是英文**：後端（`storage.shot_type_en`／`h3compose`／`imagegen`）全用
  `close_up|medium|wide|over_shoulder|pov`，前端 `SHOT_TYPES` 仍是中文 → 每張鏡頭卡顯示紅字
  「（引用已失效：medium）」。前端改成 value/label 對照＋舊中文值 alias，預設值與拆鏡頭草稿同步改英文。

### 驗收（模擬使用者實走一輪，線上 deepseek）

- 新專案 → 設定頁切 online（自動存檔）→ 故事頁填 premise／synopsis → 一鍵佈署（3 角色／2 場景／6 鏡／5 秒／覆蓋）。
- 第一次 108.3s、第二次（覆蓋重跑）144.5s；四步計時 cast 21–34s、locations 15–16s、script 14–22s、shots 44–85s。
- 落地結果：3 角色（identityAnchors 6 鍵齊）、2 場景、1 集 2 場（9／11 beats，台詞 3／4）、6 鏡；
  六段式 100%、`promptEn` 0 中文字、時長落在 124f／209f（17k+5）、`dialogLineRefs` 覆蓋全部台詞、
  `validate` **0 error**（warn 只剩「沒有參考圖」，屬預期）；七個分頁皆正常渲染、無 JS 錯誤。
- 截圖：`docs/shots/deploy-walkthrough-shots.png`、`docs/shots/deploy-walkthrough-script.png`。

### 修正

- **`/api/health` 的版本戳是硬編碼的舊里程碑**（`milestone: "M5b+M6"`，`FastAPI(version=...)` 亦同），
  發到 0.9.1 之後驗收時無法用 health 判斷版本。改由新增的 `app/version.py` 讀 `VERSION` 檔：
  health 回 `version`／`milestone: v<版本>`／`paths`（frozen、bundle、data、projects）；
  `StoryVideoStudio.spec` 的 `datas` 收錄 `VERSION`。

### 效能

- **設定頁輪詢的兩支狀態 API 在服務沒開時各要等 ~2 秒**（這台機器對未監聽埠回 TCP 拒絕要 ~2 秒，不是即時 RST）：
  `/api/gpu/status` 與 `/api/llm/status` 加唯讀探測快取（成功 5 秒／失敗 10 秒），
  實測重複查詢由 ~2100ms 降到 2–9ms；GPU 三個動作（release-comfy／stop-llama／start-llama）後主動清快取，狀態不會落後。
  動作路徑（chat、生圖、生影片）完全不受快取影響。

### 整理

- 移除未使用的 import：`server.py`（HTTPException／FileResponse／PlainTextResponse）、
  `deploy.py`（Callable、函式內未用的 `from pathlib import Path`）、`imagegen.py`（shutil）；`server.py` 換行統一為 CRLF。
- 清掉 `__pycache__/`、`temp/` 執行殘留與 `launcher.log`（執行痕跡不入庫）。

### 驗收（本次整理後實跑）

- 全 API 冒煙：10 個專案全載入、品質基準專案 `validate` **0 error**、`assemble/plan` 200（6 段）、`outputs` 1 支成品；
  專案 CRUD（建立／讀回／更新／刪除進 `_trash`）與錯誤輸入（空名稱 400、重複代號 400、不存在 404）皆正確。
- 前端七個分頁無 JS 錯誤、無 `undefined`、無 `[object Object]`；成品清單正常回 `output/final_*.mp4`。

## [0.9.1] - 2026-11-19

基線版本：功能與 0.9.0 相同，差別是把「多對話／多分支並行開發」的協作機制正式入庫，
讓後續功能開發可以在乾淨的 git 起點上分岔。

### 新增

- **分支協作機制**：`docs/BRANCH-WORKFLOW.md`（四個真相檔、檔案歸屬表、🔴 高衝突檔規則、
  開分支五句邊界模板、合併檢查清單、常見失敗模式）、`docs/handoff/_TEMPLATE.md`、
  `tools/handoff.py`（`new` / `list` / `check` / `brief`；`check` 含 `git diff` 對帳，可抓出
  「交接單說沒改、實際卻改了」的檔案）。

### 文件

- `PLAN.md` 新增「目前狀態總覽」（版本、北極星達成、各層現況、已知未完成），防偏線規則補第 8、9 條
  （子代理一律走 git worktree 隔離；每輪開工前三檢查）。
- `docs/TODO-backlog.md` 標記已修項目並註明修在哪一輪；補入「`server.py`／`app.js` 未拆檔」技術債。
- `README.md` 文件索引補上分支協作規矩。
- `.gitignore` 排除 `temp/`（對話匯出與臨時腳本）。

### 文件
- README 改為對外首頁並補齊背景資料：①模型與工作流（每個模型檔附官方 GitHub，工作流附作者 GitHub）②引用的節點包與「僅參考概念」專案的授權對照 ③ComfyUI 0.45.0 實測環境、必要／選配節點與升級踩坑 ④開發與執行所用的 AI 代理、技能與依賴來源（Hermes Agent、`story-video-studio` skill、LLM、FastAPI/pywebview/ffmpeg）⑤免責聲明（純測試用途）。
- M1–M3 開發歷程移至 `docs/DEVLOG-M1-M3.md` 保留可追溯。

## [0.9.0] - 2026-11-18

第一個可交付的桌面版：從「一句故事概念」走到「一條含聲音的長影片」，全程在同一個程式裡完成，
並且打包成乾淨的 exe 資料夾交給使用者。

### 新增

- **專案與內容層（M1/M2a/M2b）**：FastAPI 後端（埠 8788）＋純靜態繁中深色前端；專案 CRUD、原子寫入不失資料；
  人物卡（外型六欄／服裝／身世／聲音參照／參考圖鎖臉）、場景卡、劇本場次與 beat、鏡頭分鏡卡。
- **與模型對話（M3a/M3b）**：任一卡片可開對話抽屜生成或改寫；模型服務可切 `local`（本機 OpenAI 相容 `:8080`）／
  `online`（DeepSeek）／`custom`，API key 只回傳「有無＋末四碼」；模型只回文字，一律由使用者按「套用」才寫入資料。
- **結構化生成（M3c）**：故事 → 整集劇本、場次 → 多鏡頭；一律「參數 → 可編輯 JSON 預覽 → 套用」，`dryRun` 預設開啟。
- **六段式提示詞與參考圖計畫（M3d）**：`identityAnchors`（5 條文字＋4 個 Hex 色票雙向同步）、`stages`；
  確定性模板編譯（錨點逐字貼、`<Picture N>` 由參考圖計畫決定）、全專案校驗面板（分色、可點跳卡）。
- **一鍵佈署（M8）**：`app/deploy.py` 四步 cast → locations → script → shots，背景 job＋輪詢；
  一次產生角色／場景／劇本／分鏡四層文字，圖片與影片欄位留空。線上模型實測總計約 108–215 秒。
- **生圖**：Qwen Image 2.1（mode=ref 有參考圖鎖臉／t2i）與 MOSS Image SFT（質感最強、t2i only）；
  前置檢查、批次生圖 job、角色立繪批次生成並寫回 `refImages.lockedFace`。
- **生影片與合併（M5a/M5b/M6）**：H3 導演台 r2v 直接送 ComfyUI API（背景 job、逐鏡回填）；
  影片批次＋進度輪詢；ffmpeg 合併成 `final.mp4`（含音軌），自動處理 H3 音訊 32k→48k 的重新編碼。
- **GPU 資源卡（設定頁）**：`GET /api/gpu/status`、`POST /api/gpu/{release-comfy,stop-llama,start-llama}`；
  停 llama 前會檢查 `/slots` 的 `is_processing`。
- **桌面化（M7a）**：`launcher.py`（pywebview 內嵌 uvicorn、自動選埠、關窗即完全結束）、`app/paths.py`（dev／frozen 雙路徑）、
  專屬 `.venv`（Python 3.14，不動系統環境）、`啟動 Story Video Studio.bat`、PyInstaller onedir 打包。
- **乾淨發佈包（M7b）**：`release/StoryVideoStudio/` 只留成果專案《雨夜的便利商店》＋`使用說明.txt`，
  壓成 zip；附 `tools/verify_release.py` 解壓實測腳本。
- **一鍵同步發佈（本版新增）**：`tools/release.py` 一次完成「打包→同步使用端 exe→重建乾淨版→壓 zip→寫發佈紀錄→git commit」；
  `VERSION`／`CHANGELOG.md` ／`docs/VERSIONING.md` 與 git 版控。

### 修正

- exe 首次啟動會把打包機的測試專案 seed 到別人電腦 → 乾淨版停止 seed，只在明確設定環境變數時才複製專案。
- `uvicorn` 在 `--noconsole` 下因 `sys.stdout is None` 起不來 → 啟動前換成帶 `isatty()` 的假流。
- 背景 thread 動 pywebview 視窗屬性會卡死 WebView2 UI 執行緒 → 改用 `ctypes` 置前。
- 參考圖槽位不足會靜默丟掉引用（`<Picture N>` 有寫、圖沒送）→ 擴充到 4 槽並在送件前比對。
- H3 輸出音訊為 aac 32kHz，多段 `-c copy` 會時間戳錯位 → 合併前 probe，參數不一致走 filter_complex 重編碼並統一 48kHz。
- 「一鍵佈署」鏡數欄位誤解成「每場 N 個」→ 改為總數並自動分配到各場；覆蓋模式預設勾選並在確認視窗寫明會清空什麼。
- 舊專案缺少新欄位時讀進來會 400 → 自動補預設值。
- `spec.angle` 改為物件後前端顯示 `[object Object]` → 改三個下拉。

### 已知限制

- **MOSS 生圖沒有參考圖鎖臉**（本機只 t2i）：角色一致性靠 `identityAnchors` 逐字貼＋固定 seed，
  要先產「正面全身、中性背景」立繪寫回 `refImages`，H3 才有臉可鎖。
- **H3 生影片很慢**：768×1344／5 秒／steps 12 約 355–366 秒／鏡，6 鏡全跑約 36 分鐘。生影片必須背景跑、不可同步等。
- **16GB VRAM 上 LLM 與 ComfyUI 互斥**：生圖前要先停 llama（約佔 12–13GB），生完要釋放 ComfyUI 再啟回，否則掉到 0.6–4 t/s。
- **本機 27B 跑一鍵佈署不穩**：約 6 次有 4 次需重試（輸出截斷、漏引台詞），建議用線上模型。
- 多段合併的畫質對照樣本 seed 不同，不是乾淨 A/B 對照。
- 《雨夜的便利商店》殘留瑕疵：sh02 櫃台位置、sh04 前景肩、c03 下顎外露；大綱結尾斷句待修。
- 拆鏡頭無除重（重按會再長一批，需 `sourceBeatId` 欄位）；刪參考圖只從卡片移除，實體檔仍留在 `media/`。
- `compile_prompt` 的動作段沿用中文 beat 原文，H3 的 `promptEn` 混中文會掉穩定性（待翻譯或人工改）。
- 未上 GitHub（源碼已有本地 git 版控，遠端與 Release 附件下一版處理）。
