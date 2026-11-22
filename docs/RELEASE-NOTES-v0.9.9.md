# 發佈紀錄 v0.9.9

| 項目 | 值 |
|---|---|
| 版本 | **0.9.9**（預發版） |
| 日期 | 2026-11-22 |
| 發佈時間 | 2026-11-22 12:44:20（台北） |
| build 時間 | 2026-11-22 12:44:10 |
| git HEAD（發佈前） | c7ebccf fix: 故事提示詞改爆款結構、性別枚舉、移除角色背景、參考圖寫回改增量（v0.9.9） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.7 MB | `c31d226e7248c61685ec3c74c8b1cb4e962570cf294079c310b5c59d90de6d0a` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.9-乾淨版.zip` | 128.7 MB（134,906,609 B） | `9625d50c94711913727b4d71cfa9681c03e83109342ba7550f221a9e5156fa29` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.9`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.9  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 12 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.9-乾淨版.zip
```

## 本版變更摘要

### 修正：故事提示詞改爆款結構、性別寫不進下拉、背景欄位移除、參考圖刪了又復活

**為什麼**：使用者實測後回報四件事——故事寫出來「很差」、一鍵佈署的角色性別空白、角色頁的「背景」填了也沒用、刪掉的參考圖在生成完成後又出現。

- **故事擴寫改成爆款結構（只改提示詞，不加機械檢查）**：`story_outline` 從「600–1000 字、要包含結尾」改成三段骨架「鉤子／主線／爆點表」——鉤子寫前 3 秒的畫面與第一句台詞、禁止介紹世界觀與身世；爆點表逐回列「反轉／打臉／收割」與「已開始但未結束」的鉤子，最後一回不寫結局。`story_chapters` 改成每回四段硬要求（開場鉤→逼到二選一→一次翻轉或打臉→停在未完成的動作），並明文禁止收束句與在結尾解開懸念。前端 `CHAT_TASKS` 同步改寫，並把風格四項餵進訊息。依使用者定調：文章怎麼寫屬於個人意識，**只改提示詞**，不新增 validate 規則或後處理去判定文章好壞。
- **風格四項補進對話上下文**：`prompts._project_context` 的風格行加上「負向提示」，原本只帶類型／基調／視覺，模型收不到 negative。
- **性別寫成下拉選單吃得下的值（三處）**：模型常回「男性，喉結明顯，聲帶沙啞」，前端 `select` 對不到「女／男／其他」就顯示成空白（看起來像沒寫入）。現在：`deploy` 提示詞限定 gender 只能是「男／女／其他」；後端新增 `_norm_gender()` 收斂並在 warnings 寫明；前端 `selectField()` 遇到非枚舉值會動態補一個 option 顯示原值，舊資料不再假裝空白。
- **移除角色「背景」欄位（整條線拔掉）**：它不參與任何生成（生圖只吃 appearance／identityAnchors／outfits）。清掉六處——卡片 UI、`CHAT_TASKS` 的「生成身世背景」、`APPLY_FIELDS`／`MULTI_APPLY_FIELDS` 的套用目標、新角色預設值、`storage.APPLY_FIELD_WHITELIST`、以及生成端引用（`deploy.gen_cast` 的必填與輸出 schema、`gen_script` 的角色動機欄位、`storygen._card_context`）。舊專案殘留資料不主動刪除，`storage.validate_project` 不要求它，11 個專案仍 0 error。
- **長時間生圖任務不再用「開始時的舊快照」整份覆蓋專案**：立繪與多角度動輒幾分鐘到二十幾分鐘，開頭 `load_project`、結尾 `save_project`，中間的刪除與編輯全被蓋回去。新增 `imagegen.merge_character_refs()`，結束時重讀最新版、只把「本次真正產生的圖檔路徑」併進去（`only_paths` 必填，否則會把剛刪掉的舊圖誤判成新圖補回），`pipeline.logs` 用 `at+step+msg` 去重後補上。前端配套：刪參考圖立刻 `saveNow()`（先清 debounce timer）、任務完成只更新該角色的 `refImages`，不整批替換 `characters`。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 12 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
