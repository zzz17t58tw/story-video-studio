# 發佈紀錄 v0.9.1

| 項目 | 值 |
|---|---|
| 版本 | **0.9.1**（預發版） |
| 日期 | 2026-11-19 |
| 發佈時間 | 2026-11-19 11:55:32（台北） |
| build 時間 | 2026-11-19 11:55:22 |
| git HEAD（發佈前） | 3fd6551 docs: M10a 合併紀錄（PLAN 進度列、交接單 status=merged） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.6 MB | `cce7cdf1e6fe71cc6014bc8192624839c146861c870773b9f5914923cd0596e5` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.1-乾淨版.zip` | 128.5 MB（134,759,200 B） | `a6f662675fcc7cdf91c0ee14c01b87b1effec13c471ab9bf137611c3a0e7c10e` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.1`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.1  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 11 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.1-乾淨版.zip
```

## 本版變更摘要

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

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 11 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
