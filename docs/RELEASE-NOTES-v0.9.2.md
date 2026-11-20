# 發佈紀錄 v0.9.2

| 項目 | 值 |
|---|---|
| 版本 | **0.9.2**（預發版） |
| 日期 | 2026-11-20 |
| 發佈時間 | 2026-11-20 10:35:47（台北） |
| build 時間 | 2026-11-20 10:35:36 |
| git HEAD（發佈前） | 99616cd chore: VERSION 0.9.2（M11 逐步落地與除錯收版） |
| 產生工具 | `tools/release.py` |

## 產物與雜湊

| 用途 | 路徑 | 大小 | SHA-256 |
|---|---|---|---|
| exe（使用端） | `dist/StoryVideoStudio/StoryVideoStudio.exe` | 8.6 MB | `b122c78ea7ca36648f376ad7cde84ee26b912608f30906b134e86b02dd60c130` |
| 乾淨版 zip | `release\StoryVideoStudio-v0.9.2-乾淨版.zip` | 128.5 MB（134,773,179 B） | `cc24217837a6427d9feaa4a440f1b18debf573a461363d2cc34ed91a812be889` |

舊 exe 備份：`dist/StoryVideoStudio/StoryVideoStudio.exe.bak-0.9.2`（回滾用，可直接改回原名）

## 資料夾結構

```
dist/StoryVideoStudio/                 ← 你日常使用的版本（資料在此）
├── StoryVideoStudio.exe
├── StoryVideoStudio.exe.bak-0.9.2  ← 上一版備份
├── _internal/                ← 與 exe 一組，不可拆
└── projects/                 ← 11 個專案（發佈期間未動）

release/StoryVideoStudio/              ← 乾淨發佈包
├── StoryVideoStudio.exe
├── _internal/
├── projects/project-20261118-022129/  ← 只有《雨夜的便利商店》（1 個）
└── 使用說明.txt
release/StoryVideoStudio-v0.9.2-乾淨版.zip
```

## 本版變更摘要

### 變更：一鍵佈署改成「每完成一層就落地」（2026-11-20，使用者要求）

**為什麼**：原本四層全部在記憶體算完、最後才一次寫檔。輸出越大越容易「算完結果是空的」（配額被思考吃光），
而且任何一步失敗＝前面十幾分鐘全白做。改成每層各自生成、驗證通過就立刻寫進 `project.json`，
模型每次只扛一小包。

- `deploy.run_steps_landing()`：cast→寫檔、locations→寫檔、script→寫檔、**每場 shots 各自寫檔**。
  不再使用 `build_preview_with_progress`（保留給同步 `preview()` 用）。
- job 新增 `written` 欄位（`{characters, scenes, episodeScenes, shots}`），前端進度列即時顯示
  「已寫入：3 角色、2 場景、2 場劇本、3 鏡」，失敗／取消的訊息也列出已保留的內容。
- **取消**改為 `status="cancelled"`（原本誤標 failed），訊息「使用者取消（已完成的層級會保留在專案裡）；
  已保留在專案裡：3 角色、2 場景、2 場劇本、3 鏡頭」。
- 覆蓋模式的清空**延後到 cast 成功之後**：cast 就失敗時舊資料完整保留。
- 舊 media 檔最後才刪（前面任何一步失敗都還能還原）。
- 進度條在分鏡階段按場次前進（0.77 → 0.99），不再卡在 77%。

**驗收（實跑四輪）**

| 情境 | 結果 |
|---|---|
| 本地模型完整一輪 | 91.1s 完成；進度 2%→27%→52%→77%→88%→100%，`written` 逐步 3→3/2→3/2/2→3/2/2/3→3/2/2/6 |
| 本地模型（UI 實走） | 110.4s；進度列顯示「已寫入：3 角色、2 場景、2 場劇本、3 鏡」→「…6 鏡」 |
| 線上 deepseek 完整一輪 | 129.5s；`written` 同上逐步成長 |
| 中途取消（分鏡階段） | `status=cancelled`、訊息列出「3 角色、2 場景、2 場劇本、3 鏡頭」；專案實際保留 3 角色／2 場景／1 集／3 鏡 |

三個專案（本地版、線上版、品質基準）`validate` 皆 **0 error**；鏡頭 `no` 重排正確、六段式齊全。

### 驗收（本地模型 Qwen3.8-27B Q2_K_P 實走，2026-11-20）

同一支故事、同一個 UI 流程，把 provider 留在預設 `local` 再走兩輪（另加一輪思考開啟的對照）：

| 條件 | 總耗時 | 角色卡 | 場景卡 | 劇本 | 分鏡 | 落地結果 | 重試 |
|---|---|---|---|---|---|---|---|
| local（思考關，預設） | **76.8s** | 27.0s | 5.7s | 16.9s | 27.2s | 3 角色／**1 場景**（少 1）／1 場 | 劇本第 2 次 |
| local（思考關，覆蓋重跑） | **100.9s** | 45.5s | 9.6s | 14.4s | 32.2s | 3 角色／2 場景／1 集 2 場／6 鏡 | 角色卡第 2 次 |
| local（`SVS_LLM_THINKING=on` 對照） | **492.5s** | 216.6s | 32.7s | 54.5s | 188.9s | 3 角色／2 場景／6 鏡 | 角色卡第 3 次、分鏡漏台詞×2 |

- 結論：本機端點關閉思考（`chat_template_kwargs.enable_thinking=false`）是本地模型可用的關鍵——同一台機、同一模型，
  開思考時單是角色卡就要 216.6s（配額被思考吃光、結構還要重試三次），關掉後四步總計 77–101 秒、
  比線上 deepseek（108–145s）還快。原始證據：直接打 `:8080/v1/chat/completions` 問一句話，
  不帶 payload 時 `completion_tokens=64` 但 `content` 是空的（1.4s）；帶 payload 0.5s 就回完整答案。
- 本地產出的品質落差（可接受但要知道）：角色卡偶有幻覺角色（第一次跑多出故事沒有的「阿公」）、
  場景數偶爾少給（1/2，只警告不重試——prompt 明令「不要為湊數加無關場景」，故不強制補）、
  分鏡較常漏引台詞或重複引用（後端自動補齊到 100% 覆蓋並在提醒列出）。
  六段式、`promptEn` 純英文、時長合法格在本地版同樣成立；`validate` 0 error。
- 執行環境：先按「釋放 ComfyUI 模型」回收 3.3GB（VRAM 15.5GB→12.3GB 占用）後 llama 全速約 45 t/s；
  沒釋放時只剩 0.7GB 可用，會分層到 CPU。
- 截圖：`docs/shots/deploy-walkthrough-local-shots.png`。

### 修正（模擬使用者走一遍「一鍵佈署」發現）

- **覆蓋確認框用了刪除的文案**：一鍵佈署勾「覆蓋現有內容」送單時，`SVSConfirm` 把標題寫成
  「刪除「覆蓋現有內容？…」」、內文冠上「目前被 3 處引用：」、按鈕是「仍要刪除」，還補一句
  「這些引用會清成 null（鏡頭與劇本不會被刪）」——使用者會以為自己在刪資料。`SVSConfirm` 加
  `plain`／`okText` 選項，佈署改走 plain：標題原樣、條目直接列、按鈕「清空並重建」。
- **台詞量警告把動作句也算成台詞**：`deploy.shot_budget` 用全部 beats 估算，場次 8 beats（只 3 句台詞）
  卻警告「8 句共需約 51s 台詞塞不下」，還把鏡頭時長拉長。改為只算 dialogue，
  措辭改成「N 個演出點（X 句台詞＋Y 個動作）」。實測同題材總時長由 124–158 幀回到 124 幀為主。
- **完成摘要顯示設定值而非實際模型**：設定寫 `deepseek-chat`（帳號沒有）時後端自動改用
  `deepseek-flash`，摘要卻仍印 deepseek-chat。改為佈署前用 `llm.status()` 取實際模型寫進 job.params。
- **場景「禁人語」兩套判定標準**：`deploy` 要求英文 `no people in silhouette / in reflection`，
  `h3compose.validate_project` 只認中文句 → 一鍵佈署的場景永遠掛 2 筆 warn。校驗改為中英文任一皆可。
- **鏡頭類型下拉只有中文，資料是英文**：後端（`storage.shot_type_en`／`h3compose`／`imagegen`）全用
  `close_up|medium|wide|over_shoulder|pov`，前端 `SHOT_TYPES` 仍是中文 → 每張鏡頭卡顯示紅字
  「（引用已失效：medium）」。前端改成 value/label 對照＋舊中文值 alias，預設值與拆鏡頭草稿同步改英文。

### 驗收（模擬使用者實走一輪，線上 deepseek）

- 新專案 → 設定頁切 online（自動存檔）→ 故事頁填 premise／synopsis → 一鍵佈署（3 角色／2 場景／6 鏡／5 秒／覆蓋）。
- 第一次 108.3s、第二次（覆蓋重跑）144.5s；四步計時 cast 21–34s、locations 15–16s、script 14–22s、shots 44–85s。
- 落地結果：3 角色（identityAnchors 6 鍵齊）、2 場景、1 集 2 場（9／11 beats，台詞 3／4）、6 鏡；
  六段式 100%、`promptEn` 0 中文字、時長落在 124f／209f（17k+5）、`dialogLineRefs` 覆蓋全部台詞、
  `validate` **0 error**（warn 只剩「沒有參考圖」，屬預期）；七個分頁皆正常渲染、無 JS 錯誤。
- 截圖：`docs/shots/deploy-walkthrough-shots.png`、`docs/shots/deploy-walkthrough-script.png`。

### 修正

- **`/api/health` 的版本戳是硬編碼的舊里程碑**（`milestone: "M5b+M6"`，`FastAPI(version=...)` 亦同），
  發到 0.9.1 之後驗收時無法用 health 判斷版本。改由新增的 `app/version.py` 讀 `VERSION` 檔：
  health 回 `version`／`milestone: v<版本>`／`paths`（frozen、bundle、data、projects）；
  `StoryVideoStudio.spec` 的 `datas` 收錄 `VERSION`。

### 效能

- **設定頁輪詢的兩支狀態 API 在服務沒開時各要等 ~2 秒**（這台機器對未監聽埠回 TCP 拒絕要 ~2 秒，不是即時 RST）：
  `/api/gpu/status` 與 `/api/llm/status` 加唯讀探測快取（成功 5 秒／失敗 10 秒），
  實測重複查詢由 ~2100ms 降到 2–9ms；GPU 三個動作（release-comfy／stop-llama／start-llama）後主動清快取，狀態不會落後。
  動作路徑（chat、生圖、生影片）完全不受快取影響。

### 整理

- 移除未使用的 import：`server.py`（HTTPException／FileResponse／PlainTextResponse）、
  `deploy.py`（Callable、函式內未用的 `from pathlib import Path`）、`imagegen.py`（shutil）；`server.py` 換行統一為 CRLF。
- 清掉 `__pycache__/`、`temp/` 執行殘留與 `launcher.log`（執行痕跡不入庫）。

### 驗收（本次整理後實跑）

- 全 API 冒煙：10 個專案全載入、品質基準專案 `validate` **0 error**、`assemble/plan` 200（6 段）、`outputs` 1 支成品；
  專案 CRUD（建立／讀回／更新／刪除進 `_trash`）與錯誤輸入（空名稱 400、重複代號 400、不存在 404）皆正確。
- 前端七個分頁無 JS 錯誤、無 `undefined`、無 `[object Object]`；成品清單正常回 `output/final_*.mp4`。

## 驗證

- 使用端 `dist/StoryVideoStudio/projects` 發佈前後皆為 11 個專案（腳本內建檢查，數量變動即中止）
- 乾淨版 `projects` 只有 1 個專案：project-20261118-022129
- 乾淨版頂層必要項目齊全：StoryVideoStudio.exe, _internal, projects, 使用說明.txt
- 核對雜湊：`sha256sum dist/StoryVideoStudio/StoryVideoStudio.exe`（或 `certutil -hashfile`）與上表比對
