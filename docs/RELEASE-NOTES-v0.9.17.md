# 發佈紀錄 v0.9.17

| 項目 | 值 |
|---|---|
| 版本 | **0.9.17**（預發版） |
| 日期 | 2026-11-23 |
| 發佈時間 | 2026-11-23 15:29:10（台北） |
| build 時間 | 2026-11-23 15:29:02 |
| git HEAD（發佈前） | 3861099 chore: v0.9.17（分鏡缺口可見、長台詞處方、自動拆句、佈署按鈕文字、死代碼瘦身） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `8c27afca30bc3fd22e191ae1166e00a60189001fd45f4beca05defef538b30b8` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.17-乾淨版.zip` | 129.0 MB（135,293,146 B） | `5abf7e883e16047ef3360bb7ad58253914ae1eef6a2d5c7c07d58f4415a9cea4` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.17`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.17  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.17-乾淨版.zip
```

## 本版變更摘要

### 修正：分鏡缺口看得到、長台詞給得出處方、佈署按鈕不再同名

**為什麼**：使用者抱怨「影片看不出在演什麼」，根因之一是**一鍵佈署的分鏡只挑台詞 beat，動作 beat 從來沒有鏡頭**，而 validate 對「哪些演出點沒被演到」完全不報——資料缺了也看不出來。

- **分鏡表 API 補上缺口資訊**：`GET /api/projects/{slug}/storyboard` 新增 `scenes[]`（每場次 `unshot`／`unshotCount`／`note`）與 `unshotTotal`。實測「喪屍爆發」`unshotTotal=10`，逐場次寫出「劇本有 9 個演出點，鏡頭只演了 4 個：b01、b03、b05、b07、b09 沒有對應鏡頭」。前端分鏡表上方顯示琥珀色缺口框。
- **V4「台詞唸不完」改成可照著做的處方**：台詞超過 362 幀上限時直接寫「一定要拆鏡，建議拆成 N 鏡」（原本只說「請加長或拆鏡」，超過上限時「加長」是空話）；沒超過時給出「durationSec 設到至少 X 秒（Y 幀）」。
- **「🎬 由劇本自動分鏡」會先拆長台詞**：新增 `storyboard.split_long_beats()`，台詞長到一鏡唸不完時依標點拆段（**台詞一字不改**），第二段起 id 為 `<原id>p2`，再重建分鏡。同時修掉底層真 bug：`_split_long_beat()` 讓各段共用同一 id，`beatIds` 變成 `['b04','b04']`，重建被 V1＋重複引用直接擋下。實測「喪屍爆發」重建後 validate **2 error → 0 error、6 鏡 → 16 鏡**。
- **缺參考圖 warn 的去重補第二種匹配形狀**：已合併過的訊息尾巴「（被 第 N 鏡 用到）」在逐鏡掃描時又被當成新的一筆加回來，12 鏡專案會長出 6 條重複；修好後 12 鏡專案 warn 26 → 20。
- **UI**：三顆佈署按鈕的文字由「🚀 一鍵佈署 XX」改成「**開始佈署**」（原本標題與按鈕同字、畫面出現兩次，使用者會以為是重複按鈕）；校驗面板新增「✅ 已自動處理 N 項」獨立區塊。
- **瘦身**：移除 4 項 0 引用死代碼（`storyboard._chars`、`storygen._existing_episode_ids`、`MAX_FRAMES_PER_SHOT` 收斂為既有的 `FRAME_MAX` 等，合計 −8 行）。等價證明：`py_compile`＋`node --check` 全過、刪除後 grep 0 引用、逐專案 validate 對照顯示**沒有任何專案變差**。

### 已知、待人工決定
- V1「鏡頭 beatIds 串接＝全部 beats」對既有部署資料仍是 error（部署只挑台詞、動作 beat 沒鏡頭）。目前可在鏡頭頁按「🎬 由劇本自動分鏡」重建解決；若要從源頭修，得改 `deploy.gen_shots_for_scene` 把動作 beat 併進相鄰鏡。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
