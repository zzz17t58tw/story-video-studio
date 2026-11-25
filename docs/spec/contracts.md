# 1117 M0 技術契約勘察（contracts.md）

> 2026-11-17（台北）。作者：M0 子代理。所有結論皆附「檔案:行號」或「實測輸出」證據；未實測處明說。
> 實測環境：本機 ComfyUI（ComfyUI 0.45.0，.venv py3.12）已實際啟動於 127.0.0.1:8000 並成功送件兩單（見 §3/§4）。

## 0. 本機 ComfyUI 啟動方式（實測有效，M4/M5 必用）

ComfyUI 本體在 `C:\Users\user\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`（main.py），
但自訂節點／input／output／user 全在 `C:\Users\user\Documents\ComfyUI`。直接跑 main.py 會載不到
ComfyUI_MiniMaxH3_Director 等節點（實測 object_info 回 `{}`）。實測可用的啟動指令：

```bash
cd "/c/Users/user/Documents/ComfyUI" && .venv/Scripts/python.exe \
  "C:/Users/user/AppData/Local/Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI/main.py" \
  --port 8000 --disable-auto-launch \
  --input-directory "C:/Users/user/Documents/ComfyUI/input" \
  --output-directory "C:/Users/user/Documents/ComfyUI/output" \
  --user-directory "C:/Users/user/Documents/ComfyUI/user" \
  --extra-model-paths-config "C:/Users/user/Documents/ComfyUI/extra_model_paths.yaml"
```

- `--extra-model-paths-config` 必要：本檔已新增 `custom_nodes_local: base_path C:/Users/user/Documents/ComfyUI, custom_nodes: custom_nodes`（原檔備份 `extra_model_paths.yaml.m0bak`）。啟動日誌實測 `Adding extra search path custom_nodes C:\Users\user\Documents\ComfyUI\custom_nodes`、`0.1 seconds: ...ComfyUI_MiniMaxH3_Director`。
- 三個目錄參數必要：否則 input/output/user 會落到 AppData 安裝目錄（模型路徑由 extra_model_paths.yaml 的 `comfyui_shared` 段指向 ComfyUI-Shared/models，不用動）。
- 停止：`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"`（taskkill 在此 bash 環境有參數轉換問題，避開）。

## 1. 導演台 timeline.json 結構

來源：`director/pack.py`（`_group_json` L417-451、`build_export_pack` L534-659）＋ 工作流 JSON 實拆。

**timeline_data 是「一個巨大 JSON 字串」**：`MiniMaxH3Director` 節點的 `timeline_data` 是 `STRING` widget
（`nodes/director_common.py` L73-76），在工作流存檔的 `widgets_values` 陣位 **索引 11**（實拆
`templates/h3_director_r2v.ui.json`：`json.loads(wv[11])` 成功）。送 API 時放 `json.dumps(timeline)` 字串。

頂層 key（實拆 r2v 工作流 + `submit_director_fusion_smoke.py` L55-83 真實範例）：
`version`(4/5), `editMode`, `timelineMode`, `totalFrames`, `frameRate`, `width`, `height`, `refMaxSize`,
`output`, `videoClips`, `video`, `global`, `segments`（fl2v 用 `shots`，pack.py L562-565）, `gen`,
`runSelectEnabled`, `runSelection`。

`global` 內：`taskType`（中文 combo 字串，如 `"r2v — 参考主体生视频(Reference to Video)"`）、`prompt`、
`refs[]`、`refAudios[]`、`refVideos[]`、`referenceVideo`、`continuousReference`、`genImage`、
`commonEnabled/commonCollapsed`（pack.py L600-607 shared_json 欄位）。

每個 segment（`_group_json` L417-433 权威欄位表）：`id`, `prompt`, `negativePrompt`, `durationSec`,
`frameCount`/`length`（兩者互補寫）, `start`, `taskType`, `refs[]`, `refAudios[]`, `refVideos[]`,
`continuityFromPrev`, `refImageSize`, `loras[]`, `genImage{imageFile,fileName}`, `imageFile`,
`startImage/endImage{imageFile,width,height}`。i2v 段另有 `isStartFrame/isEndFrame/endImage`
（實拆 external_groups_i2v.json）。

ref 條目格式（實拆 external_groups_r2v.json）：
`{"index":0,"imageFile":"<input目錄檔名>","fileName":"","type":"input","subfolder":""}`；
refVideos 多 `durationSec/pairedAudioFile/previewImageFile`。

**槽位命名規則**：refs 的 `index` 0..8 → 提示詞用 `<Picture 1>..<Picture 9>`（最多 9 張，
`lib/ref_images.py` L1；`nodes/director_groups.py` L381）；音訊 `<Audio J>`、源影片 `<Video 1>`
（`lib/task_prompts.py` L46/L59）。打包檔名 `Picture{index+1}.<ext>`（pack.py L324）、
`Video[1-3]`、`Audio[1-3]`、`start/end.<ext>`（pack.py L37-41 正規式）。

真實小樣本（實拆 minimax_h3_director_external_groups_r2v.json 的 segment[0]，截斷）：
```json
{"id":"msjwsk4s1y247","start":0,"length":243,"frameCount":243,"durationSec":10,
 "prompt":"主體定義:\n<Picture 1> 是男孩主角…<Picture 2> 是寵物狗主角…[Shot 1] …",
 "negativePrompt":"bad video","taskType":"",
 "refs":[{"index":0,"imageFile":"C__Users_..._Gemini_Generated_Image.png","fileName":"","type":"input","subfolder":""},
         {"index":1,"imageFile":"...","fileName":"","type":"input","subfolder":""}],
 "refAudios":[],"refVideos":[],"genImage":{"imageFile":"","fileName":""}}
```

## 2. .mmxpack.zip 打包規格

來源：`director/pack.py`（檔頭註解 L3-4、`build_export_pack` L616-657、`extract_pack_zip` L682-702、`import_extracted_pack` L1019-1067）。

zip 內（全 ASCII 路徑，`ASCII_PATH_RE` L35 `^[A-Za-z0-9_./]+$`，非 ASCII 直接 `raise "Pack paths must be ASCII"` L674）：
- `pack.json`：`{format:"minimax-h3-director-pack", formatVersion:1, taskType, widgets:{steps,sampler,scheduler,cfg,shift_video,shift_audio,seed,task_type}, output}`（L589-599）
- `shared_params/shared_params.json`：`{commonEnabled,commonCollapsed,prompt,refs,refAudios,refVideos}`（L600-607）＋ 共享媒體檔 `Picture1.png` 等
- `asset_groups/NN/group.json`（NN=01 起，每 segment 一夾，L631-634）＋該段媒體檔
- `timeline.json`：完整 timeline（L635）
- `source_video/clip_N.mp4`、`extra/<ascii名>`（L571-577、L470-482）

導入端：`extract_pack_zip` 解壓＋防呆（`..`、隱藏檔、大小上限 16GB/8000 檔 L48-50）；
`import_extracted_pack` 把媒體整樹複製到 **`<input>/minimax_director_packs/<packId12hex>/`**（L1042-1044），
timeline 內所有路徑加前綴 `minimax_director_packs/<packId>`（L1046）。HTTP 路由：
`POST /minimax/director/export_pack`、`GET /minimax/director/download_pack`、`POST /minimax/director/import_pack`
（`director/http_routes.py` L661-664）。匯出 zip 暫存於 `<temp>/minimax_director_pack_export/`，TTL 1 小時（L51,63-66）。
本機 `input/minimax_director_packs/` 目前不存在（尚未匯入過，實測 ls 無此目錄）——屬正常。

## 3. 送單一鏡頭生成影片的 API 路徑（已實測跑通）

**可以**直接用 `POST http://127.0.0.1:8000/prompt`（API 格式 `{"prompt": {nodeId: {class_type, inputs}}}`）。
本機作者 `C:\Users\user\Documents\ComfyUI\submit_director_fusion_smoke.py` 就是現成可跑的 API 送件腳本（L76-107），
M0 據它構造最小 payload 實測成功。

r2v 工作流（templates/h3_director_r2v.ui.json）節點清單：1 UNETLoader、2 CLIPLoader(type=minimax)、
3/4 VAELoader(video/audio)、5 MiniMaxH3Director、6 CreateVideo(fps=24)、7 SaveVideo、8-10 PreviewAny、11 MarkdownNote。
MiniMaxH3Director 的 widgets_values 索引（**2026-11-18 實測修正**，從 `templates/h3_director_r2v.ui.json` 直接印出，共 21 格）：
`[0]task_type [1]global_prompt [2]bd_grp_sample [3]cfg(1.0) [4]seed(42) [5]control_after_generate("randomize") [6]frame_rate(24.0) [7]height [8]width [9]ref_max_size [10]total_frames [11]timeline_data(JSON字串) [12]bd_grp_advanced [13]steps(25) [14]sampler [15]scheduler [16]shift_video [17]shift_audio [18]bd_grp_perf [19][20]布林`
⚠ 舊版本文件把 `[7]` 寫成 width、`[8]` 寫成 height（順序相反），且沒標明 `[5]` 是 `control_after_generate`；照舊表改參數會把 24.0 寫進 width。**送件前一律用 `/object_info` 或重新印出 `widgets_values` 確認索引。**
（API 格式改用 inputs 欄位名，不用索引。）

**GET /api/object_info/MiniMaxH3Director 實測輸出**（ComfyUI 啟動後）：
required: model(MODEL), video_vae(VAE), audio_vae(VAE), clip(CLIP), task_type(COMBO 中文選項),
global_prompt(STRING), bd_grp_sample(BDGROUP), cfg(FLOAT), seed(INT), frame_rate(FLOAT), width(INT),
height(INT), ref_max_size(INT), total_frames(INT), timeline_data(STRING)。
outputs: `images, audio, fps, frame_count, source_images, report, images_pre_refine, images_pre_face_refine`。
輸出接法：`CreateVideo(images=["12",0], audio=["12",1], fps=24)` → `SaveVideo`。

**實測**：以 001.jpg 當 `<Picture 1>`、39 影格(1.6s)、480x864、steps=4 送件 →
`pid=9bdeb030-…`，status success，產出 `output/video/m0_contract_smoke_00001_.mp4`
（ffprobe：h264+aac，含音軌）。模型：unet `minimax_h3_ref2va_pruned_int8_convrot.safetensors`、
clip `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`、video vae `minimax_h3_video_vae_fp16.safetensors`、
audio vae `minimax_h3_audio_vae_fp32.safetensors`（皆在 Shared models，載入+生成約 20 秒內完成）。
⚠️ 影格數約束：`total_frames` 需 17k+1 附近（作者腳本 `BASE + ((5-(BASE%17))%17)` 對齊 243=10s@24fps）。

## 4. Qwen Image 2.1 生圖/改圖 payload 契約（已實測跑通）

模板來源：`qwen21/02_Qwen21_圖片編輯.api.json`（已是 API 格式）。節點：
1 UNETLoader(`qwen_image_2.1_int8_convrot`)、2 CLIPLoader(`qwen3vl_8b_int8_convrot`, type=`qwen_image`)、
3 VAELoader(`qwen_image_2.1_vae_bf16`)、9 QwenImage21Cache(可選加速)、10/11 LoadImage、
4 TextEncodeQwenImage21、6 KSampler、7 VAEDecode、8 SaveImage。

**GET /api/object_info/TextEncodeQwenImage21 實測**：required = clip, prompt(STRING), negative_prompt(STRING),
resolution(INT), images(COMFY_AUTOGROW_V3)；optional = vae(VAE)。outputs = positive/negative/latent。
- 提示詞寫在節點 4 `prompt`；參考圖用 `LoadImage`（`image` 值＝ComfyUI input 目錄檔名），
  以 `images.image_1`/`images.image_2` 連入，提示詞內用 `<image1>` `<image2>` 引用（工作流 L 實拆）。
- 尺寸由 `resolution`（如 1024）＋（t2i 時）`EmptyLatentImage` 的 width/height 決定；
  步數/cfg 在 KSampler：作者預設 steps=25, cfg=1, euler/simple, denoise=1。
- 輸出：SaveImage（t2i 檔名前綴 `qwen21_t2i`、編輯 `qwen21_edit`）。

**最小可送骨架**（i2i 編輯，一圖一參考）：
```json
{"prompt":{
 "1":{"class_type":"UNETLoader","inputs":{"unet_name":"qwen_image_2.1_int8_convrot.safetensors","weight_dtype":"default"}},
 "2":{"class_type":"CLIPLoader","inputs":{"clip_name":"qwen3vl_8b_int8_convrot.safetensors","type":"qwen_image","device":"default"}},
 "3":{"class_type":"VAELoader","inputs":{"vae_name":"qwen_image_2.1_vae_bf16.safetensors"}},
 "10":{"class_type":"LoadImage","inputs":{"image":"<input目錄檔名>.png"}},
 "4":{"class_type":"TextEncodeQwenImage21","inputs":{"clip":["2",0],"vae":["3",0],
     "prompt":"Keep the character in <image1> …","negative_prompt":"","resolution":1024,
     "images.image_1":["10",0]}},
 "6":{"class_type":"KSampler","inputs":{"model":["1",0],"positive":["4",0],"negative":["4",1],
     "latent_image":["4",2],"seed":0,"steps":25,"cfg":1,"sampler_name":"euler","scheduler":"simple","denoise":1}},
 "7":{"class_type":"VAEDecode","inputs":{"samples":["6",0],"vae":["3",0]}},
 "8":{"class_type":"SaveImage","inputs":{"images":["7",0],"filename_prefix":"qwen21_edit"}}}}
```
**實測**：雙 LoadImage 版本原樣送件（image 換成本機 001.jpg）→ `pid=9dfa2aec-…`，status success，
產出 `output/qwen21_edit_00001_.png`（1408x768，約 10 秒）。t2i 版（01_*.api.json，加 EmptyLatentImage
width/height 取代 latent_image=["4",2]）結構相同，未單獨實跑。

## 5. ffmpeg 合併多段影片（已實測）

ffmpeg 8.1.1（`ffmpeg -version` 實測），libx264/aac 皆可用的（`-encoders` 實測）。

**方案 A（優選，各段先統一參數再 concat copy）**：生成每段時就鎖 `-c:v libx264 -pix_fmt yuv420p -r 24 -c:a aac -ar 48000`，
合併只 copy，零轉碼損耗：
```bash
printf "file 'a.mp4'\nfile 'b.mp4'\n" > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
```
⚠️ 實測：參數不同的兩段（640x480@24/48k vs 1280x720@30/44.1k）直接 `-c copy` 雖 exit 0，
但輸出 duration 2.52s ≠ 2.0s（時間戳錯位、播放會抖）→ **不統一參數就不要用 copy**。

**方案 B（兜底，filter_complex 現場統一）**（實測 exit 0，輸出 2.024s、640x480@24、aac 48000）：
```bash
ffmpeg -i a.mp4 -i b.mp4 -filter_complex \
 "[0:v]scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-2:-2,setsar=1,fps=24[v0];\
  [1:v]scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-2:-2,setsar=1,fps=24[v1];\
  [0:a]aresample=48000[a0];[1:a]aresample=48000[a1];\
  [v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" \
 -map "[v]" -map "[a]" -c:v libx264 -pix_fmt yuv420p -r 24 -c:a aac -ar 48000 out.mp4
```
（注意 concat filter 的輸入順序是 v0 a0 v1 a1 交錯；先寫 `[v0][0:a]aresample…` 會報 Media type mismatch——實測踩過。）
音訊不同步風險：各段 aac 起點/採樣率不同 → concat copy 累積偏移；統一 `-ar 48000` + filter 路徑可消除。
H3 產出已是 h264+aac 24fps（§3 實測），M6 走方案 A 即可。

## 模板複製紀錄（原檔未動，copy2 保留時間戳）

| 模板檔 | 來源 | 複製時間 |
|---|---|---|
| templates/qwen21_i2i_edit.api.json | C:\Users\user\Documents\ComfyUI\user\default\workflows\qwen21\02_Qwen21_圖片編輯.api.json | 2026-11-17 05:36 |
| templates/qwen21_t2i.api.json | 同目錄 01_Qwen21_文生圖.api.json（多給一份 t2i） | 2026-11-17 05:36 |
| templates/h3_director_r2v.ui.json | …\workflows\minimax h3\AiMixer\minimax_h3_director_r2v.json | 2026-11-17 05:36 |

## 未實測／風險

- .mmxpack 匯入/匯出 HTTP 端點未實際打過（需 ComfyUI 前端或手動 POST）；規格以 pack.py 原始碼為準。
- 導演台「多段 batch」（segments 多段一次生成）未實跑；§3 只驗證單段。
- 本機 ComfyUI 是我（M0）啟動的（port 8000 原本沒在跑），勘察完**保持運行中**；M1 之後若要常駐，用 §0 指令或寫進 start.bat。
- docs/spec/data-model.md 主代理已寫好 v0.1（99 行），M0 未動它；其 `shots[].durationFrames` 與 contracts §3 的 17k+1 影格對齊規則一致，可直接沿用。
- extra_model_paths.yaml 被我加了一段 custom_nodes_local（備份 .m0bak）；ComfyUI Desktop 正常啟動是否也吃這段未驗證。
