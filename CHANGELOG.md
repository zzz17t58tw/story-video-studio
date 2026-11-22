# Changelog

本檔記錄 Story Video Studio 的所有重要變更。
格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號規則見 `docs/VERSIONING.md`（0.x 為預發版）。

## [Unreleased]

（尚無未發佈變更）

## [0.9.11] - 2026-11-22

### 整理：故事提示詞去重，規則回到單一真相

**為什麼**：0.9.10 的做法是「在舊句上疊新句」，結果自己打自己——`story_outline` 的主線叫人「展開成衝突升級」，爆點段又禁止寫「衝突升級」；`story_chapters` 的「解開懸念」在 ④ 與禁止清單各講一次、「整回沒有衝突」與「開場爆點／中段爆點」重複；前端 `CHAT_TASKS` 又把同一套規則重述一遍。三個來源互相打架，模型只會被分散注意力。

- **`prompts.py` 兩個任務整段重寫**（不是打補丁）：每條規則只出現一次，刪掉舊版遺毒——「反轉／打臉／收割」情節清單、「開場鉤」這個誤稱、「常見形式是…」的補充句。大綱改成三個具名段落（開場爆點／主線／爆點與鉤子表），章回改成四段固定順序＋獨立的寫作／禁止／收尾標記區塊。
- **前端 `CHAT_TASKS` 只留任務與資料**：故事頁 ①② 的 prompt 現在只寫「要做什麼」＋風格四項＋概念／大綱，不再重述任何規則；規則的唯一來源是 `prompts.py` 的 `_TASK_RULES`。
- **驗證（腳本檢查，不是目視）**：「衝突升級」1 次、「解開懸念」1 次、「反轉／打臉」0 次、「開場鉤」0 次、「常見形式」0 次；`py_compile`／`node --check` 通過。

## [0.9.10] - 2026-11-22

### 修正：爆點與鉤子的定義寫對——爆點抓當下注意力，鉤子勾下一段

**為什麼**：使用者指出 0.9.9 把兩者寫成「情節形式」而不是「功能」，故事的發展因此還是錯的。原稿把爆點定義成「反轉／打臉／收割」三選一、把鉤子定義成「未完成的動作」，模型照著寫會得到「結構完整但抓不住人」的段落。

- **爆點＝抓住觀眾注意力的那一下**：必須具體到一句台詞或一個畫面（一巴掌、刀抵住脖子、血濺到鏡頭、身世當眾揭露、當眾羞辱）。常見形式是反轉／打臉／衝擊畫面，但真正的標準是「正在滑手機的人，這一下會不會停住」，不准用「衝突升級」「氣氛緊張」這種抽象詞交差。**位置可以在一回的中段**。
- **鉤子＝勾起「想看下一段」的懸念**：一個未解的問題或未完成的危機（門後面是誰、他為什麼有那把刀、那句話是什麼意思）。標準是「在這裡關掉，會不會覺得沒看到答案」，**位置一定在結尾**。
- **前 3 秒正名為「開場爆點」**：原本誤標成「鉤子」。開場要的是抓住注意力（畫面＋一句台詞），不是留懸念。
- 大綱三段改名「開場爆點／主線／爆點與鉤子表」，章回四段改為「開場爆點→二選一→中段爆點→結尾鉤子」，並明確寫出兩者位置不同。只改提示詞（`prompts.py` 與 `static/app.js` 兩處同步），沒有新增任何機械檢查。

## [0.9.9] - 2026-11-22

### 修正：故事提示詞改爆款結構、性別寫不進下拉、背景欄位移除、參考圖刪了又復活

**為什麼**：使用者實測後回報四件事——故事寫出來「很差」、一鍵佈署的角色性別空白、角色頁的「背景」填了也沒用、刪掉的參考圖在生成完成後又出現。

- **故事擴寫改成爆款結構（只改提示詞，不加機械檢查）**：`story_outline` 從「600–1000 字、要包含結尾」改成三段骨架「鉤子／主線／爆點表」——鉤子寫前 3 秒的畫面與第一句台詞、禁止介紹世界觀與身世；爆點表逐回列「反轉／打臉／收割」與「已開始但未結束」的鉤子，最後一回不寫結局。`story_chapters` 改成每回四段硬要求（開場鉤→逼到二選一→一次翻轉或打臉→停在未完成的動作），並明文禁止收束句與在結尾解開懸念。前端 `CHAT_TASKS` 同步改寫，並把風格四項餵進訊息。依使用者定調：文章怎麼寫屬於個人意識，**只改提示詞**，不新增 validate 規則或後處理去判定文章好壞。
- **風格四項補進對話上下文**：`prompts._project_context` 的風格行加上「負向提示」，原本只帶類型／基調／視覺，模型收不到 negative。
- **性別寫成下拉選單吃得下的值（三處）**：模型常回「男性，喉結明顯，聲帶沙啞」，前端 `select` 對不到「女／男／其他」就顯示成空白（看起來像沒寫入）。現在：`deploy` 提示詞限定 gender 只能是「男／女／其他」；後端新增 `_norm_gender()` 收斂並在 warnings 寫明；前端 `selectField()` 遇到非枚舉值會動態補一個 option 顯示原值，舊資料不再假裝空白。
- **移除角色「背景」欄位（整條線拔掉）**：它不參與任何生成（生圖只吃 appearance／identityAnchors／outfits）。清掉六處——卡片 UI、`CHAT_TASKS` 的「生成身世背景」、`APPLY_FIELDS`／`MULTI_APPLY_FIELDS` 的套用目標、新角色預設值、`storage.APPLY_FIELD_WHITELIST`、以及生成端引用（`deploy.gen_cast` 的必填與輸出 schema、`gen_script` 的角色動機欄位、`storygen._card_context`）。舊專案殘留資料不主動刪除，`storage.validate_project` 不要求它，11 個專案仍 0 error。
- **長時間生圖任務不再用「開始時的舊快照」整份覆蓋專案**：立繪與多角度動輒幾分鐘到二十幾分鐘，開頭 `load_project`、結尾 `save_project`，中間的刪除與編輯全被蓋回去。新增 `imagegen.merge_character_refs()`，結束時重讀最新版、只把「本次真正產生的圖檔路徑」併進去（`only_paths` 必填，否則會把剛刪掉的舊圖誤判成新圖補回），`pipeline.logs` 用 `at+step+msg` 去重後補上。前端配套：刪參考圖立刻 `saveNow()`（先清 debounce timer）、任務完成只更新該角色的 `refImages`，不整批替換 `characters`。

## [0.9.8] - 2026-11-22

### 修正：影片片長沒對上鏡頭設定、首幀沒寫進提示詞、生影片警告嚇人

**為什麼**：使用者在鏡頭頁按「生影片」，`durationSec` 9.42 秒的鏡只生出 **5.17 秒**（124 幀），台詞一定唸不完；同時跳出 6 條互相矛盾的警告（說要用首幀當 `<Picture 1>`、又說沒有槽位、又說沒有任何 `<Picture N>`），看起來像程式壞了。

- **影格數跟隨鏡頭長度**：`h3gen.build_timeline()` 在 `opts` 沒給 `frames` 時，改用 `shot.durationSec × fps` 再吸附到 17k+5（H3 合法格）。前端單鏡生影片的 `frames` 預設也改成同一算法，並在欄位旁顯示「= 幾秒；本鏡設定 N 秒」。實測 9.42 秒 → **226 幀（9.417 秒）**，原本一律 124 幀／5.17 秒。
- **首幀當 `<Picture 1>` 真的寫進提示詞**：`compile_prompt()` 新增 `first_frame_as_picture`；當這鏡沒有角色／場景參考圖、送件時以首幀當 `<Picture 1>` 時，六段式第 1 段會明確寫「以 `<Picture 1>` 為準、保持同一張臉／髮型／體型／服裝」。修掉「系統說會用首幀鎖主體、編譯稿卻一個 `<Picture>` 都沒有」的斷點（H3 只會把它當氛圍參考）。`GET /shots/{id}/prompt-preview` 同步帶入，預覽＝真的送出的字。實測同一鏡：2973 字無 `<Picture>` → **3124 字含 `<Picture 1>`**。
- **生影片的警告分成「已自動處理」與「真的要你動手」**：原本標頭寫「2 項」卻列 6 條（其中一條是前兩條的重述），語氣像錯誤。現在固定三行——`✓ 已依官方六段式自動改寫提示詞（可直接生影片）`、`⚠ 這鏡沒有參考圖，本次用首幀當 <Picture 1>；要鎖臉請先生成立繪→建參考圖槽位`、`⚠ 未覆蓋場次台詞 N`；細節收在「看這次要送出的提示詞」面板（該面板的「未過規則」也改名為「原提示詞未過規則」並註明會自動改寫）。按「生影片」的確認從瀏覽器原生 `confirm()` 改成站內彈窗。
- **措辭不再誤導**：`collect_refs`／`compile_prompt` 把「沒有建槽位」和「槽位是占位圖」分開說，不再一律講成「占位圖」。
- 順帶（同批巡檢）：人物頁立繪說明去掉別的專案專屬文字、對話等待氣泡不再寫死「本機模型」、資料路徑改讀 `/api/health` 的 `paths.data`、validate 的「沒有參考圖」逐鏡重複 warn 併成一筆並標「被 第 N 鏡 用到」。

## [0.9.7] - 2026-11-21

### 新增：Agent 對接層——外部 AI 的代理人能掌握這支程式的每一個細節

**為什麼**：使用者要求「如果有使用者接入 AI，對方的代理人就能掌握這支程式的每一個細節」。實際痛點是：這支程式有 59 支 API、三層資料模型、一堆硬限制（H3 影格網格、取景規則、單張 GPU），外部 AI 只能靠猜，猜錯就寫壞資料或白花生成時間。

- **`GET /api/agent/manifest`**：機器可讀的完整說明。端點清單、參數 schema **直接從 FastAPI 的 OpenAPI 產生**（不會與程式漂移），再補上人工語意：每支端點的「用途／何時用／副作用」。另含 8 條硬限制、資料模型骨架、V1–V5 驗證規則、6 個常見配方、8 條禁止事項。
- **`GET /api/agent/guide`**：一份約 1.6 萬字元的 Markdown 操作手冊（可直接塞進 context）：連線資訊、操作順序、硬限制表、驗證規則表、配方步驟、禁止事項、全部 API 索引、背景任務輪詢方式、錯誤碼語意。
- **`GET /api/agent/coverage`** 與 **`tools/check_agent_doc.py`**：文件完整度自檢。**新增 API 卻忘了寫說明，檢查會失敗**（目前 59/59 全數有說明）。
- **`AGENTS.md`**（repo 根目錄）：入口檔，告訴任何 AI 先讀哪三個端點、三條必踩的硬限制、寫資料的唯一正確方式、文件地圖與行為準則。
- 單一真相：`app/agentdoc.py` 集中 `ROUTE_NOTES`／`HARD_LIMITS`／`DATA_MODEL`／`VALIDATION_RULES`／`RECIPES`／`FORBIDDEN`。

實測（8790，複本）：模擬「外部 AI 只讀 manifest 就操作」——找到分鏡重建端點 → 重建 18 鏡（104.38 秒）→ `validate` 0 error → 讀分鏡表 → coverage 59/59。`GET /api/agent/guide` 回 `content-type: text/markdown`、16443 bytes。



## [0.9.6] - 2026-11-21

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

## [0.9.5] - 2026-11-21

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

## [0.9.4] - 2026-11-21

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

## [0.9.3] - 2026-11-20

### 修正：佈署確認框被佈署參數窗蓋住（使用者回報）

**症狀**：按「開始佈署」跑出「覆蓋現有內容？」確認框時，確認框其實在**下面一層**——
因為 `#modal-deploy`／`#modal-gen` 是執行時才 append 到 `body`、DOM 順序在 `#modal-confirm` 之後，
而三者 z-index 同為 50，後進 DOM 的佈署窗就把確認框整片蓋住。使用者滑鼠點不到「清空並重建」，
只能在佈署窗背景亂點（那一下又把參數窗關掉），才會出現「在警告窗按繼續後參數窗不見了」的錯覺。

- `SVSConfirm()` 開啟前計算「目前所有可見彈窗的最大 z-index」，把確認框抬到其上（+10），關閉時還原。
- CSS 給 `#modal-confirm` 一個高於一般彈窗的預設層級（70，仍低於燈箱 80），雙重保險。
- 確認框開著時（`window.__svsConfirmOpen`）佈署窗的「點背景關閉」失效，避免誤以為參數窗被吃掉。
- 連按「開始佈署」不再開出兩個確認框（`dpState.confirming`）；完成摘要的「總計」缺 `elapsed` 時顯示 `—` 而不是 `undefineds`。

實測（dev 8790，真實滑鼠點擊）：修正前確認框 OK 鈕中心 `elementFromPoint` 是佈署窗的 `.dp-params`；
修正後確定打到 `.cf-ok`，按下去送出 1 次 `POST /deploy`（用假回應攔截，不動模型），
取消則回到參數窗、四個欄位與「開始佈署」仍可按。

## [0.9.2] - 2026-11-20

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

## [0.9.1] - 2026-11-19

基線版本：功能與 0.9.0 相同，差別是把「多對話／多分支並行開發」的協作機制正式入庫，
讓後續功能開發可以在乾淨的 git 起點上分岔。

### 新增

- **分支協作機制**：`docs/BRANCH-WORKFLOW.md`（四個真相檔、檔案歸屬表、🔴 高衝突檔規則、
  開分支五句邊界模板、合併檢查清單、常見失敗模式）、`docs/handoff/_TEMPLATE.md`、
  `tools/handoff.py`（`new` / `list` / `check` / `brief`；`check` 含 `git diff` 對帳，可抓出
  「交接單說沒改、實際卻改了」的檔案）。

### 文件

- `PLAN.md` 新增「目前狀態總覽」（版本、北極星達成、各層現況、已知未完成），防偏線規則補第 8、9 條
  （子代理一律走 git worktree 隔離；每輪開工前三檢查）。
- `docs/TODO-backlog.md` 標記已修項目並註明修在哪一輪；補入「`server.py`／`app.js` 未拆檔」技術債。
- `README.md` 文件索引補上分支協作規矩。
- `.gitignore` 排除 `temp/`（對話匯出與臨時腳本）。

### 文件
- README 改為對外首頁並補齊背景資料：①模型與工作流（每個模型檔附官方 GitHub，工作流附作者 GitHub）②引用的節點包與「僅參考概念」專案的授權對照 ③ComfyUI 0.45.0 實測環境、必要／選配節點與升級踩坑 ④開發與執行所用的 AI 代理、技能與依賴來源（Hermes Agent、`story-video-studio` skill、LLM、FastAPI/pywebview/ffmpeg）⑤免責聲明（純測試用途）。
- M1–M3 開發歷程移至 `docs/DEVLOG-M1-M3.md` 保留可追溯。

## [0.9.0] - 2026-11-18

第一個可交付的桌面版：從「一句故事概念」走到「一條含聲音的長影片」，全程在同一個程式裡完成，
並且打包成乾淨的 exe 資料夾交給使用者。

### 新增

- **專案與內容層（M1/M2a/M2b）**：FastAPI 後端（埠 8788）＋純靜態繁中深色前端；專案 CRUD、原子寫入不失資料；
  人物卡（外型六欄／服裝／身世／聲音參照／參考圖鎖臉）、場景卡、劇本場次與 beat、鏡頭分鏡卡。
- **與模型對話（M3a/M3b）**：任一卡片可開對話抽屜生成或改寫；模型服務可切 `local`（本機 OpenAI 相容 `:8080`）／
  `online`（DeepSeek）／`custom`，API key 只回傳「有無＋末四碼」；模型只回文字，一律由使用者按「套用」才寫入資料。
- **結構化生成（M3c）**：故事 → 整集劇本、場次 → 多鏡頭；一律「參數 → 可編輯 JSON 預覽 → 套用」，`dryRun` 預設開啟。
- **六段式提示詞與參考圖計畫（M3d）**：`identityAnchors`（5 條文字＋4 個 Hex 色票雙向同步）、`stages`；
  確定性模板編譯（錨點逐字貼、`<Picture N>` 由參考圖計畫決定）、全專案校驗面板（分色、可點跳卡）。
- **一鍵佈署（M8）**：`app/deploy.py` 四步 cast → locations → script → shots，背景 job＋輪詢；
  一次產生角色／場景／劇本／分鏡四層文字，圖片與影片欄位留空。線上模型實測總計約 108–215 秒。
- **生圖**：Qwen Image 2.1（mode=ref 有參考圖鎖臉／t2i）與 MOSS Image SFT（質感最強、t2i only）；
  前置檢查、批次生圖 job、角色立繪批次生成並寫回 `refImages.lockedFace`。
- **生影片與合併（M5a/M5b/M6）**：H3 導演台 r2v 直接送 ComfyUI API（背景 job、逐鏡回填）；
  影片批次＋進度輪詢；ffmpeg 合併成 `final.mp4`（含音軌），自動處理 H3 音訊 32k→48k 的重新編碼。
- **GPU 資源卡（設定頁）**：`GET /api/gpu/status`、`POST /api/gpu/{release-comfy,stop-llama,start-llama}`；
  停 llama 前會檢查 `/slots` 的 `is_processing`。
- **桌面化（M7a）**：`launcher.py`（pywebview 內嵌 uvicorn、自動選埠、關窗即完全結束）、`app/paths.py`（dev／frozen 雙路徑）、
  專屬 `.venv`（Python 3.14，不動系統環境）、`啟動 Story Video Studio.bat`、PyInstaller onedir 打包。
- **乾淨發佈包（M7b）**：`release/StoryVideoStudio/` 只留成果專案《雨夜的便利商店》＋`使用說明.txt`，
  壓成 zip；附 `tools/verify_release.py` 解壓實測腳本。
- **一鍵同步發佈（本版新增）**：`tools/release.py` 一次完成「打包→同步使用端 exe→重建乾淨版→壓 zip→寫發佈紀錄→git commit」；
  `VERSION`／`CHANGELOG.md` ／`docs/VERSIONING.md` 與 git 版控。

### 修正

- exe 首次啟動會把打包機的測試專案 seed 到別人電腦 → 乾淨版停止 seed，只在明確設定環境變數時才複製專案。
- `uvicorn` 在 `--noconsole` 下因 `sys.stdout is None` 起不來 → 啟動前換成帶 `isatty()` 的假流。
- 背景 thread 動 pywebview 視窗屬性會卡死 WebView2 UI 執行緒 → 改用 `ctypes` 置前。
- 參考圖槽位不足會靜默丟掉引用（`<Picture N>` 有寫、圖沒送）→ 擴充到 4 槽並在送件前比對。
- H3 輸出音訊為 aac 32kHz，多段 `-c copy` 會時間戳錯位 → 合併前 probe，參數不一致走 filter_complex 重編碼並統一 48kHz。
- 「一鍵佈署」鏡數欄位誤解成「每場 N 個」→ 改為總數並自動分配到各場；覆蓋模式預設勾選並在確認視窗寫明會清空什麼。
- 舊專案缺少新欄位時讀進來會 400 → 自動補預設值。
- `spec.angle` 改為物件後前端顯示 `[object Object]` → 改三個下拉。

### 已知限制

- **MOSS 生圖沒有參考圖鎖臉**（本機只 t2i）：角色一致性靠 `identityAnchors` 逐字貼＋固定 seed，
  要先產「正面全身、中性背景」立繪寫回 `refImages`，H3 才有臉可鎖。
- **H3 生影片很慢**：768×1344／5 秒／steps 12 約 355–366 秒／鏡，6 鏡全跑約 36 分鐘。生影片必須背景跑、不可同步等。
- **16GB VRAM 上 LLM 與 ComfyUI 互斥**：生圖前要先停 llama（約佔 12–13GB），生完要釋放 ComfyUI 再啟回，否則掉到 0.6–4 t/s。
- **本機 27B 跑一鍵佈署不穩**：約 6 次有 4 次需重試（輸出截斷、漏引台詞），建議用線上模型。
- 多段合併的畫質對照樣本 seed 不同，不是乾淨 A/B 對照。
- 《雨夜的便利商店》殘留瑕疵：sh02 櫃台位置、sh04 前景肩、c03 下顎外露；大綱結尾斷句待修。
- 拆鏡頭無除重（重按會再長一批，需 `sourceBeatId` 欄位）；刪參考圖只從卡片移除，實體檔仍留在 `media/`。
- `compile_prompt` 的動作段沿用中文 beat 原文，H3 的 `promptEn` 混中文會掉穩定性（待翻譯或人工改）。
- 未上 GitHub（源碼已有本地 git 版控，遠端與 Release 附件下一版處理）。
