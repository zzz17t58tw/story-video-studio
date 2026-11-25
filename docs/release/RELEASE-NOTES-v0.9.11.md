# 發佈紀錄 v0.9.11

| 項目 | 值 |
|---|---|
| 版本 | **0.9.11**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 13:49:39（台北） |
| build 時間 | 2026-11-22 13:49:29 |
| git HEAD（發佈前） | 9e23cc6 refactor: 故事提示詞整段重寫、去重，前端不再重述規則（v0.9.11） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `b79f4928133fc509c69802ae32d4ba90521fe792ba1117d0299e5265ff1c192d` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.11-乾淨版.zip` | 128.7 MB（134,906,413 B） | `f66ddd4462fd3eb46d520432c70bbc86f815ee2da7cc4a55029dd83f7fe87243` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.11`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.11  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.11-乾淨版.zip
```

## 本版變更摘要

### 整理：故事提示詞去重，規則回到單一真相

**為什麼**：0.9.10 的做法是「在舊句上疊新句」，結果自己打自己——`story_outline` 的主線叫人「展開成衝突升級」，爆點段又禁止寫「衝突升級」；`story_chapters` 的「解開懸念」在 ④ 與禁止清單各講一次、「整回沒有衝突」與「開場爆點／中段爆點」重複；前端 `CHAT_TASKS` 又把同一套規則重述一遍。三個來源互相打架，模型只會被分散注意力。

- **`prompts.py` 兩個任務整段重寫**（不是打補丁）：每條規則只出現一次，刪掉舊版遺毒——「反轉／打臉／收割」情節清單、「開場鉤」這個誤稱、「常見形式是…」的補充句。大綱改成三個具名段落（開場爆點／主線／爆點與鉤子表），章回改成四段固定順序＋獨立的寫作／禁止／收尾標記區塊。
- **前端 `CHAT_TASKS` 只留任務與資料**：故事頁 ①② 的 prompt 現在只寫「要做什麼」＋風格四項＋概念／大綱，不再重述任何規則；規則的唯一來源是 `prompts.py` 的 `_TASK_RULES`。
- **驗證（腳本檢查，不是目視）**：「衝突升級」1 次、「解開懸念」1 次、「反轉／打臉」0 次、「開場鉤」0 次、「常見形式」0 次；`py_compile`／`node --check` 通過。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
