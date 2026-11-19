# Changelog

本檔記錄 Story Video Studio 的所有重要變更。
格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號規則見 `docs/VERSIONING.md`（0.x 為預發版）。

## [Unreleased]

_（尚無）_

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
