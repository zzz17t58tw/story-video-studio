# 發佈紀錄 v0.9.13

| 項目 | 值 |
|---|---|
| 版本 | **0.9.13**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 14:44:56（台北） |
| build 時間 | 2026-11-22 14:44:46 |
| git HEAD（發佈前） | 9a7a3b0 revert: 擴寫大綱回到最初版本（只寫主線、不寫台詞）（v0.9.13） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `7f8a5a5b8b19e9cd1392e990ffa96941a3755ffd8ad125390f58a50059f10dff` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.13-乾淨版.zip` | 128.7 MB（134,905,845 B） | `d366ba2d77951249cc153b66cb493b098d15e0427f2107afe2efc0903400e1c3` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.13`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.13  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.13-乾淨版.zip
```

## 本版變更摘要

### 復原：擴寫大綱回到最初版本（只寫主線，不寫台詞）

**為什麼**：使用者實測「0.9.12 那樣的改動會讓模型去設計台詞，這不是故事大綱要做的事」，並指出「最一開始的反而最簡單且恰當」。核對 `ceb832a`（v0.9.0 基線）後確認：最初的大綱規則只要求主線（主角與動機、關鍵衝突、轉折、高潮、結尾），**完全沒有爆點／鉤子／台詞**，所以模型不會越層去編對白。

- **`prompts.py` 的 `story_outline` 逐字復原**（與 `ceb832a` 完全相同，腳本比對通過）：600–1000 字、分段、不要標題或編號清單、要包含主角與動機／關鍵衝突／轉折／高潮／結尾。
- **前端 `CHAT_TASKS.story` ① 復原**：回到「請把目前的故事概念擴寫成完整大綱（約 600–1000 字）…若已有大綱，請以它為骨幹整理並補強」的原始 prompt，移除「開場爆點／爆點與鉤子表」等字樣（保留 `applyHint`，那是後來才有的套用欄位）。
- **章回這一輪不動**（仍是前端 3 句），等大綱產出確認後再處理。已記下兩個待辦：(a) 章回送出後的答覆目前不能一鍵建立成章回、要手動搬；(b) 「與模型對話」的定位是協助完善主頁面，主頁面的每一項功能都要能在對話裡完成（回填要能直接落地）。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
