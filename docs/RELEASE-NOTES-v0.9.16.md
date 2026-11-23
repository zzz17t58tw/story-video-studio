# 發佈紀錄 v0.9.16

| 項目 | 值 |
|---|---|
| 版本 | **0.9.16**（預發版） |
| 日期 | 2026-11-23 |
| 發佈時間 | 2026-11-23 13:18:54（台北） |
| build 時間 | 2026-11-23 13:18:46 |
| git HEAD（發佈前） | 661b8c2 feat(deploy): 一鍵佈署拆成角色與場景／劇本／分鏡三個範圍（v0.9.16） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `1f75fba041bae42657761c2e93f4cf29557e72959b4cd5f27af1ea985e87d40d` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.16-乾淨版.zip` | 129.0 MB（135,284,502 B） | `834164f1914f1e1a7bf55f3cdad1c6531ee075ce124978351a62ad19f6a8dedc` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.16`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.16  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.16-乾淨版.zip
```

## 本版變更摘要

### 一鍵佈署拆成三個範圍（故事頁縮範圍，劇本／鏡頭各一顆）

**為什麼**：使用者指出「一鍵佈署範圍太大要拆開」——一次跑四層要 2–5 分鐘，中途失敗很難判斷是哪一層；而且故事頁那顆按鈕實際做了劇本與分鏡，跟「故事寫完只想先有角色與場景」的直覺不符。

- **故事頁**：按鈕改名「🚀 一鍵佈署到角色與場景」，只跑 `cast`＋`locations`（約 46 秒實測），劇本與分鏡留給各自的頁面。
- **劇本頁**：新增「🚀 一鍵佈署劇本」卡（`scope=script`）——用**專案現有的**角色卡／場景卡寫一集，這些卡片不動（實測 15 秒）。
- **鏡頭頁**：新增「🚀 一鍵佈署分鏡」卡（`scope=shots`）——只為**最新一集**的場次拆鏡頭，劇本／角色／場景都不動（實測 2 場 67 秒）。
- 後端：`deploy.SCOPES`／`SCOPE_LABEL`／`norm_opts` 的 `scope`；新 `_run_cast_locations`／`_run_script_only`／`_run_shots_only`；`_check_prereq()` 依範圍給「先按哪一顆」的中文指示；清理改成按範圍（`_purge_script_layer` 只清劇本＋鏡頭、`_purge_shots_of_scenes` 只清該集場次的鏡頭）。`run_steps_landing()` 對 `scope=all` 走原本四步流程，其餘在前面分派——舊行為完全保留。
- 舊呼叫端（不帶 `scope` 的 `POST /deploy`）＝`all`，向後相容。
- 前端：`DP_SCOPE` 決定彈窗標題、要顯示哪些參數、覆蓋說明的文字與進度清單；`reattachDeployJob()` 從 job 的 `params.scope` 還原範圍。三個按鈕都**沒有** `title` 提示。
- 驗證：`scripts/check_deploy_scopes.py`（離線、假模型；斷言三個範圍只動自己那層＋前置條件會擋），另有線上實跑 46.5s／15.0s／67.4s 三段。
- 影響半徑：`app/deploy.py`（新增範圍函式、`start/preview` 前置檢查）、`app/server.py`（`DeployBody.scope` 與 opts 傳遞）、`app/agentdoc.py`（route note）、`static/app.js`（三張卡＋彈窗）。各分頁原有卡片、生成／合併、對話抽屜皆未動。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
