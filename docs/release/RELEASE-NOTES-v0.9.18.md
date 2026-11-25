# 發佈紀錄 v0.9.18

| 項目 | 值 |
|---|---|
| 版本 | **0.9.18**（預發版） |
| 日期 | 2026-11-23 |
| 發佈時間 | 2026-11-23 16:33:39（台北） |
| build 時間 | 2026-11-23 16:33:31 |
| git HEAD（發佈前） | 7f3946c chore: v0.9.18（減枝 -184 行、佈署按鈕 aria-label） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `a16229b73ecbe0ae70f9308b559bea374c96e64ec165c2886a36be0f9777af87` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.18-乾淨版.zip` | 129.0 MB（135,287,319 B） | `ef54e914545e1d9a99ed6561d36f531c051fd87bd43f633563f720dc9b9728d5` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.18`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.18  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.18-乾淨版.zip
```

## 本版變更摘要

### 減枝：移除 8 項死代碼（-184 行），行為不變

**為什麼**：使用者定調優化重點是「減枝——精闢瘦身但不影響功能」。以下 8 項經全 repo 引用檢查（app/、static/、tools/、scripts/，含字串、`__all__`、`getattr`、`importlib`）確認 0 引用，或已被新路徑取代。

- `deploy.apply_preview()`（121 行）：逐步落地後由 `run_steps_landing()` 取代，同步 `preview()` 已不再呼叫。
- `h3gen._used_pictures()`（2 行）：`build_timeline` 改用 `_sendable_ordinals` 加 `promptrules.check()`。
- `imagegen._local_media_path()`（3 行）、`_same_project_mtime()`（5 行）：舊寫回路的遺留。
- `prompts.task_rules()`（3 行）：只供文件參考，無人呼叫。
- `storage.get_path()`（10 行）、`storage.shot_dialog_beat_ids()`（20 行）：M12a 後由 `dialog_beat_index`／`migrate_dialog_linkage` 接手。
- `storyboard.beat_seconds()`（3 行）：`_estimate_group` 已被直接呼叫。

**驗證**：`compileall` 與 `import app.server` 全過；8 個符號刪後全 repo 0 命中；12 個既有專案 `validate_project` 改前改後皆 0 issue／0 error；HTTP 冒煙（health、projects、agent coverage 59/59、POST validate、storyboard build dryRun、prompt-rules、app.js）全 200，server log 無 error。

**保留未刪**：`imagegen.generate_character_sheet_angles()` 目前 0 引用，但它是唯一包 `gpu_for_comfy` 的正確入口，已記入 `docs/TODO-backlog.md`。

### 無障礙

- 三顆「開始佈署」按鈕補 `aria-label`（螢幕閱讀器可分辨用途），文字與行為不變。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
