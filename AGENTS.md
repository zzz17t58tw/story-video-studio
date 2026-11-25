# AGENTS.md — 給任何要操作「Story Video Studio」的 AI 代理人

這份檔案是**入口**。你（外部 AI 的代理人）第一次接觸本程式時，照下面順序做完，就能掌握全部細節；不要靠猜。

---

## 1. 三個入口（優先順序）

| 入口 | 內容 | 什麼時候用 |
|---|---|---|
| `GET /api/agent/guide` | 一份 Markdown 操作手冊（資料模型、硬限制、驗證規則、配方、禁止事項、全部 API 索引） | **每次接手先整份讀完**（約 1 萬字，可直接塞 context） |
| `GET /api/agent/manifest` | 機器可讀 JSON：每支端點的 purpose／when／副作用／schema 名稱、硬限制、驗證規則、配方 | 要程式化決策、或需要欄位清單 |
| `GET /openapi.json` | FastAPI 自動產生的完整 schema（`components.schemas` 是所有請求／回應結構） | 需要精確欄位型別時 |

本機位址：使用端 exe 是 `http://127.0.0.1:8788`，開發模式是 `8790`。先打 `GET /api/health` 確認 `{ok:true, version:"x.y.z"}`。

---

## 2. 這個程式在做什麼（一句話）

**把故事演成影片**：`劇本 beats（誰、說什麼、做什麼）→ 分鏡 shots（一鏡一事件、引用 beats）→ 首幀圖 → 片段影片 → 合併成片`。資料是單一事實來源，順序不能亂。

---

## 3. 三條一定會踩到的硬限制

1. **H3 單鏡合法長度 = 124–362 幀（24fps → 5.17–15.08 秒）**。短句不要硬塞事件，用留白（`padSec`）或合併相鄰 beat；超過 15.08 秒必須拆鏡。
2. **劇本 `beats` 是唯一真相**：分鏡只能挑選／分組，**不得改寫台詞**。改台詞回劇本層改。
3. **一張 GPU**：生圖／生影片／本地 LLM 不能同時跑；批次任務是「依序」，不要平行送。

完整清單：`GET /api/agent/manifest` 的 `hardLimits`。

---

## 4. 寫資料的唯一正確方式

- 單一欄位：`POST /api/projects/{slug}/chat/apply`
- 多欄位／整份陣列（章回、beats、服裝）：`POST /api/projects/{slug}/chat/apply-batch`
- 任何寫入只要**新增**驗證 error（V1–V5 等）就整批不寫並回 400 —— 這是設計，不是 bug。修改後務必 `POST /api/projects/{slug}/validate` 確認 0 error。
- **不要直接改 `projects/<slug>/project.json`**：會繞過驗證與原子寫檔。

---

## 5. 文件地圖（本 repo）

> 完整索引：**`docs/00-index.md`**（全部文件的位置與用途，含「新增文件該放哪」決策樹）。

| 檔案 | 內容 |
|---|---|
| `docs/00-index.md` | **文件總索引**（先看這份，再按需深入） |
| `docs/spec/storyboard-contract.md` | 分鏡契約 v1（三層真相、長度規則、V1–V5）｜**動分鏡前必讀** |
| `docs/spec/data-model.md` | 完整資料模型與不變式 |
| `docs/spec/contracts.md` | H3 送件契約（影格網格、參數） |
| `docs/spec/shots-live-linkage.md` | 鏡頭頁即時連動契約（真相／衍生分界） |
| `docs/dev/` | 開發工程：分支規矩、版本規則、待辦、外部調查 |
| `docs/testing/` | 夜間巡檢報告與驗收證據 |
| `app/agentdoc.py` | 本 Agent 層的單一真相（`ROUTE_NOTES`／`HARD_LIMITS`／`RECIPES`／`FORBIDDEN`） |
| `tools/check_agent_doc.py` | 文件完整度自檢：新增 API 沒寫說明會失敗 |

---

## 6. 給代理人的行為準則

1. 先讀 `/api/agent/guide`，再動手；不要用猜的欄位名稱。
2. 任何破壞性操作（刪專案、整份覆寫、`force` 重建分鏡、取消任務）先向使用者確認。
3. 生成很貴（時間與電），先用 `dryRun`／`preflight`／`prompt-preview` 驗，再送真的。
4. 回報時講「使用者看得懂的話」：哪一鏡、幾秒、缺什麼、下一步建議。
5. 發現程式行為與文件不一致 → 以程式為準，但要把文件補起來（`app/agentdoc.py` + 跑 `tools/check_agent_doc.py`）。

---

## 7. 自我檢查（維護者用）

```bash
python tools/check_agent_doc.py          # 0 = 每支 API 都有語意說明
curl -s 127.0.0.1:8788/api/agent/coverage | python -m json.tool
```
