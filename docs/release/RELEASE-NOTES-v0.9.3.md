# 發佈紀錄 v0.9.3

| 項目 | 值 |
|---|---|
| 版本 | **0.9.3**（預發版） |
| 日期 | 2026-11-21 |
| 發佈時間 | 2026-11-21 12:07:40（台北） |
| build 時間 | 2026-11-21 12:07:32 |
| git HEAD（發佈前） | 2e9b582 fix(ui): 佈署確認框被參數窗蓋住（0.9.3） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.6 MB | `b122c78ea7ca36648f376ad7cde84ee26b912608f30906b134e86b02dd60c130` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.3-乾淨版.zip` | 128.9 MB（135,142,068 B） | `f1a1e0ee41d2fba5ddb662caf9b4345575f12790014f178ee2643892d73ca6f7` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.3`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.3  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.3-乾淨版.zip
```

## 本版變更摘要

### 修正：佈署確認框被佈署參數窗蓋住（使用者回報）

**症狀**：按「開始佈署」跑出「覆蓋現有內容？」確認框時，確認框其實在**下面一層**——
因為 `#modal-deploy`／`#modal-gen` 是執行時才 append 到 `body`、DOM 順序在 `#modal-confirm` 之後，
而三者 z-index 同為 50，後進 DOM 的佈署窗就把確認框整片蓋住。使用者滑鼠點不到「清空並重建」，
只能在佈署窗背景亂點（那一下又把參數窗關掉），才會出現「在警告窗按繼續後參數窗不見了」的錯覺。

- `SVSConfirm()` 開啟前計算「目前所有可見彈窗的最大 z-index」，把確認框抬到其上（+10），關閉時還原。
- CSS 給 `#modal-confirm` 一個高於一般彈窗的預設層級（70，仍低於燈箱 80），雙重保險。
- 確認框開著時（`window.__svsConfirmOpen`）佈署窗的「點背景關閉」失效，避免誤以為參數窗被吃掉。
- 連按「開始佈署」不再開出兩個確認框（`dpState.confirming`）；完成摘要的「總計」缺 `elapsed` 時顯示 `—` 而不是 `undefineds`。

實測（dev 8790，真實滑鼠點擊）：修正前確認框 OK 鈕中心 `elementFromPoint` 是佈署窗的 `.dp-params`；
修正後確定打到 `.cf-ok`，按下去送出 1 次 `POST /deploy`（用假回應攔截，不動模型），
取消則回到參數窗、四個欄位與「開始佈署」仍可按。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
