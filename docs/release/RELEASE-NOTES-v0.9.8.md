# 發佈紀錄 v0.9.8

| 項目 | 值 |
|---|---|
| 版本 | **0.9.8**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 01:00:58（台北） |
| build 時間 | 2026-11-22 01:00:51 |
| git HEAD（發佈前） | f798c1e chore: v0.9.8（影片片長跟隨鏡長、首幀寫進 <Picture 1>、生影片警告分區） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `e7ffe33b77c971b2f48a0168f893bbcbb8b4ab460913afd3b07a6daeea1178c4` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.8-乾淨版.zip` | 129.0 MB（135,268,799 B） | `ee292169d522490e34ecba024bbca1f21997bb940a5980a5e4b50030d32d5e5d` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.8`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.8  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.8-乾淨版.zip
```

## 本版變更摘要

### 修正：影片片長沒對上鏡頭設定、首幀沒寫進提示詞、生影片警告嚇人

**為什麼**：使用者在鏡頭頁按「生影片」，`durationSec` 9.42 秒的鏡只生出 **5.17 秒**（124 幀），台詞一定唸不完；同時跳出 6 條互相矛盾的警告（說要用首幀當 `<Picture 1>`、又說沒有槽位、又說沒有任何 `<Picture N>`），看起來像程式壞了。

- **影格數跟隨鏡頭長度**：`h3gen.build_timeline()` 在 `opts` 沒給 `frames` 時，改用 `shot.durationSec × fps` 再吸附到 17k+5（H3 合法格）。前端單鏡生影片的 `frames` 預設也改成同一算法，並在欄位旁顯示「= 幾秒；本鏡設定 N 秒」。實測 9.42 秒 → **226 幀（9.417 秒）**，原本一律 124 幀／5.17 秒。
- **首幀當 `<Picture 1>` 真的寫進提示詞**：`compile_prompt()` 新增 `first_frame_as_picture`；當這鏡沒有角色／場景參考圖、送件時以首幀當 `<Picture 1>` 時，六段式第 1 段會明確寫「以 `<Picture 1>` 為準、保持同一張臉／髮型／體型／服裝」。修掉「系統說會用首幀鎖主體、編譯稿卻一個 `<Picture>` 都沒有」的斷點（H3 只會把它當氛圍參考）。`GET /shots/{id}/prompt-preview` 同步帶入，預覽＝真的送出的字。實測同一鏡：2973 字無 `<Picture>` → **3124 字含 `<Picture 1>`**。
- **生影片的警告分成「已自動處理」與「真的要你動手」**：原本標頭寫「2 項」卻列 6 條（其中一條是前兩條的重述），語氣像錯誤。現在固定三行——`✓ 已依官方六段式自動改寫提示詞（可直接生影片）`、`⚠ 這鏡沒有參考圖，本次用首幀當 <Picture 1>；要鎖臉請先生成立繪→建參考圖槽位`、`⚠ 未覆蓋場次台詞 N`；細節收在「看這次要送出的提示詞」面板（該面板的「未過規則」也改名為「原提示詞未過規則」並註明會自動改寫）。按「生影片」的確認從瀏覽器原生 `confirm()` 改成站內彈窗。
- **措辭不再誤導**：`collect_refs`／`compile_prompt` 把「沒有建槽位」和「槽位是占位圖」分開說，不再一律講成「占位圖」。
- 順帶（同批巡檢）：人物頁立繪說明去掉別的專案專屬文字、對話等待氣泡不再寫死「本機模型」、資料路徑改讀 `/api/health` 的 `paths.data`、validate 的「沒有參考圖」逐鏡重複 warn 併成一筆並標「被 第 N 鏡 用到」。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
