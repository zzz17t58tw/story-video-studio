# 外部現成專案調查：構想 → 小說 → 分鏡 → 對白腳本 → 角色

調查時間：2026-11-17 22:40（台北，UTC+08:00）
調查方式：只讀 GitHub README / raw 檔 / GitHub API（stars、`pushed_at`、LICENSE、repo tree），**未 clone、未安裝、未執行**任何外部專案，未呼叫模型 API。
原則：每條事實後面附來源 URL；讀不到細節的會標「未驗證」。

> 我們的本機環境（實測）：ComfyUI 在 `http://127.0.0.1:8000`，`comfyui_version 0.45.0`（`curl /system_stats`）。文獻裡提到 ComfyUI 預設 8188 當「另一個獨立實例」，但我們只有一個 8000 的實例 → 下面的 fork 判断都以「能不能對接 8000 上的 AIMixer H3 導演台」為前提。

---

## 0. 一張表看全部

| # | 專案 | ★ | 最後 push | 授權 | 棧 | 純本機? | 能接 ComfyUI? | 一句話定位 |
|---|---|---:|---|---|---|---|---|---|
| 1 | [oiuv/ai-short-drama](https://github.com/oiuv/ai-short-drama)（雪风AI短剧工坊） | 50 | 2026-08-22 | **MIT** | Next.js 16 + React 19 + SQLite(better-sqlite3) | 否（DeepSeek/Seedream/Seedance 全雲端） | 否 | 六步流程 + 影視 Skill 最完整；README 自己聲明「本地部署 ≠ 離線模型」 |
| 2 | [xuanyustudio/LocalMiniDrama](https://github.com/xuanyustudio/LocalMiniDrama) | **2482** | **2026-11-17**（今日仍在推） | **MIT** | Vue3 + Node + Electron + SQLite | 否（資料本機，模型仍走 API） | 否 | 唯一高星＋超活躍＋MIT 的全流程短劇桌面工具 |
| 3 | [yy18570728781/AI-Comic-Director](https://github.com/yy18570728781/AI-Comic-Director) | 179 | 2026-09-23 | **AGPL-3.0** | Docker（Next/Node + DB），主 repo 無語言統計 | 否（Docker + 雲 API） | 僅概念（Agent Drama 版才有本地 ComfyUI） | 漫劇/小說轉影片，AGPL 紅燈 |
| 4 | [chatfire-AI/huobao-drama](https://github.com/chatfire-AI/huobao-drama)（火宝短剧） | **17631** | 2026-11-15 | **CC BY-NC-SA 4.0**（GitHub 判 NOASSERTION） | Hono + Drizzle + SQLite + Mastra + Nuxt3 + Electron | 否（需 API key；有 ModelRouter 可接自家端點） | **否**（接 Seedance / H3 / Wan 的**雲端 API**） | 最大的短劇專案，但**禁商用 + 禁改作**，程式碼不可用 |
| 5 | [TengXianhai/ai-short-drama-agent](https://github.com/TengXianhai/ai-short-drama-agent) | 1 | 2026-09-09 | **MIT** | 純 Python 腳本 + `AGENTS.md` + skills（給 coding agent 跑） | 否（火山方舟 + 智譜 + 小云雀訂閱） | 否 | 七步 + QC 門禁 + 斷點續作 + **身份三件套**；流程紀律最值得抄 |
| 6 | [lumosai8/MinimaxStoryBuilder](https://github.com/lumosai8/MinimaxStoryBuilder) | 20 | 2026-08-29（**只有 1 個 commit**） | **MIT** | ComfyUI 節點包（Python） | **是**（Planner 可跑本地 LLM/llama.cpp） | **原生**（就是 H3 ref2va 節點） | **與我們最貼合**：story → cast → shots → H3，含六段式 prompt 產生器 |
| 7 | [skishore23/comfy-story](https://github.com/skishore23/comfy-story) | 1 | 2026-10-07 | **AGPL-3.0** | ComfyUI 節點 + story memory 服務 | 是（本地 ComfyUI） | 原生（H3） | 可重用 cast + 故事記憶 + 時間軸；AGPL 紅燈 |
| 8 | [ZC89757/story-claw](https://github.com/ZC89757/story-claw) | 32 | 2026-11-12 | **MIT** | Node/TS + Electron + typebox | 部分（渲染本機，LLM/生圖/TTS 走雲） | **是**（自架 ComfyUI + H3 workflow） | 針對 200 章網路小說的**角色分階段參考圖 + 固定聲線 + 進度續作** |
| 9 | [benjiyaya/Calliope](https://github.com/benjiyaya/Calliope) | 306 | 2026-11-15 | **MIT** | FastAPI + SvelteKit + SQLite + Three.js | **是**（LLM 端點可指 localhost） | **是**（HTTP API + API-format workflow + role tag） | 本地 first 的 idea→video；**H3 六段式**已有確定性編譯器 |
| 10 | [storybored-app/storybored](https://github.com/storybored-app/storybored) | 9 | 2026-08-19 | **MIT** | Python(FastAPI) + Node 前端建置 + SQLite | 是（Ollama 可） | 是（純 HTTP，engine pack） | 導演的 board：takes/approve/animatic；**有 47KB 資料契約文件** |
| 備 | [sorker/ai-shotlive](https://github.com/sorker/ai-shotlive) | 351 | 2026-08-30 | **CC BY-NC-SA 4.0** | TS + Electron + MySQL + Docker | 否 | 否 | 借鑑 BigBanana/CutOS/Toonflow，非商用授權 |
| 備 | [MrYanq/PlotCraft](https://github.com/MrYanq/PlotCraft) | 1 | 2026-04-25 | MIT | Vite + Tauri 2 | 否 | 否 | 7 步工作流，README 只有行銷詞，無可借資產 |
| 備 | [yeluoge26/toonflow](https://github.com/yeluoge26/toonflow) | 4 | 2026-03-25 | **AGPL-3.0** | Node + Prisma + Electron | 否 | 否 | 早期「小說出視頻」 |
| 備 | [ljy532126/storycine](https://github.com/ljy532126/storycine) | 20 | 2026-06-24 | **無 LICENSE 檔**（README badge 寫 MIT，但無授權文件 → 以無授權處理） | Vue3 + Express + MongoDB + Docker | 否 | 否 | 舊，且授權不可信 |

星星數 / push 時間抓取於 2026-11-17 22:38–22:43，來源為 `https://api.github.com/repos/<owner>/<repo>`。

### 授權紅綠燈（因為我們要借程式碼）

- 🟢 **可搬程式碼**：`LocalMiniDrama`、`MinimaxStoryBuilder`、`Calliope`、`storybored`、`ai-short-drama`、`story-claw`、`ai-short-drama-agent`（全部 MIT）。
- 🔴 **不可搬，只可參考概念**：`huobao-drama`（CC BY-NC-SA：非商業 + 相同方式分享，搬到我們的程式會傳染整包）、`ai-shotlive`（同上）、`AI-Comic-Director` / `toonflow`（AGPL-3.0：網路服務也要開源）、`storycine`（**無 LICENSE**）。
- ⚠️ `comfy-story` 是 AGPL-3.0-only：自己在本機跑沒問題，**但把它的程式碼或衍生放進我們的 repo 就必須整體 AGPL 開源**。

---

## 1. 逐家細查（可借用資產為主）

### 1. oiuv/ai-short-drama — 雪风AI短剧工坊 🟢 MIT

- 六步：剧本 → 角色造型+空镜场景+关键道具 → 一致性参考图 → AI 分镜+逐镜视频 → 分集剪辑（README 的流程圖）。
- 技術：Next.js 16 / React 19 / SQLite；「本地優先」指資料在本機，README 明說「DeepSeek、Seedream、Seedance 仍是联网 API，并非离线模型」（`README.md` 第 138 行附近）。
- **可借資產**（全部在 repo 內，MIT）：
  1. **分鏡 prompt 契約**：`skills/drama-shot-prompt/SKILL.md`。它定義「1 shot = 1 次影片生成 = 4–15 秒」，並給出固定六段排版：`素材引用与主体定义 → 分镜提示词 → 音色说明 → 风格与画质 → 约束条件`；子鏡頭用「镜头1、镜头2」；台詞用 `{}`、音效 `<>`、BGM 全角括號、畫面文字 `【】`；輸出 JSON 為 `{shots:[{shotOrder, sceneName, characters[], action(≤30字), dialogue, prompt, duration(4–15整數)}]}`。
  2. **標籤規則**：角色標籤 `@角色名-形象名`（空格轉 `_`），`@` 只出現在定義段一次，正文只能用純文本名；提交前由程式按圖片順序確定性替換成「图片1、图片2…」。
  3. **DB schema**：`lib/db.ts`（45KB），soft delete、shot video details、planned episodes 都有 migration 可看（`scripts/migrations/`）。
  4. 劇本 Skill 組：`skills/drama-script/`（含 `satisfaction-model.md`、`theme-patterns.md`）、`skills/script-brief/`。
- 風險：★★50、2026-08 後沒動；影視 Skill 是「爽劇」套路（霸總/重生/戰神），與我們的故事類型未必合。

### 2. xuanyustudio/LocalMiniDrama 🟢 MIT，★2482，今日仍在推

- 八步：劇本 → 分集管理 → 角色圖 → 場景 → 道具 → 分鏡（景別/運鏡/台詞）→ 逐鏡圖+影片 → 合成整集；另有「劇內助手」對話式操作（可改劇本/角色/場景/分鏡並確認刪除）。
- 技術：Vue3 + Node + Electron + SQLite；模型走通义/火山/可灵/Gemini 等 API（README 徽章與「配置 AI」章節）→ **不是離線模型**。
- **可借資產**：
  1. `backend-node/migrations/13_character_identity_anchors.sql`：`characters` / `character_libraries` 各加 `identity_anchors`、`style_tokens`、`color_palette`、`four_view_image_url`、`appearance`。
  2. `backend-node/src/services/promptI18n.js` 的 `getIdentityAnchorsPrompt()`：把外貌描述蒸餾成 **6 鍵 JSON 錨點** — `face_shape` / `facial_features` / `unique_marks` / `color_anchors{hair,eyes,skin,primary_outfit 全部要求 Hex}` / `skin_texture` / `hair_style`，規則寫死「顏色一律 Hex、不得用 black/brown 這種色名、只抽已明說的、缺的寫 unspecified」。→ 這是**跨鏡鎖臉最輕量的做法**（不靠 LoRA、不靠 embedding）。
  3. `migrations/15_storyboard_angle_structured.sql`：把 `angle` 文本拆成三元組 `angle_h`（front/front_left/left/back_left/back/back_right/right/front_right）× `angle_v`（worm/low/eye_level/high）× `angle_s`（close_up/medium/wide），舊 `angle` 保留兼容。→ 分鏡角度**可枚舉**，能拿來做校驗與 UI 選擇器。
  4. `migrations/18_storyboard_narration.sql`：`narration` 與 `dialogue` 分欄（旁白 vs 角色對白分開給 TTS）。
  5. `migrations/17_character_stages.sql`：`characters.stages`（不同集不同外貌）。
  6. `migrations/14` 的 `ai_model_map`：業務鍵（`storyboard_gen`/`character_gen`/`frame_prompt`/`image_polish`）→ 模型路由表，一個 key 一個模型。
- 風險：94MB repo、Electron 桌面殼與我們純 web 的架構不同；借 schema 與 prompt 比借整個 repo 划算。

### 3. yy18570728781/AI-Comic-Director 🔴 AGPL-3.0

- README：`docker-compose up -d`，port 3005；主打「小說大綱 → 10 分鐘出分鏡影片」；另有 `AI-Comic-Director-Canvas` 無限畫布與 Agent Drama 桌面端（自己聲稱整合「本地 ComfyUI」）。
- LICENSE 檔開頭寫「This Docker directory is released under the same AGPL-3.0 license as the repository root」（`LICENSE`）。
- 借用結論：**AGPL → 只參考概念**（「角色圖/場景圖/首尾幀/音訊都能作為下游生成任務的參考」這種畫布式引用關係值得借鏡），不碰程式碼。

### 4. chatfire-AI/huobao-drama 🔴 CC BY-NC-SA 4.0，★17631

- 技術：後端 Hono + Drizzle + better-sqlite3 + **Mastra** agent + FFmpeg/Sharp；前端 Nuxt 3 + Vue 3；桌面 Electron。影片模型：Seedance 2.0、**MiniMax H3**、Wan 3.0 — 全為**雲端 API**（`backend/package.json` 只有 `@ai-sdk/openai`、`@ai-sdk/google`，無 ComfyUI client → 本專案**不能接我們的導演台**）。
- LICENSE 實文檔：`LICENSE` = Creative Commons **Attribution-NonCommercial-ShareAlike 4.0 International**（GitHub API 因此回 `NOASSERTION`）。→ **不可搬程式碼**。
- 值得**讀**的概念資產（不可搬）：
  1. `backend/src/db/schema.ts` 的 `storyboards` 表欄位設計：`shotType` / `angle` / `movement` / `result` / `atmosphere` / `imagePrompt` / `videoPrompt` / `bgmPrompt` / `soundEffect` / `duration` / `firstFrameImage` / `lastFrameImage` / `referenceImages` / `subtitleUrl` / `composedVideoUrl`，另有 `storyboardCharacters` / `storyboardProps` 多對多。
  2. `backend/workspace/prompts/storyboard_breaker.md`：**時長算式**可直接抄成規則（見 §3-A）：「一個分鏡 = 一個 8–15 秒生成任務，內含 2–4 子鏡頭」；「目標總時長 = 劇本字數 ÷ 500字/分鐘；段落數 ≈ 總時長 ÷ 12 秒，±20%」；「段落時長 ≥ 台詞總字數 ÷ 4.5字/秒 + 2 秒表演餘量」；影片 prompt 每 3 秒一段。
  3. `backend/src/services/final-prompt.ts`：角色→**三視圖**、場景→**固定視角+前中後景且絕對不能有人**（連背影、剪影、鏡中倒影都剔除）、道具→**純白背景單品**。這個「三種資產三種構圖契約」很清楚。
  4. `backend/workspace/skills/prompt-generator/*/SKILL.md`：角色/場景/道具/影片四種 prompt 產生器 Skill（中英日韓四語）。

### 5. TengXianhai/ai-short-drama-agent 🟢 MIT（★1，但含金量高）

- 形態很特別：**不是 app，是一包 skill + 19 支 Python 腳本**，給支援 `AGENTS.md` 的 coding agent 執行；產物在 `projects/{劇名}/`，`.drama-state.json` 存斷點。
- 七步：立項策划 → 剧本创作 → 剧本拆解 → 資產建設 → 分鏡提示詞 → 關鍵幀圖片 → 逐鏡視頻（→ 階段9 合成）。
- **可借資產**：
  1. **身份鎖三件套**（README「核心特性」）：「臉部參考圖 + 角色身份串 + seed」跨鏡鎖定；QC 門禁：機審（GLM 視覺）+ 零 API 機械校驗雙通道，FAIL 清零才放行。
  2. **成本紀律的工程化**：`prepare_videos.py`（零 API 備料）→ `check_videos.py`（16 類檢項硬門禁）→ `inspect_shot.py`（人工目檢登記）→ 試投 1–2 鏡 → `inspect_video.py` 抽幀 → 放量 `gen_videos.py --yes`（斷點防雙扣、限頻退避）。**這套「先生成 1 鏡再放量」的流程可以直接套在導演台上**（每次生成都是 GPU 時間）。
  3. `scripts/check_storyboard.py` 的分鏡機械校驗思路（零 API）：在花錢前用純規則檢查覆蓋率/順序。
  4. `docs/短剧生产全流程SOP.md`：十階段 SOP 與合規項（含 AI 標識卡、字幕）。
- 依賴全雲端（火山方舟、智譜、小云雀訂閱），所以**流程可借、程式不能直接用**（我們的模型是本地 H3 + Qwen）。

### 6. lumosai8/MinimaxStoryBuilder 🟢 MIT — **與我們最貼合**

- ComfyUI 節點包：`Story Planner → Story Casting (Krea2) → Story Voice Casting (H3) → Story Director (MiniMax H3)`，最後 `CreateVideo(24fps) → SaveVideo`（README「Suggested wiring」）。
- 全部本機：Planner 可用 ComfyUI 內建 CLIP 編碼器、`llama.cpp` 本地 gguf、或任何 OpenAI 相容端點（含 Ollama/LM Studio）。
- **可借資產（repo 層級）**：
  1. `SYSTEM_PROMPTS.txt`（45KB，把 planner prompt 原文攤開）＋ `prompts.py`（119KB）＋ `tests/test_prompts.py`（62KB）→ **三段式規劃**：CAST（一次）→ OUTLINE（一次）→ STAGING（每場景一次，每窗口 ≤6 shots）。理由寫在 `planner.py` docstring：整部一次 stage 會回「每個鏡頭一樣長、一樣兩拍、一樣一刀」，分場景才有多樣性。
  2. **plan JSON schema**（`planner.py` L32–53、L410–424）：
     `cast{style, mood, characters[{name, identity, appearance, voice, seen}], groups[{name,size,description}], locations[{name,description}]}`、
     `scenes[{text, summary, actions[], location, moves_to, characters[], groups[], time, pace, clip}]`、
     `shots[{text, characters[], groups[], pov, time, pace, seconds, location, beats[{start,end,cut,action,camera,location,time}], camera, dialogue[{speaker,language,line,delivery,off_screen}], vocals, soundscape, music}]`。
  3. **H3 六段式原生產生器**（README「Prompt format」）：`subject_definitions` / `summary` / `retention_analysis` / `detailed_description` / `overall_soundscape` / `non_diegetic_music`，並列出 5 條硬規則：每個 subject 都要在 `detailed_description` 被點名、crowd 是「無圖的 subject」並標 `weak_reference`、`retention_analysis` 只引用真的出現的 `[Shot N]`、`<Subject N>` 與 `<Picture i>` 是兩個命名空間（要明寫 bound to）、**LLM 不准自己寫 `<Picture i>` 序號**（用 `{{Mara}}` 佔位符，最後由程式按真實引用順序解析）。
  4. **幀格與時長**：H3 只吃 `17k+5`、訓練域 124–362 幀；124/226/328/362 幀 ≈ 5.17/9.42/13.67/15.08 秒（README 表格）；`segmentation.py` 負責切段與吸附。
  5. **角色參考圖的像素經濟學**：`ref_mode` = `portrait`(1 張特寫, 臉 ~640px) / `turnaround`(hero 大圖 + 5 個側視條, ~658px) / `sheet`(六宮格, 臉只有 ~142px)。理由：H3 會把參考圖縮到生成像素面積，六宮格浪費 5/6 在它永遠不會用的視角上。**預設用 portrait**。
  6. **取景規則**：ref2v 的身份住在「臉的像素量」上，所以只要有人的鏡頭一律 medium / medium close-up / close-up，導演的指示是「把動作演向鏡頭，不是橫過房間」；沒人的空景才可放 wide。
  7. **連續性 join**：`continuity` = off / same_location / always；用上一鏡**最後 22 幀**（不是 21——21 會被往下取整成 5）做 guide，`MiniMaxH3AddGuide` 釘在第 0 幀，做法比 frame chaining 不易累積漂移。
  8. `segmentation.py` + `tests/` 可在無 GPU 下跑 pytest（README「Tests」）。
- 風險：**只有一個 commit（2026-08-29「Initial release」）、★20**、作者 lumos675（同 ComfyUI-OrbitSheets 作者）。MIT 授權安全，但**不要 fork 當基礎**（無人維護）；當「規格書 + prompt 庫」用最好。

### 7. skishore23/comfy-story 🔴 AGPL-3.0

- ComfyUI 節點 + 編輯器（`comfy_story` 目錄）+ 本地故事記憶；`@Name` 引用角色/道具/地點；`Keep this moment` / `Keep @Name look` 把好結果存成視覺證據餵後續鏡頭；`Continue with references` / `Compose from references`。
- 概念可借：**associative memory（鏡頭歷史 + 已核准證據）決定最終參考圖集合**、reference 編碼快取、`start_frame/last_frame` 選項。
- AGPL-3.0-only（README「License」）→ **只參考概念**。

### 8. ZC89757/story-claw 🟢 MIT

- 針對「200 章網文」的三個痛點寫的：角色受傷後參考圖還完好、所有角色同一把聲音、同一鏡一直重抽。
- **可借資產**：
  1. **角色階段（stage）制**：每個角色一張 base 參考 + 只有「服裝或身上物件真的變了」才新增 stage 圖（校服→病號服→戰損）；每章由 pipeline 判斷要不要新增 stage。場景同理（教室白天 vs 考試夜）。→ 對「章回小說」場景是關鍵設計。
  2. **每本小說一份 `voice_map.json`**：角色第一次說話就從 voice pool 分配並寫入，之後每章每集按名字查回；旁白有專屬聲線不從角色池抽。
  3. 目錄約定：`characters/Alice.json` + `Alice_原型.png` + `Alice_{stage}.png`、`scenes/classroom.json`、`ep{NN}/storyboards/`、`改编进度.json`（跨集斷點）、`render.log`。
  4. **VLM 剪輯閘**：檢查 clip 首尾幀主體一致性與「憑空多出來的臉」，失敗自動重抽到上限，超過就接受不再卡整集。
  5. `runner/story-prompt-constraints.ts`：分鏡與 H3 共用的硬約束（一格一事、人不在畫就不寫他、手部特寫只描述可見部位、沿用同一 stage 不得無故換裝、不新增家具、遵守關係軸不越軸）；以及運鏡政策 `storyCameraMotionPolicy()`：固定或推近，「組內可朗讀字數 ÷ panel 數 ≥ 14」才允許小幅緩拉。
- 風險：LLM/生圖/TTS 都走雲（`config.json`/`image_gen_config.json`/`tts_config.json` 都是 API key），只有影片是本機 ComfyUI+H3；repo 256MB。

### 9. benjiyaya/Calliope 🟢 MIT，★306

- FastAPI + SvelteKit，SQLite 存專案、資料夾存媒體；四個階段 Story / Assets / Script / Video + Film view；可直接指自己的 ComfyUI。
- **可借資產**：
  1. **H3 六段式「確定性編譯器」**（README「Prompt profiles」）：把 scene 的 action/dialogue/characters/location 瞬間編譯成 `subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music`；**LLM rewrite 只在按 Regenerate 時才跑**。→ 對我們很有用：分鏡預覽不必等模型。
  2. **workflow role tag 契約**：節點標題 `Display Name (Input:prompt)` / `(Output:video)`；角色 `prompt|negative|width|height|character|location|image|video|audio|seed|duration|latent`，別名也列了（`char`/`portrait`/`sheet`/`face`/`ref`…）；多參考圖按 node-id 順序填，**這個順序就是 `<Subject N>` 的編號**。→ 我們接導演台時可以用同一套「不改節點 ID、只認標題」的辦法。
  3. **資料模型**（`calliope-backend/src/calliope/models/schemas.py`）：`Project{title, idea, genre, tone, target_duration, status, ingest_mode}`、`Beat{order_index,title,description}`、`Character{name, role, age, appearance, personality, **consistency_prompt**, portrait_path, sheet_path}`、`Location/Item{... consistency_prompt, reference_image_path}`、`Scene{order_index, heading, action, dialog, duration_sec, workflow_id, env_image_path, beat_id, character_ids[], location_id, item_ids[], video_settings}`、`Clip{order_index, description, shot_size, **dialog_lines_covered[]**, duration_sec, workflow_id, video_settings}`。
  4. **`skills_builtin/character-consistency/SKILL.md`**（2KB，MIT）：鎖臉五條——穩定特徵放最前（模型重前段 token）、每人一個 signature detail 且**逐字重用**、服裝寫成一套不要形容詞堆疊、參考圖寫「character reference sheet, front and side view, neutral pose, plain background」、negative 只放矛盾不放未陳述的風格偏好。
  5. **`dialog_lines_covered`**：一個 clip 覆蓋哪幾句台詞（scene 的 dialogue 是逐句列表，clip 用索引覆蓋）→ 保證「每句台詞剛好被一個鏡頭演到」，這個欄位我們的 `shots[].dialogue` 目前做不到。
  6. `ExpandClipsRequest{scene_ids, guidance, clip_cap}`：一場戲拆鏡有上限。
- 風險：★306 但 SvelteKit + FastAPI 兩段式部署，要 `comfy-api-proxy`（8189）當中介；內建 agent 系統（grant/session）比我們需求重。

### 10. storybored-app/storybored 🟢 MIT

- 導演向 board：projects → scenes → shots，takes 多人選、approve 才鎖、image→video、animatic MP4 匯出。
- **可借資產**：
  1. `docs/CONTRACT.md`（47KB，授權 MIT）：完整 SQLModel 資料模型 — `project{title, aspect_ratio, continuity_enabled}`、`scene{idx, title, slugline, description, **look**（一句 pinned 環境語言：地點/光/天氣/色盤）, plate_take_id}`、`shot{idx, description, shot_type(自由文本 WIDE/MED/CU), camera, dialogue, duration_s=4.0, motion_prompt, **frame_position(first|last)**, status(draft|queued|generated|approved), picked_take_id, video_take_id}`、`character{handle, trigger, class_word, lora_name, lora_strength, lora_family, bio, status}`、`take{kind, status, params_json, seed, error}`。
  2. **Continuity mode 的像素實現**：`scene.look` 確定性附加到每條 image prompt（「. Scene environment: {look}」），不叫模型；`scene.plate_take_id` + `shot.plate_hold`（loose/medium/tight → denoise .90/.75/.60）走 img2img 把同場景鏡頭拉回同一塊 plate。文中註明這是 8/18 實驗結果：img2img @ denoise .60–.75 勝過 ReferenceLatent（krea2 不穩）。
  3. **Engine pack manifest**（`workflows/<id>/manifest.json`）：`parameters[{key,type,node,input,default}]`、`output_node`、`character_injection{after_node, disable_nodes}`、`model_slots`、`frame_conditioning{node,first,last}`、`required_models`、`prompt_guide{style,examples}`。→ 換模型只要換 folder，不用改程式。
  4. **LLM breakdown 的產出契約**：`{scenes:[{title, slugline, shots:[{description, shot_type, camera, dialogue, duration_s, characters:[handle…]}]}]}` 是 **draft，不落庫**，再由 `apply-breakdown` 顯式寫入。
- 風險：角色一致性靠**訓練 LoRA**（要 24GB 卡、數小時），Windows 上訓練要 WSL2；與我們「靠參考圖 + H3 ref2v」路線不同。

---

## 2. 能力對照（只看我們主線需要的）

圖例：✅ 有 / ◐ 部分或需改造 / ✗ 無

| 能力 | oiuv | LocalMini | AIComic | huobao | drama-agent | MSB | comfy-story | story-claw | Calliope | storybored |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 故事擴寫 | ✅ | ✅ | ✅ | ✅ | ✅ | ◐(planner只拆不擴) | ◐ | ◐ | ✅ | ◐ |
| 章回小說 | ◐ 分集 | ◐ 分集 | ✅ | ◐ | ◐ | ✗ | ✗ | ✅ | ◐ | ✗ |
| 角色卡（外型/服裝/身世/聲音） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅(voice字串) | ◐ | ✅ | ✅ | ✅(bio) |
| 角色一致性手段 | 三視圖+@標籤 | 6鍵錨點+四視圖 | 角色庫 | 三視圖+@映射 | 參考圖+身份串+seed | **ref2v portrait/turnaround** | cast 參考庫 | **stage 圖+voice map** | consistency_prompt | **LoRA** |
| 場景卡 | ✅ 空鏡 | ✅ | ✅ | ✅ 無人物空鏡 | ✅ | ✅ plate | ✅ | ✅ 分時段 | ✅ | ✅ look+plate |
| 對白劇本 | ✅ | ✅ | ✅ | ✅ | ✅ | ◐(嵌在 beats) | ◐ | ✅ | ✅ dialog 逐句 | ✅ |
| 分鏡（景別/運鏡/時長） | ✅ | ✅ 三元組 | ✅ | ✅ | ✅ | ✅ beats+camera | ✅ | ✅ 固定/推近 | ✅ shot_size | ✅ |
| 分鏡圖 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅(直接 H3) | ✅ | ✅ | ✅ | ✅ |
| 影片生成 | ✅ 雲 | ✅ 雲 | ✅ 雲 | ✅ 雲 | ✅ 雲 | **✅ 本機 H3** | ✅ 本機 H3 | ✅ 本機 H3 | ✅ 本機 ComfyUI | ✅ ComfyUI |
| 合併長片 | ✅ FFmpeg | ✅ | ✅ | ✅ | ✅ | ✅ imageio-ffmpeg | ✅ | ✅ | ✅ ffmpeg 交叉淡化 | ✅ animatic |
| 與模型對話修整 | ◐ | ✅ 劇內助手 | ◐ | ◐ | ✅(agent 本身) | ✗ | ✗ | ◐ | ✅ agents | ◐ |

---

## 3. 結論 A：想「少走彎路」該抄哪三家的什麼

### 第一抄：**MinimaxStoryBuilder**（MIT）—— 抄「分鏡資料模型 + H3 六段式產生器」

理由：它是唯一「直接吐 H3 ref2va prompt、且全部本機」的，我們下游就是 H3 導演台。

要抄的具體資產：

1. `SYSTEM_PROMPTS.txt` / `planner.py` 的**三段式拆法**：CAST（一次，產出角色/群眾/地點/風格）→ OUTLINE（一次，切場景並給每場鏡頭預算）→ STAGING（**每場一次**，只帶該場文字 + 全片大綱）。直接解決「一次叫模型排完整片，回來每個鏡頭都一樣長」。
2. `planner.py` L410–424 的 **shot schema**：`beats[{start,end,cut,action,camera,location,time}]` — 把「這一鏡裡有幾個子鏡頭、第幾秒切、切到哪」寫成資料而不是散文。我們的 `shots[].spec` 目前只有一個 `cameraMove` 字串，做不到一鏡多子鏡。
3. **`<Subject N>` / `<Picture i>` 分離 + `{{name}}` 佔位符**規則：LLM 不准寫圖片序號，由程式按真實參考圖順序解析。這條能直接消掉我們最可能的錯（提示詞說「圖片1 是 A」但實際傳的是 B，模型會照錯的演）。
4. **ref2v 的取景與參考圖規則**：有人 → 只准 medium / MCU / CU；`ref_mode=portrait`（單張特寫），不要六宮格。這是它踩過的坑（臉的像素被稀釋）。
5. 幀格常數：`17k+5`、124–362 幀、24fps（`segmentation.py`），可拿來校驗我們 `durationFrames` 的計算。

> 注意：它是**單一 commit 的個人專案**，抄規格與 prompt，不要抄架構、更不要 fork。

### 第二抄：**huobao-drama 的分鏡時長算式 + 資產三契約**（🔴 只抄**規則文字**，不抄程式碼）

即使授權是 CC BY-NC-SA，**「一句劇本要拆幾個 8–15 秒鏡頭」是可以自己重寫的規則**（我們用自己的話寫進 prompt，不貼它的檔案）。

1. `storyboard_breaker.md` 的**可算式**：
   - 目標總時長 = 劇本字數 ÷ 500 字/分鐘
   - 段落數 ≈ 總時長 ÷ 12 秒（±20%）
   - 段落時長 ≥ 段內台詞字數 ÷ 4.5 字/秒 + 2 秒表演餘量
   - 過渡段 8–10s、敘事段 10–15s、爆點段 12–15s 且子鏡頭放慢
   → 這四條剛好能填 `shots[].durationSec` 的自動估算，我們目前是人工填。
2. **三種資產三種構圖**：角色=三視圖、場景=固定視角前中後景且**畫面內絕對不能有人（含背影、剪影、倒影）**、道具=純白背景單品。→ 我們的 `scenes[].promptEn` 沒有這條禁令，很容易生成「場景圖裡站著路人」，之後每鏡都會帶進去。
3. `schema.ts` 的 `storyboards` 欄位清單可當 checklist：`result`（畫面結果，區別於 `description` 過程）、`bgmPrompt`、`soundEffect`、`subtitleUrl`、`firstFrameImage`/`lastFrameImage`。

### 第三抄：**LocalMiniDrama 的角色錨點 + Calliope 的台詞覆蓋**（皆 🟢 MIT）

這兩家各有一個我們缺的「結構」，都很小、可獨立實作：

1. LocalMiniDrama 的 **6 鍵 identity anchors**（`promptI18n.js` 的 `getIdentityAnchorsPrompt`）：`face_shape / facial_features / unique_marks / color_anchors{hair,eyes,skin,primary_outfit}(全 Hex) / skin_texture / hair_style`。比我們現在的 `appearance.face + hair` 自由文本更能「逐字重用」，而逐字重用正是 Calliope 的 `character-consistency` skill 說的核心。
2. LocalMiniDrama 的 **`angle_h × angle_v × angle_s` 三元組**：把「鏡頭角度」從自由文本變成 8×4×3 的枚舉，可校驗、可下拉選。
3. Calliope 的 **`dialog_lines_covered`**：台詞是 scene 層的逐句陣列，clip 只記錄「我演第 3、4 句」。這保證不漏台詞也不重複演，而我們現在 `shots[].dialogue` 是各自複製一份字串，無法驗證覆蓋。
4. Calliope 的 **H3 六段式確定性編譯 + `(Input:role)` 節點標題契約**：預覽 prompt 不必等模型；換 workflow 只要改節點標題。

---

## 4. 結論 B：有沒有值得直接 fork/當基礎的？

**建議：不 fork 任何一家；把「分鏡引擎」做成我們自己的一層，只移植小模組。**

| 候選 | 能否當基礎 | 具體理由 |
|---|---|---|
| **Calliope**（最接近的架構） | **不建議 fork，但建議「借殼程度最高」** | 優點：MIT、FastAPI+SQLite、純 HTTP 接 ComfyUI、已有 H3 六段式編譯、role tag workflow 契約、`target_duration`、CLI 有 `--expect-hash` 防覆寫。致命點：它假設 ComfyUI 在 **8188**（README 預設）且要跑 `comfy-api-proxy` 到 8189；我們的實例是 **8000**，且導演台是**自訂節點**（AIMixer H3 導演台 segments/asset_groups），它的 `(Input:prompt)` 單一 prompt 槽位模型與導演台的多槽位（角色/場景/首尾幀/參考圖組）對不上，等於要重寫整層 adapter。另外它是 SvelteKit + 內建 agent grant 系統，比我們需要的大。 |
| **MinimaxStoryBuilder** | **不要 fork** | 是 ComfyUI 節點包、不是 app；只有 1 個 commit、★20、作者單人。當**規格與 prompt 來源**最好，當基礎風險最高。 |
| **LocalMiniDrama** | **不建議 fork** | ★2482 且活躍、MIT 很香，但 Electron 桌面殼 + 94MB repo + 全套雲端模型 adapter，我們要的「本機 H3 導演台」它沒有；fork 後要拆掉的比留下的多。 |
| **story-claw** | **不建議 fork** | MIT、本機 ComfyUI+H3，但 256MB repo、Cloud LLM/生圖/TTS 是硬依賴，且它是「小說→成片」CLI 管線，不是可編輯的工作台；它的 `stage` 概念值得搬，整套不值得。 |
| **ai-short-drama / drama-agent** | **不 fork** | 一個是 Next.js 全雲端、一個是給 coding agent 跑的腳本集，都不是「可長在本機 H3 上的 app」。 |
| 紅燈（huobao / ai-shotlive / AI-Comic-Director / toonflow / comfy-story / storycine） | **一律不 fork** | NC/AGPL/無授權：fork 會把我們的授權鎖死（NC 不能商用、AGPL 要整包開源）。 |

一句話：**沒有哪一家能直接當我們的底座——因為沒有任何一家把「AIMixer H3 導演台」當一等公民。** 我們的差異化本來就在導演台；正確策略是「自建 app + 移植 3 個小模組」：
1. `storyboard_splitter`（照 §3-A huobao 算式，自己寫）
2. `h3_prompt_compiler`（照 MSB 六段式 + `{{占位符}}` 解析，MIT 可改寫自 `prompts.py` 的規則）
3. `identity_anchors`（照 LocalMiniDrama 6 鍵，自己寫 prompt）

---

## 5. 結論 C：`docs/spec/data-model.md` 建議補的欄位（5 項，按優先序）

> 只在這裡列建議，**未改動** `data-model.md`。

1. **`story.episodes[]` 與 `voice` 的全域映射**（借 story-claw `voice_map.json`）
   現在 `characters[].voice` 綁在角色上，但同一角色跨集換裝/換聲時會缺「哪一集用哪個聲線」。建議：
   ```jsonc
   "voiceMap": { "c01": { "engine":"fish_s2", "refAudio":"media/voices/c01.wav", "assignedAt":"..." } },
   "narrator": { "engine":"...", "refAudio":"...", "note":"旁白專屬聲線，不從角色池抽" }
   ```
   理由：story-claw 明確寫「旁白有專屬聲線，永不從角色池抽」、且角色第一次說話才分配並永久寫回。

2. **`characters[].stages[]`（角色分階段造型）**（借 story-claw stage / LocalMiniDrama `17_character_stages.sql`）
   `outfits[]` 只管「日常/戰鬥」這種造型，但章回小說真正的需求是「第 12 章受傷後 → 第 13 章起參考圖要換」：
   ```jsonc
   "stages": [ { "id":"s1", "label":"入學", "fromChapter":"ch01",
                 "characterSheet":"media/characters/c01_stage1.png",
                 "outfitId":"o01", "note":"僅服裝或身上物件真的變了才新增" } ]
   ```
   理由：story-claw README「Characters and scenes evolve in stages」明說只在服裝/攜物有視覺意義的變化時才產生新 stage 圖——否則 200 章會每章重畫。

3. **`characters[].identityAnchors`（6 鍵結構化錨點）**（借 LocalMiniDrama `getIdentityAnchorsPrompt`）
   我們現在 `appearance.face`/`hair` 是自由文本，模型每次重寫都會漂。建議加**只讀、由模型離線蒸餾一次**的欄位：
   ```jsonc
   "identityAnchors": {
     "faceShape":"", "facialFeatures":"", "uniqueMarks":"",
     "colorAnchors": { "hair":"#1A0A00", "eyes":"#3D2B1F", "skin":"#F5DEB3", "primaryOutfit":"#2B2B2B" },
     "skinTexture":"", "hairStyle":""
   }
   ```
   規範：顏色一律 Hex；生成 `promptEn` 時**逐字貼上**這些錨點，不准改寫（對應 Calliope `character-consistency` 的「signature detail 逐字重用」）。

4. **`shots[].spec` 補三元組 + `beats[]` + `dialogLineRefs`**（借 LocalMiniDrama `15_storyboard_angle_structured`、MSB shot beats、Calliope `dialog_lines_covered`）
   ```jsonc
   "spec": {
     "shotType":"close_up|medium|wide",                 // 現有中文值建議保留顯示層
     "angle": { "h":"front|front_left|left|back_left|back|back_right|right|front_right",
                "v":"worm|low|eye_level|high",
                "s":"close_up|medium|wide" },
     "cameraMove":"static|push_in|pan_left|tracking|...",
     "framingRule":"has_person→medium/mcu/cu(禁wide)"   // MSB 硬規則，可做校驗
   },
   "beats": [ { "start":0.0, "end":5.0, "cut":false, "action":"...", "camera":"", "locationId":"" } ],
   "dialogLineRefs": [ 3, 4 ],        // 覆蓋 script 場次的第幾句，取代複製字串
   "sourceChapterId": "ch12",
   "stageIds": ["c01:s2"]             // 這一鏡要用角色的哪個 stage 參考圖
   ```
   理由：`shotType` 自由文本無法校驗；`beats` 是「一鏡多子鏡」的唯一表達法；`dialogLineRefs` 才能驗證「台詞無遺漏無重複」。

5. **`shots[].referencePlan`（參考圖槽位計畫，最多 9 張）**（借 MSB `<Picture i>` 解析 + Calliope role tag 順序）
   現在 `characters[].refImages` 只是池子，送出時沒有「這一鏡到底帶哪幾張、第幾號」。建議：
   ```jsonc
   "referencePlan": {
     "slots": [
       { "ordinal":1, "kind":"character", "id":"c01", "stage":"s2", "path":"...", "role":"portrait" },
       { "ordinal":2, "kind":"scene",     "id":"sc01", "path":"..." }
     ],
     "weakReferences": ["g01"],          // 無圖但要在 prompt 宣告的群眾
     "promptPlaceholders": { "{{c01}}":"<Subject 1>", "{{sc01}}":"<Subject 2>" }
   }
   ```
   理由：MSB 明說「`<Picture i>` 由程式按真實順序決定，LLM 不准自己寫」；一旦順序與 prompt 不符，模型會把錯的臉配給錯的名字，而且「它會相信你」。這個欄位就是「編譯六段式 prompt」的輸入。

---

## 6. 附帶：可以立刻做的三件小事（成本低、回報高）

1. 把 `promptEn` 的產生改成「模板 + 錨點逐字貼」而不是「叫模型自由寫」（Calliope `character-consistency` + LocalMiniDrama 錨點）。
2. 在送出前加一道**零 API 機械校驗**（借 drama-agent `check_storyboard.py` 思路）：`dialogLineRefs` 覆蓋率 100%、`<Subject N>` 與 `referencePlan` 一致、`durationSec` 在 H3 合法幀格內、有人的鏡頭不得是 wide。
3. 生成前先產 1 鏡試片再放量（drama-agent 的 `prepare → check → 試投 1–2 鏡 → 放量`）——在導演台上每次生成都是 GPU 時間。

---

## 7. 未查清 / 未驗證的地方

- **AIMixer H3 導演台的真實節點合約**：本次沒查（不屬於外部專案調查）。所有「能不能接」的判斷都是基於 README 描述與我們的 port 實測（8000）；導演台的 `segments` / `asset_groups` 具體欄位若與 MSB / Calliope 差異大，"移植 MSB 六段式" 的工作量要重估。
- **Calliope 的 H3 編譯器原始碼**：只讀了 README 與 `schemas.py`，沒讀 `authoring/*.py` 實際編譯函式（未驗證它的六段式模板與 MSB 版本有何差異）。
- **huobao / AI-Comic-Director 的 DB 完整 schema**：huobao 讀了 `backend/src/db/schema.ts`（SQLite Drizzle 版）；AI-Comic-Director 主 repo 沒有語言統計、README 也未列 schema，**未驗證**。
- **storybored 的 SQLModel 實作**：只讀 `docs/CONTRACT.md`（設計契約），未核對 `backend/storybored/models.py` 是否一致（**未驗證**）。
- **LocalMiniDrama 的角色分鏡 prompt 全文**：`episodeStoryboardService.js`（76KB）只確認存在、未逐段讀；其「經典參考圖模式 vs 全能創建模式」的差異**未驗證**。
- **各家實際生成品質**：全部未跑過，只依文件描述；「角色一致性」欄的評價是**機制層面**（有無參考圖/錨點/訓練），不是實測畫質。
- star / push 時間是 2026-11-17 22:38–22:43 的快照，會變。
