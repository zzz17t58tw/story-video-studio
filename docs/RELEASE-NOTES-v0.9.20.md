# 發佈紀錄 v0.9.20

| 項目 | 值 |
|---|---|
| 版本 | **0.9.20**（預發版） |
| 日期 | 2026-11-24 |
| 發佈時間 | 2026-11-24 13:50:41（台北） |
| build 時間 | 2026-11-24 13:50:33 |
| git HEAD（發佈前） | 8124388 docs: PLAN 補回被 shell 吃掉的函式名 |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `921f5afb2c8ce8ddd31d1bf35923dc74302259ddbe4f4cd649fe05f530003dd3` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.20-乾淨版.zip` | 129.0 MB（135,291,738 B） | `a4bcd771a555233677f5e794091934be80dff21aff089ba00b7e238df02889ff` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.20`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.20  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.20-乾淨版.zip
```

## 本版變更摘要

### 修正：一鍵佈署的分鏡現在會把「動作 beat」也演出來（V1 不再永遠報錯）

**為什麼**：使用者問「這什麼問題，該修就修」。根因是部署時模型只回**台詞**引用（`dialogLineRefs`），動作 beat 永遠不會被任何鏡頭引用——所以 `validate` 的合約 V1（鏡頭 `beatIds` 串接＝場次全部 beats）對每一份部署資料都報 error；更嚴重的是「影片看不出在演什麼」，因為劇本寫的動作根本沒有鏡頭去演。

- 新增 `deploy._align_beats_with_storyboard()`：落地前用 `app/storyboard.build_scene_shots()` 的**規則骨架**（純規則、零 token）決定「每鏡演哪些 beats／多長／什麼職責」，模型只負責文字（`promptZh`／`promptEn`／`spec`）。對應方式＝模型的 `dialogBeatIds` 命中哪一個骨架組最多，就把那顆鏡頭的文字貼到該組；對不到的組用骨架自己的草稿，並標 `storyboardFilled=True`（UI 看得出來哪些是規則補的）。
- 掛在 `_land_shots()`（逐步落地與同步 preview 兩條路徑共用），任何例外都退回舊行為，**不讓部署因為這個補齊整批失敗**。
- **實測（喪屍爆發，複本資料）**：合約 V1 error **2 → 0**、`validate` 總 error **2 → 0**；場次 `e01s01` 3 鏡 → 7 鏡、`e01s02` 3 鏡 → 9 鏡（沒被演的動作 beat 全部補上鏡頭），長度全部落在 H3 合法格 5.17–15.08 秒。
- **行為改變要記住**：部署參數的「總鏡數」從此是**參考值**——真正的鏡數由劇本 beat 數決定（一鏡一事件）。劇本寫得細，片就會變長；要短請減少 beat 或縮短台詞，不要期待填 6 就得到 6 鏡。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
