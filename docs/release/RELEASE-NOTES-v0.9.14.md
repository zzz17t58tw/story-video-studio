# 發佈紀錄 v0.9.14

| 項目 | 值 |
|---|---|
| 版本 | **0.9.14**（預發版） |
| 日期 | 2026-11-23 |
| 發佈時間 | 2026-11-23 12:02:49（台北） |
| build 時間 | 2026-11-23 12:02:41 |
| git HEAD（發佈前） | ba96496 feat(ui): 左側專案清單改為下拉選單，切換專案不再閃提示框（v0.9.14） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `16b83f77326b7c93a2d37d9fd28b6562bed8ef745b2422a1cdd66639e8ef3910` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.14-乾淨版.zip` | 129.0 MB（135,274,601 B） | `3e68cf2ae3878f00cee4e1f51746ee7aafede1ef0bc9d5e714b5b3fde864a4e4` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.14`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.14  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.14-乾淨版.zip
```

## 本版變更摘要

### UI：左側專案清單改成下拉選單（垂直切片，其餘不動）

**為什麼**：專案一多，左側清單就把「製作流程」導覽往下擠，使用者要求「縮成下拉選單，只留開新專案按鈕與下拉選單兩格」。

- `static/index.html` 的專案區塊：`<ul id="project-list">` 換成 `＋ 新專案` 按鈕＋`<select id="project-select">` 兩格（同一列）。
- `renderProjectList()` 改為渲染 `<option>`（文字＝「專案名（N 鏡）」）；未選專案時第一項是「請選擇專案…」，選單本身不再重建，所以切換時不會再讓游標底下的按鈕被換掉。
- 刪除專案收進同一個下拉（最後一項「－ 刪除目前專案（移到 _trash）－」），選到先跳站內 `SVSConfirm`，取消就把選取還原、不會誤刪；原生 `confirm()` 只剩沒有 SVSConfirm 時的退路。
- 影響半徑：只有側欄專案區塊與 `deleteProject()` 的確認框；導覽分頁、各頁卡片、儲存流程、後端 API 全部未動。

### 修正：切換專案時「一閃即逝的小黑框」

**原因**：舊清單每顆專案按鈕都帶 `title="開啟 <專案名>"`；切換專案時 `renderProjectList()` 把按鈕整批重建，游標底下的新節點重新觸發原生提示框（WebView2 深色主題＝小黑框），提示框隨即又被下一次重繪收掉。

- 專案按鈕連同 `title` 一起移除，改成下拉選單（同上）→ 切換時不再有會被重建的提示框。

### 一併入庫：前次已完成但尚未 commit 的修正

- 多角度設定表：`use_layout_image` 預設關閉並移除 layout 鏈（黑框／座標文字不再畫進成品）、`crop_sheet()` 支援上下內縮裁切；`clip` 第一候選改為實測可載入的 `qwen3vl_32b_heretic_minimax_h3_nvfp4`（原 int8_convrot 會 dequantize 失敗）。
- 參考圖：角色的刪圖改成立刻落地（`refImageGrid({saveImmediately:true})`），場景維持 debounce。
- 性別下拉：`selectField(..., keepUnknown)` 只給性別欄位開啟，舊資料的長句仍可顯示。
- `background` 欄位停用但保留白名單與重新佈署時的保留邏輯（舊資料不會被清掉）；負向提示只注入故事擴寫／章回任務，不再進全部聊天任務的 context。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
