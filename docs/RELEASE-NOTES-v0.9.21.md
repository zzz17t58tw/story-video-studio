# 發佈紀錄 v0.9.21

| 項目 | 值 |
|---|---|
| 版本 | **0.9.21**（預發版） |
| 日期 | 2026-11-24 |
| 發佈時間 | 2026-11-24 14:05:49（台北） |
| build 時間 | 2026-11-24 14:05:41 |
| git HEAD（發佈前） | d02b4b4 feat(promptrules): 三引擎送出口機械判定＋自動轉化（v0.9.21） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `c78cccb1d0264639c225acb006c876667aa76135033ad4fb875a9b3299a9f832` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.21-乾淨版.zip` | 129.0 MB（135,297,288 B） | `d3b275da610534104ace34ec2de2a0e003060a6bf805df620be7bb83359505c7` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.21`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.21  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.21-乾淨版.zip
```

## 本版變更摘要

### 修正：三隻生成底模的提示詞，在「送出的最後一道關卡」前機械轉化

**為什麼**（使用者原話）：「每個生成底模都有官方的提示詞規則與符號必須一致…三隻生成底模必須在生成送出的最後一個關卡前轉化成符合底模的規則。這是最硬條件，寫在後端機械判定。不合規則的提示詞一樣可以餵模型生成，不會報錯。但是生成品質嚴重影響。」

原本 M12b 只做到「檢查」，而且 Qwen／MOSS 的處理是**直接 raise 擋下**——非技術使用者看到紅字就卡死，但實際上那些提示詞送進去模型也不會報錯，只是品質崩掉。改成「**不擋人、自動轉化、把改了什麼講清楚**」。

- `app/promptrules.py` 新增三個送出口工具：
  - `sanitize_moss()`：移除編號分段（`1. Subject:`）、quality tag（masterpiece／8k／highly detailed…）、`<Picture N>`、caption 裡的尺寸比例（1024x1344、9:16 等）
  - `sanitize_qwen()`：`<Picture N>` → `<imageK>`（對不到這次送出圖的引用直接移除）、超範圍 `<imageK>` 移除
  - `finalize()`：機械判定 → 原稿合規回 `original`；可機械修正回 `sanitized`；修不動改官方組稿回 `built`；**兩步都修不乾淨就照送**並在 warnings 標明（絕不讓使用者卡在「生不出來」）
- 接線（四個送出口全走同一道）：`imagegen.build_prompt`（Qwen i2i／t2i）、`generate_moss_image`（鏡頭圖）、`generate_character_sheet`（人物立繪）、`server` 的 MOSS 預覽。H3 沿用「改用模板編譯」，一併在 `finalize()` 回報。
- 生成回應新增 `promptRule`／`promptSource`（original／sanitized／built／compiled），畫面上看得出來這次用的是原稿還是轉化後的版本。

**實測（純函式，未送 GPU）**
| 案例 | 轉化前 | 轉化後 |
|---|---|---|
| 手寫爛 MOSS caption（編號＋masterpiece/8k＋`<Picture 1>`＋1024x1344） | check=False，4 項違規 | 洗成「小棠站在便利商店櫃台後。她穿著藍色制服，低馬尾。」check=True |
| Qwen 引用送不出的槽位（`<Picture 9>`） | check=False | 自動移除並轉成 `<image1>`／`<image2>`，source=sanitized |
| H3 未編譯草稿（只有 1、2 段） | 缺四段 | 改用編譯稿 3196 字、六段齊全，source=built |
| 本來就合規的提示詞 | — | source=original，原樣放行（不多改一個字） |
| 空字串 | — | 退回官方組稿 2498 字，不拋錯 |

順手修掉一個真 bug：`1. Subject:` 用舊 regex 只吃得到前兩個字元，清洗後會留下「ubject:」——改用整段標題 regex。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
