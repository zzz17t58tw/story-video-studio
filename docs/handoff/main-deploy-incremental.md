---
branch: main-deploy-incremental
milestone: M11
status: merged            # 直接 commit 在 main（未開分支），內容已在 main 上
opened: 2026-11-20 10:26（台北）
files: ["app/version.py", "app/server.py", "app/gpu.py", "app/llm.py", "app/deploy.py", "app/h3compose.py", "app/imagegen.py", "static/app.js", "StoryVideoStudio.spec", "CHANGELOG.md", "docs/TODO-backlog.md", "PLAN.md"]
---

# 交接單：main-deploy-incremental（M11：整理除錯 + 一鍵佈署逐步落地）

> 這張單是給「開新對話接手的人」看的。先讀 `## 接手第一步`，再讀 `docs/TODO-backlog.md` 尾端（2026-11-20 的條目）。

## 接手第一步

1. `git -C "C:/Users/user/Desktop/hermes work/1117" log --oneline -5` → 最上面應該是 `5a546eb`（逐步落地），工作區乾淨。
2. 起後端：`python -m uvicorn app.server:app --host 127.0.0.1 --port 8788`（**不要**加 `--reload`，watchfiles 會把 8788 殺掉）。
3. `curl -s 127.0.0.1:8788/api/health` → 應回 `version: "0.9.1"`、`milestone: "v0.9.1"`、`paths`（本輪新增欄位）。
4. 前端開 `http://127.0.0.1:8788/`，專案列表有兩個本輪建的驗收專案：`一鍵佈署驗收（整理後）`（線上）、`一鍵佈署驗收（本地模型）`（local）。

## 做了什麼

四輪工作，全部已 commit 在 `main`（`7518442` → `77834fa` → `dae5998` → `5a546eb`）：

1. **整理除錯**：`/api/health` 與 `FastAPI(version)` 改用 `app/version.py` 讀 `VERSION`（原本硬編碼 `M5b+M6`／`0.6-m5b-m6`）；`/api/gpu/status`、`/api/llm/status` 加唯讀探測快取（這台機器連未監聽埠要 ~2 秒才回拒絕），實測 2100ms → 2–9ms；清未用 import、`__pycache__`、`temp/` 殘留。
2. **模擬使用者走一輪一鍵佈署（線上 deepseek）**，抓出並修掉 5 個 bug：覆蓋確認框套用刪除文案、台詞量把 action 算成台詞、摘要顯示設定模型而非實際模型、場景禁人語兩套判定、鏡頭類型下拉只有中文（資料是英文 → 顯示「引用已失效」）。
3. **本地模型（Qwen3.8-27B Q2_K_P）實走三輪含對照**：思考關（預設）76.8s／100.9s 完成，`SVS_LLM_THINKING=on` 對照組 492.5s（cast 單步 216.6s、角色卡第 3 次才合格）。結論：本機端點關思考是本地可用的關鍵，推翻舊「6 次 4 次失敗」印象。
4. **一鍵佈署改成逐步落地**（使用者要求）：`deploy.run_steps_landing()`——cast／locations／script 各自寫檔，**每場 shots 各自寫檔**；job 新增 `written`；取消改 `cancelled` 並列出已保留內容；覆蓋的清空延後到 cast 成功後、舊 media 最後才刪。

## 動到的檔案

- `app/version.py`（新增）
- `app/server.py`（health 版本／paths、import 清理、GPU 動作後清探測快取、換行統一 CRLF）
- `app/gpu.py`（`_cached_probe`／`clear_probe_cache`）
- `app/llm.py`（`_cached_models`、補 `import time`）
- `app/deploy.py`（**主要改動**：`run_steps_landing`、`_save_now`、`_land_chars/_land_scenes/_land_script/_land_shots`、`_written_of`／`_kept_note`、`shot_budget` 只算 dialogue、摘要實際模型、取消語意）
- `app/h3compose.py`（場景禁人語校驗接受中英兩種寫法）
- `app/imagegen.py`（移除未用 `shutil`）
- `static/app.js`（`SVSConfirm` 加 `plain`/`okText`；`SHOT_TYPES` 改英文 value＋中文 label＋`SHOT_TYPE_ALIAS`；失敗／取消顯示 `written`；進度列顯示「已寫入：…」）
- `StoryVideoStudio.spec`（`datas` 加 `VERSION`）
- `CHANGELOG.md`、`docs/TODO-backlog.md`、`PLAN.md`（本交接單同步更新）
- 截圖：`docs/shots/deploy-walkthrough-{shots,script,local-shots}.png`、`docs/shots/deploy-incremental-progress.png`

## 介面變更

- `GET /api/health` 新增 `version`（讀 `VERSION`）、`paths`（`paths.describe()`）；`milestone` 改為 `vv0.9.1` 格式（原本 `M5b+M6`）。
- `GET /api/projects/{slug}/deploy/{jobId}`（含 `latest`）的 job 新增 **`written`**：`{characters, scenes, episodeScenes, shots}`。
- job `status` 新增可能值 **`cancelled`**（原本取消會標成 `failed`）；此時 `failedStep="cancelled"`。
- 內部函式（不影響前端）：`_run` 改呼叫 `deploy.run_steps_landing()`；`build_preview_with_progress`／`apply_preview` 仍保留（同步 `preview()` 在用）。
- 前端：`SVSConfirm({title, refs, okText, plain})`；`SHOT_TYPES` 由字串陣列改為 `{value,text}` 陣列（`spec.shotType` 合法值＝`close_up|medium|wide|over_shoulder|pov`）。

## 未完成／已知限制

1. **exe 還是舊的**：`dist/StoryVideoStudio/` 沒有重新打包，所有修正只在原始碼。要發版跑 `python tools/release.py`（固定 `--distpath build/release-work`，不碰使用端 projects）。**發版前先確認使用者沒開著程式。**
2. **本地模型品質落差**（可接受但要主動說明）：會幻覺故事沒有的角色（實測多出「阿公」）；**場景數屢次少給**（3 次裡 2 次只給 1/2，只警告不重試，因 prompt 明令「不要為湊數加無關場景」）；分鏡較常漏引台詞或重複引用（後端自動補齊＋提醒）。線上模型沒有這些問題。
3. **逐步落地的副作用**：分鏡中途失敗／取消時，已寫入場次有鏡頭、其餘沒有 → `validate` 會正確回報「台詞沒有覆蓋」。這是半套資料的正常結果，**不要改校驗規則去消音**。
4. `validate` 的「沒有參考圖」警告會隨鏡數重複（17–26 筆裡 6 筆同因），尚未去重。
5. 集標題會被模型自行命名（覆蓋重建時出現「第二集：…」）；新建專案的畫面比例只有 3 個選項（設定頁有 6 個）。
6. `app/server.py`（1394 行）、`static/app.js`（約 5400 行）仍未拆檔；`projects/_trash` 已累積多個回收專案，沒有清空／還原 UI。
7. 本輪驗收建的兩個測試專案（`project-20261120-091108` 線上、`project-20261120-093033` 本地）留在 `projects/`，要不要刪由使用者決定。

## 驗證方式

```bash
cd "C:/Users/user/Desktop/hermes work/1117"
python -m compileall -q app tools launcher.py && echo "compile ok"

# 起後端（背景）後確認版本戳
curl -s 127.0.0.1:8788/api/health

# 一鍵佈署（本地模型）逐步落地：written 應逐步長大
curl -s -X POST 127.0.0.1:8788/api/projects/project-20261120-093033/deploy \
  -H "Content-Type: application/json" \
  -d '{"characterCount":3,"sceneCount":2,"shotCount":6,"shotSeconds":5,"overwrite":true}'
# 再輪詢（jobId 換成上一步回傳值）
curl -s 127.0.0.1:8788/api/projects/project-20261120-093033/deploy/{jobId}（上一步回傳值）

# 校驗（三個專案都應 errors=0）
curl -s -X POST 127.0.0.1:8788/api/projects/project-20261120-093033/validate
curl -s -X POST 127.0.0.1:8788/api/projects/project-20261120-091108/validate
curl -s -X POST 127.0.0.1:8788/api/projects/project-20261118-022129/validate
```

- 跑本地模型前先 `POST /api/gpu/release-comfy`（實測回收 3.3GB），否則 VRAM 只剩 0.7GB、llama 會分層到 CPU。
- 想看思考開關差異：`SVS_LLM_THINKING=on python -m uvicorn app.server:app ...`（同一輪會從 ~100s 變 ~490s）。

## 是否動到契約（data-model.md / contracts.md）

**沒有動契約**。`spec.shotType` 的合法值本來就是英文（`docs/data-model.md` 第 134 行 v0.2 規格），本輪只是把前端下拉補上英文 value，屬「前端補齊既有契約」，不是改契約。
