# 發佈紀錄 v0.9.4

| 項目 | 值 |
|---|---|
| 版本 | **0.9.4**（預發版） |
| 日期 | 2026-11-21 |
| 發佈時間 | 2026-11-21 12:50:38（台北） |
| build 時間 | 2026-11-21 12:49:20 |
| git HEAD（發佈前） | d819547 chore: v0.9.4（M12a 鏡頭↔劇本、M12b 提示詞規則） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.6 MB | `74f21320df3c314143884cb1c51b3c14511e318ad43dbcb92a90101f6938b71d` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.4-乾淨版.zip` | 128.9 MB（135,173,982 B） | `1b47e67873813b5fd76d11de6a092a51f02b8a587f952c28d273469d9d518ad7` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.4`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.4  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.4-乾淨版.zip
```

## 本版變更摘要

### 新增：鏡頭 ↔ 劇本互相連動（M12a，使用者回報「兩個對不上」）

**為什麼**：劇本頁有台詞，但鏡頭卡的「對白」是另一份自由欄位、完全不顯示引用 → 畫面上顯示「這個鏡頭沒有對白（純畫面）」，看起來像沒連動；而且引用存「第幾句」的序號，在劇本頁插一句／刪一句／調順序，所有鏡頭的引用會集體錯位且看不出來。

- `shot.dialogBeatIds`（新，**主鍵**）：本鏡演出哪幾句台詞，存所屬場次的 `beat.id`；`dialogLineRefs` 變成由它換算的衍生值。舊專案讀入時自動遷移（`storage.migrate_dialog_linkage`，換算不到的句號保留原值並回報，不 400、不清空）。
- 鏡頭卡新增「引用劇本台詞」：勾選清單（第 N 句 · 角色：「台詞」（情緒）），三態標籤「本鏡引用／被 sh0X 引用／沒有鏡頭引用」，另有唯讀「本鏡台詞（依引用順序）」。
- 劇本頁每句台詞標「← 被 sh01、sh02 引用」或「（尚無鏡頭引用）」。
- `h3compose` 的 `compile_prompt` ⑥ 段與 `validate` 改為 beat id 優先；`deploy` 寫入鏡頭時同時寫 `dialogBeatIds`。
- 原本的自由對白欄位保留，但標明「本鏡臨時對白（不寫進劇本）」。

### 新增：每個生成引擎的官方提示詞規則寫死在程式裡（M12b）

**為什麼**：H3 影片送件口原本只要 `promptEn` 有字就原樣送出，不檢查六段式、也不檢查有沒有 `<Picture N>`——實測使用者的 6 鏡 promptEn 是 839–993 字的未編譯草稿、一個 `<Picture` 都沒有，模型等於沒有 conditioning，會亂生而且不報錯。規則只活在「⌘ 模板編譯」那顆按鈕裡，不在出口。

- 新增 `app/promptrules.py`（規則的**唯一真相**）：`h3_video`（六段式＋`<Picture N>` 對應 referencePlan 槽位）、`moss_t2i`（連續英文 caption、不編號、禁 quality tag、不含 `<Picture`、尺寸不進 caption）、`qwen_image`（`<imageK>` 對應實際送出的圖、不得留 `<Picture N>`）。每張規則卡含中文條列與一手來源（模板檔名／`docs/contracts.md`）。
- 規則分成**硬性違規**（送件口擋下或改用模板編譯）與**品質提醒**（照送，只提示）：實測 caption 幾乎都會超過建議字數（identityAnchors 逐字貼就將近 1000 字）、特寫鏡頭組稿刻意不放臉部錨點——這些若當硬規則，MOSS 幾乎每鏡都生不出來。
- H3 送件口 `h3gen._pick_prompt`：`promptEn` 未過規則就自動改用 `compile_prompt` 的即時結果（**不寫回 project.json**），並在 warnings 寫明缺什麼；`_meta` 新增 `promptRule`／`promptSource`／`promptProblems`，job 也回報「這次是 promptEn 還是模板編譯」。
- 新 API：`GET /api/prompt-rules`、`GET /api/projects/{slug}/shots/{shot_id}/prompt-preview?engine=h3_video|moss_t2i|qwen_image`（純讀、不碰 GPU、可看這次真的會送出的字）。
- 前端：生圖／生影片區塊加「規則：<引擎名稱>」徽章與「看這次要送出的提示詞」面板（規則條列、未過規則、品質提醒、全文展開／複製）；按「生影片」前若會改用模板編譯，會先列出違規項再讓你決定。

實測（dev 8790，複本資料）：baseline 專案 `promptEn` 2241 字且無 `<Picture>` → `build_timeline` 產出 3196 字含 `<Picture 1>` 的編譯稿、`promptSource=compiled`、原 `project.json` 未被改寫；合格 promptEn 則 `promptSource=promptEn`、problems 空；MOSS 負向（含 masterpiece／best quality／8k／768x1344／`<Picture`）三條全報並擋下，正常 caption 通過。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
