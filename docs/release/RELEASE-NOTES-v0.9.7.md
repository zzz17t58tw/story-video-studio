# 發佈紀錄 v0.9.7

| 項目 | 值 |
|---|---|
| 版本 | **0.9.7**（預發版） |
| 日期 | 2026-11-21 |
| 發佈時間 | 2026-11-21 18:25:44（台北） |
| build 時間 | 2026-11-21 18:25:36 |
| git HEAD（發佈前） | bb19562 feat(agent): Agent 對接層——外部 AI 代理人可掌握全部細節（v0.9.7） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `0a1ac83f24ae9219f3f91b49d482b0acea2305ace48901988315bc180018338a` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.7-乾淨版.zip` | 129.0 MB（135,265,012 B） | `7f5fdfbecd12d693039aa5a60550e1de320877f5f4a6139fedbe366290ea2f01` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.7`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.7  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.7-乾淨版.zip
```

## 本版變更摘要

### 新增：Agent 對接層——外部 AI 的代理人能掌握這支程式的每一個細節

**為什麼**：使用者要求「如果有使用者接入 AI，對方的代理人就能掌握這支程式的每一個細節」。實際痛點是：這支程式有 59 支 API、三層資料模型、一堆硬限制（H3 影格網格、取景規則、單張 GPU），外部 AI 只能靠猜，猜錯就寫壞資料或白花生成時間。

- **`GET /api/agent/manifest`**：機器可讀的完整說明。端點清單、參數 schema **直接從 FastAPI 的 OpenAPI 產生**（不會與程式漂移），再補上人工語意：每支端點的「用途／何時用／副作用」。另含 8 條硬限制、資料模型骨架、V1–V5 驗證規則、6 個常見配方、8 條禁止事項。
- **`GET /api/agent/guide`**：一份約 1.6 萬字元的 Markdown 操作手冊（可直接塞進 context）：連線資訊、操作順序、硬限制表、驗證規則表、配方步驟、禁止事項、全部 API 索引、背景任務輪詢方式、錯誤碼語意。
- **`GET /api/agent/coverage`** 與 **`tools/check_agent_doc.py`**：文件完整度自檢。**新增 API 卻忘了寫說明，檢查會失敗**（目前 59/59 全數有說明）。
- **`AGENTS.md`**（repo 根目錄）：入口檔，告訴任何 AI 先讀哪三個端點、三條必踩的硬限制、寫資料的唯一正確方式、文件地圖與行為準則。
- 單一真相：`app/agentdoc.py` 集中 `ROUTE_NOTES`／`HARD_LIMITS`／`DATA_MODEL`／`VALIDATION_RULES`／`RECIPES`／`FORBIDDEN`。

實測（8790，複本）：模擬「外部 AI 只讀 manifest 就操作」——找到分鏡重建端點 → 重建 18 鏡（104.38 秒）→ `validate` 0 error → 讀分鏡表 → coverage 59/59。`GET /api/agent/guide` 回 `content-type: text/markdown`、16443 bytes。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
