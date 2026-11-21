# 發佈紀錄 v0.9.6

| 項目 | 值 |
|---|---|
| 版本 | **0.9.6**（預發版） |
| 日期 | 2026-11-21 |
| 發佈時間 | 2026-11-21 17:42:53（台北） |
| build 時間 | 2026-11-21 17:42:45 |
| git HEAD（發佈前） | 9e0c6c7 chore: v0.9.6（M14a 分鏡層） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `6815cb87251d460c591ac0570bf60c65f245c7fe4b41175b9ec244fab9ece104` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.6-乾淨版.zip` | 129.0 MB（135,248,915 B） | `c721d23010f39bb4f4f1f373a0ae56edb929f003c297b24635eb9798124cfaff` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.6`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.6  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.6-乾淨版.zip
```

## 本版變更摘要

### 新增：分鏡層（M14a）——把「故事」接回「鏡頭」

**為什麼**：使用者回報「鏡頭與劇本太過簡陋，故事要透過鏡頭與劇本演出來，但現在顛倒了，只看影片不懂在演什麼」。查證實測（`project-20261120-172537`）：劇本 2 場 × 9 beats 完整，但 6 個鏡頭的 `dialogBeatIds` 全是 `null`、每鏡 11–15 秒塞 2 個 beat——生成端只拿到一段畫面描述，所以影片只有畫面沒有戲。

**權威規格**：新增 `docs/storyboard-contract.md`（三層單一真相、長度規則、V1–V5 驗證）。與舊行為衝突時以該檔為準。

- **劇本 beats＝唯一真相**：分鏡只能挑選／分組，不得改寫台詞。
- **新分鏡欄位**：`beatIds`（主鍵）、`role`（建立／對白／反應／動作／插入／收尾）、`padSec`、`derivedFrom`、`beatPart`；`shot.dialogue`／`shot.beats` 停用（舊資料遷 `legacyBeats`）。
- **長度對齊 H3 硬限制**：合法 124–362 幀（5.17–15.08 秒）。單句估時不足 5.17 秒**先用留白補足**（上限 1.5 秒），超過才與相鄰 beat 合併；超過 15.08 秒自動拆鏡（切在原有標點，不改字）。
- **一鍵「🎬 由劇本自動分鏡」**：可選全部場次／單一場次，可保留手動調整過的鏡頭；零模型呼叫。
- **分鏡表**：鏡號｜職責｜誰｜台詞逐字（動作鏡給動作）｜秒數（含留白）｜累計時間——由上往下讀就是整齣戲。
- **驗證升級 V1–V5**：順序覆蓋、台詞覆蓋、長度合法、台詞唸得完、職責一致；**寫入時就把關**（只要這次寫入新增任何 error 就整批不寫，舊專案既有 error 不擋你改字）。

實測（複本，8792）：同一專案 18 beats → **18 鏡**（一 beat 一鏡）、總長 104.38 秒、每鏡 5.17–7.29 秒全在合法格、留白 ≤0.47 秒、`validate` 0 error；台詞逐字與劇本一致。舊專案（`project-20261118-022129`）讀取／驗證同樣 0 error，`beats` 已遷 `legacyBeats`。

同版修正：`_apply_allowed_list` 對扁平 set 誤用 `.get()`，導致「不允許寫入的欄位」這條錯誤變成 HTTP 500（現回可讀的 400 與可用欄位清單）。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
