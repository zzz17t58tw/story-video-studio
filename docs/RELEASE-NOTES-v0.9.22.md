# 發佈紀錄 v0.9.22

| 項目 | 值 |
|---|---|
| 版本 | **0.9.22**（預發版） |
| 日期 | 2026-11-24 |
| 發佈時間 | 2026-11-24 20:06:12（台北） |
| build 時間 | 2026-11-24 20:06:04 |
| git HEAD（發佈前） | c00a61e docs(使用說明): v0.9.22 版本行＋新增七之一（鏡頭頁即時連動、只補洞、手改稿保全、孤兒清理） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `ce19bf7991f634154f9c604c6f9ddbb42a056d7c1ae4fc5158b0da4bea35eb9f` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.22-乾淨版.zip` | 129.1 MB（135,319,648 B） | `919ee65c5d73b022b9fe6e7323769091ae1dc6e126a00691b665d10214aed63b` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.22`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.22  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.22-乾淨版.zip
```

## 本版變更摘要

### 新增：鏡頭頁即時連動（衍生值一律用當下真相重算）

**為什麼**（使用者原話）：「這個鏡頭頁面對於之前的連接非常低，很多都是沒辦法正常抓取，又或者在其他頁面反覆修改然後跳回鏡頭頁準備生成時根本還是舊資料。」→ 要求「全部都要機械的即時連動，而且是嚴謹的」。

權威規格：`docs/shots-live-linkage.md` v1.1。核心是把欄位分成兩類——**真相**（劇本 beats／人物／場景／分鏡決策，只由人或模型寫入）與**衍生**（`referencePlan`／`promptEn`／`dialogLineRefs`／場次覆蓋狀態／孤兒判定），後者**任何時候讀到它都必須等於用當下真相重算一次的結果**。

- `h3compose.refresh_derived(project)`：逐鏡重算衍生值並寫 `shot.derived = {at, promptChars, promptSource, promptEnStale, adoptedManual, slots, pictures, orphan, uncoveredBeats}`；單鏡失敗只記 `derived.error`，永不 raise。另新增 `scene_coverage()`（每場 covered／missing／uncoveredDialog，純讀）。
- **兩處接線**：`storage.save_project()` 寫檔前重算；`GET /api/projects/{slug}` 與 `_load_for_m3d()` 在 **deepcopy** 上重算（**讀端不寫檔**，實測四個唯讀端點打完 `project.json` 的 sha256 與 mtime 不變）。
- **手改稿保全（資料流失修正）**：舊版前端改過的 `promptEn` 沒有 `promptEnManual` 旗標，若照舊覆蓋會直接吃掉手工成果（實測 sh10 的 1881 字被換成 2596 字、主體從「塞蕾娜」變「林恩」）。判定改三段：有旗標→保留；非空且 ≠ 編譯稿且無旗標→**採認為手寫稿**（`promptEnManual`／`promptAdoptedManual`，`derived.adoptedManual` 持久化）；空或同值→寫入編譯稿。唯一例外是「一鍵佈署的短草稿」照舊自動編譯。按「⌘ 模板編譯」會清掉手寫旗標（回應 `promptManualCleared`）。
- `storyboard.backfill_shots()`：劇本插句後**增量補洞**——只為沒有任何鏡頭引用的 beat 補新鏡（`fillReason="backfill"`），只增不刪、既有鏡頭的 `beatIds` 一字不動。接進 `POST /storyboard/build`（預設先補洞，`force=true` 才全重建）。
- **排序鐵律**：沒有 `beatIds` 的既有鏡（手動／重複／孤兒）一律排在該場次所有有 `beatIds` 的鏡**之後**，既有鏡頭彼此相對順序不變。修正前的行為會把它們搬到場次最前面（`sh25–sh28` no=5…8 → no=1…4），害原本的 `establish` 退到第 5 鏡、合約 V5 必然報 error、`/storyboard/build` 回 400 擋下補鏡。
- **孤兒鏡頭**：`derived.orphan`＋`GET /storyboard` 的 `orphanShotIds`（`_load_for_m3d` 現在也會重算，否則按鈕永遠不出現），鏡頭卡顯示徽章、場次標題提供「移除本場未連動的鏡頭（N）」——**不自動刪**，走站內確認框。
- 前端：進「鏡頭／生成」頁前先存檔再整份重抓（存不進去就不覆蓋未存編輯）；生圖／生影片 job 完成改成整份重抓；鏡頭卡提示詞區塊標頭顯示衍生摘要（`已同步（N 字／M 張圖）`／`⚠ 手動稿已落後`／`⚠ 舊版手改稿已保留`）。
- 校驗未放寬：V1（順序覆蓋）與 V5（establish 只能場次第一鏡）維持 error 級，過程中曾一度降為 warn，實測補洞排序正確後已收回。

**實測**（`project-20261122-121207` 複本、埠 8790–8797，零 GPU）
| 項目 | 結果 |
|---|---|
| 官方驗收 `scripts/test_live_linkage.py` T1–T13 | 連跑三次 13/13 PASS（另一代理獨立複本、獨立埠再跑兩次也 13/13） |
| sh10 手改稿（1881 字）連存三次 | 逐字不動；`promptEnManual`／`adoptedManual` 三次都保留；sh07（＝編譯稿）未被誤標 |
| 補洞 b05 | 新增 sh29（no=15、dialogue、5.17s）；既有 28 鏡 `beatIds` 與順序一字不動；鏈序 `b01,b02,b05,b03,b04` |
| 孤兒 | 剛好 `sh25–sh28`；`/storyboard` 與 `GET 專案` 判定逐場一致 |
| validate | error 5 → 5（母本既有 3 條＋2 條補洞前的漏句／V1；補洞後 3） |
| 效能 | `save_project` 中位數 4.6ms、`GET /api/projects`（28 鏡重算）16.1ms、`/storyboard` 15.7ms |
| 幂等 | `POST /storyboard/build` 連兩次：第一次 created=`['sh29']`、第二次 `[]`，磁碟 sha 相同 |
| 壞資料 | 只有 meta 的空專案／0 鏡／參考圖路徑指向不存在檔 → 四個唯讀端點與 build 全 200，無 500 |

### 修正：空景鏡不再被塞進場次角色名單

`storyboard._group_character_ids()` 原本在整組 beat 都沒指名角色時退回場次角色名單，導致 `establish` 的空鏡被塞進兩個角色（實測 sh01「店內空鏡」），H3 照首幀把人物畫進空店。改為 `beat_chars` 為空且 `role != reaction` 時一律回空。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
