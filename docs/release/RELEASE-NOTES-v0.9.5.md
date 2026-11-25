# 發佈紀錄 v0.9.5

| 項目 | 值 |
|---|---|
| 版本 | **0.9.5**（預發版） |
| 日期 | 2026-11-21 |
| 發佈時間 | 2026-11-21 13:34:58（台北） |
| build 時間 | 2026-11-21 13:34:50 |
| git HEAD（發佈前） | d30f76f chore: v0.9.5（M13a+M13b 對話回填） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.6 MB | `ce969689117331c1f0d2c4134ecb3ed01c000fc64217720fc5c9a30152c8bf92` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.5-乾淨版.zip` | 128.9 MB（135,209,596 B） | `0d4e33fb4a5e8b43aba8aeb487c45bf8c6966a745481e4a8bc47cb4e9d8a6871` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.5`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.5  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.5-乾淨版.zip
```

## 本版變更摘要

### 修正／新增：「與模型對話」的回填真的能填完主頁面（M13a＋M13b，使用者回報「回填形同虛設」）

**為什麼**：回填 API 一次只能寫「一個欄位、一段文字」，白名單也沒開 `chapters`／`beats`／`dialogue`／`outfits`——模型回得再好，使用者也只能自己複製。逐條對過程式後確認：章回完全沒有可套欄位、場次 beats 完全不能回填、風格四項／外型四欄／身世四欄要複製四次、三版台詞與三個音色只能自己挑一句貼。

**M13a 地基（資料層＋多欄面板）**
- 新增 `POST /api/projects/{slug}/chat/apply-batch`：一次寫多項，單次驗證、單次原子寫檔；任一項不合法就整批不寫並指出「第 N 項（path）」。
- 白名單開到結構化欄位：`chapters`、場次 `beats`、鏡頭 `dialogue`／`beats`、人物 `outfits`、`spec.angle.h|v|s`。整份陣列有結構驗證：缺 id 自動編號、`type` 只收 dialogue／action、`characterId` 給名字會自動對到 id（含別名／錯一字），對不到存 null 並回報 warning。
- 場次 beats 被換掉時，引用它的鏡頭 `dialogBeatIds`／`dialogLineRefs` 會自動對齊並回報；被換掉的舊 beat 標 `_skipped` 保留（劇本頁收在「已作廢」可還原、不進生成）。
- 前端回覆氣泡「⇣ 解析成多欄」：規則式解析（「第 N 回」「角色名：台詞」「類型：…」）→ 可編輯預覽清單 → 確認才寫檔；只選取回覆裡的一段時，陣列類預設「追加」而不是整份蓋掉。
- 修掉「按下按鈕選取範圍就被收掉」導致只套到整段回覆的問題（改用 selectionchange 快取）。

**M13b 任務對應（每個任務自己說要填哪格）**
- `CHAT_TASKS` 23 個任務全部帶 `applyHint`：按鈕上直接顯示「→ 回填：整份章回／風格四項／外型四欄＋promptEn／身世四欄／音色描述…」；「③ 檢查與人物卡是否矛盾」標「僅建議，不回填」。
- 回覆氣泡一鍵「⇣ 回填到 <目標>」：只展開並預勾這次任務的目標格子，其他格子收起來。
- 新增外型解析（`parseAppearance`）：臉型五官／髮型／體型／膚色／英文 prompt 一次填五格。
- 多版本候選（三版台詞、三個音色）→ 回覆下方出現「用第 N 版」按鈕，點一下直接寫入對應欄位。

實測（dev 8790，複本資料，全程注入假回覆、不呼叫模型）：`chapters`(3 回)＋`style.tone` 一次寫入且 validate error 0；「林旭：台詞」自動對到 c01、錯名字存 null 並警告；不合法輸入 400 且 `project.json` 位元組不變；外型五欄一鍵寫入後 API 重讀正確；三版台詞點「用第 2 版」後 `b02` 變成第 2 版原文；`shot_check_consistency` 無回填按鈕、標「僅建議」。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
