# 發佈紀錄 v0.9.15

| 項目 | 值 |
|---|---|
| 版本 | **0.9.15**（預發版） |
| 日期 | 2026-11-23 |
| 發佈時間 | 2026-11-23 12:13:41（台北） |
| build 時間 | 2026-11-23 12:13:33 |
| git HEAD（發佈前） | 06d262a feat(ui): 專案下拉改自製面板，每列恢復 ✕ 刪除並新增 ✎ 編輯名稱（v0.9.15） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `16b83f77326b7c93a2d37d9fd28b6562bed8ef745b2422a1cdd66639e8ef3910` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.15-乾淨版.zip` | 129.0 MB（135,276,323 B） | `40754e628654145e56beae8db65f11319363b1cf5b3fab4792e0cee25731423e` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.15`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.15  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.15-乾淨版.zip
```

## 本版變更摘要

### UI：專案下拉改自製面板，每列右側恢復「✕ 刪除」並新增「✎ 編輯名稱」

**為什麼**：0.9.14 把專案清單收成原生 `<select>`，塞不進每列的按鈕，刪除只能藏在下拉的最後一項。使用者要求「刪除維持上一版在每個專案的右側 ✕ 按鈕，並且新增編輯按鈕可改專案名稱」，並選擇保留下拉形式。

- 側欄仍是兩格（「＋ 新專案」＋下拉），但下拉改為自製面板：點一下展開，每列＝`專案名`＋`N 鏡`＋右側 `✎`（編輯名稱）與 `✕`（刪除）；面板寬 262px、可捲動、點外面或 Esc 收合。
- **改名**：`✎` 開站內彈窗 `#modal-rename`（Enter 送出、Esc 取消、空白名稱擋下），只改 `meta.name`，資料夾 slug 不動。目前專案走 `state.project.meta.name` ＋ `saveNow()`；非目前專案走 `GET /api/projects/{slug}` → 改 `meta.name` → `PUT`（沿用既有端點，後端零改動）。
- **刪除**：回到每列右側的 `✕`，確認框維持站內 `SVSConfirm`；取消不會刪、選取不受影響。
- 按鈕不加 `title` 提示，避免 0.9.14 修掉的黑框閃爍再回來。
- 影響半徑：`static/index.html`（專案區塊＋改名彈窗）、`static/app.js`（`renderProjectList`／picker／`renameProject`／init 綁定）、`static/style.css`（picker 樣式）。後端、其他分頁、儲存流程皆未動。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
