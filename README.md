# Story Video Studio

> **版本 0.9.0**（預發版）　·　版號來源 `VERSION`　·　變更紀錄 [`CHANGELOG.md`](CHANGELOG.md)
> 從「一句故事概念」走到「一條可播放的長影片」的個人測試專案。

在單一程式裡完成：**故事 → 人物卡 → 場景卡 → 劇本（集／場／對白）→ 分鏡（六段式 prompt）→ 生圖 → 生影片（含音軌）→ 合併長片**。
每一層都能「跟模型對話」修整或手動編輯；故事寫好後可**一鍵佈署**到角色／場景／劇本／分鏡（只建文字，圖片留空待上傳或生成）。

- 介面：獨立桌面程式（pywebview 原生視窗，不是瀏覽器）／開發模式可用瀏覽器
- 執行環境：Windows + WebView2（Win11 內建）
- 資料：純 JSON 檔，每個專案一個資料夾，整包複製即可備份

---

## 快速開始

### 用發佈版（推薦）
1. 下載 [`Releases`](https://github.com/zzz17t58tw/story-video-studio/releases) 的 `StoryVideoStudio-vX.Y.Z-clean.zip`
2. 解壓到英文路徑（例如 `D:\StoryVideoStudio`，**不要**放 OneDrive／Dropbox 之類會同步的資料夾）
3. 雙擊 `StoryVideoStudio.exe`

內含一個範例專案《雨夜的便利商店》（31 秒成片），可直接播放看效果。

### 從源碼跑（開發模式）
```bash
python -m uvicorn app.server:app --host 127.0.0.1 --port 8788
# 瀏覽器開 http://127.0.0.1:8788（或雙擊 start.bat / 啟動 Story Video Studio.bat）
```

---

## 一、這個專案用了哪些模型與工作流

> 模型權重**不隨本專案散布**，需自行下載到 ComfyUI 的 models 目錄。以下為本機實測組合。

### 生圖（可在設定頁切換引擎）

| 引擎 | 模型檔 | 工作流來源 | 模型／工作流出處 |
|---|---|---|---|
| **MOSS Image** | `diffusion_models/moss_image_sft_int8_convrot`<br>`text_encoders/qwen3.5_9b_int8_convrot`<br>`vae/flux2-vae` | 社群工作流 `moss-image-sft`（作者：老臘肉 / [amao2001](https://github.com/amao2001)）。本專案另存一份**修正過的 int8 版**：把 CLIPLoader 從缺檔的 `qwen3.5_9b_bf16` 改為官方同系列 `int8_convrot`，避免 ComfyUI 前端靜默回退成 uncensored 權重。參數：768×1344、30 步、CFG 3.0、euler / sgm_uniform、skip-layer guidance 22 | 模型：[OpenMOSS/MOSS-Image](https://github.com/OpenMOSS/MOSS-Image)（Apache-2.0，含官方 ComfyUI 工作流） |
| **Qwen Image 2.1** | `qwen_image_2.1_int8_convrot`<br>`qwen3vl_8b_int8_convrot`<br>`qwen_image_2.1_vae_bf16` | ComfyUI 範例工作流 `01_Qwen21_文生圖` / `02_Qwen21_圖片編輯`（API 格式）。本專案把參考圖擴充到 4 張（`images.image_1..4`），並實測確認可送件 | 模型：[QwenLM/Qwen-Image-2.1](https://github.com/QwenLM/Qwen-Image-2.1) |

一致性策略差異：
- **Qwen 2.1** 走參考圖槽位（`<image1>`／`<Picture N>`），可鎖臉。
- **MOSS** 本機只配置 **t2i 底模**（未下載 edit LoRA），沒有參考圖鎖臉路線，角色一致性靠 `identityAnchors` 文字錨點（含 Hex 色票）逐字重用。

### 生影片（MiniMax H3 導演台）

| 項目 | 模型檔 |
|---|---|
| UNET | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| CLIP | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video VAE | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | `minimax_h3_audio_vae_fp32.safetensors` |

模型出處：[MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3)

工作流：**[AIMixer](https://github.com/AIMixer) MiniMaxH3 導演台**（節點：[AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)，Apache-2.0）。以 `r2v` 參考主體生視頻為主，另有 `i2v` / `fl2v` / `v2v` / `rv2v`。
本專案的 `app/h3gen.py` 直接組導演台的 `timeline_data`（r2v 單段）送 `POST /prompt`，輸出為 **h264 + aac（32 kHz）含音軌**。

實測時間成本（RTX 5070 Ti，僅供參考，會隨步數與解析度劇烈變動）：

| 設定 | GPU 時間 | 成片長度 |
|---|---|---|
| 39 幀 / 480×864 / 4 步 | ≈18 秒 | 1.6 秒 |
| 124 幀 / 704×1280 / 12 步 | ≈295 秒 | 5.2 秒 |
| 124 幀 / 704×1280 / 20 步 | ≈467 秒 | 5.2 秒 |

合併使用 `ffmpeg`：H3 音訊是 aac **32 kHz**，多段拼接若直接 `-c copy` 會時間戳錯位，因此程式會自動偵測並重編碼為 48 kHz。

---

## 二、開發時 clone / 引用的作者專案

### 直接依賴（ComfyUI 自訂節點，git clone）

| 專案 | 用途 |
|---|---|
| [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director) | **核心**：MiniMax H3 多段導演台節點（Apache-2.0） |
| [T8mars/Comfyui-Qwen-Image-2.1-BlockCache-T8](https://github.com/T8mars/Comfyui-Qwen-Image-2.1-BlockCache-T8) | Qwen 生圖加速（選用） |
| [T8mars/comfyui-minimax-h3-audio-T8](https://github.com/T8mars/comfyui-minimax-h3-audio-T8) | H3 音訊處理（選用） |
| [T8mars/comfyui-minimax-h3-prompt-enhancer-T8](https://github.com/T8mars/comfyui-minimax-h3-prompt-enhancer-T8) | H3 提示詞擴寫（選用） |
| [T8mars/Comfyui-Qwen-Image-2.1-MultiAngle-T8](https://github.com/T8mars/Comfyui-Qwen-Image-2.1-MultiAngle-T8) | 多視角生成（選用） |
| [Saganaki22/ComfyUI-FishAudioS2](https://github.com/Saganaki22/ComfyUI-FishAudioS2) | 角色配音（選用，未納入主線） |
| [Carasibana/ComfyUI-H3-FaceRefine](https://github.com/Carasibana/ComfyUI-H3-FaceRefine) | 臉部精修（選用） |

### 工作流來源（非 git，社群分享）
- **老臘肉（[amao2001](https://github.com/amao2001)）**：MOSS Image 工作流 `moss-image-sft`（本專案使用其修正版）；官方對應工作流見 [OpenMOSS/MOSS-Image `comfyui/`](https://github.com/OpenMOSS/MOSS-Image/tree/main/comfyui)
- **[AIMixer](https://github.com/AIMixer)**：導演台附帶的 `minimax_h3_director_*` 系列工作流
- **[T8mars](https://github.com/T8mars)**：H3 / Qwen 加速與擴充工作流

### 研究階段僅「參考概念與資料結構」、**未搬用任何程式碼**

| 專案 | 授權 | 借用的概念 |
|---|---|---|
| [lumosai8/MinimaxStoryBuilder](https://github.com/lumosai8/MinimaxStoryBuilder) | MIT | H3 ref2va 提示詞三段式、`beats` 結構、「有人物禁全景」規則 |
| [xuanyustudio/LocalMiniDrama](https://github.com/xuanyustudio/LocalMiniDrama) | MIT | 6 鍵角色身分錨點（含 Hex 色票）、鏡頭角度三元組 |
| [benjiyaya/Calliope](https://github.com/benjiyaya/Calliope) | MIT | 台詞覆蓋索引、確定性 prompt 編譯 |
| [chatfire-AI/huobao-drama](https://github.com/chatfire-AI/huobao-drama) | **CC BY-NC-SA 4.0** | 只參考分鏡時長算式，**未使用其程式碼** |

> 授權提醒：`huobao-drama`（NC-SA）、`ai-shotlive`（NC）、`toonflow`（AGPL）、`storycine`（無授權）等專案本專案**只讀概念**，未複製任何程式碼，避免授權污染。

---

## 三、ComfyUI 版本與必要的節點包

### 實測環境

| 項目 | 版本 |
|---|---|
| ComfyUI | **0.45.0**（導演台官方要求 **≥ 0.30.0**，且需含官方 MiniMax H3 節點 PR #15224 / #15228） |
| ComfyUI Frontend | 1.58.5 |
| Workflow Templates | 0.11.106 |
| Python | 3.11（後端）／ 3.14（桌面 exe 內建） |
| ffmpeg | 8.1.1（需 libx264、aac） |

### 必須具備
1. **MiniMax H3 官方節點**（ComfyUI 內建，隨版本升級）
2. **[AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)** — 生影片的核心；缺它 H3 路線完全不能用
3. **MOSS Image 支援**（ComfyUI 核心原生，`CLIPLoader type=moss_image`，不需自訂節點）
4. **Qwen Image 2.1 支援**（ComfyUI 核心原生）

### 選配（加速與周邊）
- T8 系列（Qwen BlockCache ／ H3 audio ／ prompt-enhancer ／ MultiAngle）
- `ComfyUI-FishAudioS2`（配音）、`ComfyUI-H3-FaceRefine`（臉部精修）
- 導演台選配依賴：`scenedetect`（智能分鏡）、`opencv-python-headless`（源影片解碼）、`imageio-ffmpeg`（抽原聲）

### 升級注意事項（本專案實測踩過的坑）
- **ComfyUI 升級後要重啟，前端要 Ctrl+F5**；`/api/object_info` 才是節點是否真的載入的依據（`has_errors` 抓不到缺節點）
- 導演台節點的 `widgets_values` 索引會隨 widget 增減而位移（`seed` 的 `control_after_generate` 會多佔一格），**送件前一律用 `/object_info` 或印出 `widgets_values` 核對**，不要照抄二手表格
- MOSS 工作流的 `CLIPLoader` 若指向不存在的檔名，前端會**靜默回退**成清單第一個可用權重（畫風與審查行為都會變），載入後務必讀回實際值
- 若沒設定 `--extra-model-paths-config`，放在外部目錄的自訂節點會載不到（`object_info` 回空）

---

## 四、開發與執行所用的 AI 代理、技能與依賴來源

本專案是「人 + AI 代理」協作開發，執行時也會呼叫 LLM 與外部服務。來源全部列出如下。

### 4.1 開發用的 AI 代理
| 項目 | 來源 | 授權 |
|---|---|---|
| **Hermes Agent**（開發代理框架：需求拆解、派子代理實作、實測驗收、發佈） | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | MIT |

### 4.2 專案使用的技能（skill）
| 技能 | 位置 | 內容 |
|---|---|---|
| `story-video-studio` | Hermes profile `skills/creative/story-video-studio/`（**不隨本 repo 散布**） | 本專案架構、派工紀律、建置與發佈流程、踩坑紀錄（`--noconfirm` 會清空 `dist`、H3 音訊 32 kHz、導演台 widgets 索引位移、CLIPLoader 靜默回退…） |
| `comfyui-*` 系列 | 同上 | ComfyUI 送件契約、模型家族查詢、加速堆疊等既有知識 |

> 技能檔是開發過程的知識沉澱，含本機路徑與個人環境設定，因此不進公開 repo；需要的內容已整理進 `docs/`（`contracts.md`、`data-model.md`、`VERSIONING.md`）。

### 4.3 執行時呼叫的模型
| 用途 | 供應商／模型 | 來源 |
|---|---|---|
| 對話與生成（線上） | DeepSeek `deepseek-flash` / `deepseek-v4-pro` | [deepseek-ai](https://github.com/deepseek-ai)（OpenAI 相容 API） |
| 對話與生成（本機） | Qwen 3.8-27B（GGUF 量化，llama.cpp 推論） | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) |
| 生圖 | MOSS Image | [OpenMOSS/MOSS-Image](https://github.com/OpenMOSS/MOSS-Image) |
| 生圖／改圖 | Qwen Image 2.1 | [QwenLM/Qwen-Image-2.1](https://github.com/QwenLM/Qwen-Image-2.1) |
| 生影片 | MiniMax H3 | [MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) |

### 4.4 其他關鍵依賴
| 套件 | 用途 | 來源 |
|---|---|---|
| ComfyUI | 圖／影片生成後端 | [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) |
| ffmpeg | 合併、重編碼、音視同步 | [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) |
| FastAPI | 本機 API 服務 | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| uvicorn | ASGI 伺服器 | [encode/uvicorn](https://github.com/encode/uvicorn) |
| pywebview | 桌面原生視窗 | [r0x0r/pywebview](https://github.com/r0x0r/pywebview) |
| PyInstaller | 打包成 exe | [pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller) |

---

## 五、免責聲明

1. **本專案純粹為個人測試與技術研究用途**，不是商業產品，未經完整測試，**不保證任何正確性、穩定性或可用性**。
2. 本專案**不散布任何模型權重、資料集或第三方節點**。使用者需自行取得並遵守各模型與節點原作者之授權條款（Apache-2.0／MIT／CC BY-NC-SA 等各自不同，**商用前請自行確認**）。
3. 由本程式生成之文字、圖像、影片、聲音內容，其正確性、合法性與相關法律責任**完全由使用者自負**。請勿用於侵害他人肖像權、商標權、著作權或散布違法內容。
4. 使用本專案所產生之任何後果（含資料遺失、硬體損壞、時間成本、第三方服務費用），作者**不承擔任何責任**。
5. 專案內附的範例資料（《雨夜的便利商店》之故事、角色、圖片與影片）皆為 AI 生成之虛構內容，與真實人物、事件無關。
6. 本專案以預發版（0.x）形式釋出，**API 與資料格式可能隨時變動且不另行通知**。

---

## 授權

本專案自身程式碼以 **MIT** 釋出。
不含 `templates/` 內來自第三方之工作流、亦不含任何模型權重；第三方專案授權各自隨原作者。

---

## 專案結構

```
app/          FastAPI 後端（storage / llm / comfy / imagegen / h3gen / deploy / assemble / h3compose）
static/       前端（純 HTML/CSS/JS，無 build、無 CDN）
templates/    ComfyUI 工作流模板（Qwen 生圖、MOSS 生圖、H3 導演台）
docs/         data-model.md、contracts.md、VERSIONING.md、RELEASE-NOTES、研究報告
tools/        release.py（一鍵打包發佈）、make_icon.py
launcher.py   桌面啟動器（pywebview + 內嵌 uvicorn）
projects/     使用者資料（不進版控）
```

- 資料模型：[`docs/data-model.md`](docs/data-model.md)
- 下游 ComfyUI 契約（實測）：[`docs/contracts.md`](docs/contracts.md)
- 版本與發佈：[`docs/VERSIONING.md`](docs/VERSIONING.md)
- 開發歷程（M1–M3 詳細紀錄）：[`docs/DEVLOG-M1-M3.md`](docs/DEVLOG-M1-M3.md)
- 主線與里程碑：[`PLAN.md`](PLAN.md)
