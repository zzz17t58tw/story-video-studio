# 發佈紀錄 v0.9.12

| 項目 | 值 |
|---|---|
| 版本 | **0.9.12**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 13:58:06（台北） |
| build 時間 | 2026-11-22 13:57:56 |
| git HEAD（發佈前） | f4acaad refactor: 故事規則全部移到前端、各 3 句；後端移除兩個任務規則（v0.9.12） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `130c841fc873d74994ade19fd2625eec4003e4714d6023d7a5e03f0dff880b0b` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.12-乾淨版.zip` | 128.7 MB（134,905,128 B） | `8be239e1a34d6a619ac836c8c0d643943c9b165d30afac356ff01e28731e76bd` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.12`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.12  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.12-乾淨版.zip
```

## 本版變更摘要

### 調整：故事規則全部留在前端，各只剩 3 句

**為什麼**：使用者要求「全部都留在前端，而且只留 3 句話」。0.9.11 雖然去重，但規則仍分兩層（後端 `_TASK_RULES` ＋ 前端樣板），模型收到的指示還是比需要的多。

- **後端移除兩個任務規則**：`_TASK_RULES` 刪掉 `story_outline`／`story_chapters`（整段移除，不是清空），後端不再有第二份故事規則。`build_system()` 對這兩個任務只剩「專案資料＋通則」，之前是 3814 字，現在 1220 字。
- **前端各 3 句**：故事頁 ① 只寫「三段格式」＋「爆點是抓住當下注意力的一句台詞或一個畫面，鉤子是結尾讓人想看下一段的未解問題」＋「風格四項與概念」；② 只寫「3 回、每回 500–800 字」＋「開場爆點→二選一→中段爆點→結尾鉤子，不准收束」＋「風格與大綱」。
- 驗證：`_TASK_RULES` 已無這兩個 key；`build_system(..., 'story_outline')` 不含【本次任務】區塊、字數 1220；`py_compile`／`node --check` 通過。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
